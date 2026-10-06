"""Le geste « désactiver un tenant » : ses gardes, ses deux faces, sa réversibilité.

oto-backend#1165. La capacité `admin.tenant_disablement` (REST
`POST /api/admin/tenants/{slug}/disablement`) et `oto_admin_tenant op=disable|enable`
partagent UN handler. Ce banc éprouve les refus déclarés (tenant inconnu, tenant
primaire, motif, soi-même), le palier super admin, et ce que la réponse rend. Le SQL de
la révocation est éprouvé contre PostgreSQL dans `test_tenant_desactivation_live.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import registry
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities import tenant_desactivation as td
from oto_mcp.capabilities import tenants_admin


@pytest.fixture
def tenants(monkeypatch):
    """`acme` (id 7) et le primaire (id 1) ; les écritures sont relevées."""
    lignes = {"acme": {"id": 7, "slug": "acme", "disabled_at": None},
              "oto": {"id": 1, "slug": "oto", "disabled_at": None}}
    gestes = []
    monkeypatch.setattr(td.db, "tenant_ligne", lambda slug: lignes.get(slug))

    def _desactiver(slug, *, by, reason):
        gestes.append(("disable", slug, by, reason))
        return {"slug": slug, "disabled_at": "2026-10-06T10:00:00", "disabled_by": by,
                "disabled_reason": reason, "changed": True,
                "revoked": {"user": 2, "delegation": 1}, "accounts_cut": 3,
                "aliases_cut": 1, "orgs_suspended": [11, 12],
                "orgs_already_suspended": 1}
    monkeypatch.setattr(td.db, "desactiver_tenant", _desactiver)
    monkeypatch.setattr(td.db, "reactiver_tenant",
                        lambda slug: gestes.append(("enable", slug))
                        or {"changed": True, "orgs_resumed": [11, 12]})
    from oto_mcp import server, org_suspension
    monkeypatch.setattr(server, "reload_tenant_registry",
                        lambda: gestes.append(("reload",)) or {})
    # Le geste fait relire la liste des orgs suspendues à CE processus : relevé aussi.
    monkeypatch.setattr(org_suspension, "invalider",
                        lambda: gestes.append(("invalider",)))
    return gestes


def _ctx(sub="op-1"):
    return ResolvedCtx(sub=sub, role="super_admin")


def _refus(inp, ctx=None):
    with pytest.raises(AuthzDenied) as leve:
        td._disablement(ctx or _ctx(), td.TenantDisablementInput(**inp))
    return leve.value.status, leve.value.code


def test_un_tenant_inconnu_est_un_404_nomme(tenants):
    assert _refus({"slug": "inconnu", "op": "disable", "reason": "x"}) == \
        (404, "unknown_tenant")
    assert tenants == []


def test_le_tenant_primaire_ne_se_desactive_pas(tenants):
    assert _refus({"slug": "oto", "op": "disable", "reason": "x"}) == \
        (409, "primary_tenant")
    # …ni ne se « réactive » : il n'a pas d'état à lever.
    assert _refus({"slug": "oto", "op": "enable"}) == (409, "primary_tenant")
    assert tenants == []


def test_la_ligne_1_est_le_primaire_quel_que_soit_son_slug(tenants, monkeypatch):
    """La garde tient sur l'IDENTIFIANT, pas seulement sur le slug déclaré : une base
    dont la ligne 1 porterait un autre nom reste protégée."""
    monkeypatch.setattr(td.db, "tenant_ligne",
                        lambda slug: {"id": 1, "slug": slug, "disabled_at": None})
    assert _refus({"slug": "acme", "op": "disable", "reason": "x"}) == \
        (409, "primary_tenant")


@pytest.mark.parametrize("motif", [None, "", "   "])
def test_desactiver_exige_un_motif(tenants, motif):
    assert _refus({"slug": "acme", "op": "disable", "reason": motif}) == \
        (400, "missing_reason")
    assert tenants == []


def test_un_motif_trop_long_est_refuse_entier(tenants):
    assert _refus({"slug": "acme", "op": "disable", "reason": "x" * 501}) == \
        (400, "reason_too_long")


def test_on_ne_desactive_pas_son_propre_tenant(tenants):
    assert _refus({"slug": "acme", "op": "disable", "reason": "x"},
                  ctx=_ctx("acme:op-du-tenant")) == (409, "self_tenant")
    # Un préfixe voisin n'est pas le même tenant.
    td._disablement(_ctx("acme-x:op"), td.TenantDisablementInput(
        slug="acme", op="disable", reason="x"))


def test_desactiver_rend_les_compteurs_par_type(tenants):
    out = td._disablement(_ctx(), td.TenantDisablementInput(
        slug="acme", op="disable", reason="contrat terminé"))
    assert tenants == [("disable", "acme", "op-1", "contrat terminé"), ("invalider",),
                       ("reload",)]
    assert out["disabled"] is True and out["changed"] is True
    assert out["registry_reloaded"] is True
    assert out["revoked"] == {"user": 2, "delegation": 1}
    assert out["accounts_cut"] == 3 and out["aliases_cut"] == 1
    # Le nombre d'orgs suspendues par CE geste, leurs ids, et celles qu'il n'a pas touchées.
    assert out["orgs_suspended"] == 2 and out["orgs_suspended_ids"] == [11, 12]
    assert out["orgs_already_suspended"] == 1
    # Ce qui n'est pas stocké est NOMMÉ, pas compté à zéro.
    assert "dashboard_session_jwt" in out["refused_not_stored"]
    assert "mcp_oauth_refresh_token" in out["refused_not_stored"]
    td.TenantDisablementOut(**out)          # la forme servie est la forme déclarée


def test_reactiver_ne_rend_aucun_jeton(tenants):
    out = td._disablement(_ctx(), td.TenantDisablementInput(slug="acme", op="enable"))
    assert tenants == [("enable", "acme"), ("invalider",), ("reload",)]
    assert out["disabled"] is False and out["changed"] is True
    assert out.get("revoked", {}) == {}
    assert out["orgs_resumed"] == 2 and out["orgs_resumed_ids"] == [11, 12]
    td.TenantDisablementOut(**out)


def test_la_console_mcp_et_la_route_rest_partagent_le_handler(tenants):
    out = tenants_admin._console(_ctx(), tenants_admin.TenantConsoleInput(
        op="disable", slug="acme", reason="contrat terminé"))
    assert out["disablement"]["revoked"] == {"user": 2, "delegation": 1}
    out = tenants_admin._console(_ctx(), tenants_admin.TenantConsoleInput(
        op="enable", slug="acme"))
    assert out["disablement"]["disabled"] is False
    assert [g[0] for g in tenants] == ["disable", "invalider", "reload",
                                       "enable", "invalider", "reload"]


def test_un_registre_illisible_ne_defait_pas_le_geste_et_le_dit(tenants, monkeypatch):
    """L'état est écrit et la garde d'identité le lit en BASE : un rechargement raté ne
    rouvre rien. Il est DIT (`registry_reloaded=false`), pas avalé."""
    from oto_mcp import server

    def _boum():
        raise RuntimeError("base illisible")
    monkeypatch.setattr(server, "reload_tenant_registry", _boum)
    out = td._disablement(_ctx(), td.TenantDisablementInput(
        slug="acme", op="disable", reason="contrat terminé"))
    assert out["disabled"] is True and out["registry_reloaded"] is False


def test_le_palier_est_super_admin_sur_les_deux_faces():
    from oto_mcp.capabilities._authz import SUPER_ADMIN
    cap = next(c for c in registry.CAPABILITIES if c.key == "admin.tenant_disablement")
    assert cap.authz is SUPER_ADMIN
    assert (cap.rest.verb, cap.rest.path) == \
        ("POST", "/api/admin/tenants/{slug}/disablement")
    import inspect
    console = next(c for c in registry.CAPABILITIES if c.key == "admin.tenant_console")
    par_op = inspect.getclosurevars(console.authz).nonlocals["by_op"]
    for op in ("disable", "enable"):
        assert par_op[op] is SUPER_ADMIN, op
