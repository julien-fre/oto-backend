"""oto#166, barreau 2 — UNE fonction d'application pour les gestes du kit d'organisation.

ADR 0050 §E4/§E8 et décision Q4 du 11/09/2026. Avant ce lot, le kit n'était lu
qu'au premier passage d'un membre : le modifier ne changeait rien pour les membres
déjà entrés (mesuré en production : un kit posé le 10/09, deux membres arrivés avant,
jamais reçu). Désormais poser le kit, y ajouter et en retirer passent par
`connectors.kit.appliquer`, qui écrit le kit ET les boîtes des membres dans une
transaction, n'applique que la DIFFÉRENCE (Q4), ne passe jamais par-dessus le membre
(E6), et rend une réponse chiffrée.

Comportement SERVI : la vraie chaîne REST (`_datastore_rest.call`), la face MCP par
la console `oto_connector`, et ce que voit l'agent d'un membre par le vrai
`compute_hidden_tools` — sur un vrai PostgreSQL bootté par `init_db()`.
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
            return [type("T", (), {"name": f"{c}_x"})() for c in CONNECTEURS] + [
                type("T", (), {"name": "oto_whoami"})()]

    fastmcp = _FastMCP()


def _vus_par_l_agent(sub: str, org: int) -> set[str]:
    """Les connecteurs dont l'agent de `sub` voit les outils — le calcul du handshake."""
    from oto_mcp import session_visibility as SV
    caches = asyncio.run(SV.compute_hidden_tools(_Ctx(), sub, org=org))
    return {c for c in CONNECTEURS if f"{c}_x" not in caches}


def _geste(monkeypatch, cle: str, admin: str, org: int, **kw) -> dict:
    stub_authz(monkeypatch, org_id=org)
    if cle == "connectors.recommend":
        code, corps = call(cle, sub=admin, path_params={"id": str(org)},
                           body={"connectors": kw["connectors"]})
    else:
        code, corps = call(cle, sub=admin, path_params={"id": str(org), "name": kw["name"]})
    assert code == 200, corps
    return corps


def _membre(monkeypatch, cle: str, sub: str, org: int, connecteur: str) -> None:
    stub_authz(monkeypatch, org_id=org)
    code, corps = call(cle, sub=sub, path_params={"name": connecteur})
    assert code == 200, corps


def _ch(corps: dict, connecteur: str) -> dict:
    (ch,) = [c for c in corps["changes"] if c["connector"] == connecteur]
    return ch


# ── E4 : un ajout au kit atteint les membres DÉJÀ entrés ─────────────────────────

def test_poser_le_kit_installe_chez_les_membres_deja_entres(live, monkeypatch):
    """Le cas mesuré en production : deux membres entrés AVANT la pose du kit."""
    admin, m1, m2 = "k2-pose-admin", "k2-pose-m1", "k2-pose-m2"
    org = _org("Kit2 pose", admin, m1, m2)
    for m in (m1, m2):
        assert _vus_par_l_agent(m, org) == set()      # semés, kit vide : rien
    corps = _geste(monkeypatch, "connectors.recommend", admin, org, connectors=["hunter"])
    ch = _ch(corps, "hunter")
    assert corps["members"] == 3 and ch["change"] == "added" and ch["installed"] == 3
    assert corps["kit"] == corps["recommended"] == ["hunter"]
    for m in (m1, m2):
        assert _ligne(m, org, "hunter") == {"state": "active", "origin": "kit"}
        # Ce que reçoit son agent à la PROCHAINE conversation.
        assert _vus_par_l_agent(m, org) == {"hunter"}
    assert "PROCHAINE conversation" in corps["note"]


def test_le_geste_du_membre_prime(live, monkeypatch):
    admin, a, b, c, d = (f"k2-prime-{x}" for x in ("admin", "pause", "retire", "actif", "rien"))
    org = _org("Kit2 prime", admin, a, b, c, d)
    _membre(monkeypatch, "connectors.pause", a, org, "hunter")
    _membre(monkeypatch, "connectors.select", b, org, "hunter")
    _membre(monkeypatch, "connectors.unselect", b, org, "hunter")
    _membre(monkeypatch, "connectors.select", c, org, "hunter")
    corps = _geste(monkeypatch, "connectors.bulk_select", admin, org, name="hunter")
    ch = _ch(corps, "hunter")
    assert (ch["installed"], ch["already_active"], ch["paused"], ch["removed_by_member"]) \
        == (2, 1, 1, 1)
    assert (corps["activated"], corps["skipped"], corps["added_to_org_defaults"]) == (2, 3, True)
    assert _ligne(a, org, "hunter") == {"state": "paused", "origin": "membre"}
    assert _ligne(b, org, "hunter") is None
    assert _ligne(c, org, "hunter") == {"state": "active", "origin": "membre"}
    assert _ligne(d, org, "hunter") == {"state": "active", "origin": "kit"}


# ── Q4 : seule la DIFFÉRENCE s'applique ; ce qui était posé avant ne se rejoue pas ─

def test_seule_la_difference_du_kit_s_applique(live, monkeypatch):
    from oto_mcp import org_store
    admin, m = "k2-diff-admin", "k2-diff-m"
    org = _org("Kit2 diff", admin, m)
    assert _vus_par_l_agent(m, org) == set()
    org_store.set_org_default_connectors(org, ["hunter"])       # un kit posé AVANT la règle
    corps = _geste(monkeypatch, "connectors.recommend", admin, org, connectors=["hunter", "kaspr"])
    assert [c["connector"] for c in corps["changes"]] == ["kaspr"]
    assert corps["unchanged"] == ["hunter"]
    assert _ligne(m, org, "hunter") is None                     # pas rejoué
    assert _ligne(m, org, "kaspr") == {"state": "active", "origin": "kit"}


