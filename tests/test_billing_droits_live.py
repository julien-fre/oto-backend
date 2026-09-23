"""Le commerce pose les droits déclarés de l'org (ADR 0070 §7), sur une vraie base.

Ce que ces bancs tiennent :
- un abonnement payé actif pose ses droits jusqu'à la fin de période + délai de grâce ;
  résilié, jusqu'à la fin de période ; en impayé, jusqu'à la fin de la grâce ; clos, rien ;
- un plan offert et un don d'org posent `offered`, à l'échéance du don ; chez un
  partenaire, `partner` sans date ;
- le don fait à une PERSONNE n'écrit rien ;
- la réconciliation ne retire que ce que ses sources ont posé, et se rejoue sans effet ;
- les gestes du commerce et de l'admin la déclenchent.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import billing, billing_droits, db
from oto_mcp.access.entitlements import PLATFORM_UNMETERED, org_has
from oto_mcp.db import billing as db_billing
from oto_mcp.db import entitlements as E
from oto_mcp.db._conn import _connect

MAINTENANT = datetime.now(timezone.utc).replace(microsecond=0)
DANS_UN_MOIS = MAINTENANT + timedelta(days=30)
HIER = MAINTENANT - timedelta(days=1)


def _org() -> int:
    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (f"org-{uuid.uuid4().hex[:8]}",)).fetchone()["id"]


def _lignes(org: int) -> dict[tuple[str, str], object]:
    """(droit, source) → échéance en UTC (ou None), relue sans passer par le row factory."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT right_key, source, EXTRACT(EPOCH FROM expires_at) AS fin "
            "FROM org_entitlements WHERE org_id = %s", (org,)).fetchall()
    return {(r["right_key"], r["source"]):
            (None if r["fin"] is None
             else datetime.fromtimestamp(float(r["fin"]), timezone.utc))
            for r in rows}


@pytest.fixture(autouse=True)
def _client_direct(monkeypatch):
    monkeypatch.setattr(billing, "_hosted_by_partner", lambda org_id: False)


def _abonner(org: int, *, fin=DANS_UN_MOIS, plan="standard") -> None:
    db_billing.upsert_org_subscription(org, plan=plan, provider="mollie", status="active",
                                       customer_id="cst_test", mandate_id="mdt_test",
                                       current_period_end=fin, next_billing_at=fin)


def test_un_abonnement_actif_pose_ses_droits_jusqu_a_la_fin_plus_la_grace(live):
    org = _org()
    _abonner(org)
    billing_droits.reconcilier(org)
    attendu = DANS_UN_MOIS + billing_droits.delai_de_grace()
    assert _lignes(org) == {("unipile", "subscription"): attendu,
                            (PLATFORM_UNMETERED, "subscription"): attendu}
    assert org_has(org, "unipile") and org_has(org, PLATFORM_UNMETERED)


def test_la_grace_couvre_les_relances_puis_la_grace_contractuelle(live):
    assert billing_droits.delai_de_grace() == timedelta(days=21)


def test_la_resiliation_borne_a_la_fin_de_periode(live):
    org = _org()
    _abonner(org)
    db_billing.mark_cancel_at_period_end(org)
    billing_droits.reconcilier(org)
    assert _lignes(org)[("unipile", "subscription")] == DANS_UN_MOIS


def test_l_impaye_borne_a_la_fin_de_la_grace_puis_la_fermeture_retire(live):
    org = _org()
    _abonner(org, fin=HIER)
    grace = MAINTENANT + timedelta(days=15)
    db_billing.set_subscription_status(org, "past_due", grace_until=grace)
    billing_droits.reconcilier(org)
    assert _lignes(org)[("unipile", "subscription")] == grace
    db_billing.set_subscription_status(org, "canceled")
    out = billing_droits.reconcilier(org)
    assert out == {"poses": 0, "retires": 2}
    assert _lignes(org) == {}
    assert not org_has(org, "unipile")


def test_un_droit_echu_ne_donne_plus_rien_sans_attendre_personne(live):
    """La fin de période + grâce passée, la ligne est échue : le droit se ferme par sa
    date, même si le runner n'est pas passé basculer l'abonnement."""
    org = _org()
    _abonner(org, fin=MAINTENANT - timedelta(days=30))
    billing_droits.reconcilier(org)
    assert ("unipile", "subscription") in _lignes(org)
    assert not org_has(org, "unipile")


def test_le_plan_offert_pose_offered_sans_date_et_son_retrait_le_retire(live):
    org = _org()
    billing.admin_set_plan(org, "premium", granted_by="admin-test")
    assert _lignes(org) == {("unipile", "offered"): None,
                            (PLATFORM_UNMETERED, "offered"): None}
    billing.admin_clear_plan(org)
    assert _lignes(org) == {}


