"""Dépôt d'un fichier joint à un message de l'agent intégré (`docs/copilot.md` §Pièces jointes).

- `POST /api/copilot/attachments` → multipart, une partie `file`. Rend
  `{id, name, media_type, size}` ; le tour le nomme ensuite par son `id`
  (`TurnInput.attachments`). Le `GET` du même chemin, ce qui est accepté, est une capacité
  (`capabilities/copilot/routes.py`).

Corps binaire → hors couche capacité par nature (ADR 0009), comme l'avatar ou l'audio à
transcrire. La porte est CELLE des routes de l'agent intégré, appliquée telle quelle :
session interactive seulement (le jeton est refusé deux fois — à l'authentification et
sur le geste de la requête, que ce handler pose comme l'adaptateur des capacités),
membre de l'org consultée (`ORG_MEMBER`), allowlist, jamais en « voir en tant que ».
Le corps se lit en flux, borné au plus gros type accepté, et seulement une fois la porte
passée : un `Content-Length` annoncé au-delà (plus l'enveloppe multipart) est refusé
413 sans rien lire, un corps sans annonce s'arrête dès qu'il passe la borne
(`uploads.lire_multipart_borne`). Le jugement du type et de la taille, et le quota de
dépôt par personne (429), sont `capabilities/copilot/pieces.py`.

La table de routes reste assemblée dans `api.routes.make_routes` ; ce module ne porte
que le handler.
"""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import geste, media_store, org_suspension
from ..capabilities._authz import ORG_MEMBER
from ..capabilities._types import AuthzDenied, ContratDeRoute, DeclaredError, RawCtx
from ..capabilities.copilot import pieces
from ..capabilities.copilot import routes as copilot_routes
from .base import CLE_PRINCIPAL, _authenticate, _json, _json_error
from .uploads import CorpsRefuse, lire_multipart_borne


def _porte(sub: str):
    """La porte des routes de l'agent intégré : membre de l'org consultée, puis
    `_garde` (geste de console, pas de « voir en tant que », allowlist), puis l'org
    suspendue comme toute capacité."""
    ctx = ORG_MEMBER(RawCtx(sub=sub))
    copilot_routes._garde(ctx)
    org_suspension.garde_capacite("copilot.attachment", ctx)
    return ctx


async def piece_depot(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    sub, err = await _authenticate(request, verifier, allow_api_token=False)
    if err:
        return err
    principal = request.scope.get(CLE_PRINCIPAL) or {}
    # Le geste de la requête, posé comme le pose l'adaptateur des capacités : sans lui,
    # `_garde` ne reconnaîtrait aucune session (refus par défaut).
    with geste.portee(geste.source_rest(principal.get("token_kind")),
                      principal.get("sub")):
        try:
            ctx = await run_in_threadpool(_porte, sub)
        except AuthzDenied as d:
            return _json_error(request, d.status, d.code, d.message or None)
        try:
            _form, upload, data = await lire_multipart_borne(request, pieces.MAX_DEPOT)
        except CorpsRefuse as e:
            if e.status == 413:
                return _json_error(request, 413, "attachment_too_large",
                                   f"{pieces.MAX_DEPOT // (1024 * 1024)} MB at most.")
            return _json_error(request, e.status, e.code)
        try:
            out = await run_in_threadpool(
                pieces.deposer, ctx.sub, ctx.org_id, data,
                getattr(upload, "filename", None), getattr(upload, "content_type", None))
        except pieces.Refus as r:
            return _json_error(request, r.status, r.code, r.message)
        except media_store.MediaError as e:
            return _json_error(request, e.status, e.code)
    return _json(request, out)


class PieceDeposee(BaseModel):
    id: str
    name: str
    media_type: str
    size: int


# Route de NATURE (corps multipart) : son corps déclaré la fait reconnaître comme une route
# d'UPLOAD par la borne de corps du processus (`borne_corps`, #1232).
piece_depot.contrat = ContratDeRoute(
    description=(
        "Joint un fichier à un prochain message de l'agent intégré. Corps multipart, une "
        "partie `file`. Images png, jpeg, gif, webp (5 Mo), PDF (20 Mo), texte UTF-8 "
        "text/plain, text/markdown, text/csv, application/json (512 Ko) — le type est "
        "jugé sur les OCTETS et doit être celui que le fichier annonce. Rend `{id, name, "
        "media_type, size}` ; le tour le nomme dans `attachments`. 200 Mo au plus par "
        "personne sur 24 heures glissantes. Ce qui est accepté : `GET` sur le même "
        "chemin."),
    Output=PieceDeposee,
    errors=(
        DeclaredError(403, "api_token_forbidden",
                      "an API or delegated token, not an interactive session"),
        DeclaredError(403, "copilot_not_enabled",
                      "the account is not in the instance's allowlist"),
        DeclaredError(403, "copilot_view_as", "the request views as someone else"),
        DeclaredError(400, "invalid_multipart", "le corps n'est pas un multipart lisible"),
        DeclaredError(400, "missing_file", "aucune partie `file`, ou un fichier vide"),
        DeclaredError(413, "attachment_too_large",
                      "au-delà de la borne de son type, ou un Content-Length annoncé "
                      "au-delà de la plus grande"),
        DeclaredError(429, "attachment_quota_exceeded",
                      "200 Mo déposés au plus par personne sur 24 heures glissantes"),
        DeclaredError(415, "attachment_type",
                      "type non accepté, ou contenu qui n'est pas le type annoncé"),
    ),
    corps={"POST": {"multipart/form-data": {
        "type": "object", "required": ["file"],
        "properties": {"file": {"type": "string", "format": "binary"}}}}},
)
