"""Override par tenant des documents légaux — `legal_docs.docs_for` +
`capabilities/tenant_legal_docs_admin.py` + `me_legal`'s use of both.

Stub de `db.*` (convention repo : pas de vrai PG en unit, cf. test_legal_acceptances.py).
"""
from __future__ import annotations

import pytest

from oto_mcp import db, legal_docs, tenancy
from oto_mcp.capabilities import me_legal, tenant_legal_docs_admin as tld
from oto_mcp.capabilities._authz import PLATFORM_ADMIN
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES

CTX = ResolvedCtx(sub="operateur", org_id=None, role="super_admin")


# ── legal_docs.docs_for ──────────────────────────────────────────────────────

def test_primary_tenant_short_circuits_without_a_db_call(monkeypatch):
    """oto IS the default — no row to look up, ever."""
    def _boom(tenant_slug):
        raise AssertionError("docs_for(oto) hit the DB — it must short-circuit")
    monkeypatch.setattr(db, "get_tenant_legal_docs", _boom)
    assert legal_docs.docs_for(tenancy.PRIMARY_SLUG) is legal_docs.CURRENT_DOCS
    assert legal_docs.docs_for("") is legal_docs.CURRENT_DOCS


def test_tenant_without_override_falls_back_to_platform_default(monkeypatch):
    monkeypatch.setattr(db, "get_tenant_legal_docs", lambda slug: {})
    assert legal_docs.docs_for("tulina") is legal_docs.CURRENT_DOCS


def test_tenant_override_replaces_only_its_own_slug(monkeypatch):
    monkeypatch.setattr(db, "get_tenant_legal_docs", lambda slug: (
        {"terms": {"version": "1.0", "label": "Tulina Terms", "url": "https://tulina.ai/terms"}}
        if slug == "tulina" else {}
    ))
    docs = legal_docs.docs_for("tulina")
    assert docs["terms"] == {"version": "1.0", "label": "Tulina Terms", "url": "https://tulina.ai/terms"}
    # cgv/dpa untouched — Tulina hasn't declared its own.
    assert docs["cgv"] == legal_docs.CURRENT_DOCS["cgv"]
    assert docs["dpa"] == legal_docs.CURRENT_DOCS["dpa"]


# ── me_legal is tenant-aware ──────────────────────────────────────────────────

@pytest.fixture
def acceptances(monkeypatch):
    state: dict[str, dict] = {}
    monkeypatch.setattr(db, "get_legal_acceptances", lambda sub: dict(state))

    def _record(sub, items):
        for slug, version in items:
            state[slug] = {"version": version, "accepted_at": "2026-08-20 10:00:00"}
    monkeypatch.setattr(db, "record_legal_acceptances", _record)
    return state


def test_tulina_sub_sees_tulina_terms_and_still_owes_them(monkeypatch, acceptances):
    monkeypatch.setattr(db, "get_tenant_legal_docs", lambda slug: (
        {"terms": {"version": "1.0", "label": "Tulina Terms", "url": "https://tulina.ai/terms"}}
        if slug == "tulina" else {}
    ))
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(
        tenancy.build("https://auth.oto.ninja/oidc",
                      tenants=[{"slug": "tulina", "issuer": "https://auth.tulina.ai/oidc"}])))

    st = me_legal._get(ResolvedCtx(sub="tulina:u1", org_id=None, role="member"), me_legal._NoInput())
    terms = next(d for d in st["documents"] if d["slug"] == "terms")
    assert terms == {
        "slug": "terms", "version": "1.0", "url": "https://tulina.ai/terms",
        "label": "Tulina Terms", "accepted": False, "accepted_version": None, "accepted_at": None,
    }
    # An oto sub in the SAME process still owes oto's own terms, unaffected.
    oto_st = me_legal._get(ResolvedCtx(sub="u2", org_id=None, role="member"), me_legal._NoInput())
    oto_terms = next(d for d in oto_st["documents"] if d["slug"] == "terms")
    assert oto_terms["version"] == legal_docs.CURRENT_DOCS["terms"]["version"]


