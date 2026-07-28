"""Routes REST `/api/fr/accords/*` — le REPLI de la CLI sur les accords d'entreprise.

Même raison d'être qu'`api_routes_sirene` : l'index ACCO vit dans le service FOD
(réseau privé, injoignable depuis un poste), donc un client local ne peut pas
l'interroger directement. Ces routes le republient derrière l'authentification
habituelle, ce qui donne à `oto fr accords …` un chemin qui ne dépend pas du
transport MCP — le cas vécu étant une indisponibilité du connecteur en pleine
campagne, sans aucun plan B.

- `POST /api/fr/accords/search` {query, themes, idcc, siren, …} → page de résultats
- `GET  /api/fr/accords/themes`                                 → nomenclature
- `GET  /api/fr/accords/{id_or_numero}`                         → un accord

Auth : Bearer Logto JWT ou token API `oto_*` (même `authenticate` que le reste).
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from oto_mcp import fod_fr

AuthFn = Callable[..., Awaitable[tuple]]


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    async def search(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return json_error(request, 400, "invalid_json")
        if not isinstance(body, dict):
            return json_error(request, 400, "invalid_body")
        # Surface identique au tool MCP `fr_accords_search` : mêmes filtres, mêmes
        # défauts — un agent qui bascule sur la CLI ne réapprend rien.
        return json_response(request, fod_fr.search_acco(
            query=body.get("query"), themes=body.get("themes"),
            nature=body.get("nature"), siren=body.get("siren"),
            siret=body.get("siret"), idcc=body.get("idcc"),
            departement=body.get("departement"),
            date_from=body.get("date_from"), date_to=body.get("date_to"),
            latest_per_siret=bool(body.get("latest_per_siret")),
            sort_by=body.get("sort_by") or "date",
            sort_dir=body.get("sort_dir") or "desc",
            limit=int(body.get("limit") or 50), offset=int(body.get("offset") or 0),
        ))

    async def themes(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        return json_response(request, {"themes": fod_fr.acco_themes()})

    async def get_one(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        ref = request.path_params["id_or_numero"]
        row = fod_fr.get_acco(ref)
        if not row:
            return json_error(request, 404, "not_found", f"Aucun accord « {ref} ».")
        return json_response(request, row)

    return [
        Route("/api/fr/accords/search", search, methods=["POST"]),
        Route("/api/fr/accords/search", options_handler, methods=["OPTIONS"]),
        Route("/api/fr/accords/themes", themes, methods=["GET"]),
        Route("/api/fr/accords/themes", options_handler, methods=["OPTIONS"]),
        Route("/api/fr/accords/{id_or_numero}", get_one, methods=["GET"]),
        Route("/api/fr/accords/{id_or_numero}", options_handler, methods=["OPTIONS"]),
    ]
