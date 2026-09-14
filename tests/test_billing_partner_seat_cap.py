"""Le plan d'oto n'écrit pas le plafond de messagerie d'une org hébergée par un tenant
TIERS. Ce plafond appartient à la facturation du partenaire, qui le pose lui-même
(`PUT /api/admin/orgs/{id}/unipile-limit`) : un `oto_admin_set_plan` sur son org le
remettait au défaut de la plateforme, dans son dos (le partenaire, 2026-09-11)."""
from __future__ import annotations

from oto_mcp import billing, tenancy
from oto_mcp.db import billing as db_billing


def _wire(monkeypatch, tenant):
    state: dict = {"limits": []}

    def slug(org_id):
        if isinstance(tenant, Exception):
            raise tenant
        return tenant

    monkeypatch.setattr(billing.db, "org_tenant_slug", slug)
    monkeypatch.setattr(billing.db, "set_org_unipile_limit",
                        lambda org, lim: state["limits"].append((org, lim)))
    monkeypatch.setattr(db_billing, "set_comp_subscription",
                        lambda org, plan, granted_by=None: None)
    monkeypatch.setattr(db_billing, "get_org_subscription", lambda org: {"provider": "comp"})
    monkeypatch.setattr(db_billing, "delete_subscription", lambda org: None)
    monkeypatch.setattr(billing, "status", lambda org: {"subscribed": True, "org_id": org})
    return state


def test_une_cliente_directe_recoit_le_plafond_de_son_plan(monkeypatch):
    s = _wire(monkeypatch, tenancy.PRIMARY_SLUG)
    billing.admin_set_plan(7, "business", granted_by="admin")
    assert s["limits"] == [(7, None)]


def test_le_plan_force_sur_l_org_d_un_partenaire_n_ecrase_pas_son_plafond(monkeypatch):
    s = _wire(monkeypatch, "acme")
    billing.admin_set_plan(7, "business", granted_by="admin")
    assert s["limits"] == []


def test_retirer_le_plan_comp_d_un_partenaire_laisse_son_plafond(monkeypatch):
    s = _wire(monkeypatch, "acme")
    billing.admin_clear_plan(7)
    assert s["limits"] == []


def test_retirer_le_plan_comp_d_une_cliente_directe_leve_le_plafond_comme_avant(monkeypatch):
    s = _wire(monkeypatch, tenancy.PRIMARY_SLUG)
    billing.admin_clear_plan(7)
    assert s["limits"] == [(7, None)]


def test_un_tenant_illisible_garde_le_comportement_d_avant(monkeypatch):
    """Ouvert par défaut : jamais priver une cliente directe du plafond de son plan."""
    s = _wire(monkeypatch, RuntimeError("base indisponible"))
    billing.apply_plan_entitlements(7, "business")
    assert s["limits"] == [(7, None)]


def test_une_org_sans_tenant_est_une_cliente_directe(monkeypatch):
    s = _wire(monkeypatch, None)
    billing.apply_plan_entitlements(7, "business")
    assert s["limits"] == [(7, None)]
