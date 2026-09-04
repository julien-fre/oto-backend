"""Routes REST `/api/sirene/*` — consommé par oto-cli (HTTP client) et autres
scripts qui veulent du batch enrichment sans gérer un parquet local.

Backend = service FOD dédié (ADR 0028) via `fod/client` — le scan DuckDB ne tourne
plus in-process, il est déporté sur la box `fod-0`. Surface inchangée.

- `POST /api/sirene/headquarters` {sirens:[...]}  → sièges en batch (1 scan)
- `GET /api/sirene/siege?siren=`                 → siège (1 dict ou null)
- `GET /api/sirene/etablissements?siren=`        → tous établissements (list)
- `GET /api/sirene/siret?siret=`                 → 1 établissement
- `GET /api/sirene/search?naf=&code_commune=...` → paginé
- `GET /api/sirene/info`                         → métadonnées parquet (size, mtime, count)

Auth : Bearer Logto JWT ou API token `oto_*` (même `_authenticate` que le reste).
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from oto_mcp.fod import client as sirene_duckdb  # ADR 0028 : scan déporté sur FOD


AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

# oto-backend#867 lot 2 — ces six routes sont des routes Starlette `async def` qui
# appelaient le client FOD (httpx SYNC, `fod/http.py`) nûment : un scan FOD lent tenait
# la boucle jusqu'à son read timeout de 100s (partagé par TOUS les clients FOD, non
# modifié ici — un scan SIRENE légitime peut en avoir besoin). `_fod` sort l'appel de
# la boucle (`run_in_threadpool`, même primitive qu'`api/zoho.py:85`) et le borne à un
# délai REST défendable, plus court que ce timeout partagé :
# - `_FICHE_S` (fiche unique — siege/siret/etablissements/info) : ne scanne jamais plus
#   d'un SIREN, doit répondre en une fraction de seconde en fonctionnement normal.
# - `_SCAN_S` (search, et surtout `headquarters` — jusqu'à 10 000 SIREN en UN scan) :
#   un vrai lot volumineux peut légitimement approcher les dizaines de secondes.
_FOD_TIMEOUT_FICHE_S = 20
_FOD_TIMEOUT_SCAN_S = 60


async def _fod(fn, *args, timeout: float, **kwargs):
    """Un appel FOD (fonction sync de `fod/client`), hors boucle et borné.

    Lève `asyncio.TimeoutError` au-delà de `timeout` — le thread continue en
    arrière-plan (impossible d'interrompre un appel HTTP en cours), mais
    l'APPELANT REST reçoit un 504 nommé au lieu d'un gel de tout le processus."""
    return await asyncio.wait_for(run_in_threadpool(fn, *args, **kwargs), timeout=timeout)


def _qp(request: Request, name: str) -> str | None:
    v = request.query_params.get(name)
    return v.strip() if v else None


def _qp_int(request: Request, name: str, default: int) -> int:
    v = request.query_params.get(name)
    try:
        return int(v) if v else default
    except ValueError:
        return default


def _qp_bool(request: Request, name: str, default: bool) -> bool:
    v = request.query_params.get(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def make_routes(
    verifier: JWTVerifier,
    authenticate: AuthFn,
    json_response: Callable[..., JSONResponse],
    json_error: Callable[..., JSONResponse],
    options_handler: Callable[[Request], Awaitable[Response]],
) -> list[Route]:

    async def siege(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        siren = _qp(request, "siren")
        if not siren or not siren.isdigit() or len(siren) != 9:
            return json_error(request, 400, "invalid_siren")
        try:
            siege = await _fod(sirene_duckdb.lookup_siege, siren, timeout=_FOD_TIMEOUT_FICHE_S)
        except asyncio.TimeoutError:
            return json_error(request, 504, f"fod_timeout: no response within {_FOD_TIMEOUT_FICHE_S}s")
        return json_response(request, {"siege": siege})

    async def etablissements(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        siren = _qp(request, "siren")
        if not siren or not siren.isdigit() or len(siren) != 9:
            return json_error(request, 400, "invalid_siren")
        active_only = _qp_bool(request, "active_only", True)
        try:
            items = await _fod(sirene_duckdb.list_establishments, siren,
                               active_only=active_only, timeout=_FOD_TIMEOUT_FICHE_S)
        except asyncio.TimeoutError:
            return json_error(request, 504, f"fod_timeout: no response within {_FOD_TIMEOUT_FICHE_S}s")
        return json_response(request, {"items": items, "count": len(items)})

    async def siret(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        s = _qp(request, "siret")
        if not s or not s.isdigit() or len(s) != 14:
            return json_error(request, 400, "invalid_siret")
        try:
            etab = await _fod(sirene_duckdb.lookup_siret, s, timeout=_FOD_TIMEOUT_FICHE_S)
        except asyncio.TimeoutError:
            return json_error(request, 504, f"fod_timeout: no response within {_FOD_TIMEOUT_FICHE_S}s")
        return json_response(request, {"etablissement": etab})

    async def search(request: Request) -> JSONResponse:
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        _tranche = _qp(request, "tranche_effectifs")
        try:
            items = await _fod(
                sirene_duckdb.search,
                naf=_qp(request, "naf"),
                code_commune=_qp(request, "code_commune"),
                code_postal=_qp(request, "code_postal"),
                departement=_qp(request, "departement"),
                denomination=_qp(request, "denomination"),
                enseigne=_qp(request, "enseigne"),
                active_only=_qp_bool(request, "active_only", True),
                sieges_only=_qp_bool(request, "sieges_only", False),
                tranche_effectifs=(
                    [c.strip() for c in _tranche.split(",") if c.strip()]
                    if _tranche
                    else None
                ),
                limit=_qp_int(request, "limit", 100),
                offset=_qp_int(request, "offset", 0),
                timeout=_FOD_TIMEOUT_SCAN_S,
            )
        except asyncio.TimeoutError:
            return json_error(request, 504, f"fod_timeout: no response within {_FOD_TIMEOUT_SCAN_S}s")
        return json_response(request, {
            "items": items,
            "count": len(items),
            "limit": _qp_int(request, "limit", 100),
            "offset": _qp_int(request, "offset", 0),
        })

    async def headquarters(request: Request) -> JSONResponse:
        # Batch enrichment : une LISTE de SIREN → siège de chacun en UN scan
        # (vs N appels /siege). Indispensable sur parquet distant (httpfs) où
        # chaque appel coûte une requête réseau. Body JSON {"sirens": [...]}.
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return json_error(request, 400, "invalid_json")
        sirens = body.get("sirens") if isinstance(body, dict) else None
        if not isinstance(sirens, list) or not sirens:
            return json_error(request, 400, "sirens_required")
        if len(sirens) > 10000:
            return json_error(request, 400, "too_many_sirens")
        clean = [str(s).strip() for s in sirens]
        if not all(s.isdigit() and len(s) == 9 for s in clean):
            return json_error(request, 400, "invalid_siren")
        try:
            addresses = await _fod(sirene_duckdb.headquarters_addresses, clean,
                                   timeout=_FOD_TIMEOUT_SCAN_S)
        except asyncio.TimeoutError:
            return json_error(request, 504, f"fod_timeout: no response within {_FOD_TIMEOUT_SCAN_S}s")
        return json_response(request, {"headquarters": addresses, "count": len(addresses)})

    async def info(request: Request) -> JSONResponse:
        # Public-ish — utile pour healthcheck depuis n'importe quel client.
        # Auth quand même pour éviter de divulguer la taille.
        sub, err = await authenticate(request, verifier)
        if err:
            return err
        try:
            meta = await _fod(sirene_duckdb.parquet_info, timeout=_FOD_TIMEOUT_FICHE_S)
        except asyncio.TimeoutError:
            return json_error(request, 504, f"fod_timeout: no response within {_FOD_TIMEOUT_FICHE_S}s")
        return json_response(request, meta)

    return [
        Route("/api/sirene/headquarters", headquarters, methods=["POST"]),
        Route("/api/sirene/headquarters", options_handler, methods=["OPTIONS"]),
        Route("/api/sirene/siege", siege, methods=["GET"]),
        Route("/api/sirene/siege", options_handler, methods=["OPTIONS"]),
        Route("/api/sirene/etablissements", etablissements, methods=["GET"]),
        Route("/api/sirene/etablissements", options_handler, methods=["OPTIONS"]),
        Route("/api/sirene/siret", siret, methods=["GET"]),
        Route("/api/sirene/siret", options_handler, methods=["OPTIONS"]),
        Route("/api/sirene/search", search, methods=["GET"]),
        Route("/api/sirene/search", options_handler, methods=["OPTIONS"]),
        Route("/api/sirene/info", info, methods=["GET"]),
        Route("/api/sirene/info", options_handler, methods=["OPTIONS"]),
    ]
