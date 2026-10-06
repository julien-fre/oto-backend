"""Yousign — electronic signature (create a request, activate it, track its
status, retrieve the signed document).

Credential resolved per call via `access.resolve_credential("yousign", want="byo")`:
everyone sets THEIR key (byo user or org), no platform key. Two fields: the key
(`key`, secret) and the `environment` (`production` by default, or `sandbox`). Yousign
has two distinct HOSTS and a sandbox key is rejected by the production host (and
vice versa): it is this declared field that chooses the host — never a guess.

⚠️ Unlike a read connector (gocardless), `yousign_envoyer`
really WRITES: it notifies real people by email. The cycle is
deliberately split into two calls (`yousign_creer` then `yousign_envoyer`)
rather than a single gesture that would send without confirmation — an agent that
composes a request can re-read it before triggering the send.

**Signature fields come from the PDF's Smart Anchors.** Yousign refuses
to activate a signer who has no signature field. Coordinates are never sent:
the PDF carries one `{{sN|signature|width|height}}` anchor per
signer (N = the signer's rank, s1 = first), and `yousign_creer` creates the
signers BEFORE the document so that this rank is the one in the list. A PDF that
carries fewer anchors than signers is refused, and the draft deleted:
a template without an anchor is to be fixed, not hidden.
"""
from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Any, Callable

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

if TYPE_CHECKING:  # the annotation of `_client()` only — never evaluated
    from oto.tools.yousign import YousignClient

#: Where the user creates their key, in THEIR Yousign account.
OU_CREER_LA_CLE = "Yousign → Developers → API keys"

#: The syntax of an anchor, as recalled in a refusal.
ANCRE = "{{sN|signature|width|height}}"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _sandbox(environment: Any) -> bool:
    """The credential's `environment` → sandbox host? Empty = production. A value
    outside the declared set (the field's `choices`) is refused at setup; here we
    refuse too, rather than fall back to a random host."""
    valeur = str(environment or "production").strip().lower()
    if valeur not in ("production", "sandbox"):
        raise _bad(f"Yousign: unknown environment \"{environment}\" — "
                   "\"production\" or \"sandbox\".")
    return valeur == "sandbox"


def _detail(body: Any) -> str:
    """The readable reason for a Yousign refusal (`problem+json` body: `detail`, otherwise
    `message`/`title`)."""
    if not isinstance(body, dict):
        return str(body or "")
    return str(body.get("detail") or body.get("message") or body.get("title") or body)


def refus(e: Any) -> str:
    """The instruction matching a Yousign refusal (`UpstreamHTTPError`)."""
    status, detail = e.status_code, _detail(e.body)
    if status in (401, 403):
        return (f"Yousign rejects this key ({status}): it is unknown, revoked, or "
                "set on the wrong environment — a sandbox key is not valid "
                "in production, and vice versa (`environment` field of the Yousign card). "
                f"Create a key at {OU_CREER_LA_CLE}.")
    if status == 404:
        return f"Yousign: not found (404){' — ' + detail if detail else ''}."
    return f"Yousign refused the request (HTTP {status}): {detail}"


def _run(fn: Callable[[], Any]) -> Any:
    """4xx → named refusal (the call is to be changed, or the key). 429 and 5xx stay what
    they are: the error taxonomy classifies them as retryable, rightly so."""
    from oto.tools.common import UpstreamHTTPError

    try:
        return fn()
    except UpstreamHTTPError as e:
        if 400 <= e.status_code < 500 and e.status_code != 429:
            raise _bad(refus(e)) from None
        raise


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `list_signature_requests` is the cheapest read that requires a valid key
    with no side effect (unlike `create_signature_request`, which
    would create a real draft). The host follows the credential's `environment`."""
    from oto.tools.common import UpstreamHTTPError
    from oto.tools.yousign import YousignClient

    try:
        YousignClient(api_key=fields["key"],
                      sandbox=_sandbox(fields.get("environment"))
                      ).list_signature_requests()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Yousign HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Yousign: HTTP {e.status_code}: {e.body}")


