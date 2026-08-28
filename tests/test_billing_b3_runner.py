"""Billing (ADR 0043) — billing_runner : échéances MIT (Mollie recurring), dunning
borné, sweeps, réconciliation. Mollie + store monkeypatchés, logique pure testée."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oto_mcp import billing, billing_runner
from oto_mcp.db import billing as db_billing
from oto_mcp.mollie_client import MollieError

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def _sub(**over) -> dict:
    base = {"org_id": 42, "plan": "premium", "method": "card",
            "mandate_id": "mdt_1", "customer_id": "cst_1",
            "current_period_end": NOW - timedelta(hours=2), "status": "active"}
    base.update(over)
    return base


# L'échéance est prélevée TTC depuis #486, au taux de l'identité de facturation
# lue AU MOMENT du prélèvement — le même seam que `subscribe`. Une org abonnée en a
# forcément une (subscribe l'exige) : on la câble ici pour que ces tests restent sur
# le dunning, et un test dédié couvre l'org dont l'identité s'est cassée.
IDENTITE_FR = {"legal_name": "ACME SAS", "country_code": "FR", "vat_number": None,
               "address_line": "1 rue de la Paix", "postal_code": "13001",
               "city": "Marseille"}


def _wire(monkeypatch, *, attempts_before=0, payment=None, payment_exc=None,
          identity=IDENTITE_FR):
    state = {"journal": [], "updates": [], "schedule": None, "retry": None,
             "status": None}
    monkeypatch.setattr(db_billing, "get_billing_identity", lambda org: identity)
    monkeypatch.setattr(db_billing, "count_renewal_attempts",
                        lambda org, since: attempts_before)
    monkeypatch.setattr(db_billing, "insert_billing_payment",
                        lambda *a, **k: state["journal"].append((a, k)) or 11)
    monkeypatch.setattr(db_billing, "update_billing_payment",
                        lambda rid, **k: state["updates"].append((rid, k)) or True)
    monkeypatch.setattr(db_billing, "schedule_next_billing",
                        lambda org, pe, nb: state.update(schedule=(org, pe, nb)) or True)
    monkeypatch.setattr(db_billing, "retry_billing_at",
                        lambda org, when: state.update(retry=(org, when)) or True)
    monkeypatch.setattr(db_billing, "set_subscription_status",
                        lambda org, st, **k: state.update(status=(org, st, k)) or True)

    def fake_payment(amount, **k):
        state["charge"] = (amount, k)
        if payment_exc:
            raise payment_exc
        return payment or {"id": "tr_r1", "status": "paid"}

    monkeypatch.setattr(billing_runner.mollie_client, "create_recurring_payment",
                        fake_payment)
    return state


# ── _charge_one ──────────────────────────────────────────────────────────────

def test_renewal_success_anchors_on_period_end(monkeypatch):
    state = _wire(monkeypatch)
    assert billing_runner._charge_one(_sub(), NOW) == "renewed"
    amount, kw = state["charge"]
    # #486 : le renouvellement prélève le TTC, exactement comme la souscription —
    # 49,00 € HT + 20 % = 58,80 €. Un renouvellement resté au HT aurait signifié que
    # le client paie deux montants différents selon le mois.
    assert amount == 5880 == billing.PLANS["premium"]["amount"] * 6 // 5
    # …et la décomposition est journalisée sur la tentative, pas seulement débitée.
    assert state["journal"][-1][1]["tax"]["vat_scheme"] == "fr_ttc"
    assert state["journal"][-1][0][2] == 5880
    assert kw["customer_id"] == "cst_1" and kw["mandate_id"] == "mdt_1"
    # idempotency_key déterministe période+tentative (anti double-débit)
    assert kw["idempotency_key"] == "org42-2026-07-06-a1"
    org, period_end, next_at = state["schedule"]
    # ancré sur current_period_end (+1 mois calendaire), PAS sur l'heure du tick
    assert (period_end.year, period_end.month, period_end.day) == (2026, 8, 6)
    assert period_end == next_at


def test_renewal_pending_sepa_counts_as_renewed(monkeypatch):
    # un prélèvement SEPA soumis reste 'pending' plusieurs jours → on avance le
    # cycle (la réconciliation/webhook rattrape un rejet ultérieur).
    state = _wire(monkeypatch, payment={"id": "tr_r1", "status": "pending"})
    assert billing_runner._charge_one(_sub(method="sepa"), NOW) == "renewed"
    assert state["schedule"] is not None


def test_renewal_far_overdue_catches_up(monkeypatch):
    state = _wire(monkeypatch)
    old = _sub(current_period_end=NOW - timedelta(days=70))
    billing_runner._charge_one(old, NOW)
    assert state["schedule"][1] > NOW          # jamais une échéance dans le passé


def test_failure_schedules_retry(monkeypatch):
    state = _wire(monkeypatch, attempts_before=0,
                  payment={"id": "tr_r1", "status": "failed"})
    assert billing_runner._charge_one(_sub(), NOW) == "retry"
    assert state["retry"] == (42, NOW + billing_runner._RETRY_DELAY)
    assert state["status"] is None             # pas encore past_due


def test_third_failure_goes_past_due_with_grace(monkeypatch):
    state = _wire(monkeypatch, attempts_before=2, payment_exc=MollieError(422, "declined"))
    assert billing_runner._charge_one(_sub(), NOW) == "past_due"
    org, st, kw = state["status"]
    assert (org, st) == (42, "past_due")
    assert kw["grace_until"] == NOW + billing_runner._GRACE
    # l'échec est journalisé (audit du dunning)
    assert state["updates"][-1][1]["status"] == "failed"


def test_unknown_plan_or_missing_mandate_skips(monkeypatch):
    state = _wire(monkeypatch)
    assert billing_runner._charge_one(_sub(plan="gold"), NOW) == "skipped"
    assert billing_runner._charge_one(_sub(mandate_id=None), NOW) == "skipped"
    assert "charge" not in state               # aucun débit tenté


def test_une_identite_devenue_incalculable_bloque_le_prelevement(monkeypatch):
    """#486 : sans identité exploitable, il n'y a pas de montant CORRECT à prendre.

    Le runner ne retombe donc pas sur le HT « en attendant » — c'est exactement ce
    que ce lot répare. Rien n'est débité, rien n'est journalisé, et le cycle n'est
    pas décalé : l'échéance reste due et repartira dès l'identité réparée."""
    state = _wire(monkeypatch, identity=None)
    assert billing_runner._charge_one(_sub(), NOW) == "tax_blocked"
    assert "charge" not in state and state["journal"] == []
    assert state["schedule"] is None and state["retry"] is None


# ── réconciliation ───────────────────────────────────────────────────────────

def test_reconcile_payment_updates_status(monkeypatch):
    updates = []
    monkeypatch.setattr(db_billing, "update_billing_payment",
                        lambda rid, **k: updates.append((rid, k)) or True)
    monkeypatch.setattr(billing_runner.mollie_client, "get_payment",
                        lambda pid: {"status": "paid"})
    billing_runner._reconcile_one({"id": 5, "kind": "renewal", "payment_id": "tr_r1",
                                   "status": "pending"}, NOW)
    assert updates == [(5, {"status": "paid"})]


def test_reconcile_paid_initial_replays_confirm(monkeypatch):
    called = {}
    monkeypatch.setattr(billing_runner.mollie_client, "get_payment",
                        lambda i: {"status": "paid"})
    monkeypatch.setattr(billing_runner.billing, "confirm",
                        lambda org, payment_ref=None: called.update(
                            org=org, ref=payment_ref))
    billing_runner._reconcile_one({"id": 5, "org_id": 42, "kind": "initial",
                                   "payment_id": None, "payment_intent_id": "tr_1",
                                   "status": "open"}, NOW)
    assert called["org"] == 42                 # onglet fermé → rattrapage miroir
    # #291/#493 : le rattrapage DIT lequel — « le plus récent » peut être un autre
    # checkout de la même org.
    assert called["ref"] == "tr_1"


def test_tick_reprend_les_encaissements_sans_mandat(monkeypatch):
    """#493 : un encaissement est journalisé `paid` dès son constat, donc TERMINAL,
    donc invisible de `open_billing_payments`. Sans cette seconde file, un payeur qui
    ferme son onglet pendant la course au mandat reste débité et sans droits."""
    called = []
    monkeypatch.setattr(billing_runner.mollie_client, "is_configured", lambda: True)
    monkeypatch.setattr(db_billing, "sweep_period_end_cancellations", lambda: [])
    monkeypatch.setattr(db_billing, "sweep_grace_expired", lambda: [])
    monkeypatch.setattr(db_billing, "due_subscriptions", lambda: [])
    monkeypatch.setattr(db_billing, "open_billing_payments", lambda: [])
    monkeypatch.setattr(db_billing, "paid_initials_awaiting_subscription",
                        lambda **k: [{"org_id": 42, "payment_intent_id": "tr_1",
                                      "payment_id": None}])
    monkeypatch.setattr(billing_runner.billing, "confirm",
                        lambda org, payment_ref=None: called.append((org, payment_ref)))
    assert billing_runner.tick() == {"mandate_catchup": 1}
    assert called == [(42, "tr_1")]


def test_reconcile_stale_initial_payment_expires(monkeypatch):
    updates = []
    monkeypatch.setattr(db_billing, "update_billing_payment",
                        lambda rid, **k: updates.append((rid, k)) or True)
    monkeypatch.setattr(billing_runner.mollie_client, "get_payment",
                        lambda i: {"status": "open"})
    billing_runner._reconcile_one(
        {"id": 5, "org_id": 42, "kind": "initial", "payment_id": None,
         "payment_intent_id": "tr_1", "status": "open",
         "created_at": NOW - timedelta(hours=72)}, NOW)
    assert updates == [(5, {"status": "expired"})]


# ── tick ─────────────────────────────────────────────────────────────────────

def test_tick_noop_without_key(monkeypatch):
    monkeypatch.setattr(billing_runner.mollie_client, "is_configured", lambda: False)
    assert billing_runner.tick() == {}


def test_tick_sweeps_and_counts(monkeypatch):
    monkeypatch.setattr(billing_runner.mollie_client, "is_configured", lambda: True)
    monkeypatch.setattr(db_billing, "sweep_period_end_cancellations", lambda: [1])
    monkeypatch.setattr(db_billing, "sweep_grace_expired", lambda: [2, 3])
    monkeypatch.setattr(db_billing, "due_subscriptions", lambda: [])
    monkeypatch.setattr(db_billing, "open_billing_payments", lambda: [])
    monkeypatch.setattr(db_billing, "paid_initials_awaiting_subscription",
                        lambda **k: [])
    assert billing_runner.tick() == {"closed": 3}


def test_runner_loop_registered_at_boot():
    # le lifespan du serveur embarque la boucle (gatée OTO_BILLING_RUNNER_ENABLED)
    import inspect
    from oto_mcp import server

    src = inspect.getsource(server.main)
    assert "billing_runner.run_billing_loop" in src
    assert "OTO_BILLING_RUNNER_ENABLED" in src
