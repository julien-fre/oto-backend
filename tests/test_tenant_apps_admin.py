"""L'app OAuth d'un tenant sur SA surface admin — scopée à son slug.

Ce que Julien a demandé en clair (23/09/2026) : « my credentials should not be used by
anyone else ». Deux faces à tenir pour que ce soit vrai :

1. **L'usage** : `google_oauth.app_for` lit le slug SUR LE SUB — figé dans
   `tests/auth/test_google_tenant_app.py` (un autre tenant, un sub nu, ne voient jamais
   l'app de Tulina).
2. **La pose** (ici) : la clé de l'app est le slug DE LA ROUTE, et la route n'accepte que
   l'admin de CE tenant (ou l'opérateur). Un admin de `pilote` ne pose, ne lit, ne retire
   rien sous `tulina`. La pose est REST seule (un secret brut ne traverse pas un outil),
   et la réponse nomme le rappel EXACT à déclarer chez le fournisseur.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from oto_mcp import access, credentials_store, tenancy
from oto_mcp.auth import google as google_oauth  # noqa: F401 — déclare le flux google
from oto_mcp.capabilities import _authz
from oto_mcp.capabilities import tenant_apps as tap
from oto_mcp.capabilities import tenant_keys as tk
from oto_mcp.capabilities._types import AuthzDenied, RawCtx, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES

CTX = ResolvedCtx(sub="operateur", role="super_admin")
TULINA = "tulina"
RAPPEL_TULINA = "https://mcp.tulina.ai/api/google/oauth/callback"


def _cap(key: str):
    return next(c for c in CAPABILITIES if c.key == key)


@pytest.fixture
def registre(monkeypatch):
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.oto.cx")
    monkeypatch.setattr(tk.db, "tenant_exists", lambda slug: slug in (TULINA, "pilote", "oto"))
    entree = tenancy.TenantIssuer(slug=TULINA, issuer="https://auth.tulina.ai/oidc",
                                  jwks_uri="https://auth.tulina.ai/oidc/jwks",
                                  hosts=("mcp.tulina.ai",))
    pilote = tenancy.TenantIssuer(slug="pilote", issuer="https://auth.pilote.test/oidc",
                                  jwks_uri="https://auth.pilote.test/oidc/jwks", hosts=())
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(
        {entree.issuer: entree, pilote.issuer: pilote}))


# ── 1. les surfaces et leurs planchers ────────────────────────────────────────

def test_la_pose_est_rest_seule_et_porte_sa_sortie():
    for key, verbe, chemin in (
            ("admin.tenant_apps", "GET", "/api/admin/tenants/{slug}/apps"),
            ("admin.tenant_app_set", "PUT", "/api/admin/tenants/{slug}/apps/{connector}"),
            ("admin.tenant_app_clear", "DELETE", "/api/admin/tenants/{slug}/apps/{connector}")):
        cap = _cap(key)
        assert cap.mcp is None, f"{key} : un secret brut ne traverse pas un appel d'outil"
        assert (cap.rest.verb, cap.rest.path) == (verbe, chemin)
        assert cap.Output is not None


def test_l_admin_du_tenant_passe_chez_lui_et_nulle_part_ailleurs(registre, monkeypatch):
    """Le cœur du « scopé tenant » côté pose : le rôle se lit sur le sub qualifié,
    et ne vaut que pour le slug de la route."""
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: False)
    monkeypatch.setattr(access, "is_super_admin", lambda sub: False)
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(_authz.db, "is_tenant_admin",
                        lambda slug, sub: (slug, sub) == (TULINA, "tulina:admin"))
    for key in ("admin.tenant_apps", "admin.tenant_app_set", "admin.tenant_app_clear"):
        rule = _cap(key).authz
        assert rule(RawCtx(sub="tulina:admin"), SimpleNamespace(slug=TULINA)).sub == "tulina:admin"
        with pytest.raises(AuthzDenied) as e:
            rule(RawCtx(sub="tulina:admin"), SimpleNamespace(slug="pilote"))
        assert e.value.status == 403, key
        with pytest.raises(AuthzDenied):
            rule(RawCtx(sub="tulina:membre"), SimpleNamespace(slug=TULINA))
        with pytest.raises(AuthzDenied):
            rule(RawCtx(sub="nu-sub"), SimpleNamespace(slug=TULINA))


def test_l_operateur_lit_mais_seul_le_super_admin_pose(registre, monkeypatch):
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(access, "is_super_admin", lambda sub: False)
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "admin")
    monkeypatch.setattr(_authz.db, "is_tenant_admin", lambda slug, sub: False)
    assert _cap("admin.tenant_apps").authz(RawCtx(sub="op"), SimpleNamespace(slug=TULINA)).sub == "op"
    for key in ("admin.tenant_app_set", "admin.tenant_app_clear"):
        with pytest.raises(AuthzDenied) as e:
            _cap(key).authz(RawCtx(sub="op"), SimpleNamespace(slug=TULINA))
        assert e.value.status == 403


# ── 2. la pose ────────────────────────────────────────────────────────────────

def test_la_pose_ecrit_sous_le_slug_de_la_route_et_nomme_le_rappel_du_tenant(
        registre, monkeypatch):
    vu = {}
    monkeypatch.setattr(credentials_store, "set_editor_app",
                        lambda connector, key, fields, set_by=None:
                        vu.update(connector=connector, key=key, fields=fields, set_by=set_by))
    out = tap._set_app(CTX, tap.TenantAppSetInput(
        slug=TULINA, connector="google", client_id=" cid ", client_secret=" sec "))
    assert vu == {"connector": "google", "key": "tenant:tulina",
                  "fields": {"client_id": "cid", "client_secret": "sec"}, "set_by": "operateur"}
    assert out == {"ok": True, "slug": TULINA, "connector": "google",
                   "callback_url": RAPPEL_TULINA}
    assert "sec" not in repr(out).replace("secret", "")


def test_sans_host_declare_le_rappel_rendu_est_le_notre(registre, monkeypatch):
    monkeypatch.setattr(credentials_store, "set_editor_app", lambda *a, **k: None)
    out = tap._set_app(CTX, tap.TenantAppSetInput(
        slug="pilote", connector="google", client_id="cid", client_secret="sec"))
    assert out["callback_url"] == "https://mcp.oto.cx/api/google/oauth/callback"


def test_le_tenant_primaire_est_refuse(registre, monkeypatch):
    ecrit = []
    monkeypatch.setattr(credentials_store, "set_editor_app", lambda *a, **k: ecrit.append(a))
    with pytest.raises(AuthzDenied) as e:
        tap._set_app(CTX, tap.TenantAppSetInput(
            slug="oto", connector="google", client_id="cid", client_secret="sec"))
    assert e.value.status == 400 and e.value.code == "primary_tenant_app"
    assert ecrit == []


def test_un_connecteur_qui_ne_lit_pas_l_app_du_tenant_est_refuse(registre, monkeypatch):
    ecrit = []
    monkeypatch.setattr(credentials_store, "set_editor_app", lambda *a, **k: ecrit.append(a))
    with pytest.raises(AuthzDenied) as e:
        tap._set_app(CTX, tap.TenantAppSetInput(
            slug=TULINA, connector="hunter", client_id="cid", client_secret="sec"))
    assert e.value.status == 400 and e.value.code == "tenant_app_unsupported"
    assert ecrit == []


def test_un_champ_vide_est_refuse_en_400(registre, monkeypatch):
    with pytest.raises(AuthzDenied) as e:
        tap._set_app(CTX, tap.TenantAppSetInput(
            slug=TULINA, connector="google", client_id="cid", client_secret="  "))
    assert e.value.status == 400 and e.value.code == "invalid_app"


def test_un_slug_inconnu_est_un_404(registre, monkeypatch):
    monkeypatch.setattr(tk.db, "tenant_exists", lambda slug: False)
    with pytest.raises(AuthzDenied) as e:
        tap._set_app(CTX, tap.TenantAppSetInput(
            slug="fantome", connector="google", client_id="cid", client_secret="sec"))
    assert e.value.status == 404 and e.value.code == "unknown_tenant"


# ── 3. lecture et retrait ─────────────────────────────────────────────────────

def test_la_liste_ne_montre_que_les_apps_du_slug_et_aucun_secret(registre, monkeypatch):
    monkeypatch.setattr(credentials_store, "list_editor_apps", lambda connector=None: [
        {"connector": "google", "data_center": "tenant:tulina", "set_at": "2026-09-23T10:00:00+00:00"},
        {"connector": "google", "data_center": "tenant:pilote", "set_at": "2026-09-23T10:00:00+00:00"},
        {"connector": "zoho", "data_center": "eu", "set_at": "2026-09-23T10:00:00+00:00"}])
    out = tap._list_apps(CTX, tap.TenantAppsInput(slug=TULINA))
    assert out["slug"] == TULINA and out["host"] == "mcp.tulina.ai"
    assert out["apps"] == [{"connector": "google", "set_at": "2026-09-23T10:00:00+00:00",
                            "callback_url": RAPPEL_TULINA}]
    assert "google" in out["eligible"]
    assert "client" not in repr(out)


def test_le_retrait_est_idempotent(registre, monkeypatch):
    monkeypatch.setattr(credentials_store, "clear_editor_app", lambda connector, key: False)
    out = tap._clear_app(CTX, tap.TenantAppClearInput(slug=TULINA, connector="google"))
    assert out == {"ok": True, "slug": TULINA, "connector": "google", "deleted": False}
