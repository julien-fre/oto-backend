"""L-clés, PR 1 — la clé de connecteur d'un tenant sur la surface admin.

Trois choses à tenir, et une décision qui se relit mal dans le code seul :

1. **La pose d'un secret brut est REST seule** (`PUT /api/admin/tenants/{slug}/keys/
   {provider}`), comme les clés d'org et les clés plateforme depuis le 2026-06-25 —
   un secret ne traverse pas un appel d'outil. La console `oto_admin_tenant` LISTE et
   RETIRE ; elle ne pose pas.
2. **Le plancher.** Lire = `PLATFORM_ADMIN` (comme les autres lentilles) ; poser et
   retirer = `SUPER_ADMIN` (ça change ce que la résolution sert à tout un tenant, comme
   `reload` change ce que le process authentifie) — OU, depuis la PR 2, l'admin du
   tenant lui-même (`TENANT_ADMIN_OF`, testé dans `test_tenant_admin_role.py`).
3. **Le tenant primaire est refusé** : ses clés partagées sont les instances plateforme.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oto_mcp import access, credentials_store, tenant_vault
from oto_mcp.capabilities import tenant_keys as tk
from oto_mcp.capabilities import tenants_admin as ta
from oto_mcp.capabilities import _authz
from oto_mcp.capabilities._types import AuthzDenied, RawCtx, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES

CTX = ResolvedCtx(sub="operateur", role="super_admin")
PILOTE = "pilote"


def _cap(key: str):
    return next(c for c in CAPABILITIES if c.key == key)


@pytest.fixture
def tenant_connu(monkeypatch):
    monkeypatch.setattr(tk.db, "tenant_exists", lambda slug: slug in (PILOTE, "oto"))


# ── 1. les surfaces et leurs planchers ────────────────────────────────────────

@pytest.fixture
def operateur(monkeypatch):
    """Un admin plateforme (non super), sans rôle de tenant."""
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(access, "is_super_admin", lambda sub: False)
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "admin")
    monkeypatch.setattr(_authz.db, "is_tenant_admin", lambda slug, sub: False)


def test_la_pose_est_rest_seule_et_super_admin(operateur):
    pose = _cap("admin.tenant_key_set")
    assert pose.mcp is None, "un secret brut ne traverse pas un appel d'outil (25/06)"
    assert (pose.rest.verb, pose.rest.path) == ("PUT", "/api/admin/tenants/{slug}/keys/{provider}")
    with pytest.raises(AuthzDenied) as e:
        pose.authz(RawCtx(sub="op"), SimpleNamespace(slug=PILOTE))
    assert e.value.status == 403
    assert pose.Output is not None


def test_la_lecture_est_platform_admin_le_retrait_super_admin(operateur):
    liste, retrait = _cap("admin.tenant_keys"), _cap("admin.tenant_key_clear")
    assert (liste.rest.verb, liste.rest.path) == ("GET", "/api/admin/tenants/{slug}/keys")
    assert liste.authz(RawCtx(sub="op"), SimpleNamespace(slug=PILOTE)).sub == "op"
    assert (retrait.rest.verb, retrait.rest.path) == (
        "DELETE", "/api/admin/tenants/{slug}/keys/{provider}")
    with pytest.raises(AuthzDenied) as e:
        retrait.authz(RawCtx(sub="op"), SimpleNamespace(slug=PILOTE))
    assert e.value.status == 403


def test_la_console_liste_et_retire_pour_les_bons_planchers(monkeypatch):
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "admin")
    rule = _cap("admin.tenant_console").authz
    assert rule(RawCtx(sub="op"), SimpleNamespace(op="keys")).sub == "op"
    with pytest.raises(AuthzDenied) as ei:
        rule(RawCtx(sub="op"), SimpleNamespace(op="key_clear"))
    assert ei.value.status == 403


def test_la_console_n_a_pas_d_op_de_pose():
    with pytest.raises(ValidationError):
        ta.TenantConsoleInput(op="key_set", slug=PILOTE, provider="hunter")


# ── 2. la pose ────────────────────────────────────────────────────────────────

def test_un_slug_inconnu_est_un_404(monkeypatch):
    monkeypatch.setattr(tk.db, "tenant_exists", lambda slug: False)
    with pytest.raises(AuthzDenied) as e:
        tk._set_key(CTX, tk.TenantKeySetInput(slug="fantome", provider="hunter", api_key="k"))
    assert e.value.status == 404 and e.value.code == "unknown_tenant"


def test_le_tenant_primaire_est_refuse(tenant_connu, monkeypatch):
    ecrit = []
    monkeypatch.setattr(tenant_vault, "set_tenant_secret",
                        lambda *a, **k: ecrit.append(a))
    with pytest.raises(AuthzDenied) as e:
        tk._set_key(CTX, tk.TenantKeySetInput(slug="oto", provider="hunter", api_key="k"))
    assert e.value.status == 400 and e.value.code == "primary_tenant_key"
    assert ecrit == []


def test_la_pose_ecrit_au_coffre_et_n_echo_rien(tenant_connu, monkeypatch):
    vu = {}
    monkeypatch.setattr(tenant_vault, "set_tenant_secret",
                        lambda slug, provider, secret, set_by=None, meta=None, account="":
                        vu.update(slug=slug, provider=provider, secret=secret,
                                  set_by=set_by, account=account))
    monkeypatch.setattr(credentials_store, "guard_account_write", lambda *a, **k: None)
    out = tk._set_key(CTX, tk.TenantKeySetInput(slug=PILOTE, provider="hunter",
                                                api_key=" k-secret "))
    assert out == {"ok": True, "slug": PILOTE, "provider": "hunter"}
    assert vu == {"slug": PILOTE, "provider": "hunter", "secret": "k-secret",
                  "set_by": "operateur", "account": ""}
    assert "k-secret" not in repr(out)


def test_un_provider_non_partageable_est_refuse_en_400(tenant_connu, monkeypatch):
    """`silae` est byo_user seul : la cascade ne lit jamais un palier partagé pour lui.
    Accepter la clé écrirait une ligne que personne n'irait lire (#409)."""
    ecrit = []
    monkeypatch.setattr(tenant_vault, "set_tenant_secret", lambda *a, **k: ecrit.append(a))
    with pytest.raises(AuthzDenied) as e:
        tk._set_key(CTX, tk.TenantKeySetInput(slug=PILOTE, provider="silae",
                                              fields={"a": "b"}))
    assert e.value.status == 400 and ecrit == []


