"""Ce qu'Anthropic a facturé aux clés de plateforme, contre ce que le journal a mesuré
(`admin.model_spend`) — le contrôle qui ne fait pas confiance à la mesure.

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


def _res(cle, model="claude-sonnet-5", entree=1_000_000, sortie=0, c5=0, c1=0, lu=0):
    return {"api_key_id": cle, "model": model, "uncached_input_tokens": entree,
            "output_tokens": sortie, "cache_read_input_tokens": lu,
            "cache_creation": {"ephemeral_5m_input_tokens": c5, "ephemeral_1h_input_tokens": c1}}


def _rapprocher(usage, mesure, plateforme=(("principale", SECRET),), cles=CLES):
    return PMS.rapprocher(JOUR, usage=usage, cles=cles, plateforme=list(plateforme),
                          mesure_microdollars=mesure)


# ── le prix d'un résultat du relevé ──────────────────────────────────────────

def test_chaque_poste_est_au_tarif_du_catalogue():
    # Sonnet 5 : 2 / 10 $ ; écriture 5 min 2,5 $ ; 1 h = 2 × l'entrée ; lecture 0,2 $.
    r = _res("k", entree=1, sortie=1, c5=1, c1=1, lu=10)
    assert AU.microdollars(r) == 2 + 10 + 3 + 4 + 2   # 2,5 arrondi au-dessus avec le reste


def test_un_id_date_prend_le_tarif_de_son_alias_un_inconnu_n_en_a_pas():
    assert AU.microdollars(_res("k", model="claude-sonnet-5-20260601")) == 2_000_000
    assert AU.microdollars(_res("k", model="claude-inconnu")) is None


@pytest.mark.parametrize("indice,attendu", [
    ("sk-ant-api03-R2D...igAA", True), ("sk-ant-api03-R2D...igAB", False),
    ("sk-ant-api03-XXX...igAA", False), (None, False), ("sans-points", False)])
def test_un_indice_designe_un_secret_par_sa_tete_et_sa_queue(indice, attendu):
    assert AU.indice_correspond(indice, SECRET) is attendu


# ── le rapprochement ─────────────────────────────────────────────────────────

def test_un_jour_entierement_mesure_n_a_pas_d_ecart():
    out = _rapprocher([_res("apikey_plateforme")], 2_000_000)
    assert (out["anthropic_usd"], out["metered_usd"], out["gap_usd"], out["gap_pct"]) == \
        (2.0, 2.0, 0.0, 0.0)
    assert out["platform_keys"] == [{"label": "principale", "api_key_id": "apikey_plateforme",
                                     "name": "plateforme", "match": "matched",
                                     "anthropic_usd": 2.0}]
    assert out["other_keys"] == [] and out["unmatched_platform_rows"] == []


def test_ce_qu_aucune_ligne_ne_porte_est_l_ecart():
    out = _rapprocher([_res("apikey_plateforme")], 1_500_000)
    assert (out["gap_usd"], out["gap_pct"]) == (0.5, 25.0)


def test_un_journal_au_dessus_d_anthropic_est_un_ecart_negatif():
    assert _rapprocher([_res("apikey_plateforme")], 2_500_000)["gap_usd"] == -0.5


def test_la_depense_d_une_AUTRE_cle_est_rendue_a_part():
    """Le trou de la clé d'environnement : un worker qui tourne sur la sienne n'est
    mesuré nulle part. `null` = la Console."""
    out = _rapprocher([_res("apikey_plateforme"), _res("apikey_poste", entree=500_000),
                       _res(None, entree=250_000)], 2_000_000)
    assert out["gap_usd"] == 0.0
    assert out["other_keys"] == [{"api_key_id": "apikey_poste", "name": "poste de dev", "usd": 1.0},
                                 {"api_key_id": None, "name": None, "usd": 0.5}]


def test_un_modele_sans_tarif_est_nomme_jamais_price():
    out = _rapprocher([_res("apikey_plateforme"), _res("apikey_plateforme", model="claude-x")],
                      2_000_000)
    assert out["unknown_models"] == ["claude-x"] and out["anthropic_usd"] == 2.0


def test_une_ligne_sans_indice_ou_a_deux_indices_n_est_jamais_devinee():
    out = _rapprocher([_res("apikey_plateforme")], 0,
                      plateforme=[("sans", "sk-ant-api03-QQQq-zzzz"), ("principale", SECRET)])
    assert out["unmatched_platform_rows"] == ["sans"]
    doubles = CLES + [{"id": "apikey_jumelle", "name": "j", "partial_key_hint": "sk-ant-api03-R2D...igAA"}]
    out = _rapprocher([_res("apikey_plateforme")], 0, cles=doubles)
    assert out["platform_keys"][0]["match"] == "ambiguous"
    assert out["anthropic_usd"] == 0.0, "une clé ambiguë n'est comptée sous aucune ligne"


def test_la_sortie_ne_porte_ni_secret_ni_indice():
    texte = repr(_rapprocher([_res("apikey_plateforme")], 0))
    assert SECRET not in texte and "R2D" not in texte


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
    monkeypatch.setattr(PMS.db, "quantite_du_jour",
                        lambda outil, mode, jour: vu.update(q=(outil, mode, jour)) or 2_000_000)
    out = _appel(day=JOUR)
    assert out["gap_usd"] == 0.0 and out["day"] == "2026-10-18"
    assert vu["q"] == ("anthropic_tokens", "platform", JOUR), "le rang plateforme seul"


def test_une_admin_api_muette_est_un_refus_nomme(monkeypatch):
    monkeypatch.setenv("OTO_ANTHROPIC_ADMIN_KEY", "sk-ant-admin-x")

    def muette(jour, key):
        raise AU.AnthropicAdminError("Admin API : HTTP 503", status=503)
    monkeypatch.setattr(AU, "usage_du_jour", muette)
    with pytest.raises(AuthzDenied) as e:
        _appel(day=JOUR)
    assert (e.value.status, e.value.code) == (502, "anthropic_admin_unavailable")
