"""Yousign — signature électronique (créer une demande, l'activer, suivre son
statut, récupérer le document signé).

Credential résolu par appel via `access.resolve_credential("yousign", want="byo")` :
chacun pose SA clé (byo user ou org), pas de clé plateforme. Deux champs : la clé
(`key`, secrète) et l'`environment` (`production` par défaut, ou `sandbox`). Yousign
a deux HÔTES distincts et une clé sandbox est refusée par l'hôte de production (et
inversement) : c'est ce champ, déclaré, qui choisit l'hôte — jamais une devinette.

⚠️ À la différence d'un connecteur en lecture (gocardless), `yousign_envoyer`
ÉCRIT réellement : elle notifie des personnes réelles par email. Le cycle est
volontairement scindé en deux appels (`yousign_creer` puis `yousign_envoyer`)
plutôt qu'un seul geste qui enverrait sans confirmation — un agent qui
composerait une demande peut la relire avant de déclencher l'envoi.

**Les champs de signature viennent des Smart Anchors du PDF.** Yousign refuse
d'activer un signataire qui n'a aucun champ de signature. On n'envoie jamais de
coordonnées : le PDF porte une ancre `{{sN|signature|largeur|hauteur}}` par
signataire (N = rang du signataire, s1 = premier), et `yousign_creer` crée les
signataires AVANT le document pour que ce rang soit celui de la liste. Un PDF qui
porte moins d'ancres que de signataires est refusé, et le brouillon supprimé :
un gabarit sans ancre est à corriger, pas à masquer.
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

if TYPE_CHECKING:  # l'annotation de `_client()` seulement — jamais évaluée
    from oto.tools.yousign import YousignClient

#: Où l'utilisateur crée sa clé, dans SON compte Yousign.
OU_CREER_LA_CLE = "Yousign → Développeurs → Clés d'API"

#: La syntaxe d'une ancre, telle qu'on la rappelle dans un refus.
ANCRE = "{{sN|signature|largeur|hauteur}}"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _sandbox(environment: Any) -> bool:
    """`environment` du credential → hôte sandbox ? Vide = production. Une valeur
    hors du jeu déclaré (`choices` du champ) est refusée à la pose ; ici on refuse
    aussi, plutôt que de retomber sur un hôte au hasard."""
    valeur = str(environment or "production").strip().lower()
    if valeur not in ("production", "sandbox"):
        raise _bad(f"Yousign : environnement « {environment} » inconnu — "
                   "« production » ou « sandbox ».")
    return valeur == "sandbox"


def _detail(body: Any) -> str:
    """Le motif lisible d'un refus Yousign (corps `problem+json` : `detail`, sinon
    `message`/`title`)."""
    if not isinstance(body, dict):
        return str(body or "")
    return str(body.get("detail") or body.get("message") or body.get("title") or body)


def refus(e: Any) -> str:
    """La consigne qui correspond à un refus de Yousign (`UpstreamHTTPError`)."""
    status, detail = e.status_code, _detail(e.body)
    if status in (401, 403):
        return (f"Yousign refuse cette clé ({status}) : elle est inconnue, révoquée, ou "
                "posée sur le mauvais environnement — une clé du bac à sable ne vaut pas "
                "en production, et inversement (champ `environment` de la carte Yousign). "
                f"Crée une clé sur {OU_CREER_LA_CLE}.")
    if status == 404:
        return f"Yousign : introuvable (404){' — ' + detail if detail else ''}."
    return f"Yousign a refusé la requête (HTTP {status}) : {detail}"


def _run(fn: Callable[[], Any]) -> Any:
    """4xx → refus nommé (l'appel est à changer, ou la clé). 429 et 5xx restent ce
    qu'ils sont : la taxonomie d'erreurs les classe réessayables, à raison."""
    from oto.tools.common import UpstreamHTTPError

    try:
        return fn()
    except UpstreamHTTPError as e:
        if 400 <= e.status_code < 500 and e.status_code != 429:
            raise _bad(refus(e)) from None
        raise


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » — otomata-tech/oto#69. Couvre `auth` SEUL.

    `list_signature_requests` est la lecture la moins chère qui exige une clé
    valide sans effet de bord (contrairement à `create_signature_request`, qui
    créerait un brouillon réel). L'hôte suit l'`environment` du credential."""
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
    """Le client Yousign pour le credential de CET appelant, sur l'hôte de son
    `environment`. L'import réel est fait dans le corps : les tests remplacent le
    client, et la sonde de version-skew lit l'annotation de retour."""
    from oto.tools.yousign import YousignClient

    rc = access.resolve_credential("yousign", want="byo")
    fields = rc.fields
    return YousignClient(api_key=fields["key"], sandbox=_sandbox(fields.get("environment")))


def _pdf(pdf_base64: str) -> bytes:
    try:
        contenu = base64.b64decode(pdf_base64, validate=True)
    except (binascii.Error, ValueError):
        raise _bad("`pdf_base64` n'est pas du base64 valide — encode le PDF entier, "
                   "sans en-tête `data:` ni retour à la ligne.") from None
    if not contenu:
        raise _bad("`pdf_base64` est vide.")
    return contenu


def _signataires(signataires: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not signataires:
        raise _bad("`signataires` est requis : au moins un signataire "
                   "[{prenom, nom, email, langue?}].")
    infos = []
    for i, s in enumerate(signataires):
        if not isinstance(s, dict):
            raise _bad(f"signataires[{i}] : un objet {{prenom, nom, email, langue?}}.")
        manquants = [k for k in ("prenom", "nom", "email") if not str(s.get(k) or "").strip()]
        if manquants:
            raise _bad(f"signataires[{i}] : {', '.join('`' + m + '`' for m in manquants)} requis.")
        infos.append({"first_name": s["prenom"], "last_name": s["nom"],
                      "email": s["email"], "locale": s.get("langue") or "fr"})
    return infos


def _composer(c: YousignClient, nom: str, pdf: bytes, nom_fichier: str,
              infos: list[dict[str, Any]], message_livraison: str) -> dict:
    """Brouillon → signataires (dans l'ordre) → document, ancres analysées. Aucun
    envoi. Lève si le PDF n'a pas une ancre par signataire."""
    corps: dict[str, Any] = {}
    if len(infos) > 1:
        corps["ordered_signers"] = True     # l'ordre de la liste est l'ordre de signature
    demande = c.create_signature_request(nom, delivery_mode=message_livraison, **corps)
    demande_id = demande["id"]
    try:
        signataires_crees, precedent = [], None
        for info in infos:
            extra = {"insert_after_id": precedent} if precedent else {}
            reponse = c.add_signer(demande_id, info, **extra)
            signataires_crees.append({"id": reponse["id"], "email": info["email"]})
            precedent = reponse["id"]
        # Multipart : la valeur part en texte, d'où la chaîne « true ».
        fichier = c.add_document(demande_id, pdf, nom_fichier, parse_anchors="true")
        ancres = (fichier or {}).get("total_anchors") or 0
        if ancres < len(infos):
            raise _bad(
                f"le PDF porte {ancres} ancre(s) de signature pour {len(infos)} "
                f"signataire(s) : Yousign refuse d'activer un signataire sans champ de "
                f"signature. Ajoute au PDF une ancre {ANCRE} par signataire, sur une "
                f"seule ligne, N = rang du signataire dans `signataires` (s1 = premier). "
                f"Aucun repli sur des coordonnées : la demande n'a pas été créée.")
    except Exception as e:
        try:
            c.delete_signature_request(demande_id)
        except Exception as nettoyage:
            raise _bad(f"{_message(e)} — et le brouillon {demande_id} n'a pas pu être "
                       f"supprimé ({nettoyage}) : à retirer à la main dans Yousign.") from e
        raise
    return {"demande_id": demande_id, "document_id": fichier["id"],
            "ancres": ancres, "signataires": signataires_crees}


def _message(e: Exception) -> str:
    """Le texte d'une erreur, qu'elle soit déjà un refus nommé ou un refus amont."""
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
        """Compose une demande de signature : crée le brouillon, y attache les
        signataires puis le PDF. N'ENVOIE RIEN — `yousign_envoyer` déclenche
        l'envoi réel des invitations.

        ⚠️ Le PDF doit porter une ancre de signature par signataire, écrite dans
        le document sur une seule ligne : `{{s1|signature|180|60}}` pour le
        premier signataire, `{{s2|signature|180|60}}` pour le deuxième, etc.
        (largeur et hauteur du champ en points). Yousign place le champ à
        l'emplacement de l'ancre. Moins d'ancres que de signataires → refus, et
        rien n'est créé.

        Rend `{"demande_id", "document_id", "ancres", "signataires": [{"id",
        "email"}]}` — à repasser tels quels à `yousign_envoyer`/`yousign_statut`/
        `yousign_document_signe`.

        Args:
            nom: nom de la demande (1-128 caractères), visible par les
                signataires dans l'email.
            pdf_base64: le PDF à faire signer, encodé en base64.
            nom_fichier: nom du fichier tel qu'il apparaîtra (ex. "nda.pdf").
            signataires: `[{"prenom", "nom", "email", "langue"?}, …]`, au moins
                un — `langue` ∈ en|fr|de|it|nl|es|pl|pt|ro, défaut "fr". Avec
                plusieurs signataires, l'ordre de la liste est l'ordre de
                signature (chacun signe après le précédent) et le rang N de
                l'ancre `sN`.
            message_livraison: "email" (Yousign envoie le lien) ou "none"
                (le lien est à distribuer soi-même : `yousign_envoyer` le rend).
        """
        pdf = _pdf(pdf_base64)
        infos = _signataires(signataires)
        return _run(lambda: _composer(_client(), nom, pdf, nom_fichier, infos,
                                      message_livraison))

    @mcp.tool()
    def yousign_envoyer(demande_id: str) -> dict:
        """Active une demande composée par `yousign_creer` : quitte l'état
        brouillon, notifie les signataires (sauf `message_livraison="none"`).

        ⚠️ Envoie réellement des emails à des personnes réelles — pas
        d'annulation propre une fois envoyé (seulement `cancel`, qui clôt la
        demande sans en effacer la trace).

        ⚠️ La réponse porte le `signature_link` de chaque signataire : un lien
        SENSIBLE, qui permet à son porteur de signer. Ne le transmettre qu'à la
        personne visée (c'est le but en `message_livraison="none"`).
        """
        return _run(lambda: _client().activate_signature_request(demande_id))

    @mcp.tool()
    def yousign_statut(demande_id: str) -> dict:
        """Statut d'une demande de signature et de ses signataires.

        `statut` ∈ draft (composée, pas envoyée) | ongoing (envoyée, en
        attente) | done (tous les signataires ont signé) | expired | canceled
        | declined | rejected | paused | approval. Seul `done` signifie que
        le document signé est prêt (`yousign_document_signe`)."""
        demande = _run(lambda: _client().get_signature_request(demande_id))
        return {"statut": demande.get("status"), "nom": demande.get("name"),
                "signataires": demande.get("signers"), "documents": demande.get("documents")}

    @mcp.tool()
    def yousign_document_signe(demande_id: str, document_id: str) -> dict:
        """Télécharge le PDF signé d'UN document d'une demande `done`.

        Avant `done`, rend le document ORIGINAL non signé (Yousign ne
        distingue pas les deux par cet appel — vérifier `yousign_statut`
        d'abord). Une demande à plusieurs documents demande un appel PAR
        document (pas de téléchargement groupé).

        Returns:
            `{"nom_fichier", "pdf_base64"}` — le PDF encodé en base64 (le
            transport MCP ne porte pas de binaire brut).
        """
        pdf_bytes = _run(lambda: _client().download_document(demande_id, document_id))
        if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
            raise _bad(f"Yousign n'a rendu aucun fichier pour le document {document_id} "
                       f"de la demande {demande_id} — vérifie les deux identifiants "
                       "(`yousign_statut` liste les documents).")
        return {"nom_fichier": f"{document_id}.pdf",
                "pdf_base64": base64.b64encode(pdf_bytes).decode()}
