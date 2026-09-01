"""Lire les PAGES d'un projet publié depuis son endpoint MCP (feedback #310).

Deux invariants : le tool n'est LISTÉ que si le propriétaire a explicitement exposé
les pages (sinon on reproduit #193 à l'envers — visible et refusé à 100 %), et le
destinataire ne peut que LIRE, uniquement CE projet.
"""
import pytest

from oto_mcp import subdomain_project as sp
from oto_mcp.capabilities.docs import common as docs_common
from oto_mcp.capabilities._authz import PROJECT_SHARED_READ
from oto_mcp.capabilities._types import AuthzDenied, RawCtx


def _ctx(**kw):
    base = dict(project_id=169, org_id=2,
                tools=frozenset({"oto_doc", "oto_search", "fr_get"}))
    base.update(kw)
    return sp.AnonContext(**base)


def _with_ctx(monkeypatch, ctx):
    monkeypatch.setattr(sp, "current_anon_context", lambda: ctx)
    monkeypatch.setattr(sp, "current_anon_project_id",
                        lambda: ctx.project_id if ctx else None)
    monkeypatch.setattr(sp, "current_anon_docs_exposed",
                        lambda: bool(ctx and ctx.docs_exposed))


def test_docs_tool_hidden_until_explicitly_exposed(monkeypatch):
    _with_ctx(monkeypatch, _ctx(docs_exposed=False))
    allow = sp.current_allowlist()
    assert "oto_doc" not in allow          # pas listé plutôt que listé-et-cassé
    assert "fr_get" in allow


def test_docs_tool_listed_when_exposed(monkeypatch):
    _with_ctx(monkeypatch, _ctx(docs_exposed=True))
    assert "oto_doc" in sp.current_allowlist()


def test_search_and_app_never_served_anonymously(monkeypatch):
    _with_ctx(monkeypatch, _ctx(docs_exposed=True))
    allow = sp.current_allowlist()
    assert "oto_search" not in allow       # jamais résolvable sans sub → jamais listé


def test_authz_refuses_without_the_optin(monkeypatch):
    _with_ctx(monkeypatch, _ctx(docs_exposed=False))
    with pytest.raises(AuthzDenied) as e:
        PROJECT_SHARED_READ(RawCtx(sub=None), None)
    assert e.value.status == 401


def test_authz_resolves_anonymous_reader_when_exposed(monkeypatch):
    _with_ctx(monkeypatch, _ctx(docs_exposed=True))
    monkeypatch.setattr(sp, "current_anon_org", lambda: 2)
    ctx = PROJECT_SHARED_READ(RawCtx(sub=None), None)
    assert ctx.sub is None and ctx.org_id == 2


def test_reader_is_scoped_to_the_published_project(monkeypatch):
    _with_ctx(monkeypatch, _ctx(docs_exposed=True))
    assert docs_common.can(None, 169, "read") is True
    assert docs_common.can(None, 170, "read") is False    # autre projet de l'org
    assert docs_common.can(None, 169, "write") is False   # lecture seule
