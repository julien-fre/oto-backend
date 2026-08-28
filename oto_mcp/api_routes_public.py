"""Handlers des surfaces REST **publiques** — servies sans en-tête d'auth.

Ce qu'elles ont en commun n'est pas un sujet métier mais un régime : l'adaptateur
REST des capacités authentifie TOUJOURS, donc une surface anonyme ne peut pas y
passer et reste écrite à la main (l'argument est déjà dans `doctrines_library_public`).
Quatre d'entre elles sont consommées par un PROGRAMME sans en-tête : le build du
site vitrine (`refresh-catalog.mjs` → catalog/connectors/doctrines/guides) et celui
de docs.oto.cx (`refresh-openapi.mjs` → openapi.json).

- `GET /favicon.svg` + `/favicon.ico`      → mark de marque (l'endpoint MCP n'a pas de page racine)
- `GET /api/mcp/catalog`                   → catalogue des tools MCP (autodoc)
- `GET /openapi.json` + `/api/openapi.json` → descriptif REST dérivé (`openapi.py`)
- `GET /api/connectors`                    → catalogue des connecteurs (auth OPTIONNELLE)
- `GET /api/doctrines/library[/{slug}]`    → bibliothèque publique de doctrines
- `GET /api/guides/library[/{slug}]`       → guides PLATEFORME
- `GET /api/invitations/{token}` + `/code/{code}` → aperçu d'invitation (le jeton EST le secret)
- `GET /api/public/docs/{token}`           → doc partagé (JSON)
- `GET /p/d/{token}`                       → le même, server-rendered (lisible par un agent sans JS)

`/api/connectors` est la seule MIXTE : anonyme pour la vitrine, authentifiée pour
le dashboard qui y scope son catalogue sur l'org active — d'où son `verifier`.

La table de routes (chemins, méthodes, ORDRE) reste assemblée dans
`api_routes.make_routes` : l'ordre de montage est un contrat, il se lit d'un seul
endroit. Ce module ne porte que les handlers.
"""
from __future__ import annotations

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                                 Response)

from . import access, providers, db, guide_store, openapi, org_store
from .connectors import activation as connector_activation
from .api_routes_base import _authenticate, _json, _json_error


async def favicon(request: Request) -> Response:
    """Favicon de marque servi sur mcp.oto.cx (mark canonique, aligné oto.cx).

    L'endpoint MCP n'a pas de page HTML racine → un navigateur/annuaire qui
    sonde `/favicon.svg` ou `/favicon.ico` tombait sur un 404 (aucune icône
    de marque). On sert le mark Otomata (source unique `brand.py`) sur les
    deux chemins.
    """
    from . import brand
    return Response(
        brand.FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def mcp_catalog(request: Request, *, mcp_instance) -> JSONResponse:
    """Liste publique des tools MCP exposés — alimente l'autodoc oto.ninja.

    Pas d'auth : la doc des tools (nom, description, schémas) est de toute
    façon découvrable via tools/list du protocole MCP. CORS large pour
    permettre fetch côté oto.ninja.
    """
    if mcp_instance is None:
        return _json(request, {"tools": []})
    try:
        tools = await mcp_instance.list_tools(run_middleware=False)
    except Exception as e:
        return _json_error(request, 500, f"list_tools_failed:{e}")
    payload = []
    # (Le filtre « bridges remote per-namespace » a été retiré — ADR 0034 B4 :
    # le namespace `bridge` est générique, aucun nom client n'atteint l'autodoc.)
    for t in tools:
        # Tool object exposes name, description, parameters (input schema),
        # output_schema. Some attributes may be None depending on the type.
        payload.append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": getattr(t, "parameters", None),
            "output_schema": getattr(t, "output_schema", None),
        })
    return _json(request, {"tools": payload, "count": len(payload)})


