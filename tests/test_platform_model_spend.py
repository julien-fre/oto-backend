"""Ce qu'Anthropic a compté sur les clés de plateforme, contre ce que le budget d'unités
a réglé (`admin.model_spend`), EN JETONS — le contrôle qui ne fait pas confiance à la
mesure.

Aucun appel réel : le réseau est une doublure de `requests.get`, le coffre et la base
sont injectés (`rapprocher` est pur).
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from oto_mcp import anthropic_usage as AU
from oto_mcp.capabilities import platform_model_spend as PMS
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

JOUR = date(2026, 10, 18)
SECRET = "sk-ant-api03-R2Dxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-igAA"
CLES = [{"id": "apikey_plateforme", "name": "plateforme", "partial_key_hint": "sk-ant-api03-R2D...igAA"},
        {"id": "apikey_poste", "name": "poste de dev", "partial_key_hint": "sk-ant-api03-ZZZ...0000"}]


def _res(cle, model="claude-sonnet-5-5", entree=1_000_000, sortie=0, c5=0, c1=0, lu=0):
    return {"api_key_id": cle, "model": model, "uncached_input_tokens": entree,
            "output_tokens": sortie, "cache_read_input_tokens": lu,
            "cache_creation": {"ephemeral_5m_input_tokens": c5, "ephemeral_1h_input_tokens": c1}}


def _jetons(entree=0, sortie=0, ecriture=0, lu=0):
    return {"input_tokens": entree, "output_tokens": sortie,
            "cache_creation_input_tokens": ecriture, "cache_read_input_tokens": lu}


#: Les règlements d'un jour, par fonction du barème : `messages:<modèle>`.
_S = "messages:claude-sonnet-5-5"


def _rapprocher(usage, regle, plateforme=(("principale", SECRET),), cles=CLES):
    return PMS.rapprocher(JOUR, usage=usage, cles=cles, plateforme=list(plateforme),
                          regle=regle)


# ── les jetons d'un résultat du relevé ───────────────────────────────────────

def test_chaque_poste_tombe_sous_son_unite_les_deux_ecritures_ensemble():
    r = _res("k", entree=1, sortie=2, c5=3, c1=4, lu=10)
    assert AU.jetons(r) == _jetons(entree=1, sortie=2, ecriture=7, lu=10)


def test_un_modele_inconnu_se_compte_aussi_aucun_tarif():
    assert AU.jetons(_res("k", model="claude-inconnu")) == _jetons(entree=1_000_000)


@pytest.mark.parametrize("indice,attendu", [
    ("sk-ant-api03-R2D...igAA", True), ("sk-ant-api03-R2D...igAB", False),
    ("sk-ant-api03-XXX...igAA", False), (None, False), ("sans-points", False)])
def test_un_indice_designe_un_secret_par_sa_tete_et_sa_queue(indice, attendu):
    assert AU.indice_correspond(indice, SECRET) is attendu


# ── le rapprochement ─────────────────────────────────────────────────────────

def test_un_jour_entierement_regle_n_a_pas_d_ecart():
    out = _rapprocher([_res("apikey_plateforme", sortie=10)], {_S: _jetons(1_000_000, 10)})
    assert out["anthropic_tokens"] == out["settled_tokens"] == _jetons(1_000_000, 10)
    assert out["gap_tokens"] == _jetons()
    assert out["platform_keys"] == [{"label": "principale", "api_key_id": "apikey_plateforme",
                                     "name": "plateforme", "match": "matched",
                                     "anthropic_tokens": _jetons(1_000_000, 10)}]
    assert out["other_keys"] == [] and out["unmatched_platform_rows"] == []


def test_ce_qu_aucun_reglement_ne_porte_est_l_ecart_unite_par_unite():
    out = _rapprocher([_res("apikey_plateforme", lu=500)], {_S: _jetons(750_000)})
    assert out["gap_tokens"] == _jetons(entree=250_000, lu=500)


def test_un_reglement_au_dessus_d_anthropic_est_un_ecart_negatif():
    out = _rapprocher([_res("apikey_plateforme")], {_S: _jetons(1_200_000)})
    assert out["gap_tokens"]["input_tokens"] == -200_000


def test_les_jetons_d_une_AUTRE_cle_sont_rendus_a_part():
    """Le trou de la clé d'environnement : un worker qui tourne sur la sienne n'est
    réglé nulle part. `null` = la Console."""
    out = _rapprocher([_res("apikey_plateforme"), _res("apikey_poste", entree=500_000),
                       _res(None, entree=250_000)], {_S: _jetons(1_000_000)})
    assert out["gap_tokens"] == _jetons()
    assert out["other_keys"] == [
        {"api_key_id": "apikey_poste", "name": "poste de dev", "tokens": _jetons(500_000)},
        {"api_key_id": None, "name": None, "tokens": _jetons(250_000)}]


def test_une_ligne_sans_indice_ou_a_deux_indices_n_est_jamais_devinee():
    out = _rapprocher([_res("apikey_plateforme")], {},
                      plateforme=[("sans", "sk-ant-api03-QQQq-zzzz"), ("principale", SECRET)])
    assert out["unmatched_platform_rows"] == ["sans"]
    doubles = CLES + [{"id": "apikey_jumelle", "name": "j", "partial_key_hint": "sk-ant-api03-R2D...igAA"}]
    out = _rapprocher([_res("apikey_plateforme")], {}, cles=doubles)
    assert out["platform_keys"][0]["match"] == "ambiguous"
    assert out["anthropic_tokens"] == _jetons(), "une clé ambiguë n'est comptée sous aucune ligne"


def test_la_sortie_ne_porte_ni_secret_ni_indice():
    texte = repr(_rapprocher([_res("apikey_plateforme")], {}))
    assert SECRET not in texte and "R2D" not in texte


def test_la_sortie_tient_son_contrat():
    PMS.ModelSpend(**_rapprocher([_res("apikey_plateforme"), _res(None)], {_S: _jetons(5)}))


def test_le_rapprochement_se_fait_aussi_par_modele_sans_la_date():
    """Chaque modèle a ses lignes au barème : l'écart se lit par modèle, l'id daté
    d'Anthropic réduit à l'id sans date que règle le budget. Une autre clé n'y entre
    pas."""
    usage = [_res("apikey_plateforme", model="claude-sonnet-5-5-20261001"),
             _res("apikey_plateforme", model="claude-opus-5-5", entree=0, sortie=10),
             _res("apikey_poste", model="claude-haiku-5-5")]
    out = _rapprocher(usage, {_S: _jetons(1_000_000),
                              "messages:claude-opus-5-5": _jetons(sortie=4)})
    assert out["by_model"] == [
        {"model": "claude-opus-5-5", "anthropic_tokens": _jetons(sortie=10),
         "settled_tokens": _jetons(sortie=4), "gap_tokens": _jetons(sortie=6)},
        {"model": "claude-sonnet-5-5", "anthropic_tokens": _jetons(1_000_000),
         "settled_tokens": _jetons(1_000_000), "gap_tokens": _jetons()}]
    assert out["settled_tokens"] == _jetons(1_000_000, 4)
    assert out["gap_tokens"] == _jetons(sortie=6)


# ── l'Admin API ──────────────────────────────────────────────────────────────

class _Rep:
    def __init__(self, corps, status=200):
        self.corps, self.status_code = corps, status

    def json(self):
        return self.corps


def test_le_releve_se_lit_page_par_page(monkeypatch):
    vu = []

    def get(url, params=None, headers=None, timeout=None):
        vu.append((url, dict(params), headers["x-api-key"], headers["anthropic-version"]))
        if not any(k == "page" for k, _ in params):
            return _Rep({"data": [{"results": [{"model": "a"}]}], "has_more": True,
                         "next_page": "p2"})
        return _Rep({"data": [{"results": [{"model": "b"}]}], "has_more": False})
    monkeypatch.setattr(AU.requests, "get", get)
    assert [r["model"] for r in AU.usage_du_jour(JOUR, "sk-ant-admin-x")] == ["a", "b"]
    url, params, cle, version = vu[0]
    assert url.endswith("/v1/organizations/usage_report/messages") and cle == "sk-ant-admin-x"
    assert params["starting_at"] == "2026-10-18T00:00:00Z" and params["ending_at"] == "2026-10-19T00:00:00Z"
    assert params["bucket_width"] == "1d" and version == "2023-06-01"


def test_les_cles_se_lisent_page_par_page(monkeypatch):
    def get(url, params=None, headers=None, timeout=None):
        if not any(k == "after_id" for k, _ in params):
            return _Rep({"data": [{"id": "a"}], "has_more": True, "last_id": "a"})
        return _Rep({"data": [{"id": "b"}], "has_more": False, "last_id": "b"})
    monkeypatch.setattr(AU.requests, "get", get)
    assert [c["id"] for c in AU.cles_d_api("k")] == ["a", "b"]


def test_un_refus_de_l_admin_api_est_nomme_sans_recopier_l_url(monkeypatch):
    monkeypatch.setattr(AU.requests, "get", lambda *a, **k: _Rep({}, status=401))
    with pytest.raises(AU.AnthropicAdminError) as e:
        AU.usage_du_jour(JOUR, "k")
    assert e.value.status == 401 and "http" not in str(e.value)


# ── la capacité ──────────────────────────────────────────────────────────────

def _appel(**kw):
    return asyncio.run(PMS._model_spend(ResolvedCtx(sub="admin", org_id=None),
                                        PMS.ModelSpendInput(**kw)))


def test_sans_cle_d_admin_le_refus_est_nomme(monkeypatch):
    monkeypatch.delenv("OTO_ANTHROPIC_ADMIN_KEY", raising=False)
    with pytest.raises(AuthzDenied) as e:
        _appel()
    assert (e.value.status, e.value.code) == (409, "admin_key_not_configured")


def test_la_capacite_rapproche_le_jour_demande(monkeypatch):
    monkeypatch.setenv("OTO_ANTHROPIC_ADMIN_KEY", "sk-ant-admin-x")
    monkeypatch.setattr(AU, "usage_du_jour", lambda jour, key: [_res("apikey_plateforme")])
    monkeypatch.setattr(AU, "cles_d_api", lambda key: CLES)
    monkeypatch.setattr(PMS, "_cles_plateforme", lambda: [("principale", SECRET)])
    vu = {}
    monkeypatch.setattr(PMS.db_credits, "jetons_du_jour",
                        lambda c, f, mode, jour, unites: vu.update(q=(c, f, mode, jour))
                        or {_S: _jetons(1_000_000)})
    out = _appel(day=JOUR)
    assert out["gap_tokens"] == _jetons() and out["day"] == "2026-10-18"
    assert vu["q"] == ("anthropic", "messages:", "platform", JOUR), \
        "toutes les fonctions `messages:<modèle>`, au rang plateforme seul"


def test_une_admin_api_muette_est_un_refus_nomme(monkeypatch):
    monkeypatch.setenv("OTO_ANTHROPIC_ADMIN_KEY", "sk-ant-admin-x")

    def muette(jour, key):
        raise AU.AnthropicAdminError("Admin API : HTTP 503", status=503)
    monkeypatch.setattr(AU, "usage_du_jour", muette)
    with pytest.raises(AuthzDenied) as e:
        _appel(day=JOUR)
    assert (e.value.status, e.value.code) == (502, "anthropic_admin_unavailable")
