"""Les modèles OUVERTS servis par OpenRouter (famille `openrouter`).

Ce que ces bancs tiennent :

1. **le catalogue** porte les modèles dans la famille `openrouter`, après les familles
   d'avant (le défaut proposé ne bouge pas), chacun avec son effort, son plafond de
   complétion et ses hôtes épinglés — et le travail les emporte (`charge`) ;
2. **le dépôt de clé** `openrouter` est un porteur de clé (aucun outil), à clé de
   plateforme FERMÉE : c'est ce qui permet à la réservation de le remettre au worker ;
3. **un tour sur la clé partagée** se réserve et se règle sous le connecteur
   `openrouter` (`openrouter.messages:<model>`), jamais sous `anthropic` : sans ses lignes,
   il est `credits_unpriced`, comme un modèle Anthropic non tarifé ;
4. **les modes par fonction** acceptent `openrouter.messages:*`, surface comprise.
"""
from __future__ import annotations

import pytest

from oto_mcp import providers, runner_models
from oto_mcp.access import credits, credits_modes as modes, credits_tours as tours
from oto_mcp.db import credits as db_credits

FLASH = "deepseek/deepseek-v4.1-flash-1017"


@pytest.fixture(autouse=True)
def _propre(monkeypatch):
    for v in ("OTO_CREDITS_MODE", "OTO_CREDITS_MODE_FUNCTIONS", "OTO_CREDITS_JOB_FLOOR"):
        monkeypatch.delenv(v, raising=False)


# ── 1. le catalogue ─────────────────────────────────────────────────────────

def test_les_modeles_ouverts_sont_de_la_famille_openrouter_et_apres_les_autres():
    ouverts = [m for m in runner_models.MODELES if m.family == "openrouter"]
    assert [m.id for m in ouverts] == [FLASH, "deepseek/deepseek-v4-pro", "z-ai/glm-5.3",
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


# ── 3. réserver et régler sous le connecteur `openrouter` ───────────────────

@pytest.fixture
def bareme(monkeypatch):
    vu = {"reserve": [], "lignes": [
        {"connector": "openrouter", "function": f"messages:{FLASH}", "unit": u,
         "budget": 5, "per_n": 346} for u in tours.UNITES_DU_MODELE]}  # forme seule
    monkeypatch.setattr(db_credits, "version_du_bareme", lambda: 1)
    monkeypatch.setattr(db_credits, "lignes_du_bareme", lambda v: vu["lignes"])
    monkeypatch.setattr(db_credits, "orgs_en_mode_personne", lambda: set())

    def reserver(**k):
        vu["reserve"].append(k)
        return db_credits.Reservation(posee=True, deja=False, insuffisant=False,
                                      disponible=10**9)
    monkeypatch.setattr(db_credits, "reserver", reserver)
    credits.oublier()
    yield vu
    credits.oublier()


def test_le_connecteur_d_un_modele_est_sa_famille_facturee():
    assert tours.connecteur_du_modele(FLASH) == "openrouter"
    assert tours.connecteur_du_modele("claude-sonnet-5") == "anthropic"
    assert tours.connecteur_du_modele("mistral-large-2512") == "anthropic"
    assert tours.connecteur_du_modele(None) == "anthropic"
    assert tours.fonction_du_modele(FLASH) == f"messages:{FLASH}"


def test_un_tour_openrouter_se_reserve_sous_son_connecteur(monkeypatch, bareme):
    monkeypatch.setenv("OTO_CREDITS_MODE", "report")
    r = tours.reserver_tour(tours.cle_du_tour(7, 1, 0), "s", 42, 16000, FLASH)
    assert r["held"] is True
    (k,) = bareme["reserve"]
    assert (k["connector"], k["function"], k["unit"]) == (
        "openrouter", f"messages:{FLASH}", "output_tokens")


def test_un_tour_openrouter_sans_ses_lignes_est_non_tarife(monkeypatch, bareme):
    """Des lignes `anthropic` au même nom ne le tarifent pas : le connecteur compte."""
    bareme["lignes"] = [{**l, "connector": "anthropic"} for l in bareme["lignes"]]
    monkeypatch.setenv("OTO_CREDITS_MODE", "enforce")
    with pytest.raises(tours.TourRefuse) as e:
        tours.reserver_tour(tours.cle_du_tour(7, 1, 0), "s", 42, 16000, FLASH)
    assert e.value.code == credits.SANS_BAREME


def test_un_tour_openrouter_se_regle_sous_son_connecteur(monkeypatch, bareme):
    monkeypatch.setenv("OTO_CREDITS_MODE", "report")
    monkeypatch.setattr(db_credits, "reservation", lambda uid: None)
    regle = {}
    monkeypatch.setattr(db_credits, "regler",
                        lambda uid, **k: regle.update(k) or {"ok": True})
    jetons = {"input_tokens": 3000, "output_tokens": 800,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 40000}
    r = tours.regler_tour(tours.cle_du_tour(7, 1, 0), "s", 42, jetons, {}, FLASH)
    assert r["settled"] is True
    (k,) = bareme["reserve"]  # réservé à l'instant, forcé : il a été consommé
    assert k["connector"] == "openrouter" and k["forcer"] is True
    # 3 000/346 → 9 paquets, 800 → 3, 40 000 → 116, à 5 unités le paquet
    assert r["amount"] == 5 * (9 + 3 + 0 + 116)


# ── 4. les modes par fonction ───────────────────────────────────────────────

def test_un_mode_se_pose_sur_les_tours_openrouter(monkeypatch):
    monkeypatch.setenv("OTO_CREDITS_MODE", "report")
    monkeypatch.setenv("OTO_CREDITS_MODE_FUNCTIONS", "runner/openrouter.messages:*=off")
    modes.verifier()
    assert tours.mode_du_modele(FLASH, "runner") == "off"
    assert tours.mode_du_modele("claude-sonnet-5", "runner") == "report"