async def openapi_doc(request: Request) -> JSONResponse:
    """Descriptif OpenAPI de l'API REST — **dérivé** du registre de capacités et
    de la table de routes VIVANTE (`request.app.routes`), donc jamais désynchronisé.

    Pas d'auth, comme `/api/mcp/catalog` : un descriptif d'API décrit des FORMES,
    aucune valeur. Sans lui, chaque intégrateur redécouvre la surface par sondage
    de chemins — et conclut faux (cf. `openapi.py`). `/api/admin/*` est exclu.
    """
    try:
        routes = getattr(request.app, "routes", None)
    # noqa: SILENT — document OpenAPI servi même si la table de routes n'est pas lisible
    except Exception:                                   # pas d'app Starlette exposée
        routes = None
    base = str(request.base_url).rstrip("/") or None
    return _json(request, openapi.build(routes, server_url=base))


async def connectors_catalog(request: Request, *, verifier: JWTVerifier) -> JSONResponse:
    """Catalogue des connecteurs (registre source unique), auth optionnelle.

    Cran d'activation (ADR 0010) filtré EN AMONT de la visibilité : un
    connecteur non activé (master global OFF sans override d'org ON) n'apparaît
    pas dans la vue PRODUIT (anonyme + non-admin). L'**admin voit tout le
    registre** — sa vue de gouvernance sert justement à activer/désactiver.
    Ensuite, visibilité : anonyme → self-serve seuls (les `platform_granted`,
    dont les bridges client-sensibles ADR 0003, sont deny-by-default comme sur
    la face MCP) ; non-admin authentifié → + ceux dont un namespace est entitled
    pour le sub (override d'org appliqué via son org active).
    """
    cat = providers.public_catalog()
    if not request.headers.get("authorization"):
        exposed = connector_activation.exposed_connectors(None)
        cat = [c for c in cat if c["name"] in exposed]
        cat = [c for c in cat if c["availability"] != "platform_granted"]
        return _json(request, {"connectors": cat})
    sub, err = await _authenticate(request, verifier)
    if err:
        return err
    if not access.is_platform_operator(sub):
        # Visibilité par l'activation (master × override d'org). Un connecteur à
        # clé plateforme réservé (ex. scaleway) est tenu hors des orgs non
        # autorisées par son activation (master OFF + override org ON), plus par
        # un grant de namespace (retiré, ADR 0031).
        # Org de CONTEXTE (seam ADR 0023 : consultation X-Oto-Org > maison) —
        # le catalogue suit l'org consultée au dashboard, comme status_for.
        exposed = connector_activation.exposed_connectors(access.current_org(sub))
        cat = [c for c in cat if c["name"] in exposed]
    return _json(request, {"connectors": cat})


async def doctrines_library_public(request: Request) -> JSONResponse:
    """Catalogue PUBLIC des doctrines (bibliothèque/marketplace) — pas d'auth.

    Alimente le site vitrine oto.ninja. Deny-by-default : `visibility='public'`
    UNIQUEMENT (jamais 'unlisted' ni les brouillons d'org). Filtres gros grain
    en query params (`q`/`category`/`author`) ; le filtrage fin reste client.
    Route écrite à la main car l'adaptateur REST des capacités authentifie
    toujours (l'anonyme ne peut pas y passer).
    """
    q = request.query_params
    try:
        limit = min(int(q.get("limit", "100")), 200)
    except ValueError:
        limit = 100
    items = org_store.list_library(
        query=q.get("q"), category=q.get("category"),
        author_kind=q.get("author"), include_unlisted=False, limit=limit)
    return _json(request, {"doctrines": items})


async def doctrines_library_public_get(request: Request) -> JSONResponse:
    """Une doctrine PUBLIQUE complète (markdown) par slug — vitrine, pas d'auth.
    Public-only : une entrée 'unlisted' n'est jamais servie ici."""
    entry = org_store.get_library_entry(
        slug=request.path_params["slug"], include_unlisted=False)
    if not entry:
        return _json_error(request, 404, "unknown_entry")
    return _json(request, entry)