def test_ajouter_ce_qui_est_deja_au_kit_dit_pourquoi_et_comment(live, monkeypatch):
    """La condition posée à cette lecture de Q4 : l'admin ne doit pas croire que son
    clic a échoué — la réponse dit POURQUOI, et COMMENT faire s'il le veut vraiment ;
    et le « comment » doit marcher."""
    from oto_mcp import org_store
    admin, m = "k2-deja-admin", "k2-deja-m"
    org = _org("Kit2 deja", admin, m)
    assert _vus_par_l_agent(m, org) == set()
    org_store.set_org_default_connectors(org, ["folk"])
    corps = _geste(monkeypatch, "connectors.bulk_select", admin, org, name="folk")
    assert (corps["activated"], corps["added_to_org_defaults"]) == (0, False)
    assert corps["changes"] == [] and corps["unchanged"] == ["folk"]
    note = corps["unchanged_note"]
    assert "Déjà dans le kit" in note and "bien été reçu" in note
    assert "retire-le du kit, puis remets-le" in note
    assert "ne désinstalle rien que le kit n'ait posé" in note
    assert _ligne(m, org, "folk") is None
    # Le « comment » : retirer puis remettre l'installe chez le membre actuel.
    _geste(monkeypatch, "connectors.unset_default", admin, org, name="folk")
    corps = _geste(monkeypatch, "connectors.bulk_select", admin, org, name="folk")
    assert corps["activated"] == 2 and "unchanged_note" not in corps
    assert _ligne(m, org, "folk") == {"state": "active", "origin": "kit"}


# ── E5, décision Q1 : un retrait désinstalle là où le kit a posé, et le compte ──────

def test_retirer_du_kit_desinstalle_ce_que_le_kit_a_pose_et_le_compte(live, monkeypatch):
    admin, m1, m2 = "k2-ret-admin", "k2-ret-m1", "k2-ret-m2"
    org = _org("Kit2 retrait", admin, m1, m2)
    _membre(monkeypatch, "connectors.select", m2, org, "osm")          # m2 : le sien
    _geste(monkeypatch, "connectors.recommend", admin, org, connectors=["osm"])
    corps = _geste(monkeypatch, "connectors.unset_default", admin, org, name="osm")
    ch = _ch(corps, "osm")
    assert corps["removed"] is True and corps["kit"] == []
    assert ch["change"] == "removed" and ch["uninstalled"] == 2     # admin + m1 : le kit
    assert ch["kept"] == {"membre": 1}                                # m2 : le sien
    assert _ligne(m1, org, "osm") is None
    assert _ligne(m2, org, "osm") == {"state": "active", "origin": "membre"}
    # Retirer ce qui n'est pas au kit : rien ne change, et c'est dit.
    corps = _geste(monkeypatch, "connectors.unset_default", admin, org, name="osm")
    assert corps["removed"] is False and corps["unchanged"] == ["osm"]


# ── la face MCP passe par la même fonction ────────────────────────────────────────

def test_oto_connector_op_recommend_passe_par_la_meme_fonction(live):
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.connectors import console
    admin, m = "k2-mcp-admin", "k2-mcp-m"
    org = _org("Kit2 mcp", admin, m)
    out = asyncio.run(console._connector(
        ResolvedCtx(sub=admin, org_id=org),
        console.ConnectorInput(op="recommend", org_id=org, connectors=["kaspr"])))
    assert _ch(out, "kaspr")["installed"] == 2 and out["recommended"] == ["kaspr"]
    assert _ligne(m, org, "kaspr") == {"state": "active", "origin": "kit"}


# ── un membre PAS ENCORE VENU reçoit la ligne ; son semis la garde ────────────────

def test_un_membre_pas_encore_venu_recoit_la_ligne_et_son_semis_la_garde(live, monkeypatch):
    from oto_mcp.connectors import selection as sel
    admin, n = "k2-neuf-admin", "k2-neuf-n"
    org = _org("Kit2 neuf", admin, n)
    assert not sel.is_seeded(n, org)
    _geste(monkeypatch, "connectors.recommend", admin, org, connectors=["folk"])
    assert _ligne(n, org, "folk") == {"state": "active", "origin": "kit"}
    assert _vus_par_l_agent(n, org) == {"folk"}                      # premier passage
    assert sel.is_seeded(n, org)
    assert _ligne(n, org, "folk") == {"state": "active", "origin": "kit"}


# ── une transaction : un échec au milieu ne laisse ni kit ni boîte à moitié ───────

def test_le_geste_est_une_seule_transaction(live, monkeypatch):
    from oto_mcp import org_store
    from oto_mcp.connectors import kit, selection as sel
    admin, m = "k2-tx-admin", "k2-tx-m"
    org = _org("Kit2 tx", admin, m)
    vrai = sel.install_for_member
    appels = []

    def casse_au_second(conn, sub, *a):
        appels.append(sub)
        if len(appels) == 2:
            raise RuntimeError("panne au milieu du geste")
        return vrai(conn, sub, *a)
    monkeypatch.setattr(sel, "install_for_member", casse_au_second)
    with pytest.raises(RuntimeError):
        kit.appliquer(org, kit=["hunter"])
    assert len(appels) == 2
    assert not org_store.get_org_default_connectors(org)          # kit non écrit
    assert _ligne(appels[0], org, "hunter") is None                # premier membre annulé