def test_un_credential_vide_est_refuse_en_nommant_la_faute(tenant_connu, monkeypatch):
    monkeypatch.setattr(credentials_store, "guard_account_write", lambda *a, **k: None)
    with pytest.raises(AuthzDenied) as e:
        tk._set_key(CTX, tk.TenantKeySetInput(slug=PILOTE, provider="hunter", api_key=""))
    assert e.value.status == 400 and e.value.code == "empty_api_key"


# ── 3. lecture et retrait ─────────────────────────────────────────────────────

def test_la_liste_ne_porte_aucun_secret(tenant_connu, monkeypatch):
    monkeypatch.setattr(tenant_vault, "list_tenant_secrets", lambda slug: [
        {"provider": "hunter", "account": "", "set_by": "operateur",
         "set_at": "2026-08-29T10:00:00+00:00"}])
    out = tk._list_keys(CTX, tk.TenantKeysInput(slug=PILOTE))
    assert out["slug"] == PILOTE and [k["provider"] for k in out["keys"]] == ["hunter"]
    assert "secret" not in repr(out).lower()


def test_la_liste_d_un_slug_inconnu_est_un_404(monkeypatch):
    monkeypatch.setattr(tk.db, "tenant_exists", lambda slug: False)
    with pytest.raises(AuthzDenied) as e:
        tk._list_keys(CTX, tk.TenantKeysInput(slug="fantome"))
    assert e.value.status == 404


def test_le_retrait_est_idempotent(tenant_connu, monkeypatch):
    monkeypatch.setattr(tenant_vault, "delete_tenant_secret",
                        lambda slug, provider, account="": False)
    out = tk._clear_key(CTX, tk.TenantKeyClearInput(slug=PILOTE, provider="hunter"))
    assert out == {"ok": True, "slug": PILOTE, "provider": "hunter", "account": "",
                   "deleted": False}


# ── 4. la console ─────────────────────────────────────────────────────────────

def test_la_console_dispatch_keys_et_key_clear(tenant_connu, monkeypatch):
    monkeypatch.setattr(tenant_vault, "list_tenant_secrets", lambda slug: [])
    monkeypatch.setattr(tenant_vault, "delete_tenant_secret",
                        lambda slug, provider, account="": True)
    out = ta._console(CTX, ta.TenantConsoleInput(op="keys", slug=PILOTE))
    assert out["keys"] == {"slug": PILOTE, "keys": []}
    out = ta._console(CTX, ta.TenantConsoleInput(op="key_clear", slug=PILOTE,
                                                 provider="hunter"))
    assert out["key_clear"]["deleted"] is True


def test_la_console_exige_slug_et_provider():
    with pytest.raises(AuthzDenied) as e:
        ta._console(CTX, ta.TenantConsoleInput(op="keys"))
    assert e.value.code == "missing_slug"
    with pytest.raises(AuthzDenied) as e:
        ta._console(CTX, ta.TenantConsoleInput(op="key_clear", slug=PILOTE))
    assert e.value.code == "missing_provider"
