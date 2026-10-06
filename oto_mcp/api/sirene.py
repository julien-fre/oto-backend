"""REST routes `/api/sirene/*` — consumed by oto-cli (HTTP client) and other
scripts that want batch enrichment without managing a local parquet.

Backend = dedicated FOD service (ADR 0028) via `fod/client` — the DuckDB scan no longer
runs in-process, it is offloaded to the `fod-0` box. Surface unchanged.

- `POST /api/sirene/headquarters` {sirens:[...]}  → headquarters in batch (1 scan)
- `GET /api/sirene/siege?siren=`                 → headquarters (1 dict or null)
- `GET /api/sirene/etablissements?siren=`        → all establishments (list)
- `GET /api/sirene/siret?siret=`                 → 1 establishment
- `GET /api/sirene/search?naf=&code_commune=...` → paginated
- `GET /api/sirene/info`                         → parquet metadata (size, mtime, count)

Auth: Bearer Logto JWT or `oto_*` API token (same `_authenticate` as the rest).
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from oto_mcp.fod import client as sirene_duckdb  # ADR 0028: scan offloaded to FOD


AuthFn = Callable[..., Awaitable[tuple[str | None, JSONResponse | None]]]

# oto-backend#867 batch 2 — these six routes are Starlette `async def` routes that
# called the FOD client (SYNC httpx, `fod/http.py`) bare: a slow FOD scan held
# the loop until its 100s read timeout (shared by ALL FOD clients, not
# changed here — a legitimate SIRENE scan may need it). `_fod` takes the call off
# the loop (`run_in_threadpool`, same primitive as `api/zoho.py:85`) and bounds it to a
# defensible REST delay, shorter than this shared timeout:
# - `_FICHE_S` (single record — siege/siret/etablissements/info): never scans more
#   than one SIREN, must answer in a fraction of a second in normal operation.
# - `_SCAN_S` (search, and above all `headquarters` — up to 10,000 SIRENs in ONE scan):
#   a genuinely large batch may legitimately approach tens of seconds.
_FOD_TIMEOUT_FICHE_S = 20
_FOD_TIMEOUT_SCAN_S = 60


async def _fod(fn, *args, timeout: float, **kwargs):
    """A FOD call (sync function of `fod/client`), off the loop and bounded.

    Raises `asyncio.TimeoutError` beyond `timeout` — the thread keeps running in the
    background (an in-flight HTTP call cannot be interrupted), but
    the REST CALLER gets a named 504 instead of a freeze of the whole process."""
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
        # Batch enrichment: a LIST of SIRENs → headquarters of each in ONE scan
        # (vs N /siege calls). Essential on a remote parquet (httpfs) where
        # each call costs a network request. JSON body {"sirens": [...]}.
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
        # Public-ish — useful for a healthcheck from any client.
        # Auth anyway to avoid disclosing the size.
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
