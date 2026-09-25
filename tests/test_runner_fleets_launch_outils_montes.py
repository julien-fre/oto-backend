"""`op=launch` refuse, en les nommant, les outils déclarés qu'une org ne monte pas
(incident du 22/09/2026 : une bascule d'org maison a fait perdre tous ses
connecteurs à un compte pendant que ses flottes écrivaient, sans qu'aucune ne
le signale au moment où ça comptait — 80 fiches écrites, une partie sans
registre ni recherche web).
"""
from __future__ import annotations

import types

import pytest

from oto_mcp.capabilities import _outils_manquants
from oto_mcp.capabilities import runner_fleets as RF
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


# ── le calcul lui-même, isolé de `_fleets` ────────────────────────────────────

def _connecteur(nom):
    return types.SimpleNamespace(name=nom)


def test_un_outil_spine_nest_jamais_nomme(monkeypatch):
    """`connector_for_namespace` rend None pour `data_*`/`run_*`/`oto_*` — rien à
    monter, rien à refuser."""
    monkeypatch.setattr(_outils_manquants.providers, "connector_for_namespace",
                        lambda ns: None)
    assert _outils_manquants.manquants(2, "alexis", ["data_write", "run_start"]) == []


def test_un_connecteur_expose_et_selectionne_actif_nest_pas_manquant(monkeypatch):
    monkeypatch.setattr(_outils_manquants.providers, "connector_for_namespace",
                        lambda ns: _connecteur("fr"))
    monkeypatch.setattr(_outils_manquants.connector_activation, "exposed_connectors",
                        lambda org: {"fr"})
    monkeypatch.setattr(_outils_manquants.connector_selection, "list_selection",
                        lambda sub, org: {"fr": "active"})
    assert _outils_manquants.manquants(2, "alexis", ["fr_directors"]) == []


def test_un_connecteur_non_expose_est_nomme(monkeypatch):
    monkeypatch.setattr(_outils_manquants.providers, "connector_for_namespace",
                        lambda ns: _connecteur("fr"))
    monkeypatch.setattr(_outils_manquants.connector_activation, "exposed_connectors",
                        lambda org: set())
    monkeypatch.setattr(_outils_manquants.connector_selection, "list_selection",
                        lambda sub, org: {"fr": "active"})
    assert _outils_manquants.manquants(226, "alexis", ["fr_directors"]) == ["fr_directors"]


def test_un_connecteur_non_selectionne_est_nomme(monkeypatch):
    """Exposé à l'org, mais jamais installé/en pause pour ce sub : c'est
    exactement l'état de l'org 226 le 22/09 — `list_selection` vide."""
    monkeypatch.setattr(_outils_manquants.providers, "connector_for_namespace",
                        lambda ns: _connecteur("serper"))
    monkeypatch.setattr(_outils_manquants.connector_activation, "exposed_connectors",
                        lambda org: {"serper"})
    monkeypatch.setattr(_outils_manquants.connector_selection, "list_selection",
                        lambda sub, org: {})
    assert _outils_manquants.manquants(
        226, "alexis", ["serper_search", "serper_scrape"]) == ["serper_search", "serper_scrape"]


def test_une_liste_vide_ou_absente_ne_leve_rien(monkeypatch):
    monkeypatch.setattr(_outils_manquants.providers, "connector_for_namespace",
                        lambda ns: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    assert _outils_manquants.manquants(2, "alexis", None) == []
    assert _outils_manquants.manquants(2, "alexis", []) == []


# ── câblé dans `op=launch` ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cle_de_modele_non_exigee(monkeypatch):
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _compte_beta(monkeypatch):
    monkeypatch.setattr(RF.access, "has_option", lambda sub, option, *, org=None: True)


@pytest.fixture(autouse=True)
def _un_worker_par_defaut(monkeypatch):
    monkeypatch.setattr(RF.db, "runner_arme", lambda org: {
        "armed": True, "workers": 1, "last_seen": "2026-09-17 08:00:00", "families": ["anthropic"]})


def _ctx(sub="alexis", org_id=226):
    return ResolvedCtx(sub=sub, org_id=org_id)


def _appel(ctx, **kw):
    return RF._fleets(ctx, RF.FleetInput(**kw))


def _stub_admin_et_flotte(monkeypatch, tools, sub="pilote"):
    from oto_mcp import roles
    monkeypatch.setattr(roles, "is_org_admin", lambda *a, **k: True)
    monkeypatch.setattr(RF, "_run_courant", lambda: None)
    monkeypatch.setattr(RF.db, "get_fleet", lambda *a, **k: {"model": "claude-sonnet-5", 
        "id": 1, "status": "draft", "procedure": "p", "input": "x",
        "sub": sub, "tools": tools})


def test_launch_refuse_et_nomme_les_outils_absents_de_lorg_visee(monkeypatch):
    _stub_admin_et_flotte(monkeypatch, ["data_write", "fr_directors", "serper_scrape"])
    monkeypatch.setattr(RF._outils_manquants, "manquants",
                        lambda org, sub, tools: ["fr_directors", "serper_scrape"])
    arme = {}
    monkeypatch.setattr(RF.db, "armer", lambda *a, **k: arme.setdefault("oui", True))

    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="launch", fleet_id=1)
    assert e.value.code == "tools_not_mounted"
    assert "fr_directors" in e.value.message and "serper_scrape" in e.value.message
    assert "oui" not in arme, "un refus n'arme rien"


def test_launch_passe_quand_tous_les_outils_sont_montes(monkeypatch):
    _stub_admin_et_flotte(monkeypatch, ["data_write"])
    monkeypatch.setattr(RF._outils_manquants, "manquants", lambda org, sub, tools: [])
    monkeypatch.setattr(RF.db, "armer", lambda *a, **k: {
        "id": 1, "status": "armed", "max_rows": None, "max_tokens_per_row": None})

    rendu = _appel(_ctx(), op="launch", fleet_id=1)
    assert rendu["fleet"]["status"] == "armed"


def test_le_controle_porte_sur_le_sub_PROPRIETAIRE_de_la_flotte_pas_lappelant(monkeypatch):
    """La sélection de connecteurs est per-membre : c'est le `sub` qui a créé (et
    qui fera tourner) la flotte qui compte, pas forcément l'admin qui lance."""
    _stub_admin_et_flotte(monkeypatch, ["fr_directors"], sub="pilote-de-flotte")
    vu = {}
    monkeypatch.setattr(RF._outils_manquants, "manquants",
                        lambda org, sub, tools: vu.update(sub=sub, org=org) or [])
    monkeypatch.setattr(RF.db, "armer", lambda *a, **k: {
        "id": 1, "status": "armed", "max_rows": None, "max_tokens_per_row": None})

    _appel(_ctx(sub="alexis", org_id=226), op="launch", fleet_id=1)
    assert vu == {"sub": "pilote-de-flotte", "org": 226}
