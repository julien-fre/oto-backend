"""Un paiement de test n'ouvre jamais de droit en production — et hors production, aucun
paiement n'en ouvre.

Relevé le 10/09/2026 : la préproduction expose la souscription avec la clé Mollie de
TEST, sur la base qu'elle partage avec la production, et `confirm` ne regardait pas le
mode du paiement reçu. Un paiement de test y ouvrait un droit — réel, puisque la base est
celle de la production. La règle (`billing_mode`) se lit sur la réponse du PRESTATAIRE
(`mode`) et sur l'environnement déclaré (`config.est_la_production`), et elle joue AVANT
toute écriture tirée du paiement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import billing, billing_method, config
from oto_mcp.capabilities import billing as cap_billing
from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.db import billing as db_billing


@pytest.fixture(autouse=True)
def _droits_declares_hors_banc(monkeypatch):
    """La réconciliation des droits déclarés lit la base : son banc est
    `test_billing_droits_live`. Ici, elle est neutralisée."""
    from oto_mcp import billing as _billing
    monkeypatch.setattr(_billing, "reconcilier_droits", lambda org_id: None)


# Les déclarations relevées sur les processus servis le 10/09/2026 — chaque
# environnement se nommait alors par son URL publique ; c'est `OTO_ENV` qui le porte
# depuis le 15/09, et Sentry reste le second témoin qu'on recoupe.
PROD = (config.PROD, "production")
PREPROD = (config.PREPROD, "canari")


def _env(monkeypatch, declare, sentry=None):
    if declare is None:
        monkeypatch.delenv("OTO_ENV", raising=False)
    else:
        monkeypatch.setenv("OTO_ENV", declare)
    if sentry is None:
        monkeypatch.delenv("OTO_SENTRY_ENV", raising=False)
    else:
        monkeypatch.setenv("OTO_SENTRY_ENV", sentry)


# ── la souscription ──────────────────────────────────────────────────────────

@pytest.fixture
def souscription(monkeypatch):
    """Un premier paiement en cours ; le store et le PSP simulés, chaque écriture tracée."""
    ecrit: list = []
    row = {"id": 7, "org_id": 42, "kind": "initial", "status": "open",
           "payment_intent_id": "tr_1",
           "created_at": datetime.now(timezone.utc) - timedelta(seconds=2)}
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: None)
    monkeypatch.setattr(db_billing, "list_billing_payments", lambda org, limit=20: [row])
    monkeypatch.setattr(db_billing, "update_billing_payment",
                        lambda rid, **k: ecrit.append(("journal", rid, k)) or True)
    monkeypatch.setattr(db_billing, "upsert_org_subscription",
                        lambda org, **k: ecrit.append(("abonnement", org, k["plan"])))
    monkeypatch.setattr(billing, "apply_plan_entitlements",
                        lambda org, plan: ecrit.append(("droit", org, plan)))
    from oto_mcp import billing_invoices
    monkeypatch.setattr(billing_invoices, "tracer_encaissement",
                        lambda rid: ecrit.append(("facture", rid)))
    monkeypatch.setattr(billing.mollie_client, "valid_mandate",
                        lambda cid: {"id": "mdt_1", "mandateReference": "RUM1"})
    psp: dict = {}
    monkeypatch.setattr(billing.mollie_client, "get_payment",
                        lambda ref: dict(psp["paiement"]))

    def poser(mode, status="paid"):
        psp["paiement"] = {"id": "tr_1", "status": status, "customerId": "cst_1",
                           "method": "creditcard", "metadata": {"plan": "standard"}}
        if mode is not None:
            psp["paiement"]["mode"] = mode

    return poser, ecrit


def test_en_production_un_paiement_reel_ouvre_le_droit(souscription, monkeypatch):
    poser, ecrit = souscription
    _env(monkeypatch, *PROD)
    poser("live")
    assert billing.confirm(42)["status"] == "active"
    assert ("abonnement", 42, "standard") in ecrit and ("droit", 42, "standard") in ecrit


@pytest.mark.parametrize("mode", ["test", None], ids=["test", "absent"])
def test_en_production_un_paiement_qui_n_est_pas_reel_n_ouvre_rien(souscription,
                                                                 monkeypatch, mode):
    poser, ecrit = souscription
    _env(monkeypatch, *PROD)
    poser(mode)
    with pytest.raises(RuntimeError, match="payment_mode_mismatch"):
        billing.confirm(42)
    assert ecrit == [], "ni journal, ni trace de facture, ni abonnement, ni droit"


@pytest.mark.parametrize("mode", ["test", "live"])
def test_hors_production_aucun_paiement_n_ouvre_de_droit(souscription, monkeypatch, mode):
    # La préproduction partage la base : un droit qu'elle ouvrirait serait réel.
    poser, ecrit = souscription
    _env(monkeypatch, *PREPROD)
    poser(mode)
    with pytest.raises(RuntimeError, match="billing_not_production"):
        billing.confirm(42)
    assert ecrit == []


def test_un_environnement_ambigu_n_ouvre_rien(souscription, monkeypatch):
    poser, ecrit = souscription
    _env(monkeypatch, None)
    poser("live")
    with pytest.raises(config.EnvironnementAmbigu):
        billing.confirm(42)
    assert ecrit == []


def test_meme_un_echec_de_test_ne_s_ecrit_pas(souscription, monkeypatch):
    # Le refus précède TOUTE écriture tirée du paiement, pas seulement l'ouverture.
    poser, ecrit = souscription
    _env(monkeypatch, *PROD)
    poser("test", status="failed")
    with pytest.raises(RuntimeError, match="payment_mode_mismatch"):
        billing.confirm(42)
    assert ecrit == []


def test_le_webhook_de_la_preprod_n_ouvre_rien(souscription, monkeypatch):
    poser, ecrit = souscription
    _env(monkeypatch, *PREPROD)
    poser("test")
    monkeypatch.setattr(db_billing, "get_billing_payment_by_ref", lambda ref: {
        "id": 7, "org_id": 42, "kind": "initial", "status": "open"})
    assert billing.process_webhook("tr_1") == "not_confirmed"
    assert ecrit == []


def test_la_surface_rend_un_refus_definitif():
    for code in ("payment_mode_mismatch", "billing_not_production"):
        def refuse():
            raise RuntimeError(f"{code}: détail")
        with pytest.raises(AuthzDenied) as e:
            cap_billing._domain(refuse)
        assert (e.value.status, e.value.code) == (409, code)


# ── le changement de moyen ───────────────────────────────────────────────────

@pytest.fixture
def changement(monkeypatch):
    """Un changement de moyen en cours sur un abonnement réel ; chaque écriture tracée."""
    ecrit: list = []
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda o: {
        "status": "active", "customer_id": "cst_1", "mandate_id": "mdt_ancien",
        "plan": "standard"})
    monkeypatch.setattr(db_billing, "list_billing_payments", lambda o, limit=20: [
        {"id": 3, "kind": "method_change", "payment_intent_id": "tr_m", "status": "open"}])
    monkeypatch.setattr(db_billing, "update_billing_payment",
                        lambda rid, **k: ecrit.append(("journal", rid, k)) or True)
    monkeypatch.setattr(db_billing, "swap_mandate",
                        lambda o, **k: ecrit.append(("mandat", k["mandate_id"]))
                        or "mdt_ancien")
    monkeypatch.setattr(billing_method.mollie_client, "valid_mandate",
                        lambda cid: {"id": "mdt_neuf", "method": "creditcard"})
    monkeypatch.setattr(billing_method.mollie_client, "revoke_mandate",
                        lambda c, m: ecrit.append(("revocation", m)))
    psp: dict = {}
    monkeypatch.setattr(billing_method.mollie_client, "get_payment",
                        lambda ref: dict(psp["paiement"]))

    def poser(mode):
        psp["paiement"] = {"id": "tr_m", "status": "paid", "mode": mode}

    return poser, ecrit


def test_un_moyen_reel_en_production_bascule(changement, monkeypatch):
    poser, ecrit = changement
    _env(monkeypatch, *PROD)
    poser("live")
    assert billing_method.confirm(7)["status"] == "changed"
    assert ("mandat", "mdt_neuf") in ecrit


@pytest.mark.parametrize("env, mode, code", [
    (PROD, "test", "payment_mode_mismatch"),
    (PREPROD, "test", "billing_not_production"),
    (PREPROD, "live", "billing_not_production"),
])
def test_un_mandat_qui_n_est_pas_reel_ne_remplace_jamais_le_moyen(changement, monkeypatch,
                                                                  env, mode, code):
    poser, ecrit = changement
    _env(monkeypatch, *env)
    poser(mode)
    with pytest.raises(RuntimeError, match=code):
        billing_method.confirm(7)
    assert ecrit == [], "l'ancien moyen reste en place : ni journal, ni bascule"