async def guides_library_public(request: Request) -> JSONResponse:
    """Catalogue PUBLIC des guides PLATEFORME — pas d'auth.

    Même rôle que `doctrines_library_public` : alimenter la vitrine (snapshot
    build-time du site) et rendre lisible par un humain ce que l'agent charge
    via `oto_guide`. Deny-by-default par CONSTRUCTION plutôt que par filtre :
    `list_guides_for()` sans `sub` ni `org_id` ne rend que le scope plateforme
    — un guide d'org ou d'user ne peut pas fuir ici, même par erreur d'appel.
    """
    return _json(request, {"guides": guide_store.list_guides_for()})


async def guides_library_public_get(request: Request) -> JSONResponse:
    """Un guide PLATEFORME complet (markdown) par slug — vitrine, pas d'auth.
    `scope='platform'` est EXPLICITE : sans lui, `read_guide_scoped` cherche
    aussi org puis user, ce qu'une route anonyme ne doit jamais faire."""
    g = guide_store.read_guide_scoped(request.path_params["slug"], scope="platform")
    if not g:
        return _json_error(request, 404, "unknown_guide")
    return _json(request, g)


async def invite_preview(request: Request) -> JSONResponse:
    """Aperçu PUBLIC d'une invitation (pas d'auth — le token est le secret).
    Alimente la page d'accueil « vous êtes invité·e » avant la création de
    compte : email visé + inviteur, pour accompagner l'onboarding."""
    p = org_store.preview_invitation(request.path_params.get("token", ""))
    if not p:
        return _json_error(request, 404, "invalid_or_expired")
    return _json(request, p)


async def invite_preview_by_code(request: Request) -> JSONResponse:
    """Aperçu PUBLIC d'une invitation d'org par code court (/invitation/<code>)."""
    p = org_store.preview_invitation_by_code(request.path_params.get("code", ""))
    if not p:
        return _json_error(request, 404, "invalid_or_expired")
    return _json(request, p)


async def public_doc(request: Request) -> JSONResponse:
    """Lecture publique d'un doc partagé par token (gap #4a) — PAS d'auth,
    lecture seule. Le dashboard rend le markdown sur sa route publique /p/d/<token>."""
    token = request.path_params.get("token", "")
    doc = db.get_doc_by_public_token(token) if token else None
    if not doc:
        return _json_error(request, 404, "not_found")
    return _json(request, {"title": doc["title"], "body_md": doc["body_md"],
                           "updated_at": doc.get("updated_at")})


async def public_doc_view(request: Request) -> Response:
    """Page de partage PUBLIQUE d'un doc — route `/p/d/<token>`, **server-rendered**
    pour être lisible par un agent (WebFetch sans JS) autant que par un navigateur.
    Négocie sur `Accept` : `application/json` → JSON, `text/markdown` → markdown brut,
    sinon HTML autoporté (`public_doc_page`). PAS d'auth, lecture seule."""
    from . import public_doc_page
    token = request.path_params.get("token", "")
    doc = db.get_doc_by_public_token(token) if token else None
    accept = request.headers.get("accept", "").lower()
    wants_json = "application/json" in accept
    if not doc:
        if wants_json:
            return _json_error(request, 404, "not_found")
        return HTMLResponse(public_doc_page.render_missing(), status_code=404)
    title, body_md = doc["title"], doc.get("body_md") or ""
    if wants_json:
        return _json(request, {"title": title, "body_md": body_md,
                               "updated_at": doc.get("updated_at")})
    if "text/markdown" in accept:
        md = f"# {title}\n\n{body_md}" if title else body_md
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8",
                                 headers={"Cache-Control": "public, max-age=300"})
    html_page = public_doc_page.render(title=title, body_md=body_md,
                                       updated_at=doc.get("updated_at"))
    return HTMLResponse(html_page, headers={"Cache-Control": "public, max-age=300"})
