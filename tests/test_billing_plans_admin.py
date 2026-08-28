"""Billing — 4 plans (Alexis 06/07) + abonnement forcé par un admin (comp,
non payé) + levée de quota (fin des credits d'appel). ADR 0043."""
from __future__ import annotations

import pytest

from oto_mcp import access, billing, billing_runner
from oto_mcp.db import billing as db_billing


# ── catalogue des 4 plans ────────────────────────────────────────────────────

def test_four_plans_with_prices():
    p = {x["plan"]: x for x in billing.plans()}
    assert set(p) == {"standard", "premium", "business", "enterprise"}
    # 19 / 49 / 249 / 499 € (Alexis 2026-08-03)
    assert (p["standard"]["amount"], p["premium"]["amount"],
            p["business"]["amount"], p["enterprise"]["amount"]) \
        == (1900, 4900, 24900, 49900)


def test_self_serve_refuses_custom_plan(monkeypatch):
    # aucun plan n'est `custom` aujourd'hui (4 paliers self-serve à prix fixe) ;
    # le garde-fou reste, testé sur un plan custom injecté.
    monkeypatch.setitem(billing.PLANS, "devis", {
        "label": "Devis", "amount": None, "currency": "eur", "interval": "month",
        "options": ("unipile",), "unipile_accounts": None, "unmetered": True,
        "custom": True})
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: None)
    with pytest.raises(ValueError, match="custom_plan"):
        # `sub` est obligatoire depuis #487, mais le palier sur devis est refusé
        # AVANT tout préalable — le catalogue tranche en premier.
        billing.subscribe(42, "devis", "https://oto.cx/billing", sub="u-1")


def test_plan_carries_no_cap_and_unmetered():
    # modèle simplifié : plus de plafond de comptes (None = illimité), tous unmetered.
    assert billing.plan_is_unmetered("business") is True
    assert billing.PLANS["premium"]["unipile_accounts"] is None
    assert billing.PLANS["enterprise"]["unipile_accounts"] is None


# ── admin force plan (comp) ──────────────────────────────────────────────────

def _wire_admin(monkeypatch):
    state = {}
    monkeypatch.setattr(db_billing, "set_comp_subscription",
                        lambda org, plan, granted_by=None: state.update(comp=(org, plan, granted_by)))
    monkeypatch.setattr(billing.db, "set_org_unipile_limit",
                        lambda org, lim: state.update(limit=(org, lim)))
    monkeypatch.setattr(billing, "status", lambda org: {"subscribed": True, "org_id": org})
    return state


def test_admin_set_plan_forces_comp_and_configures_org(monkeypatch):
    state = _wire_admin(monkeypatch)
    billing.admin_set_plan(7, "business", granted_by="admin-sub")
    assert state["comp"] == (7, "business", "admin-sub")
    # le plan CONFIGURE l'org : plafond messagerie posé d'un coup (None = illimité,
    # modèle simplifié — plus de facturation au nombre de comptes)
    assert state["limit"] == (7, None)


def test_admin_set_plan_rejects_unknown(monkeypatch):
    _wire_admin(monkeypatch)
    with pytest.raises(ValueError, match="unknown_plan"):
        billing.admin_set_plan(7, "platinum", granted_by="admin-sub")


def test_admin_clear_refuses_paid(monkeypatch):
    monkeypatch.setattr(db_billing, "get_org_subscription",
                        lambda org: {"provider": "mollie", "status": "active"})
    with pytest.raises(ValueError, match="paid_subscription"):
        billing.admin_clear_plan(7)


def test_admin_clear_removes_comp(monkeypatch):
    state = {}
    monkeypatch.setattr(db_billing, "get_org_subscription",
                        lambda org: {"provider": "comp", "status": "active"})
    monkeypatch.setattr(db_billing, "delete_subscription",
                        lambda org: state.update(deleted=org) or True)
    monkeypatch.setattr(billing.db, "set_org_unipile_limit",
                        lambda org, lim: state.update(limit=(org, lim)))
    out = billing.admin_clear_plan(7)
    assert out["subscribed"] is False
    assert state["deleted"] == 7 and state["limit"] == (7, None)


def test_runner_never_charges_comp(monkeypatch):
    charged = {}
    monkeypatch.setattr(billing_runner.mollie_client, "create_recurring_payment",
                        lambda *a, **k: charged.setdefault("hit", True))
    sub = {"org_id": 7, "provider": "comp", "plan": "business", "method": "comp",
           "status": "active", "current_period_end": None}
    from datetime import datetime, timezone
    assert billing_runner._charge_one(sub, datetime(2026, 7, 6, tzinfo=timezone.utc)) == "skipped"
    assert "hit" not in charged                 # jamais de PSP derrière un comp


# ── levée de quota (fin des credits d'appel) ─────────────────────────────────

def test_unmetered_org_bypasses_quota(monkeypatch):
    monkeypatch.setattr(access.db, "subscription_plan_for_org", lambda oid: "premium")
    assert access._org_unmetered(5) is True


def test_no_plan_org_keeps_quota(monkeypatch):
    monkeypatch.setattr(access.db, "subscription_plan_for_org", lambda oid: None)
    assert access._org_unmetered(5) is False
