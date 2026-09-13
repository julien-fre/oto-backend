"""Coûts et Consommation SANS base — le tarif, l'unité de budget, la capacité servie.

Un compteur de Coûts faux ressemble exactement à un compteur juste : il rend un
nombre. Ces bancs portent sur les façons dont il ment sans lever — un zéro qui se lit
« gratuit », un cache tarifé comme l'entrée, un prix dérivé que personne n'a sourcé,
un prix provisoire présenté comme vérifié, un majorant publié comme un montant.
"""
from __future__ import annotations

import datetime

import pytest

from oto_mcp import runner_prix as P

ANTHROPIC_VIDE = {"usage_input": 0, "usage_output": 0, "usage_cache_read": 0,
                  "usage_cache_write": 0}


def _postes(**kw):
    return {**ANTHROPIC_VIDE, **kw}


# ── le tarif ──────────────────────────────────────────────────────────────────

def test_un_million_de_jetons_d_entree_opus_vaut_cinq_dollars_au_prix_verifie():
    tarif = P.tarifer("anthropic", "claude-opus-5", _postes(usage_input=10**6))
    assert tarif == P.Tarif(5 * 10**9, P.BAREME_COURANT, None, False)


def test_le_cache_anthropic_n_est_PAS_tarife_comme_l_entree():
    lu = P.tarifer("anthropic", "claude-opus-5", _postes(usage_cache_read=10**6)).nano_usd
    ecrit = P.tarifer("anthropic", "claude-opus-5", _postes(usage_cache_write=10**6)).nano_usd
    assert (lu, ecrit) == (5 * 10**8, 625 * 10**7), "lire 0,1×, écrire 1,25×"


def test_un_mistral_au_prix_PROVISOIRE_est_tarife_et_le_dit():
    """Prix provisoire ≠ prix absent : le montant existe, et il porte son marqueur."""
    tarif = P.tarifer("mistral", "mistral-large-2512",
                      {"usage_input": 10**6, "usage_output": 10**6,
                       "usage_cache_read": 0, "usage_cache_write": None})
    assert tarif == P.Tarif(500 * 10**6 + 1_500 * 10**6, P.BAREME_COURANT, None, True)


def test_le_cache_mistral_LU_sans_prix_source_reste_ABSENT_et_non_provisoire():
    tarif = P.tarifer("mistral", "mistral-large-2512",
                      {"usage_input": 10, "usage_output": 10, "usage_cache_read": 1000})
    assert tarif == P.Tarif(None, None, P.PRIX_ABSENT, False)


@pytest.mark.parametrize("famille,postes", [
    ("anthropic", _postes(usage_cache_write=None)),
    ("mistral", {"usage_input": None, "usage_output": 5, "usage_cache_read": 0}),
])
def test_un_poste_requis_inconnu_rend_sa_raison_et_pas_zero(famille, postes):
    modele = "claude-opus-5" if famille == "anthropic" else "mistral-large-2512"
    assert P.tarifer(famille, modele, postes) == P.Tarif(None, None, P.POSTE_INCONNU)


def test_l_entree_TOTALE_ne_se_tarife_jamais():
    """Le majorant ne remplace pas le non-caché inconnu."""
    tarif = P.tarifer("mistral", "mistral-large-2512",
                      {"usage_input": None, "usage_input_total": 10**6,
                       "usage_output": 1, "usage_cache_read": None})
    assert tarif.nano_usd is None and tarif.raison == P.POSTE_INCONNU


@pytest.mark.parametrize("famille,modele,raison", [
    (None, "claude-opus-5", P.FAMILLE_INCONNUE),
    ("scaleway", "claude-opus-5", P.FAMILLE_INCONNUE),
    ("anthropic", None, P.MODELE_ABSENT),
    ("anthropic", "claude-opus-5-20260101", P.MODELE_NON_TARIFE),
])
def test_chaque_montant_absent_dit_pourquoi(famille, modele, raison):
    assert P.tarifer(famille, modele, _postes(usage_input=1)).raison == raison


def test_avant_le_premier_bareme_aucun_montant_n_est_invente():
    tarif = P.tarifer("anthropic", "claude-opus-5", _postes(usage_input=1),
                      le=datetime.date(2020, 1, 1))
    assert tarif == P.Tarif(None, None, P.AUCUN_BAREME)


def test_les_raisons_d_ABSENCE_ne_contiennent_pas_le_motif_du_provisoire():
    assert set(P.RAISONS) == {P.FAMILLE_INCONNUE, P.POSTE_INCONNU, P.MODELE_ABSENT,
                              P.MODELE_NON_TARIFE, P.PRIX_ABSENT, P.AUCUN_BAREME}
    assert P.PRIX_NON_VERIFIE not in P.RAISONS


# ── l'unité de budget U ───────────────────────────────────────────────────────

