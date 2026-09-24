"""oto#166, E10 — la route du calcul de l'agent rend EXACTEMENT ce que la poignée de main
lui donne, pour trois comptes : org sans kit, org avec kit, org dont un connecteur du
kit est coupé. Plus un membre qui a mis en pause, et un connecteur restreint.

Le calcul n'est pas refait ici : le banc compare la réponse SERVIE (chaîne REST) à la
sortie de `compute_hidden_tools` sur la même instance — vrai PostgreSQL, vrais gestes.
"""
from __future__ import annotations

import asyncio
import os
import types

import pytest

from _datastore_rest import call, stub_authz

CONNECTEURS = ("hunter", "kaspr", "folk", "osm")
OUTILS = [f"{c}_{v}" for c in CONNECTEURS for v in ("a", "b")] + ["oto_whoami", "data_rows"]


class _Instance:
    async def list_tools(self, run_middleware=False):
        return [types.SimpleNamespace(name=n) for n in OUTILS]


@pytest.fixture(scope="module")
def live(pg_module_dsn):
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        from oto_mcp.connectors import activation
        init_db()
        for nom in CONNECTEURS:
            activation.set_activation(nom, True)
        yield
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


@pytest.fixture()
def instance(monkeypatch):
    from oto_mcp import tool_registry
    from oto_mcp.capabilities import agent_toolbox as at
    inst = _Instance()
    monkeypatch.setattr(tool_registry, "bound_instance", lambda: inst)
    monkeypatch.setattr(at.tool_registry, "bound_instance", lambda: inst)
    return inst


def _org(nom, admin, *membres):
    from oto_mcp import org_store
    oid = org_store.create_org(nom, created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    for m in membres:
        org_store.add_org_member(oid, m, "org_member")
    return oid


def _poignee_de_main(inst, sub, org) -> list[str]:
    from oto_mcp import session_visibility as SV
    caches = asyncio.run(SV.compute_hidden_tools(types.SimpleNamespace(fastmcp=inst), sub, org=org))
    return sorted(set(OUTILS) - caches)


def _route(monkeypatch, sub, org) -> dict:
    stub_authz(monkeypatch, org_id=org)
    code, corps = call("me.agent_toolbox", sub=sub)
    assert code == 200, corps
    return corps


def _geste(monkeypatch, cle, sub, org, **kw):
    stub_authz(monkeypatch, org_id=org)
    path = {"id": str(org), **({"name": kw["name"]} if "name" in kw else {})}
    code, corps = call(cle, sub=sub, path_params=path, body=kw.get("body"))
    assert code == 200, corps
    return corps


def test_org_sans_kit_ne_voit_que_le_spine(live, instance, monkeypatch):
    sub = "e10-vide"
    org = _org("E10 sans kit", sub)
    out = _route(monkeypatch, sub, org)
    assert out["available"] is True and out["org_id"] == org
    assert out["tools"] == _poignee_de_main(instance, sub, org) == ["data_rows", "oto_whoami"]
    assert out["connectors"] == [] and out["spine_tools"] == 2 and out["tools_total"] == len(OUTILS)
    assert out["installed_not_seen"] == []


def test_org_avec_kit_voit_les_outils_du_kit(live, instance, monkeypatch):
    admin, m = "e10-kit-admin", "e10-kit-m"
    org = _org("E10 kit", admin, m)
    _geste(monkeypatch, "connectors.recommend", admin, org, body={"connectors": ["hunter", "kaspr"]})
    out = _route(monkeypatch, m, org)
    assert out["tools"] == _poignee_de_main(instance, m, org)
    assert set(out["tools"]) >= {"hunter_a", "hunter_b", "kaspr_a", "kaspr_b"}
    assert [(c["name"], c["tools"], c["origin"]) for c in out["connectors"]] == [
        ("hunter", 2, "kit"), ("kaspr", 2, "kit")]


def test_connecteur_du_kit_coupe_est_installe_mais_pas_vu_et_dit_pourquoi(live, instance, monkeypatch):
    admin, m = "e10-cut-admin", "e10-cut-m"
    org = _org("E10 coupe", admin, m)
    _geste(monkeypatch, "connectors.recommend", admin, org, body={"connectors": ["folk", "osm"]})
    _geste(monkeypatch, "connectors.activation.set_org", admin, org, name="folk", body={"enabled": False})
    out = _route(monkeypatch, m, org)
    assert out["tools"] == _poignee_de_main(instance, m, org)
    assert [c["name"] for c in out["connectors"]] == ["osm"]
    assert out["installed_not_seen"] == [
        {"name": "folk", "label": out["installed_not_seen"][0]["label"], "state": "active",
         "origin": "kit", "reason": "cut"}]
    # Réouverture : sans geste de rattrapage, la route le voit — comme l'agent.
    _geste(monkeypatch, "connectors.activation.set_org", admin, org, name="folk", body={"enabled": True})
    out = _route(monkeypatch, m, org)
    assert [c["name"] for c in out["connectors"]] == ["folk", "osm"] and out["installed_not_seen"] == []


def test_la_pause_est_nommee(live, instance, monkeypatch):
    admin, m = "e10-pr-admin", "e10-pr-m"
    org = _org("E10 pause", admin, m)
    stub_authz(monkeypatch, org_id=org)
    assert call("connectors.select", sub=m, path_params={"name": "hunter"})[0] == 200
    assert call("connectors.pause", sub=m, path_params={"name": "hunter"})[0] == 200
    out = _route(monkeypatch, m, org)
    assert out["tools"] == _poignee_de_main(instance, m, org) == ["data_rows", "oto_whoami"]
    assert {(x["name"], x["state"], x["origin"], x["reason"]) for x in out["installed_not_seen"]} == {
        ("hunter", "paused", "membre", "paused")}


def test_sans_instance_liee_la_vue_dit_indisponible_jamais_zero(live, monkeypatch):
    from oto_mcp import tool_registry
    from oto_mcp.capabilities import agent_toolbox as at
    monkeypatch.setattr(at.tool_registry, "bound_instance", lambda: None)
    sub = "e10-nu"
    org = _org("E10 indisponible", sub)
    out = _route(monkeypatch, sub, org)
    assert out == {"org_id": org, "available": False}
