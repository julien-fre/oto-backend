"""Handler du RELAIS d'upload Pennylane (cabinet) — `POST /api/relay/{token}`.

Route SŒUR de `/api/upload/{token}`, pas la même : celle-ci lit tout le corps en
mémoire (`lire_multipart_borne`, 25 Mo), ce qu'un fichier de cent mégaoctets interdit.
Ici le multipart (`curl -F file=@…`) est confié au parseur de Starlette, qui DÉBORDE
SUR DISQUE au-delà d'un mégaoctet (fichier temporaire) — un transit disque assumé, borné
par le plafond du relais, compté PENDANT la lecture, et fermé en `finally` quoi qu'il
arrive. Le fichier n'est jamais lu en entier : son objet est passé à la lib, qui
l'envoie à Pennylane par morceaux.

**Pas de JWT** : le jeton de l'URL fait foi (`typ="relay"`, scellé sub / org / cible,
15 min, usage unique). Ordre des contrôles, du moins cher au plus cher, et rien
d'irréversible avant le dernier :

1. signature, `typ`, expiration (`relais.verifier`) ;
2. garde d'identité du compte scellé (`garde_identite.refus`) ;
3. org scellée REJOUÉE : appartenance, suspension, activation, jeton de cabinet
   résolu sous CETTE org, doublon (`relais.preparer`, hors boucle) ;
4. `Content-Length` présent (411) et sous le plafond (413) ;
5. une place de relais libre (503 + `Retry-After`, le jeton n'est PAS consommé) ;
6. consommation du `jti` (409) ;
7. lecture bornée du multipart, puis relais (`relais.relayer`, hors boucle, sans
   connexion base) et la réponse de Pennylane telle quelle.

Une ligne de journal par dépôt tenté au-delà de la signature (`tool_calls`, en tâche
de fond comme `RestCallLogger`).
"""
from __future__ import annotations

import asyncio
import logging
import time

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

from .. import db, garde_identite
from ..capabilities._types import ContratDeRoute, DeclaredError
from ..tools import pennylane_firm_relais as relais
from ..tools import pennylane_firm_socle as socle
from .base import _json, _json_error

logger = logging.getLogger(__name__)

# Ce que l'enveloppe multipart ajoute autour du fichier (frontière, en-têtes de partie).
_MARGE_MULTIPART = 64 * 1024
# Délai conseillé quand toutes les places de relais sont prises.
_ATTENTE_OCCUPE_S = 5
_TACHES_JOURNAL: set = set()  # références des tâches de journal (anti-GC)


class _Plafond(MultiPartException):
    """Le corps dépasse le plafond. Sous-classe de `MultiPartException` à dessein :
    c'est la seule famille pour laquelle le parseur ferme ses fichiers temporaires."""


class _Interrompu(MultiPartException):
    """Le client est parti en cours d'envoi — même raison d'héritage."""


async def _flux_borne(request: Request, borne: int):
    """Le corps de la requête, compté morceau par morceau, coupé au-delà de `borne`."""
    recu = 0
    try:
        async for morceau in request.stream():
            recu += len(morceau)
            if recu > borne:
                raise _Plafond("content_too_large")
            yield morceau
    except ClientDisconnect:
        raise _Interrompu("client_disconnected") from None


async def _lire_formulaire(request: Request, plafond: int):
    """Le multipart lu par le parseur de Starlette (une seule partie fichier, quelques
    champs tolérés), sous la borne `plafond` + l'enveloppe."""
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise socle.Refus(400, "invalid_multipart",
                          "Send the file as multipart form data: curl -F 'file=@<path>'.")
    parseur = MultiPartParser(request.headers,
                              _flux_borne(request, plafond + _MARGE_MULTIPART),
                              max_files=1, max_fields=8)
    try:
        return await parseur.parse()
    except _Plafond:
        raise socle.Refus(413, "content_too_large",
                          f"The file exceeds the relay's limit ({plafond} bytes).") from None
    except _Interrompu:
        raise socle.Refus(400, "upload_interrupted",
                          "The upload stopped before its end: mint a new link.") from None
    except MultiPartException:
        raise socle.Refus(400, "invalid_multipart",
                          "The body is not readable multipart form data.") from None