def test_accept_records_the_tenants_own_version_not_otos(monkeypatch, acceptances):
    monkeypatch.setattr(db, "get_tenant_legal_docs", lambda slug: (
        {"terms": {"version": "1.0", "label": "Tulina Terms", "url": "https://tulina.ai/terms"}}
        if slug == "tulina" else {}
    ))
    monkeypatch.setattr(tenancy, "_INSTALLED", tenancy.IssuerRegistry(
        tenancy.build("https://auth.oto.ninja/oidc",
                      tenants=[{"slug": "tulina", "issuer": "https://auth.tulina.ai/oidc"}])))

    ctx = ResolvedCtx(sub="tulina:u1", org_id=None, role="member")
    st = me_legal._accept(ctx, me_legal.AcceptInput(context="access"))
    terms = next(d for d in st["documents"] if d["slug"] == "terms")
    assert terms["accepted"] is True and terms["accepted_version"] == "1.0"

    # Oto bumping ITS OWN terms afterwards must not reopen Tulina's gate.
    monkeypatch.setitem(legal_docs.CURRENT_DOCS["terms"], "version", "999.0")
    st2 = me_legal._get(ctx, me_legal._NoInput())
    assert st2["contexts"]["access"]["outstanding"] == []


# ── admin.legal_docs.{list,set,delete} ───────────────────────────────────────

def _caps():
    return [c for c in CAPABILITIES if c.key.startswith("admin.legal_docs")]


def test_every_surface_is_platform_admin():
    caps = _caps()
    assert {c.key for c in caps} == {
        "admin.legal_docs.list", "admin.legal_docs.set", "admin.legal_docs.delete"}
    for c in caps:
        assert c.authz is PLATFORM_ADMIN


@pytest.fixture
def tenant_docs(monkeypatch):
    state: dict[str, dict] = {}
    monkeypatch.setattr(db, "get_tenant_legal_docs", lambda slug: dict(state))

    def _set(slug, doc_slug, version, label, url):
        state[doc_slug] = {"version": version, "label": label, "url": url}
    monkeypatch.setattr(db, "set_tenant_legal_doc", _set)

    def _delete(slug, doc_slug):
        return state.pop(doc_slug, None) is not None
    monkeypatch.setattr(db, "delete_tenant_legal_doc", _delete)
    return state


def test_set_then_list_reports_the_override_and_the_defaults(tenant_docs):
    tld._set(CTX, tld.TenantDocSetInput(
        tenant="tulina", slug="terms", version="1.0",
        label="Tulina Terms", url="https://tulina.ai/terms"))
    out = tld._list(CTX, tld.TenantSlugInput(tenant="tulina"))
    by_slug = {d["slug"]: d for d in out["docs"]}
    assert by_slug["terms"]["overridden"] is True
    assert by_slug["terms"]["version"] == "1.0"
    assert by_slug["cgv"]["overridden"] is False
    assert by_slug["cgv"]["version"] == legal_docs.CURRENT_DOCS["cgv"]["version"]


def test_delete_falls_back_to_the_platform_default(tenant_docs):
    tld._set(CTX, tld.TenantDocSetInput(
        tenant="tulina", slug="terms", version="1.0", label="Tulina Terms", url="https://tulina.ai/terms"))
    out = tld._delete(CTX, tld.TenantDocInput(tenant="tulina", slug="terms"))
    assert out == {"tenant": "tulina", "slug": "terms", "deleted": True}
    by_slug = {d["slug"]: d for d in tld._list(CTX, tld.TenantSlugInput(tenant="tulina"))["docs"]}
    assert by_slug["terms"]["overridden"] is False


def test_unknown_slug_is_rejected(tenant_docs):
    with pytest.raises(AuthzDenied) as exc:
        tld._set(CTX, tld.TenantDocSetInput(
            tenant="tulina", slug="cookies", version="1.0", label="x", url="https://x"))
    assert exc.value.code == "unknown_doc_slug"
