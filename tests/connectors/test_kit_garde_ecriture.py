"""oto#166, barreau 3 — la garde à l'écriture du kit, et plus rien d'écarté en silence.

ADR 0050 §E2. Au tag v1.264.0, l'écriture du kit (`connectors.recommend`) ne vérifiait
ni le nom ni l'exposition — la garde au handler que l'ADR 0019 §1 prescrit n'existait
pas — et le semis ÉCARTAIT EN SILENCE un connecteur du kit que l'org avait coupé (une
entrée sur 182 en production le 11/09). Désormais :

- un AJOUT au kit nomme un connecteur connu et exposé pour l'org, sinon tout le geste
  est refusé, raison nommée, et rien n'est écrit ;
- un connecteur coupé APRÈS sa mise au kit y reste : il s'installe (semis compris),
  masqué chez tous tant que la coupure dure, et revient SEUL à la réouverture ;
- les réponses le disent : `cut` sur les gestes du kit, `in_kit` sur la coupure.

Comportement SERVI : la chaîne REST, la console MCP, et ce que voit l'agent d'un
membre par le vrai `compute_hidden_tools` — sur un vrai PostgreSQL.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from _datastore_rest import call, stub_authz

CONNECTEURS = ("hunter", "kaspr", "folk", "osm")


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
            activation.set_activation(nom, True)       # master plateforme ON
        yield
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def _org(nom: str, admin: str, *membres: str) -> int:
    from oto_mcp import org_store
    oid = org_store.create_org(nom, created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    for m in membres:
        org_store.add_org_member(oid, m, "org_member")
    return oid


def _ligne(sub: str, org: int, connecteur: str):
    from oto_mcp import db
    with db._connect() as c:
        r = c.execute("SELECT state, origin FROM user_selected_connectors "
                      "WHERE sub = %s AND org_id = %s AND connector = %s",
                      (sub, org, connecteur)).fetchone()
    return dict(r) if r else None


class _Ctx:
    class _FastMCP:
        async def list_tools(self, run_middleware=False):
            return [type("T", (), {"name": f"{c}_x"})() for c in CONNECTEURS]

    fastmcp = _FastMCP()


def _vus_par_l_agent(sub: str, org: int) -> set[str]:
    from oto_mcp import session_visibility as SV
    caches = asyncio.run(SV.compute_hidden_tools(_Ctx(), sub, org=org))
    return {c for c in CONNECTEURS if f"{c}_x" not in caches}


def _kit(monkeypatch, admin: str, org: int, connecteurs: list[str]) -> tuple[int, dict]:
    stub_authz(monkeypatch, org_id=org)
    return call("connectors.recommend", sub=admin, path_params={"id": str(org)},
                body={"connectors": connecteurs})


def _ajout(monkeypatch, admin: str, org: int, nom: str) -> tuple[int, dict]:
    stub_authz(monkeypatch, org_id=org)
    return call("connectors.bulk_select", sub=admin, path_params={"id": str(org), "name": nom})


def _coupe(monkeypatch, admin: str, org: int, nom: str, enabled: bool) -> dict:
    stub_authz(monkeypatch, org_id=org)
    code, corps = call("connectors.activation.set_org", sub=admin,
                       path_params={"id": str(org), "name": nom}, body={"enabled": enabled})
    assert code == 200, corps
    return corps


def _rien_n_est_ecrit(org: int, *subs: str) -> None:
    from oto_mcp import org_store
    assert not org_store.get_org_default_connectors(org)
    for s in subs:
        for c in CONNECTEURS:
            assert _ligne(s, org, c) is None, (s, c)


# ── le refus nommé, au geste, et rien n'est écrit ─────────────────────────────────

def test_un_nom_inconnu_est_refuse_et_le_refus_le_nomme(live, monkeypatch):
    admin, m = "k3-inc-admin", "k3-inc-m"
    org = _org("Kit3 inconnu", admin, m)
    code, corps = _kit(monkeypatch, admin, org, ["hunter", "pas-un-connecteur"])
    assert code == 404 and corps["error"] == "unknown_connector"
    assert "`pas-un-connecteur` is unknown to the connector registry" in corps["detail"]
    assert "nothing was written" in corps["detail"]
    assert corps["details"] == {"refused": [{"connector": "pas-un-connecteur",
                                             "reason": "unknown"}]}
    _rien_n_est_ecrit(org, admin, m)        # hunter, valide, n'est PAS passé non plus


def test_un_connecteur_coupe_par_l_org_est_refuse_et_dit_quoi_faire(live, monkeypatch):
    admin, m = "k3-org-admin", "k3-org-m"
    org = _org("Kit3 coupe org", admin, m)
    _coupe(monkeypatch, admin, org, "kaspr", False)
    code, corps = _ajout(monkeypatch, admin, org, "kaspr")
    assert code == 409 and corps["error"] == "org_disabled"
    assert "make it available first" in corps["detail"]
    code, corps = _kit(monkeypatch, admin, org, ["kaspr"])
    assert code == 409 and corps["error"] == "org_disabled"
    _rien_n_est_ecrit(org, admin, m)


def test_un_connecteur_coupe_par_la_plateforme_est_refuse_comme_tel(live, monkeypatch):
    from oto_mcp.connectors import activation
    admin = "k3-plat-admin"
    org = _org("Kit3 coupe plateforme", admin)
    activation.set_activation("osm", False)                 # master plateforme OFF
    try:
        code, corps = _kit(monkeypatch, admin, org, ["osm"])
    finally:
        activation.set_activation("osm", True)
    assert code == 409 and corps["error"] == "platform_disabled"
    assert "is cut by the platform" in corps["detail"]
    _rien_n_est_ecrit(org, admin)


def test_la_face_mcp_refuse_de_meme(live):
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    from oto_mcp.capabilities.connectors import console
    admin = "k3-mcp-admin"
    org = _org("Kit3 mcp", admin)
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(console._connector(
            ResolvedCtx(sub=admin, org_id=org),
            console.ConnectorInput(op="recommend", org_id=org, connectors=["inexistant"])))
    assert e.value.code == "unknown_connector" and "`inexistant`" in e.value.message
    _rien_n_est_ecrit(org, admin)


# ── coupé APRÈS sa mise au kit : il reste, s'installe masqué, revient seul ────────

def test_coupe_apres_coup_reste_au_kit_s_installe_masque_et_revient_seul(live, monkeypatch):
    from oto_mcp import org_store
    admin, ancien, arrivant = "k3-apres-admin", "k3-apres-ancien", "k3-apres-arrivant"
    org = _org("Kit3 apres", admin, ancien)
    code, corps = _kit(monkeypatch, admin, org, ["folk"])
    assert code == 200 and _vus_par_l_agent(ancien, org) == {"folk"}
    # L'org coupe folk : la réponse dit qu'il est au kit, et ce que ça fait.
    corps = _coupe(monkeypatch, admin, org, "folk", False)
    assert corps["in_kit"] is True and "cutting does not remove it from there" in corps["kit_note"]
    assert _vus_par_l_agent(ancien, org) == set()          # masqué chez l'ancien…
    assert _ligne(ancien, org, "folk") == {"state": "active", "origin": "kit"}   # … installé
    # Un membre arrive PENDANT la coupure : son semis installe le kit ENTIER.
    org_store.add_org_member(org, arrivant, "org_member")
    assert _vus_par_l_agent(arrivant, org) == set()
    assert _ligne(arrivant, org, "folk") == {"state": "active", "origin": "kit"}
    # Un geste du kit liste le connecteur coupé, et le dit.
    code, corps = _kit(monkeypatch, admin, org, ["folk", "osm"])
    assert code == 200 and corps["cut"] == ["folk"] and "comes back on its own" in corps["cut_note"]
    # Réouverture : sans AUCUN geste de rattrapage, les deux le voient.
    corps = _coupe(monkeypatch, admin, org, "folk", True)
    assert corps["in_kit"] is True and "come back" in corps["kit_note"]
    assert "folk" in _vus_par_l_agent(ancien, org)
    assert "folk" in _vus_par_l_agent(arrivant, org)


def test_la_coupure_d_un_connecteur_hors_kit_ne_dit_rien_du_kit(live, monkeypatch):
    admin = "k3-hors-admin"
    org = _org("Kit3 hors kit", admin)
    corps = _coupe(monkeypatch, admin, org, "hunter", False)
    assert "in_kit" not in corps and "kit_note" not in corps
