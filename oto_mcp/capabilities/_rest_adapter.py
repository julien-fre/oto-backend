"""Adaptateur REST de la couche capacité (ADR 0009).

Boucle sur le registre et monte une Route Starlette par capacité ayant un
binding `rest`. Même séquence que l'adaptateur MCP : authenticate → input
(path_params + body) → autz → handler. L'`AuthzDenied` neutre est re-émis via
`json_error(request, status, code)` — **conserve l'enveloppe + les en-têtes
CORS** consommés par le dashboard.

Dépend du core (sens unique ADR 0004).
"""
from __future__ import annotations

import inspect
import logging
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

from ..json_body import InvalidJsonBody, read_json_body
from ._types import AuthzDenied, Capability, NotModified, RawCtx

AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def _make_handler(cap: Capability, binding, verifier, authenticate, json_response, json_error):
    async def _handler(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        data: dict = {}
        # Query string (filtres des GET/DELETE sans body : `?query=…&limit=…`).
        # Valeurs str → pydantic coerce vers le type du champ Input. Priorité la
        # plus basse (body puis path params écrasent).
        if request.query_params:
            data.update(dict(request.query_params))
        if request.method in ("POST", "PUT", "PATCH") or binding.reads_body:
            # Un corps illisible est REFUSÉ, jamais ignoré — c'est le même principe
            # que la garde des champs inconnus vingt lignes plus bas, et il lui
            # manquait exactement ce cas : la garde couvrait « un champ que je ne
            # connais pas », pas « un corps que je ne comprends pas ». Sur ~200 routes
            # générées, l'appelant recevait un 200 et des valeurs par défaut
            # (`docs/silences-2026-08-27.md`, site B4). Un corps ABSENT reste `{}` :
            # les routes sans argument ne changent pas de contrat.
            try:
                body = await read_json_body(request)
            except InvalidJsonBody as e:
                logger.warning("capacité %s : corps de requête refusé (%s)",
                               cap.key, e.code)
                return json_error(request, 400, e.code, e.detail)
            if body:
                if binding.body_field:
                    # Corps LIBRE (les colonnes d'une ligne de tableau) : il ne se
                    # fusionne pas clé par clé, il EST la valeur d'un champ déclaré.
                    # Cf. `RestBinding.body_field` — la garde ci-dessous continue
                    # donc de couvrir la query string et les params de chemin.
                    data[binding.body_field] = body
                else:
                    data.update(body)
        # path params : mapping explicite placeholder->champ Input, sinon nom identique.
        for ph, value in request.path_params.items():
            field = (binding.path_map or {}).get(ph, ph)
            data[field] = value
        # REFUSER un champ inconnu, jamais l'IGNORER.
        #
        # Pydantic ignore par défaut les clés qu'il ne connaît pas (`extra="ignore"`).
        # Un client qui se trompe de forme reçoit donc un 200 et un comportement de
        # repli, sans le moindre signal. Vécu le 05/08 : un front envoyait
        # `{app, scope}` au premier niveau alors que l'`Input` déclare `params: dict` —
        # les deux ont été jetés en silence, le scope est retombé sur sa valeur par
        # défaut et le retour OAuth est parti chez le mauvais front. Aucune erreur,
        # aucun log, une demi-journée pour le trouver.
        #
        # C'est la MÊME famille que le bug des jetons de contexte du 28/07 (`account`
        # métier mangé par l'axe `account`) : un argument légitime avalé sans bruit.
        # Le remède est le même — refuser plutôt qu'ignorer — et il vaut pour les ~200
        # routes générées, pas connecteur par connecteur.
        #
        # Les noms sont RENDUS au client : un refus qui ne dit pas quel champ pose
        # problème oblige à deviner, et c'est exactement ce qu'on cherche à supprimer.
        inconnus = sorted(set(data) - set(cap.Input.model_fields))
        if inconnus:
            logger.warning("capacité %s : champ(s) inconnu(s) refusé(s) : %s",
                           cap.key, ", ".join(inconnus))
            return json_error(
                request, 400, "unknown_fields",
                f"Champ(s) non reconnu(s) : {', '.join(inconnus)}. "
                f"Attendus : {', '.join(sorted(cap.Input.model_fields))}.")
        try:
            inp = cap.Input(**data)
        except ValidationError:
            return json_error(request, 400, "invalid_input")
        try:
            ctx = cap.authz(RawCtx(sub=sub), inp)
            result = cap.handler(ctx, inp)
            if inspect.isawaitable(result):           # handler async (ex. doctrine + manifeste)
                result = await result
        except AuthzDenied as d:
            # `message` EN 4e ARG, sinon il est jeté et le client ne voit qu'un code nu.
            # Les auteurs de capacités écrivent des refus actionnables (« Enregistre
            # d'abord le Consumer Key… ») qui n'atteignaient personne : `_json_error`
            # n'émet `detail` que s'il lui est passé. La face MCP, elle, rendait déjà
            # `d.message` — les deux surfaces disaient donc des choses différentes du
            # MÊME refus.
            return json_error(request, d.status, d.code, d.message or None)
        if isinstance(result, NotModified):
            # 304 : **sans corps**, c'est la spec et c'est tout l'intérêt — le client
            # garde ce qu'il a en cache. Un 200 portant « rien n'a changé » ferait
            # ranger CE message à la place des données.
            return Response(status_code=304, headers=_cors_of(request, json_response))
        return json_response(request, result, status=binding.status)
    return _handler


def _cors_of(request, json_response) -> dict:
    """Les en-têtes CORS de la réponse ordinaire, recopiés sur la 304.

    Une 304 est une réponse comme une autre pour le navigateur : sans `Access-Control-
    Allow-Origin`, le dashboard voit une erreur CORS là où le serveur a répondu « ton
    cache est bon ». On les DÉRIVE de la réponse normale plutôt que de les réécrire —
    la politique CORS vit dans `json_response`, et un second endroit qui la décide
    divergerait au premier changement d'origine autorisée."""
    try:
        modele = json_response(request, {}, status=200)
        return {k: v for k, v in modele.headers.items()
                if k.lower().startswith("access-control-")}
    except Exception:
        return {}


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
    capabilities: list[Capability],
) -> list[Route]:
    """Une Route (+ OPTIONS) par capacité REST. Liste vide si rien (canari)."""
    routes: list[Route] = []
    for cap in capabilities:
        if not cap.is_exposed():
            continue
        for binding in cap.rest_bindings():
            h = _make_handler(cap, binding, verifier, authenticate, json_response, json_error)
            routes.append(Route(binding.path, h, methods=[binding.verb]))
            routes.append(Route(binding.path, options_handler, methods=["OPTIONS"]))
    return routes
