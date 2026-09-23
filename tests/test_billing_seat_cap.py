"""Le plafond de comptes de messagerie d'une org (`orgs.unipile_account_limit`) face au
plan d'oto.

Arbitrage du 23/09 (#805) : un plan sans nombre de sièges (`unipile_accounts=None`, tous
les paliers aujourd'hui) n'a PAS d'avis. Ni la souscription ni le retrait n'écrivent la
colonne — écrire `NULL` la ramenait au défaut plateforme (5) : un plafond posé à la main
(20) retombait à 5 au moment où le client payait.

Un plan qui porterait un nombre l'écrit, sauf sur l'org d'un tenant TIERS, dont le
plafond appartient à la facturation du partenaire (`PUT /api/admin/orgs/{id}/unipile-limit`,
2026-09-11)."""
from __future__ import annotations

import pytest

from oto_mcp import billing, tenancy
from oto_mcp.db import billing as db_billing


@pytest.fixture(autouse=True)
def _droits_declares_hors_banc(monkeypatch):
    """La réconciliation des droits déclarés lit la base : son banc est
    `test_billing_droits_live`. Ici, elle est neutralisée."""
    from oto_mcp import billing as _billing
    monkeypatch.setattr(_billing, "reconcilier_droits", lambda org_id: None)


MANUAL_CAP = 20


def _wire(monkeypatch, tenant):
    """Une org 7 dont le plafond a été réglé à la main à 20 ; `limits` est la colonne."""
    state: dict = {"limits": {7: MANUAL_CAP}, "writes": []}

    def slug(org_id):
        if isinstance(tenant, Exception):
            raise tenant
        return tenant

    def set_limit(org, lim):
        state["writes"].append((org, lim))
        state["limits"][org] = lim

    monkeypatch.setattr(billing.db, "org_tenant_slug", slug)
    monkeypatch.setattr(billing.db, "set_org_unipile_limit", set_limit)
    monkeypatch.setattr(db_billing, "set_comp_subscription",
                        lambda org, plan, granted_by=None: None)
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: {"provider": "comp"})
    monkeypatch.setattr(db_billing, "delete_subscription", lambda org: None)
    monkeypatch.setattr(billing, "status", lambda org: {"subscribed": True, "org_id": org})
    return state


@pytest.fixture
def plan_with_seats(monkeypatch):
    """Un palier qui porte un nombre de sièges (aucun aujourd'hui)."""
    monkeypatch.setitem(billing.PLANS, "seats12",
                        {**billing.PLANS["business"], "unipile_accounts": 12})
    return "seats12"


# ── plan sans avis : le plafond en place survit ─────────────────────────────

@pytest.mark.parametrize("plan", list(billing.PLANS))
def test_souscrire_un_plan_sans_avis_garde_le_plafond_manuel(monkeypatch, plan):
    """Le cliquet de #805 : c'est le chemin de la souscription payée (`confirm`) et du
    plan forcé (`admin_set_plan`)."""
    s = _wire(monkeypatch, tenancy.PRIMARY_SLUG)
    billing.apply_plan_entitlements(7, plan)
    assert s["limits"][7] == MANUAL_CAP and s["writes"] == []


def test_forcer_un_plan_sur_une_cliente_directe_garde_son_plafond(monkeypatch):
    s = _wire(monkeypatch, None)
    billing.admin_set_plan(7, "business", granted_by="admin")
    assert s["limits"][7] == MANUAL_CAP and s["writes"] == []


@pytest.mark.parametrize("tenant", [tenancy.PRIMARY_SLUG, "acme"])
def test_retirer_le_plan_comp_garde_le_plafond(monkeypatch, tenant):
    s = _wire(monkeypatch, tenant)
    billing.admin_clear_plan(7)
    assert s["limits"][7] == MANUAL_CAP and s["writes"] == []


def test_aucun_palier_ne_porte_de_sieges_aujourd_hui():
    """Si un palier en porte un jour, la souscription l'ÉCRIRA sur les clientes
    directes : ce test tombe pour qu'on le décide en connaissance de cause."""
    assert all(meta["unipile_accounts"] is None for meta in billing.PLANS.values())


# ── plan qui porte un nombre de sièges ──────────────────────────────────────

def test_un_plan_avec_sieges_ecrit_le_plafond_d_une_cliente_directe(monkeypatch, plan_with_seats):
    s = _wire(monkeypatch, tenancy.PRIMARY_SLUG)
    billing.apply_plan_entitlements(7, plan_with_seats)
    assert s["writes"] == [(7, 12)]


def test_un_plan_avec_sieges_n_ecrase_pas_le_plafond_d_un_partenaire(monkeypatch, plan_with_seats):
    s = _wire(monkeypatch, "acme")
    billing.admin_set_plan(7, plan_with_seats, granted_by="admin")
    assert s["limits"][7] == MANUAL_CAP and s["writes"] == []


def test_un_tenant_illisible_suit_le_chemin_des_clientes_directes(monkeypatch, plan_with_seats):
    s = _wire(monkeypatch, RuntimeError("base indisponible"))
    billing.apply_plan_entitlements(7, plan_with_seats)
    assert s["writes"] == [(7, 12)]