def _fichier(form, plafond: int):
    """La partie `file` du formulaire. Sa taille vient du parseur (octets écrits), le
    fichier n'est pas relu pour la mesurer."""
    upload = form.get("file")
    if upload is None or not hasattr(upload, "file"):
        raise socle.Refus(400, "missing_file", "No `file` part: curl -F 'file=@<path>'.")
    if not upload.size:
        raise socle.Refus(400, "empty_file", "The file is empty.")
    if upload.size > plafond:
        raise socle.Refus(413, "content_too_large",
                          f"The file exceeds the relay's limit ({plafond} bytes).")
    return upload


async def _deposer(request: Request, payload: dict, trace: dict) -> dict:
    sub = payload["sub"]
    if (coupe := await run_in_threadpool(garde_identite.refus, sub)):
        raise socle.Refus(403, coupe[0], coupe[1])
    prep = await run_in_threadpool(relais.preparer, sub, payload.get("org"),
                                   payload["target"])
    trace["key_mode"] = prep.key_mode
    plafond = relais.max_bytes()
    annonce = request.headers.get("content-length", "")
    if not annonce.isdigit():
        raise socle.Refus(411, "length_required",
                          "The relay needs a Content-Length: send a file with "
                          "curl -F 'file=@<path>', not a stream.")
    if int(annonce) > plafond + _MARGE_MULTIPART:
        raise socle.Refus(413, "content_too_large",
                          f"The file exceeds the relay's limit ({plafond} bytes).")
    if not relais.prendre_place():
        raise socle.Refus(503, "relay_busy",
                          "Every relay slot is taken: retry this same link in a few "
                          "seconds (it is not used up).", retryable=True,
                          retry_after=_ATTENTE_OCCUPE_S)
    try:
        if not await run_in_threadpool(db.consume_upload_token, payload["jti"]):
            raise socle.Refus(409, "token_already_used",
                              "This link has already been used: mint a new one.")
        form = None
        try:
            form = await _lire_formulaire(request, plafond)
            upload = _fichier(form, plafond)
            trace["octets"] = upload.size
            return await run_in_threadpool(relais.relayer, prep, upload.file,
                                           upload.filename, upload.content_type)
        finally:
            if form is not None:
                await form.close()
    finally:
        relais.rendre_place()


def _refus(request: Request, e: socle.Refus) -> JSONResponse:
    reponse = _json_error(request, e.status, e.code, e.message, e.details)
    if e.retry_after is not None:
        reponse.headers["Retry-After"] = str(e.retry_after)
    return reponse


async def _ecrire_journal(ligne: dict) -> None:
    try:
        await asyncio.to_thread(db.insert_tool_call, ligne)
    except Exception:  # noqa: BLE001 — le journal ne casse jamais le dépôt
        logger.warning("pennylane_firm relay: journal write failed", exc_info=True)


def _journaliser(payload: dict, trace: dict, debut: float, *, ok: bool,
                 error, file_id=None) -> None:
    ligne = relais.ligne_journal(
        payload, ok=ok, error=error, duree_ms=int((time.monotonic() - debut) * 1000),
        octets=trace.get("octets"), file_id=file_id, key_mode=trace.get("key_mode"))
    tache = asyncio.create_task(_ecrire_journal(ligne))
    _TACHES_JOURNAL.add(tache)
    tache.add_done_callback(_TACHES_JOURNAL.discard)


async def relay_receive(request: Request) -> JSONResponse:
    """Reçoit le fichier d'un lien de relais et le dépose dans la GED de la société
    scellée ; rend l'objet fichier que Pennylane a créé."""
    payload = relais.verifier(request.path_params.get("token", ""))
    if payload is None:
        return _json_error(request, 401, "invalid_or_expired_token",
                           "Unreadable, forged or expired link: mint a new one.")
    debut, trace = time.monotonic(), {}
    try:
        resultat = await _deposer(request, payload, trace)
    except socle.Refus as e:
        _journaliser(payload, trace, debut, ok=False, error=e.code)
        return _refus(request, e)
    except Exception as e:
        _journaliser(payload, trace, debut, ok=False, error=type(e).__name__)
        raise
    file_id = resultat.get("id") if isinstance(resultat, dict) else None
    _journaliser(payload, trace, debut, ok=True, error=None, file_id=file_id)
    return _json(request, resultat)


# ── Le contrat publié (oto#106) ───────────────────────────────────────────────────

