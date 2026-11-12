"""Les modèles OUVERTS servis par OpenRouter (famille `openrouter`).

Ce que ces bancs tiennent :

1. **le catalogue** porte les modèles dans la famille `openrouter`, après les familles
   d'avant (le défaut proposé ne bouge pas), chacun avec son effort, son plafond de
   complétion et ses hôtes épinglés — et le travail les emporte (`charge`) ;
2. **le dépôt de clé** `openrouter` est un porteur de clé (aucun outil), à clé de
   plateforme FERMÉE : c'est ce qui permet à la réservation de le remettre au worker ;
3. **un tour sur la clé partagée** n'est ni réservé ni réglé au budget d'unités : il est
   JOURNALISÉ au coût que le fournisseur a rendu (`tours_au_cout`), une ligne par tour,
   sans aucun tarif côté oto ; sans coût déclaré il est refusé, rejoué il n'est écrit
   qu'une fois.
"""
from __future__ import annotations

import pytest

from oto_mcp import providers, runner_models, tours_au_cout
from oto_mcp.access import credits_tours as tours
from oto_mcp.capabilities import runner_jobs
from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.db import usage as db_usage

FLASH = "deepseek/deepseek-v4.1-flash-1017"


@pytest.fixture(autouse=True)
def _propre(monkeypatch):
    for v in ("OTO_CREDITS_MODE", "OTO_CREDITS_MODE_FUNCTIONS", "OTO_CREDITS_JOB_FLOOR"):
        monkeypatch.delenv(v, raising=False)


# ── 1. le catalogue ─────────────────────────────────────────────────────────

def test_les_modeles_ouverts_sont_de_la_famille_openrouter_et_apres_les_autres():
    ouverts = [m for m in runner_models.MODELES if m.family == "openrouter"]
    assert [m.id for m in ouverts] == [FLASH, "deepseek/deepseek-v4-pro",
                                       "minimax/minimax-m3"]
    ids = [m.id for m in runner_models.MODELES]
    assert ids.index(FLASH) > ids.index("mistral-small-2603")
    for m in ouverts:
        assert m.providers and m.max_output_tokens and m.effort, m.id
        # jamais fp4 ni un alias `~` : la liste borne le prix réglé et la quantification
        assert not any("fp4" in p or p.startswith("~") for p in m.providers), m.id


def test_le_defaut_propose_ne_bouge_pas_quand_openrouter_est_servi():
    cat = runner_models.catalogue({"anthropic", "openrouter"})
    assert [m["id"] for m in cat if m["default"]] == ["claude-sonnet-5"]
    seul = runner_models.catalogue({"openrouter"})
    assert [m["id"] for m in seul if m["default"]] == [FLASH]


def test_le_travail_emporte_les_hotes_epingles():
    c = runner_models.charge(FLASH)
    assert c["model_family"] == "openrouter" and c["effort"] == "medium"
    assert c["max_output_tokens"] == 16000
    assert c["providers"] == ["stepfun/fp8", "streamlake/fp8", "deepinfra/fp8"]
    assert "providers" not in runner_models.charge("claude-sonnet-5")


# ── 2. le dépôt de clé ──────────────────────────────────────────────────────

def test_openrouter_est_un_porteur_de_cle_a_cle_de_plateforme_fermee():
    from oto_mcp.capabilities import _cle_exigee
    c = providers.connector_for_provider("openrouter")
    assert c.kind == "credential" and not c.namespaces
    assert {"byo_org", "platform"} <= set(c.auth_modes) and c.platform_key_open is False
    assert "openrouter" in _cle_exigee.fournisseurs_de_modele()


# ── 3. journalisé au coût, jamais tarifé ici ─────────────────────────────────

def test_seule_la_famille_openrouter_est_journalisee_au_cout():
    assert tours_au_cout.au_cout(FLASH)
    assert not tours_au_cout.au_cout("claude-sonnet-5")
    assert not tours_au_cout.au_cout("mistral-large-2512")
    assert not tours_au_cout.au_cout(None)


def test_le_cout_en_microdollars_arrondi_au_dessus():
    assert tours_au_cout.microdollars(0.00113355722) == 1134  # un tour V4 Pro, 12/11
    assert tours_au_cout.microdollars(3 * 1e-05) == 30         # pas 31
    assert tours_au_cout.microdollars(0) == 0
    for brut in (None, True, "0.1", -1e-6, float("nan"), float("inf")):
        assert tours_au_cout.cout_declare(brut) is None, brut
    assert tours_au_cout.cout_declare(0) == 0.0


class _Ctx:
    sub = "worker-1"


@pytest.fixture
def tour_partage(monkeypatch):
    vu = {"lignes": [], "existe": False}
    monkeypatch.setattr(runner_jobs.db, "tentative_en_cours", lambda job, sub: {
        "org_id": 42, "sub": "u-1", "attempts": 2, "run_id": "run-9", "model": FLASH,
        "cle_partagee": {"tentative": 2, "mode": "platform", "depot": "openrouter"}})
    monkeypatch.setattr(db_usage, "releve_existe", lambda kind, tool, uid: vu["existe"])
    monkeypatch.setattr(db_usage, "insert_tool_call", lambda row: vu["lignes"].append(row))

    def interdit(*a, **k):
        raise AssertionError("un tour au coût ne touche pas le budget d'unités")
    monkeypatch.setattr(tours, "reserver_tour", interdit)
    monkeypatch.setattr(tours, "regler_tour", interdit)
    return vu


_USAGE = {"model": FLASH, "usage_input": 152, "usage_output": 81,
          "usage_cache_read": 6912, "usage_cost_usd": 6.44952e-05}


def test_un_tour_au_cout_ne_se_reserve_pas(tour_partage):
    assert runner_jobs._reserver_un_tour(_Ctx(), 7, 0, 16000) == {"ok": True, "held": False}


def test_un_tour_au_cout_est_journalise_a_son_cout(tour_partage):
    r = runner_jobs._regler_un_tour(_Ctx(), 7, 3, dict(_USAGE))
    assert r == {"ok": True, "metered": True, "microdollars": 65, "duplicate": False}
    (ligne,) = tour_partage["lignes"]
    assert ligne["kind"] == "settlement" and ligne["tool"] == "openrouter"
    assert ligne["quantity"] == 65 and ligne["key_mode"] == "platform"
    assert (ligne["org_id"], ligne["sub"], ligne["run_id"]) == (42, "u-1", "run-9")
    assert ligne["call_uid"] == tours.cle_du_tour(7, 2, 3)
    assert {k: ligne["args"].get(k) for k in ("input_tokens", "output_tokens",
                                               "cache_read_input_tokens", "turn")} == {
        "input_tokens": 152, "output_tokens": 81, "cache_read_input_tokens": 6912, "turn": 3}
    assert ligne["args"]["cost_usd"] == 6.44952e-05


def test_un_tour_rejoue_n_est_ecrit_qu_une_fois(tour_partage):
    tour_partage["existe"] = True
    r = runner_jobs._regler_un_tour(_Ctx(), 7, 3, dict(_USAGE))
    assert r["duplicate"] is True and tour_partage["lignes"] == []


def test_un_tour_sans_cout_declare_arrete_le_travail(tour_partage):
    with pytest.raises(AuthzDenied) as e:
        runner_jobs._regler_un_tour(_Ctx(), 7, 3, {**_USAGE, "usage_cost_usd": None})
    assert e.value.status == 422 and tour_partage["lignes"] == []
