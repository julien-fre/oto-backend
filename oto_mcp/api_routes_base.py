"""Primitives partagées par TOUS les modules de routes REST (`api_routes*.py`).

Ce module ne déclare **aucune** route : il porte ce que les handlers de tous les
domaines appellent — l'authentification (`_authenticate`), les en-têtes CORS, les
deux fabriques de réponse JSON, le préflight `OPTIONS`, et `bind` (le passeur de
dépendances explicites).

**Pourquoi un module à part plutôt que `api_routes.py`.** Depuis la découpe du
2026-08-27, les handlers vivent dans des `api_routes_<domaine>.py` que
`api_routes.py` importe pour assembler la table. S'ils allaient rechercher
`_authenticate` dans `api_routes`, l'import serait circulaire ; la base est donc
sous eux, jamais au-dessus. `api_routes` **ré-exporte** ces noms — `api_routes._authenticate`
et `api_routes._cors_headers` restent valides pour les appelants (et les tests)
d'avant la découpe.

Les dix modules de routes ANTÉRIEURS à la découpe (`api_routes_datastore.py`,
`api_routes_sirene.py`, …) reçoivent encore ces mêmes fonctions en PARAMÈTRES de
leur `make_routes` — c'est leur patron historique, né du même besoin d'éviter le
cycle. Il n'a pas été touché : les convertir serait un second lot, sans effet sur
ce qui est servi.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import db
from .auth import token_scopes

# Signature de `_authenticate`, telle que la consomment les modules de routes.
AuthFn = Callable[..., Awaitable["tuple[str | None, JSONResponse | None]"]]


def _allowed_origins() -> list[str]:
    raw = os.environ.get("OTO_MCP_CORS_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://oto.cx",                   # domaine marketing canonique (cutover ADR 0040)
        "https://www.oto.cx",
        "https://manage.oto.cx",            # oto-dashboard PROD (cutover ADR 0040)
        "https://oto.ninja",                # preprod/canari + redirections
        "https://www.oto.ninja",
        "https://app.oto.ninja",
        "https://otomata.tech",             # formulaire de contact vitrine
        "https://www.otomata.tech",
        "https://app.tulina.ai",            # front Tulina PROD (box tulina-0)
        "https://tulina.oto.zone",          # front Tulina PREPROD (même box, :3001)
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:5182",
        "http://localhost:5184",
        "http://localhost:5192",            # oto-dashboard dev (ADR 0007)
        "http://localhost:5193",            # front Tulina dev, ports alternatifs (tulina-app-front#90)
        "http://localhost:5194",
        "http://localhost:5195",
        "http://localhost:5196",
        "https://dashboard.otoninja.dev",   # oto-dashboard via Caddy local
        "https://dashboard.oto.ninja",      # oto-dashboard prod
    ]



def _cors_headers(origin: str | None) -> dict[str, str]:
    if origin and origin in _allowed_origins():
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Oto-Org, X-Oto-Group, X-Oto-View-As",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }
    return {}


def _maybe_view_as(real_sub: str, apply_view_as: bool) -> str:
    """Applique le « voir en tant que » (axe user, REST lecture seule) : si un sub
    de consultation est posé pour la requête (par ViewAsMiddleware, qui a DÉJÀ validé
    opérateur + cible + GET), renvoie ce sub cible ; sinon le sub réel. `apply_view_as`
    False = chemin du middleware lui-même (qui doit voir le sub RÉEL pour gater)."""
    if not apply_view_as:
        return real_sub
    from . import session_org
    target = session_org.current_view_user()
    return target if (target and target != real_sub) else real_sub


async def _authenticate(
    request: Request,
    verifier: JWTVerifier,
    *,
    allow_query_token: bool = False,
    apply_view_as: bool = True,
    allow_api_token: bool = True,
) -> tuple[str | None, JSONResponse | None]:
    """Résout l'appelant (JWT Logto **ou** jeton API `oto_`) et **garde la portée**.

    `allow_api_token=False` = route réservée à une **session interactive** : un
    porteur de jeton y est refusé. Réservé à la gestion des jetons eux-mêmes — un
    jeton qui peut en créer d'autres rend sa fuite auto-entretenue (révoquer le
    jeton fuité ne suffit plus, l'attaquant s'en est fait un second, non-expirant).
    """
    auth = request.headers.get("authorization", "")
    token: str | None = None
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif allow_query_token:
        # Fallback pour SSE via EventSource (qui n'autorise pas les headers).
        token = request.query_params.get("token")
    if not token:
        return None, _json_error(request, 401, "missing_bearer")

    # API token long-lived (CLI) : préfixe `oto_` → lookup hash en DB.
    # Pas de upsert_user ici : la FK CASCADE garantit que si la row user a
    # été supprimée, le token a été supprimé avec.
    if token.startswith("oto_"):
        if not allow_api_token:
            token_scopes.set_current(None)
            return None, _json_error(
                request, 403, "api_token_forbidden",
                "La gestion des jetons demande une session interactive (JWT) — "
                "un jeton API ne peut ni lister, ni créer, ni révoquer de jeton.")
        # DB HORS de la loop (threadpool) : un blip DB ne doit jamais geler le
        # serveur mono-loop entier (vécu 2026-07-02, py-spy : getconn wait ici).
        row = await run_in_threadpool(db.verify_api_token, token)
        if not row:
            token_scopes.set_current(None)
            return None, _json_error(request, 401, "invalid_api_token")
        # Portée du jeton (`token_scopes`) : posée à CHAQUE requête (None comprise),
        # puis gate deny-by-default. Un jeton non porté (`scopes` NULL) est inchangé.
        scopes = row.get("scopes")
        token_scopes.set_current(scopes)
        if not token_scopes.authorize(scopes, request.method, request.url.path):
            granted = []
            if token_scopes.namespaces(scopes):
                granted.append(f"les tableaux {sorted(token_scopes.namespaces(scopes))}")
            if token_scopes.projects(scopes):
                granted.append(f"les projets {sorted(token_scopes.projects(scopes))}")
            return None, _json_error(
                request, 403, "token_scope_forbidden",
                f"Ce jeton est porté : il n'ouvre que {' et '.join(granted)}, en "
                "lecture ou écriture selon sa portée. Rien d'autre de l'organisation "
                "ne lui est accessible.")
        return _maybe_view_as(row["sub"], apply_view_as), None

    # Sinon, JWT Logto (session interactive) — jamais de portée de jeton.
    token_scopes.set_current(None)
    access_token = await verifier.verify_token(token)
    if not access_token or not getattr(access_token, "claims", None):
        return None, _json_error(request, 401, "invalid_token")
    sub = access_token.claims.get("sub")
    if not sub:
        return None, _json_error(request, 401, "missing_sub")
    # Bascule de tenant (B1) : pendant la fenêtre, canonicaliser le sub AVANT l'upsert
    # (un vieux token de l'ancien tenant en drain → compte migré, sinon il re-créerait
    # le compte supprimé). Gaté env → no-op hors bascule.
    if os.environ.get("OTO_MCP_TENANT_MIGRATION_ISS"):
        sub = await run_in_threadpool(db.resolve_sub, sub)
    # upsert_user = DB à CHAQUE requête REST → threadpool (jamais dans la loop).
    await run_in_threadpool(
        lambda: db.upsert_user(sub, email=access_token.claims.get("email"),
                               name=access_token.claims.get("name"),
                               iss=access_token.claims.get("iss")))
    return _maybe_view_as(sub, apply_view_as), None


def _json_error(request: Request, status: int, code: str,
                detail: str | None = None) -> JSONResponse:
    payload = {"error": code}
    if detail:
        payload["detail"] = detail
    return JSONResponse(
        payload,
        status_code=status,
        headers=_cors_headers(request.headers.get("origin")),
    )


def _json(request: Request, payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(
        payload, status_code=status, headers=_cors_headers(request.headers.get("origin"))
    )


async def options_handler(request: Request) -> Response:
    return Response(status_code=204, headers=_cors_headers(request.headers.get("origin")))


def bind(handler: Callable[..., Awaitable[Response]], **deps):
    """Fige les dépendances explicites d'un handler de module en un endpoint
    Starlette `(request) -> Response`.

    Les handlers étaient des CLOSURES de `make_routes` : ils lisaient `verifier` et
    `mcp_instance` dans la portée englobante. Devenus fonctions de module, ils les
    reçoivent en paramètres nommés — et `bind` est le seul endroit où ces paramètres
    sont fournis, à l'assemblage. Rien n'est posé en global : deux appels de
    `make_routes` avec deux verifiers différents restent indépendants.

    ⚠️ **Pas `functools.partial`** : Starlette teste `inspect.isfunction(endpoint)`
    pour choisir entre « handler de requête » et « app ASGI brute ». Un `partial`
    tombe du mauvais côté et la route cesse de répondre. La fonction interne reprend
    le `__name__` du handler pour que `route.name` (donc `url_for`) reste identique
    à celui d'avant la découpe.
    """
    async def endpoint(request: Request):
        return await handler(request, **deps)

    endpoint.__name__ = handler.__name__
    endpoint.__qualname__ = handler.__qualname__
    endpoint.__doc__ = handler.__doc__
    endpoint.__module__ = handler.__module__
    return endpoint