class FichierCree(BaseModel):
    """Le fichier que Pennylane a créé dans la GED, tel que Pennylane le rend
    (`id`, `name`, `path`, `parent_folder`, `url`, `created_at`, `updated_at`)."""
    id: int
    name: str
    model_config = {"extra": "allow"}


_REFUS_DU_RELAIS = (
    DeclaredError(401, "invalid_or_expired_token",
                  "le jeton de l'adresse est illisible, falsifié, expiré, ou n'est pas "
                  "un jeton de relais : en frapper un autre (`pennylane_firm_upload_url`)"),
    DeclaredError(403, "no_organization", "le lien n'a pas d'org scellée"),
    DeclaredError(403, "not_an_org_member",
                  "le compte qui a frappé le lien n'est plus membre de l'org scellée"),
    DeclaredError(403, "org_suspended", "l'org scellée est suspendue"),
    DeclaredError(403, "connector_disabled",
                  "le connecteur est désactivé pour l'org scellée"),
    DeclaredError(424, "credential_unavailable",
                  "aucun jeton de cabinet n'est joignable sous l'org scellée"),
    DeclaredError(400, "invalid_name", "le nom scellé n'a pas 1 à 255 caractères"),
    DeclaredError(409, "name_already_exists",
                  "un fichier de ce nom existe (ou part) déjà dans le dossier"),
    DeclaredError(409, "duplicate_check_incomplete",
                  "le dossier est trop grand pour être lu en entier : le doublon ne "
                  "peut pas être exclu, rien n'est envoyé"),
    DeclaredError(411, "length_required", "pas de `Content-Length` (envoi en flux)"),
    DeclaredError(413, "content_too_large",
                  "le fichier dépasse le plafond du relais (`max_bytes`, rendu à la "
                  "frappe) — annoncé ou constaté pendant la lecture"),
    DeclaredError(503, "relay_busy",
                  "toutes les places de relais sont prises : réessayer le MÊME lien "
                  "après `Retry-After` (il n'est pas consommé)"),
    DeclaredError(409, "token_already_used", "le lien a déjà servi"),
    DeclaredError(400, "invalid_multipart", "le corps n'est pas un multipart lisible"),
    DeclaredError(400, "upload_interrupted", "le client est parti en cours d'envoi"),
    DeclaredError(400, "missing_file", "pas de partie `file`"),
    DeclaredError(400, "empty_file", "le fichier est vide"),
    DeclaredError(429, "pennylane_rate_limited",
                  "Pennylane a refusé le débit : attendre 60 s (`Retry-After`) puis "
                  "frapper un nouveau lien — celui-ci est consommé"),
    DeclaredError(403, "pennylane_scope_missing",
                  "le jeton de cabinet n'a pas le scope `dms_files:all` (nommé)"),
    DeclaredError(502, "pennylane_token_invalid",
                  "Pennylane refuse le jeton de cabinet de l'org"),
    DeclaredError(404, "pennylane_not_found",
                  "Pennylane ne trouve pas la société ou le dossier"),
    DeclaredError(422, "pennylane_rejected",
                  "Pennylane refuse le fichier (nom, type, dossier) — extrait borné"),
    DeclaredError(502, "pennylane_unavailable", "Pennylane est indisponible (5xx)"),
    DeclaredError(502, "pennylane_error", "autre refus de Pennylane — statut et extrait"),
    DeclaredError(502, "pennylane_unreachable", "Pennylane n'a pas répondu"),
)

relay_receive.contrat = ContratDeRoute(
    description=(
        "Relays one file to a company's document store (GED) in Pennylane, through a "
        "link minted by the agent tool `pennylane_firm_upload_url`. No `Authorization` "
        "header: the link's token seals the account, the organization and the target "
        "(company, folder, file name), lives 15 minutes and serves once. Multipart, one "
        "`file` part, with a `Content-Length` (`curl -F 'file=@<path>' '<url>'`). The "
        "server sends the file with the organization's firm token and returns the file "
        "Pennylane created. Pennylane cannot delete, move or rename it through its API."),
    Output=FichierCree,
    errors=_REFUS_DU_RELAIS,
    corps={"POST": {"multipart/form-data": {
        "type": "object", "required": ["file"],
        "properties": {"file": {"type": "string", "format": "binary"}}}}},
    authentifiee=False,
)
