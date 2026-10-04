"""`GardeRoutesLourdes` : débit par jeton et concurrence par route, sans attente (#1141).

Le mécanisme est générique et sa liste vit à UN endroit (`ROUTES_LOURDES`) ; ces
bancs lui injectent une liste à eux pour le jouer sans dépendre de ce qui y est
inscrit aujourd'hui.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from oto_mcp.api import base as api_base
from oto_mcp.api import routes_lourdes as rl
from oto_mcp.api.routes_lourdes import GardeRoutesLourdes, RouteLourde

_LOURDE = RouteLourde("GET", "/api/orgs/{org_id}/lourd", par_minute=3, concurrence=1,
                      pourquoi="banc")


async def _ok(request):
    return JSONResponse({"ok": True})


def _client(routes=(_LOURDE,), handler=_ok) -> TestClient:
    app = Starlette(routes=[Route("/api/orgs/{org_id}/lourd", handler),
                            Route("/api/orgs/{org_id}/leger", _ok)])
    return TestClient(GardeRoutesLourdes(app, routes=routes))


def _jeton(n: str) -> dict:
    return {"Authorization": f"Bearer jeton-{n}"}


def test_le_gabarit_vise_la_route_et_seulement_elle():
    assert _LOURDE.vise("GET", "/api/orgs/7/lourd")
    assert not _LOURDE.vise("POST", "/api/orgs/7/lourd")
    assert not _LOURDE.vise("GET", "/api/orgs/7/lourd/plus")
    assert not _LOURDE.vise("GET", "/api/orgs/7/8/lourd")


def test_au_dela_du_debit_429_nomme_avec_retry_after(monkeypatch):
    monkeypatch.setattr(api_base, "_allowed_origins", lambda: {"https://front.exemple"})
    c = _client()
    for _ in range(3):
        assert c.get("/api/orgs/1/lourd", headers=_jeton("a")).status_code == 200
    r = c.get("/api/orgs/1/lourd",
              headers={**_jeton("a"), "Origin": "https://front.exemple"})
    assert r.status_code == 429
    corps = r.json()
    assert corps["error"] == "rate_limited"
    assert corps["details"]["retryable"] is True
    assert corps["details"]["limit_per_minute"] == 3
    attente = int(r.headers["retry-after"])
    assert 1 <= attente <= 60
    assert corps["details"]["retry_after_seconds"] == attente
    assert r.headers["access-control-allow-origin"] == "https://front.exemple", (
        "un navigateur doit pouvoir LIRE le refus")


def test_le_debit_se_compte_par_jeton():
    c = _client()
    for _ in range(3):
        c.get("/api/orgs/1/lourd", headers=_jeton("a"))
    assert c.get("/api/orgs/1/lourd", headers=_jeton("a")).status_code == 429
    assert c.get("/api/orgs/1/lourd", headers=_jeton("b")).status_code == 200


def test_le_jeton_n_est_jamais_garde_en_clair():
    garde = _client().app
    TestClient(garde).get("/api/orgs/1/lourd", headers=_jeton("secret"))
    cles = [cle for (_gabarit, cle) in garde._c.appels]
    assert cles and all("secret" not in cle for cle in cles)


def test_la_fenetre_glisse(monkeypatch):
    horloge = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: horloge[0])
    c = _client()
    for _ in range(3):
        c.get("/api/orgs/1/lourd", headers=_jeton("a"))
    assert c.get("/api/orgs/1/lourd", headers=_jeton("a")).status_code == 429
    horloge[0] += 60
    assert c.get("/api/orgs/1/lourd", headers=_jeton("a")).status_code == 200


def test_au_dela_de_la_concurrence_503_sans_attendre_et_sans_consommer_de_debit():
    """La place est tenue par une requête en cours : la suivante est refusée tout
    de suite, et ce refus ne compte pas dans son débit."""
    garde = GardeRoutesLourdes(None, routes=(_LOURDE,))
    assert garde._prendre(_LOURDE, "j:a", 0.0) is None      # une requête en cours
    for _ in range(5):
        refus = garde._prendre(_LOURDE, "j:b", 0.0)
        assert refus is not None and refus[:2] == (503, "route_busy")
    assert len(garde._c.appels.get((_LOURDE.gabarit, "j:b"), ())) == 0


def test_la_place_est_rendue_meme_quand_le_handler_casse():
    async def _boum(request):
        raise RuntimeError("boum")

    c = TestClient(GardeRoutesLourdes(
        Starlette(routes=[Route("/api/orgs/{org_id}/lourd", _boum)]),
        routes=(RouteLourde("GET", "/api/orgs/{org_id}/lourd", par_minute=0,
                            concurrence=1, pourquoi="banc"),)),
        raise_server_exceptions=False)
    for _ in range(3):
        assert c.get("/api/orgs/1/lourd").status_code == 500, (
            "une place perdue ferait répondre 503 à tous les appels suivants")


def test_la_concurrence_est_tenue_pendant_la_requete():
    """Bout en bout : une requête lente tient la place, une seconde concurrente est
    refusée en 503 avec `Retry-After`."""
    entree, sortie = asyncio.Event(), asyncio.Event()

    async def _lent(scope, receive, send):
        entree.set()
        await sortie.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    garde = GardeRoutesLourdes(_lent, routes=(_LOURDE,))
    envoyes: list[dict] = []

    async def _send(m):
        envoyes.append(m)

    async def _recv():
        return {"type": "http.request", "body": b""}

    def _scope():
        return {"type": "http", "method": "GET", "path": "/api/orgs/1/lourd",
                "headers": [(b"authorization", b"Bearer x")], "client": ("t", 0)}

    async def _scenario():
        premiere = asyncio.create_task(garde(_scope(), _recv, _send))
        await entree.wait()
        await garde(_scope(), _recv, _send)
        refus = envoyes[0]
        sortie.set()
        await premiere
        return refus

    refus = asyncio.run(_scenario())
    assert refus["status"] == 503
    assert (b"retry-after", b"2") in refus["headers"]
    assert garde._c.en_cours[_LOURDE.gabarit] == 0


def test_hors_des_routes_declarees_rien_ne_change():
    c = _client()
    for _ in range(10):
        assert c.get("/api/orgs/1/leger", headers=_jeton("a")).status_code == 200


@pytest.mark.parametrize("routes", [(), None])
def test_une_liste_vide_est_un_pass_through(routes, monkeypatch):
    monkeypatch.setattr(rl, "ROUTES_LOURDES", ())
    c = _client(routes=routes)
    for _ in range(10):
        assert c.get("/api/orgs/1/lourd", headers=_jeton("a")).status_code == 200


def test_chaque_route_declaree_dit_pourquoi_et_porte_une_borne():
    for r in rl.ROUTES_LOURDES:
        assert r.pourquoi.strip(), f"{r.methode} {r.gabarit} : sa raison manque"
        assert r.par_minute > 0 or r.concurrence > 0, f"{r.gabarit} : aucune borne"