def test_U_anthropic_compte_le_cache_ECRIT_et_jamais_le_cache_lu():
    assert P.unite_budget("anthropic", {"usage_input": 10, "usage_output": 5,
                                        "usage_cache_write": 3, "usage_cache_read": 1000}) == 18


def test_U_mistral_ignore_l_ecriture_de_cache():
    assert P.unite_budget("mistral", {"usage_input": 10, "usage_output": 5,
                                      "usage_cache_write": None, "usage_cache_read": 7}) == 15


@pytest.mark.parametrize("famille,postes", [
    ("anthropic", {"usage_input": 1, "usage_output": 1, "usage_cache_write": None}),
    ("mistral", {"usage_input": None, "usage_output": 1}),
    ("scaleway", {"usage_input": 1, "usage_output": 1}),
])
def test_U_est_NULL_des_qu_un_poste_manque_ou_que_la_famille_n_est_pas_nommee(famille, postes):
    assert P.unite_budget(famille, postes) is None


def test_U_en_SQL_suit_la_meme_table_que_U_en_Python():
    sql = P.unite_budget_sql()
    branches = sql.split("ELSE", 1)[1]
    for famille, compose in P._BUDGET.items():
        assert f"WHEN '{famille}' THEN " + " + ".join(compose) in branches
    assert "usage_cache_read" not in branches, "le cache lu n'entre jamais dans U"
    assert all(f"{p} = 0" in sql.split("ELSE", 1)[0] for p in P.POSTES_RECUS)


# ── le zéro attesté (contrat §3) ──────────────────────────────────────────────

ZEROS = dict.fromkeys(P.POSTES_RECUS, 0)


@pytest.mark.parametrize("famille,modele", [(None, None), ("scaleway", None),
                                            ("anthropic", "modele-inedit")])
def test_un_ZERO_ATTESTE_vaut_zero_quelle_que_soit_la_famille(famille, modele):
    """Zéro jeton coûte zéro dans tout barème : ni `unpriced`, ni famille inventée."""
    assert P.tarifer(famille, modele, ZEROS, atteste=True) == P.Tarif(
        0, P.BAREME_COURANT, None, False)
    assert P.unite_budget(famille, ZEROS, atteste=True) == 0


def test_un_zero_NON_atteste_ne_vaut_pas_zero():
    assert P.tarifer(None, None, ZEROS).raison == P.FAMILLE_INCONNUE
    assert P.unite_budget(None, ZEROS) is None


@pytest.mark.parametrize("poste", P.POSTES_RECUS)
def test_un_seul_poste_non_nul_ou_inconnu_sort_du_zero_atteste(poste):
    for valeur in (1, None):
        postes = {**ZEROS, poste: valeur}
        assert P.tarifer(None, None, postes, atteste=True).raison == P.FAMILLE_INCONNUE
        assert P.unite_budget(None, postes, atteste=True) is None


# ── la capacité servie ────────────────────────────────────────────────────────

def _cap():
    from oto_mcp.capabilities.registry import CAPABILITIES
    return next(c for c in CAPABILITIES if c.key == "runner.cost")


def test_la_capacite_est_servie_sous_oto_cost_aux_seuls_admins_d_org():
    from oto_mcp.capabilities import _authz
    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp.capabilities.runner_cost import CostOut
    cap = _cap()
    assert (cap.mcp, cap.authz, cap.Output) == ("oto_cost", _authz.ORG_ADMIN, CostOut)
    assert [(b.verb, b.path) for b in cap.rest_bindings()] == [("POST", "/api/me/runner/cost")]
    assert not [c.key for c in CAPABILITIES if c.mcp == "oto_cout"]


def test_la_description_distingue_Consommation_et_Couts_et_dit_qui_lit():
    description = _cap().description
    for mot in ("Consommation", "Coûts", "administrateurs", "price_unverified"):
        assert mot in description


@pytest.mark.parametrize("corps,code", [
    ({"op": "run"}, "missing_fields"), ({"op": "agent"}, "missing_fields"),
    ({"op": "fleet"}, "missing_fields"), ({"op": "run", "run_id": ""}, "missing_fields"),
    ({"op": "org", "detail": True}, "detail_requires_run"),
])
def test_un_regroupement_mal_designe_est_un_refus_nomme_avant_toute_lecture(
        monkeypatch, corps, code):
    from oto_mcp import db
    from oto_mcp.capabilities import runner_cost
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

    def interdit(*a, **k):
        raise AssertionError("aucune lecture avant le refus")

    monkeypatch.setattr(db, "cout_des_tentatives", interdit)
    with pytest.raises(AuthzDenied) as refus:
        runner_cost._cost(ResolvedCtx(sub="usr_banc", org_id=1),
                          runner_cost.CostInput(**corps))
    assert (refus.value.status, refus.value.code) == (400, code)
