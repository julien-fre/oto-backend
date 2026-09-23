"""Le don d'option sur un TENANT (23/09/2026).

Un partenaire hébergé sort les agents hébergés de bêta pour TOUTE sa population :
poser `beta`/`agents` org par org est un drapeau qu'on oublie sur la 61ᵉ org et sur
chaque org qui naît demain. D'où la troisième entité de `oto_admin_set_option`
(`entity_type='tenant'`, `entity_id=<slug>`), lue en fin de cascade par
`access.org_has_option` (`tests/test_billing_b4_entitlement.py` couvre la lecture) —
et l'option `agents`, à part de `beta`, pour que la sortie de bêta des agents
n'entraîne pas `oto_node`, `oto_function`… (`tests/test_outils_beta.py`).

Ici : ce que la CAPACITÉ admin fait d'un don tenant, et le seam `hosted_agents_open`.
"""
from __future__ import annotations

import pytest

from oto_mcp import tool_visibility as TV
from oto_mcp.capabilities import users_admin as ua
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

ADMIN = "sub-admin"
CTX = ResolvedCtx(sub=ADMIN, org_id=7)


@pytest.fixture
def sans_db(monkeypatch):
    poses, retraits = [], []
    monkeypatch.setattr(ua.db, "tenant_exists", lambda slug: slug in ("oto", "acme"))
    monkeypatch.setattr(ua.db, "set_option_comp",
                        lambda et, eid, opt, granted_by=None, expires_at=None:
                        poses.append((et, eid, opt, expires_at)))
    monkeypatch.setattr(ua.db, "clear_option_comp",
                        lambda et, eid, opt: retraits.append((et, eid, opt)) or True)
    monkeypatch.setattr(ua.db, "org_tenant_slug", lambda oid: "acme" if oid == 7 else "oto")
    monkeypatch.setattr(ua.access, "current_org", lambda sub: 7)
    return poses, retraits


def _set(**kw):
    return ua._set_option(CTX, ua.OptionInput(**kw))


def test_un_don_tenant_se_pose_sur_le_slug_et_ne_grante_aucune_cle(sans_db):
    poses, _ = sans_db
    out = _set(entity_type="tenant", entity_id="acme", option="agents", on=True)
    assert poses == [("tenant", "acme", "agents", ua.db.KEEP_EXPIRY)]
    assert out["entity_type"] == "tenant" and out["on"] is True
    # `agents` n'est pas un connecteur : rien à composer côté clé plateforme.
    assert out["platform_key"] is None


def test_un_don_tenant_se_retire(sans_db):
    _, retraits = sans_db
    _set(entity_type="tenant", entity_id="acme", option="agents", on=False)
    assert retraits == [("tenant", "acme", "agents")]


def test_un_tenant_inconnu_est_refuse_404(sans_db):
    with pytest.raises(AuthzDenied) as e:
        _set(entity_type="tenant", entity_id="nulle-part", option="agents", on=True)
    assert (e.value.status, e.value.code) == (404, "unknown_tenant")


def test_une_echeance_sur_un_tenant_TIERS_est_refusee(sans_db):
    """Même règle que l'org d'un partenaire, un cran plus haut : borner le don d'un
    tenant tiers, c'est borner d'un coup tous ses clients."""
    with pytest.raises(AuthzDenied) as e:
        _set(entity_type="tenant", entity_id="acme", option="agents", on=True,
             expires_at="2026-12-31")
    assert (e.value.status, e.value.code) == (409, "partner_org_out_of_scope")


def test_une_echeance_sur_le_tenant_PRIMAIRE_passe(sans_db):
    poses, _ = sans_db
    _set(entity_type="tenant", entity_id="oto", option="agents", on=True,
         expires_at="2026-12-31")
    assert poses[0][:3] == ("tenant", "oto", "agents") and poses[0][3] is not None


def test_l_effet_atterrit_ici_si_mon_org_est_dans_ce_tenant(sans_db):
    assert _set(entity_type="tenant", entity_id="acme", option="agents",
                on=True)["visible_next_session"] is False
    assert _set(entity_type="tenant", entity_id="oto", option="agents",
                on=True)["visible_next_session"] is True


def test_un_connecteur_plateforme_ne_se_grante_pas_au_tenant(sans_db, monkeypatch):
    """Une clé plateforme se grante par compte ou par org (ADR 0044 §F R4) : le comp
    tenant est posé, la clé non — et la réponse le DIT."""
    monkeypatch.setattr(ua.providers, "connector_for_provider",
                        lambda p: type("C", (), {"auth_modes": ("platform", "byo")})())
    out = _set(entity_type="tenant", entity_id="acme", option="unipile", on=True)
    assert out["platform_key"] == {
        "granted": False, "reason": "tenant_scope_unsupported",
        "hint": out["platform_key"]["hint"]}
    assert _set(entity_type="tenant", entity_id="acme", option="unipile",
                on=False)["platform_key"] is None


# ── Le seam des agents hébergés ─────────────────────────────────────────────

def test_hosted_agents_open_lit_agents_puis_beta_avec_la_portee_transmise(monkeypatch):
    vus = []

    def _has_option(sub, option, **kw):
        vus.append((sub, option, kw))
        return option == "beta"

    monkeypatch.setattr(TV.access if hasattr(TV, "access") else
                        __import__("oto_mcp.access", fromlist=["x"]),
                        "has_option", _has_option)
    assert TV.hosted_agents_open("u", org=42) is True
    assert vus == [("u", "agents", {"org": 42}), ("u", "beta", {"org": 42})]
    vus.clear()
    assert TV.hosted_agents_open("u") is True
    assert vus == [("u", "agents", {}), ("u", "beta", {})]


def test_hosted_agents_open_ne_capture_rien(monkeypatch):
    import oto_mcp.access as access

    def _boom(sub, option, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(access, "has_option", _boom)
    with pytest.raises(RuntimeError):
        TV.hosted_agents_open("u", org=1)


def test_la_flotte_a_quitte_la_beta_commune():
    assert "oto_fleet" in TV.AGENTS_TOOLS and "oto_fleet" not in TV.BETA_TOOLS
    assert TV.AGENTS_OPTION == "agents" and TV.BETA_OPTION == "beta"
