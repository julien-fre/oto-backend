"""oto-backend#867 lot 2 — les neuf routes REST FOD (SIRENE + accords d'entreprise)
ne bloquent plus la boucle d'événements, et un FOD lent rend une erreur nommée.

Même méthode que `tests/test_capacites_hors_boucle.py` et le lot 1
(`tests/connectors/test_identities_unipile_hors_boucle.py`) : **on OBSERVE le
thread**, jamais le source. Et un contrôle qui MORD : la sonde de thread doit
savoir dire « dans la boucle » quand on lui rejoue le geste nu — sinon un vert
ne prouverait rien.
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.requests import Request

from oto_mcp.api import accords as accords_routes
from oto_mcp.api import sirene as sirene_routes
from oto_mcp.fod import client as sirene_duckdb
from oto_mcp.fod import fr as fod_fr


# --------------------------------------------------------------------------- #
# Le montage minimal : authentifié d'office, erreurs capturées telles quelles.
# --------------------------------------------------------------------------- #

async def _authenticate(_request, _verifier):
    return "sub-test", None


def _json_response(_request, payload, status=200):
    return {"status": status, "body": payload}


def _json_error(_request, status, code, message=None):
    return {"status": status, "error": code, "detail": message}


async def _options_handler(_request):
    return None


def _routes(module):
    return module.make_routes(None, _authenticate, _json_response, _json_error,
                              _options_handler)


def _endpoint(routes, path, method):
    return next(r.endpoint for r in routes
               if r.path == path and method in r.methods)


def _get(path: str, query: str = "") -> Request:
    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "GET", "path": path, "headers": [],
                    "query_string": query.encode(), "path_params": {}}, receive=_receive)


def _post(path: str, body: bytes) -> Request:
    async def _receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": path,
                    "headers": [(b"content-type", b"application/json")],
                    "query_string": b"", "path_params": {}}, receive=_receive)


def _joue(coro):
    """Joue une coroutine dans une boucle neuve ; rend le thread de CETTE boucle."""
    porteur: dict = {}

    async def _run():
        porteur["boucle"] = threading.current_thread()
        return await coro
    result = asyncio.run(_run())
    return porteur["boucle"], result


# --------------------------------------------------------------------------- #
# Table des neuf routes : (module, path, method, request, cible sync mockée,
# nom de la cible sur le module, palier de timeout attendu)
# --------------------------------------------------------------------------- #

SIRENE_FICHE = sirene_routes._FOD_TIMEOUT_FICHE_S
SIRENE_SCAN = sirene_routes._FOD_TIMEOUT_SCAN_S
ACCORDS_FICHE = accords_routes._FOD_TIMEOUT_FICHE_S
ACCORDS_SCAN = accords_routes._FOD_TIMEOUT_SCAN_S

CASES = [
    # (module, path, method, request builder, module à patcher, nom de la cible,
    #  attribut du délai attendu, valeur de retour NEUTRE (forme attendue par la route))
    pytest.param(sirene_routes, "/api/sirene/siege", "GET",
                lambda: _get("/api/sirene/siege", "siren=123456789"),
                sirene_duckdb, "lookup_siege", "_FOD_TIMEOUT_FICHE_S", None,
                id="sirene-siege"),
    pytest.param(sirene_routes, "/api/sirene/etablissements", "GET",
                lambda: _get("/api/sirene/etablissements", "siren=123456789"),
                sirene_duckdb, "list_establishments", "_FOD_TIMEOUT_FICHE_S", [],
                id="sirene-etablissements"),
    pytest.param(sirene_routes, "/api/sirene/siret", "GET",
                lambda: _get("/api/sirene/siret", "siret=12345678900012"),
                sirene_duckdb, "lookup_siret", "_FOD_TIMEOUT_FICHE_S", None,
                id="sirene-siret"),
    pytest.param(sirene_routes, "/api/sirene/search", "GET",
                lambda: _get("/api/sirene/search", "naf=6201Z"),
                sirene_duckdb, "search", "_FOD_TIMEOUT_SCAN_S", [],
                id="sirene-search"),
    pytest.param(sirene_routes, "/api/sirene/headquarters", "POST",
                lambda: _post("/api/sirene/headquarters", b'{"sirens": ["123456789"]}'),
                sirene_duckdb, "headquarters_addresses", "_FOD_TIMEOUT_SCAN_S", {},
                id="sirene-headquarters"),
    pytest.param(sirene_routes, "/api/sirene/info", "GET",
                lambda: _get("/api/sirene/info"),
                sirene_duckdb, "parquet_info", "_FOD_TIMEOUT_FICHE_S", {},
                id="sirene-info"),
    pytest.param(accords_routes, "/api/fr/accords/search", "POST",
                lambda: _post("/api/fr/accords/search", b'{"query": "test"}'),
                fod_fr, "search_acco", "_FOD_TIMEOUT_SCAN_S", {"items": [], "count": 0},
                id="accords-search"),
    pytest.param(accords_routes, "/api/fr/accords/themes", "GET",
                lambda: _get("/api/fr/accords/themes"),
                fod_fr, "acco_themes", "_FOD_TIMEOUT_FICHE_S", [],
                id="accords-themes"),
    pytest.param(accords_routes, "/api/fr/accords/{id_or_numero}", "GET",
                lambda: _get("/api/fr/accords/T07524001234"),
                fod_fr, "get_acco", "_FOD_TIMEOUT_FICHE_S", None,
                id="accords-get-one"),
]


def _wire_path_params(req: Request, ref: str):
    req.scope["path_params"] = {"id_or_numero": ref}
    return req


def _prep(build_req, path):
    req = build_req()
    if path == "/api/fr/accords/{id_or_numero}":
        _wire_path_params(req, "T07524001234")
    return req


@pytest.mark.parametrize("module,path,method,build_req,target_mod,target_name,"
                        "timeout_attr,neutre", CASES)
def test_route_tourne_hors_boucle(monkeypatch, module, path, method, build_req,
                                  target_mod, target_name, timeout_attr, neutre):
    vu: dict = {}

    def _sync(*a, **k):
        vu["thread"] = threading.current_thread()
        return neutre

    monkeypatch.setattr(target_mod, target_name, _sync)
    handler = _endpoint(_routes(module), path, method)

    boucle, _ = _joue(handler(_prep(build_req, path)))
    assert vu["thread"] is not boucle, (
        f"{path} ({method}) a appelé FOD dans le thread de l'event loop — "
        "c'est exactement ce qui a gelé la production (oto-backend#867)")


@pytest.mark.parametrize("module,path,method,build_req,target_mod,target_name,"
                        "timeout_attr,neutre", CASES)
def test_route_lente_rend_un_504_nomme(monkeypatch, module, path, method, build_req,
                                       target_mod, target_name, timeout_attr, neutre):
    monkeypatch.setattr(module, timeout_attr, 0.05)

    def _lent(*a, **k):
        import time
        time.sleep(1)

    monkeypatch.setattr(target_mod, target_name, _lent)
    handler = _endpoint(_routes(module), path, method)

    _, result = _joue(handler(_prep(build_req, path)))
    assert result["status"] == 504 and result["error"].startswith("fod_timeout"), (
        f"{path} ({method}) lent doit rendre un 504 fod_timeout nommé, pas un gel "
        f"ni une autre forme d'échec — reçu {result!r}")


def test_le_controle_mord__un_appel_NU_dans_la_boucle_est_detecte():
    """Contrôle négatif, comme au lot 1 : la sonde de thread doit savoir dire
    « dans la boucle » — sinon un vert sur les tests ci-dessus ne prouverait rien."""
    vu: dict = {}

    async def _nu():
        vu["thread"] = threading.current_thread()
        return 1

    boucle, _ = _joue(_nu())
    assert vu["thread"] is boucle, "la sonde elle-même doit savoir dire « dans la boucle »"
