"""Ce qui reste ici : le CALLBACK Google OAuth, et les jetons API.

Le nom du fichier est un vestige — il est le point d'accroche d'`api_routes.py`, et deux
vagues de migration l'ont déjà vidé :

- **2026-08-12 (#302)** : les 17 routes du datastore sont devenues des CAPACITÉS
  (`capabilities/datastore_*.py`) — mêmes chemins, mêmes réponses, entrée ET sortie
  déclarées, donc décrites dans `/api/openapi.json` ;
- **2026-08-27** : les VERBES Google OAuth (`start`, `status`, `DELETE`, `default`) →
  `capabilities/federated_oauth.py`, avec ceux d'atlassian et de folkmcp.

⚠️ **Le CALLBACK, lui, ne migrera pas.** Google y redirige le NAVIGATEUR de
l'utilisateur : pas d'en-tête d'auth (l'identité vient du `state` HMAC-signé) et la
réponse est une **302** vers la page connecteurs, pas du JSON. Or `_rest_adapter`
authentifie toujours et répond toujours en JSON. Il est hors du moule par construction,
et classé par NATURE comme les autres callbacks OAuth.

Restent aussi les **jetons API** (`/api/me/tokens*`), réservés à une session interactive
(`allow_api_token=False` : un jeton ne fabrique pas de jeton). ⚠️ La création d'un jeton
PORTÉ lit le catalogue des tableaux — pour refuser un nom que l'émetteur ne voit pas —
et c'est la seule attache qui reste avec le datastore.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import access, db, google_oauth, token_scopes
from .datastore import make_store
from .json_body import InvalidJsonBody, read_json_body


# Type alias for the auth helper passed in from api_routes.
AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]


def _app_url() -> str:
    return os.environ.get("OTO_APP_URL", "https://app.oto.ninja").rstrip("/")


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    cors_headers: Callable[[str | None], dict[str, str]],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:
    """Construit ces routes.

    Les helpers `authenticate`/`json_response`/`json_error`/`cors_headers`/
    `options_handler` sont passés depuis `api_routes.py` pour partager les
    primitives (auth Logto + token, CORS).
    """

    # --- Google OAuth ----------------------------------------------------


    async def google_oauth_callback(request: Request) -> Response:
        # Pas d'auth Logto — Google redirige depuis le navigateur user.
        # Validation via le `state` HMAC-signé.
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return json_error(request, 400, "missing_code_or_state")
        parsed = google_oauth.verify_state(state)
        if not parsed:
            return json_error(request, 400, "invalid_state")
        sub, org_id = parsed
        try:
            tokens = google_oauth.exchange_code(code)
            google_oauth.persist_token(sub, org_id, tokens)
        except Exception as e:
            return json_error(request, 502, f"oauth_exchange_failed: {e}")
        # Retour vers la page connecteurs (où vit la config Google, ADR 0024 B2).
        # `datastore` n'est plus Google Sheets (ADR 0016, PG natif) → ex-signal
        # `?datastore=connected` retiré.
        return RedirectResponse(url=f"{_app_url()}/console/connectors?google=connected", status_code=302)




    # --- API tokens (CLI auth) -------------------------------------------

    # --- Jetons API : gestion réservée à une SESSION INTERACTIVE ---------------
    # `allow_api_token=False` sur les trois : un jeton `oto_` ne peut ni lister, ni
    # créer, ni révoquer de jeton. Sinon une fuite est auto-entretenue (l'attaquant
    # s'émet un second jeton non-expirant avant qu'on révoque le premier) et peut
    # révoquer les jetons légitimes. Émettre un jeton reste donc un acte humain,
    # ce qui est exactement ce qu'on veut d'un jeton confié à un tiers.

    async def me_tokens_list(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        return json_response(request, {"tokens": db.list_api_tokens(sub)})

    async def me_tokens_create(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        # Corps illisible ⇒ REFUS, jamais un jeton. Le silence d'avant produisait le
        # cas le plus dangereux : `scopes` mal formé ⇒ `scopes=None` ⇒ jeton NON PORTÉ
        # (les droits pleins du sub) à la place du jeton borné demandé — l'inverse
        # exact de ce que redoute le commentaire vingt lignes plus bas
        # (`docs/silences-2026-08-27.md`, site B2). Corps ABSENT ⇒ `{}` : le jeton
        # `cli` non porté par défaut reste émissible, et c'est le contrat.
        try:
            body = await read_json_body(request)
        except InvalidJsonBody as e:
            return json_error(request, 400, e.code, e.detail)
        label = body.get("label") or "cli"
        # Portée optionnelle (`token_scopes`) : absente ⇒ jeton non porté (il EST le
        # sub). Présente ⇒ jeton borné à des tableaux nommés — la forme à confier à
        # une intégration tierce. Validée ici, jamais côté porteur.
        try:
            scopes = token_scopes.parse(body.get("scopes"))
        except token_scopes.ScopeError as e:
            return json_error(request, 400, "invalid_scopes", str(e))
        if scopes is not None:
            # Refuser un tableau que l'émetteur ne voit pas : le jeton ne peut de
            # toute façon pas dépasser les droits du sub, mais une faute de frappe
            # produirait un jeton muet qu'on croirait branché.
            visible = {n["namespace"] for n in make_store(sub).list_namespaces()}
            missing = sorted(set(scopes["namespaces"]) - visible)
            if missing:
                return json_error(request, 400, "unknown_namespace",
                                  f"Tableaux inconnus dans l'org active : {missing}")
        token = db.create_api_token(sub, label=label.strip()[:32], scopes=scopes)
        return json_response(request, {"token": token, "label": label, "scopes": scopes},
                             status=201)

    async def me_tokens_delete(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier, allow_api_token=False)
        if err:
            return err
        try:
            token_id = int(request.path_params["token_id"])
        except (ValueError, KeyError):
            return json_error(request, 400, "invalid_id")
        ok = db.delete_api_token(sub, token_id)
        if not ok:
            return json_error(request, 404, "unknown_token")
        return json_response(request, {"ok": True})

    # --- Datastore (PG natif, ADR 0016) ----------------------------------

    # Lister / créer / supprimer / renommer un tableau, et son deep-link, sont des
    # CAPACITÉS (`capabilities/datastore_namespaces.py`, #302) : mêmes chemins, mêmes
    # réponses, mais entrée ET sortie déclarées — donc décrites dans
    # `/api/openapi.json`, donc générables chez un intégrateur.

    # Les LIGNES sont des CAPACITÉS (`capabilities/datastore_rows.py`, #302) : page,
    # fiche, ajout, modification, suppression, file de travail et agrégat. Mêmes
    # chemins, mêmes réponses, mais entrée ET sortie déclarées — les deux corps LIBRES
    # (ajouter/modifier : les colonnes du tableau) le sont explicitement, par
    # `RestBinding.body_field`.

    # Le SCHÉMA (pose) et le PARTAGE sont des CAPACITÉS (#302) :
    # `capabilities/datastore_schema.py` (la lecture y vivait déjà) et
    # `capabilities/datastore_sharing.py`. Le corps du DELETE de partage — forme
    # historique du client `oto-core` — est déclaré par `RestBinding.reads_body`.

    # Le TRANSFERT de propriété d'un datastore passe par la capacité UNIQUE `oto_resource`
    # (op=transfer, resource_type='datastore_namespace' — même seam `ownership` + garde-fou
    # anti-lockout + cibles user/org/GROUPE). L'ancien endpoint bespoke `ds_transfer` a été
    # retiré (2026-07-24) : il dupliquait la résolution de cible et court-circuitait la
    # confirmation de perte de contrôle. Le front vise `/api/resources` par l'id du namespace.

    return [
        # Google OAuth
        Route("/api/google/oauth/callback", google_oauth_callback, methods=["GET"]),
        # API tokens
        Route("/api/me/tokens", me_tokens_list, methods=["GET"]),
        Route("/api/me/tokens", me_tokens_create, methods=["POST"]),
        Route("/api/me/tokens", options_handler, methods=["OPTIONS"]),
        Route("/api/me/tokens/{token_id}", me_tokens_delete, methods=["DELETE"]),
        Route("/api/me/tokens/{token_id}", options_handler, methods=["OPTIONS"]),
    ]