def _client() -> YousignClient:
    """The Yousign client for THIS caller's credential, on the host of its
    `environment`. The real import is done in the body: tests replace the
    client, and the version-skew probe reads the return annotation."""
    from oto.tools.yousign import YousignClient

    rc = access.resolve_credential("yousign", want="byo")
    fields = rc.fields
    return YousignClient(api_key=fields["key"], sandbox=_sandbox(fields.get("environment")))


def _pdf(pdf_base64: str) -> bytes:
    try:
        contenu = base64.b64decode(pdf_base64, validate=True)
    except (binascii.Error, ValueError):
        raise _bad("`pdf_base64` is not valid base64 — encode the whole PDF, "
                   "without a `data:` header or line breaks.") from None
    if not contenu:
        raise _bad("`pdf_base64` is empty.")
    return contenu


def _signataires(signataires: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not signataires:
        raise _bad("`signataires` is required: at least one signer "
                   "[{prenom, nom, email, langue?}].")
    infos = []
    for i, s in enumerate(signataires):
        if not isinstance(s, dict):
            raise _bad(f"signataires[{i}]: an object {{prenom, nom, email, langue?}}.")
        manquants = [k for k in ("prenom", "nom", "email") if not str(s.get(k) or "").strip()]
        if manquants:
            raise _bad(f"signataires[{i}]: {', '.join('`' + m + '`' for m in manquants)} required.")
        infos.append({"first_name": s["prenom"], "last_name": s["nom"],
                      "email": s["email"], "locale": s.get("langue") or "fr"})
    return infos


def _composer(c: YousignClient, nom: str, pdf: bytes, nom_fichier: str,
              infos: list[dict[str, Any]], message_livraison: str) -> dict:
    """Draft → signers (in order) → document, anchors parsed. No
    send. Raises if the PDF does not have one anchor per signer."""
    corps: dict[str, Any] = {}
    if len(infos) > 1:
        corps["ordered_signers"] = True     # the list order is the signing order
    demande = c.create_signature_request(nom, delivery_mode=message_livraison, **corps)
    demande_id = demande["id"]
    try:
        signataires_crees, precedent = [], None
        for info in infos:
            extra = {"insert_after_id": precedent} if precedent else {}
            reponse = c.add_signer(demande_id, info, **extra)
            signataires_crees.append({"id": reponse["id"], "email": info["email"]})
            precedent = reponse["id"]
        # Multipart: the value goes out as text, hence the string "true".
        fichier = c.add_document(demande_id, pdf, nom_fichier, parse_anchors="true")
        ancres = (fichier or {}).get("total_anchors") or 0
        if ancres < len(infos):
            raise _bad(
                f"the PDF carries {ancres} signature anchor(s) for {len(infos)} "
                f"signer(s): Yousign refuses to activate a signer without a signature "
                f"field. Add to the PDF one {ANCRE} anchor per signer, on a "
                f"single line, N = the signer's rank in `signataires` (s1 = first). "
                f"No fallback on coordinates: the request was not created.")
    except Exception as e:
        try:
            c.delete_signature_request(demande_id)
        except Exception as nettoyage:
            raise _bad(f"{_message(e)} — and draft {demande_id} could not be "
                       f"deleted ({nettoyage}): to be removed by hand in Yousign.") from e
        raise
    return {"demande_id": demande_id, "document_id": fichier["id"],
            "ancres": ancres, "signataires": signataires_crees}


def _message(e: Exception) -> str:
    """An error's text, whether it is already a named refusal or an upstream refusal."""
    from oto.tools.common import UpstreamHTTPError

    if isinstance(e, McpError):
        return e.error.message
    if isinstance(e, UpstreamHTTPError):
        return refus(e)
    return str(e)


def register(mcp: FastMCP) -> None:
    connector_verify.register("yousign", _verify)

    @mcp.tool()
    def yousign_creer(
        nom: str,
        pdf_base64: str,
        nom_fichier: str,
        signataires: list[dict[str, Any]],
        message_livraison: str = "email",
    ) -> dict:
        """Composes a signature request: creates the draft, attaches the
        signers then the PDF. SENDS NOTHING — `yousign_envoyer` triggers
        the actual sending of the invitations.

        ⚠️ The PDF must carry one signature anchor per signer, written in
        the document on a single line: `{{s1|signature|180|60}}` for the
        first signer, `{{s2|signature|180|60}}` for the second, etc.
        (field width and height in points). Yousign places the field at
        the anchor's location. Fewer anchors than signers → refusal, and
        nothing is created.

        Returns `{"demande_id", "document_id", "ancres", "signataires": [{"id",
        "email"}]}` — to be passed as is to `yousign_envoyer`/`yousign_statut`/
        `yousign_document_signe`.

        Args:
            nom: name of the request (1-128 characters), visible to the
                signers in the email.
            pdf_base64: the PDF to have signed, base64-encoded.
            nom_fichier: file name as it will appear (e.g. "nda.pdf").
            signataires: `[{"prenom", "nom", "email", "langue"?}, …]`, at least
                one — `langue` ∈ en|fr|de|it|nl|es|pl|pt|ro, default "fr". With
                several signers, the list order is the signing order
                (each signs after the previous one) and the rank N of the
                `sN` anchor.
            message_livraison: "email" (Yousign sends the link) or "none"
                (the link is to be distributed yourself: `yousign_envoyer` returns it).
        """
        pdf = _pdf(pdf_base64)
        infos = _signataires(signataires)
        return _run(lambda: _composer(_client(), nom, pdf, nom_fichier, infos,
                                      message_livraison))

    @mcp.tool()
    def yousign_envoyer(demande_id: str) -> dict:
        """Activates a request composed by `yousign_creer`: leaves the draft
        state, notifies the signers (unless `message_livraison="none"`).

        ⚠️ Really sends emails to real people — no clean
        undo once sent (only `cancel`, which closes the
        request without erasing its trace).

        ⚠️ The response carries each signer's `signature_link`: a SENSITIVE
        link, which lets its holder sign. Pass it only to the
        intended person (that is the point with `message_livraison="none"`).
        """
        return _run(lambda: _client().activate_signature_request(demande_id))

    @mcp.tool()
    def yousign_statut(demande_id: str) -> dict:
        """Status of a signature request and of its signers.

        `statut` ∈ draft (composed, not sent) | ongoing (sent, awaiting)
        | done (all signers have signed) | expired | canceled
        | declined | rejected | paused | approval. Only `done` means that
        the signed document is ready (`yousign_document_signe`)."""
        demande = _run(lambda: _client().get_signature_request(demande_id))
        return {"statut": demande.get("status"), "nom": demande.get("name"),
                "signataires": demande.get("signers"), "documents": demande.get("documents")}

    @mcp.tool()
    def yousign_document_signe(demande_id: str, document_id: str) -> dict:
        """Downloads the signed PDF of ONE document of a `done` request.

        Before `done`, returns the ORIGINAL unsigned document (Yousign does not
        distinguish the two through this call — check `yousign_statut`
        first). A request with several documents takes one call PER
        document (no grouped download).

        Returns:
            `{"nom_fichier", "pdf_base64"}` — the PDF base64-encoded (the
            MCP transport carries no raw binary).
        """
        pdf_bytes = _run(lambda: _client().download_document(demande_id, document_id))
        if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
            raise _bad(f"Yousign returned no file for document {document_id} "
                       f"of request {demande_id} — check both identifiers "
                       "(`yousign_statut` lists the documents).")
        return {"nom_fichier": f"{document_id}.pdf",
                "pdf_base64": base64.b64encode(pdf_bytes).decode()}