def test_le_don_d_org_pose_offered_a_son_echeance(live):
    org = _org()
    db.set_option_comp("org", str(org), "unipile", granted_by="admin-test",
                       expires_at=DANS_UN_MOIS)
    billing_droits.reconcilier(org)
    assert _lignes(org) == {("unipile", "offered"): DANS_UN_MOIS}
    with _connect() as conn:
        assert conn.execute("SELECT granted_by FROM org_entitlements WHERE org_id = %s",
                            (org,)).fetchone()["granted_by"] == "admin-test"
    assert org_has(org, "unipile")


def test_un_don_echu_se_recopie_echu_et_ne_donne_rien(live):
    org = _org()
    db.set_option_comp("org", str(org), "unipile", expires_at=HIER)
    billing_droits.reconcilier(org)
    assert _lignes(org) == {("unipile", "offered"): HIER}
    assert not org_has(org, "unipile")


def test_don_et_plan_offert_la_plus_genereuse_des_echeances_gagne(live):
    org = _org()
    db.set_option_comp("org", str(org), "unipile", expires_at=DANS_UN_MOIS)
    billing.admin_set_plan(org, "standard", granted_by="admin-test")
    assert _lignes(org)[("unipile", "offered")] is None
    billing.admin_clear_plan(org)
    assert _lignes(org) == {("unipile", "offered"): DANS_UN_MOIS}, (
        "retirer le plan offert ne reprend pas le don qui reste")


def test_chez_un_partenaire_partner_sans_date(live, monkeypatch):
    monkeypatch.setattr(billing, "_hosted_by_partner", lambda org_id: True)
    org = _org()
    db.set_option_comp("org", str(org), "unipile", expires_at=DANS_UN_MOIS)
    billing_droits.reconcilier(org)
    assert _lignes(org) == {("unipile", "partner"): None}


def test_le_don_fait_a_une_personne_n_ecrit_rien(live):
    org = _org()
    sub = f"u-{uuid.uuid4().hex[:8]}"
    db.upsert_user(sub)
    db.set_option_comp("user", sub, "unipile")
    billing_droits.reconcilier(org)
    assert _lignes(org) == {}


def test_elle_ne_retire_que_ce_que_ses_sources_ont_pose(live):
    org = _org()
    E.grant(org, "unipile", "trial", expires_at=DANS_UN_MOIS)
    E.grant(org, "unipile", "offered")          # posé sans raison d'être : retiré
    out = billing_droits.reconcilier(org)
    assert out == {"poses": 0, "retires": 1}
    assert _lignes(org) == {("unipile", "trial"): DANS_UN_MOIS}


def test_la_reprise_couvre_toutes_les_orgs_et_se_rejoue_sans_effet(live):
    payee, offerte, donnee, vide = _org(), _org(), _org(), _org()
    _abonner(payee)
    db_billing.set_comp_subscription(offerte, "business")
    db.set_option_comp("org", str(donnee), "unipile", expires_at=DANS_UN_MOIS)
    premier = billing_droits.reconcilier_tout()
    etat = {o: _lignes(o) for o in (payee, offerte, donnee, vide)}
    assert premier["echecs"] == 0
    assert etat[payee] and etat[offerte] and etat[donnee] and not etat[vide]
    second = billing_droits.reconcilier_tout()
    assert second["retires"] == 0 and second["echecs"] == 0
    assert {o: _lignes(o) for o in etat} == etat


def test_la_reprise_a_blanc_n_ecrit_rien(live):
    org = _org()
    _abonner(org)
    out = billing_droits.reconcilier_tout(dry_run=True)
    assert out["poses"] >= 2
    assert _lignes(org) == {}


def test_la_resiliation_et_la_reprise_du_commerce_redatent_les_droits(live, monkeypatch):
    monkeypatch.setattr(billing.billing_grants, "granted_benefits", lambda *a, **k: [])
    monkeypatch.setattr(billing.billing_grants, "monthly_usage", lambda *a, **k: {})
    org = _org()
    _abonner(org)
    billing_droits.reconcilier(org)
    billing.cancel(org)
    assert _lignes(org)[("unipile", "subscription")] == DANS_UN_MOIS
    billing.resume(org)
    assert (_lignes(org)[("unipile", "subscription")]
            == DANS_UN_MOIS + billing_droits.delai_de_grace())


def test_le_travail_de_maintenance_est_au_timer_quotidien():
    from oto_mcp import maintenance
    assert maintenance._TRAVAUX["droits"] is maintenance.droits
    assert "droits" in maintenance._ALL
