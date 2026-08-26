"""Connecteur TheirStack — offres d'emploi par employeur + technologies (api.theirstack.com).

Verrouille : l'entrée de registre (keyed byo-only, catégorie Prospection, hors socle),
la surface MCP sous le namespace `theirstack` (2 tools, chacun avec une description —
régression du piège f-string-docstring), la jointure tool↔client oto-core (garde
version-skew), la sonde « tester la connexion », et les deux points où le module ne fait
pas que passer le plat : la construction du payload (args typés puis `extra` fusionné EN
DERNIER) et la projection du retour (`full=False` resserre chaque item, l'enveloppe
`metadata` reste ; `full=True` rend le brut).
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import connector_verify, providers
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {"theirstack_jobs_search", "theirstack_companies_search"}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name: t for t in asyncio.run(m._list_tools())}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False))


def _tool(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import theirstack

    m = FastMCP("t")
    theirstack.register(m)
    return asyncio.run(m.get_tool(name))


# --- registre -----------------------------------------------------------------

def test_theirstack_is_keyed_connector_platform_grant_only():
    c = providers.REGISTRY["theirstack"]
    assert c.kind == "tools"
    assert c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org", "platform"})
    assert c.platform_key_open is False           # revente au crédit → grant explicite
    assert c.default_active is False               # deny-by-default
    assert c.default_quota == 0
    assert "theirstack" in providers.KEY_PROVIDERS
    assert c.category == "Prospection"
    assert c.publisher_name == "TheirStack"
    assert c.label == "TheirStack"
    assert providers._LOGO_DOMAIN_BY_CONNECTOR["theirstack"] == "theirstack.com"


def test_theirstack_has_onboarding_doc():
    kinds = {s.kind for s in providers.REGISTRY["theirstack"].doc_sections}
    assert {"prerequisite", "usage"} <= kinds


# --- surface MCP --------------------------------------------------------------

def test_theirstack_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= set(all_tools)
    assert all(namespace_of(t) == "theirstack"
               for t in all_tools if t.startswith("theirstack_"))


def test_theirstack_tools_all_have_descriptions(all_tools):
    for name in EXPECTED_TOOLS:
        assert all_tools[name].description, f"{name} has no description"


def test_docstrings_carry_the_billing_and_coverage_facts(all_tools):
    for name in EXPECTED_TOOLS:
        d = all_tools[name].description
        assert "per company record" in d
        assert "8%" in d and "not an error" in d


def test_verify_probe_registered():
    assert connector_verify.supports("theirstack")


# --- jointure tool ↔ client oto-core (garde version-skew) ---------------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.theirstack.client import TheirStackClient
    for meth in ("search_jobs", "search_companies", "credit_balance"):
        assert callable(getattr(TheirStackClient, meth, None)), \
            f"TheirStackClient.{meth} manquant"


# --- payload + projection (client mocké) --------------------------------------

_JOB = {"id": 1, "job_title": "Comptable", "url": "https://x/1", "date_posted": "2026-08-01",
        "company": "PUIG & FILS", "location": "Perpignan", "description": "long…",
        "salary_string": None, "technology_slugs": ["sap"], "company_object": {"id": "c1"}}
_COMPANY = {"id": "c1", "name": "PUIG & FILS", "domain": "puig.fr", "employee_count": 40,
            "industry": "wholesale", "technology_names": ["SAP"], "technology_slugs": ["sap"],
            "num_jobs": 3, "jobs_found": [], "linkedin_url": "https://linkedin.com/company/x"}


def test_jobs_search_builds_payload_and_projects():
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        inst = cls.return_value
        inst.search_jobs.return_value = {"metadata": {"total_results": 1, "truncated_results": 0},
                                         "data": [_JOB]}
        out = _tool("theirstack_jobs_search").fn(
            company_names=["PUIG & FILS", "  "], job_country_code_or=["FR"], limit=10, page=2,
            extra={"job_title_or": ["comptable"], "limit": 5})

    payload = inst.search_jobs.call_args.args[0]
    assert payload["company_name_or"] == ["PUIG & FILS"]      # vides retirés
    assert payload["posted_at_max_age_days"] == 90            # défaut : satisfait l'exigence API
    assert payload["job_country_code_or"] == ["FR"]
    assert payload["page"] == 2
    assert payload["limit"] == 5                              # `extra` prime (fusionné en dernier)
    assert payload["job_title_or"] == ["comptable"]
    # Projection : l'item est resserré, l'enveloppe reste.
    assert out["metadata"] == {"total_results": 1, "truncated_results": 0}
    assert out["data"] == [{"company": "PUIG & FILS", "job_title": "Comptable",
                            "date_posted": "2026-08-01", "url": "https://x/1",
                            "location": "Perpignan"}]


def test_jobs_search_full_returns_raw_records():
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        cls.return_value.search_jobs.return_value = {"metadata": {}, "data": [_JOB]}
        out = _tool("theirstack_jobs_search").fn(company_names=["PUIG & FILS"], full=True)
    assert out["data"] == [_JOB]


def test_companies_search_builds_payload_and_projects():
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        inst = cls.return_value
        inst.search_companies.return_value = {
            "metadata": {"total_companies": 1, "truncated_companies": 0}, "data": [_COMPANY]}
        out = _tool("theirstack_companies_search").fn(
            company_names=["PUIG & FILS"], company_country_code_or=["FR"],
            extra={"company_technology_slug_or": ["sap"]})

    payload = inst.search_companies.call_args.args[0]
    assert payload == {"page": 0, "limit": 25, "company_name_or": ["PUIG & FILS"],
                       "company_country_code_or": ["FR"],
                       "company_technology_slug_or": ["sap"]}
    assert "posted_at_max_age_days" not in payload
    assert out["metadata"]["truncated_companies"] == 0
    assert out["data"] == [{"name": "PUIG & FILS", "domain": "puig.fr", "employee_count": 40,
                            "industry": "wholesale", "technology_names": ["SAP"]}]


def test_companies_search_empty_result_is_not_an_error():
    """≈ 8 % de couverture sur les petits grossistes : `data: []` doit revenir tel quel."""
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        cls.return_value.search_companies.return_value = {
            "metadata": {"total_results": 0, "truncated_results": 0}, "data": []}
        out = _tool("theirstack_companies_search").fn(company_names=["Inconnue SARL"])
    assert out == {"metadata": {"total_results": 0, "truncated_results": 0}, "data": []}


def test_invalid_args_never_hit_the_client():
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        with pytest.raises(McpError):
            _tool("theirstack_jobs_search").fn(company_names="PUIG")       # pas une liste
        with pytest.raises(McpError):
            _tool("theirstack_jobs_search").fn(extra=["not", "a", "dict"])
        with pytest.raises(McpError):
            _tool("theirstack_companies_search").fn(limit=0)
    cls.return_value.search_jobs.assert_not_called()
    cls.return_value.search_companies.assert_not_called()


def test_upstream_402_becomes_an_actionable_tool_error():
    from oto.tools.common.errors import UpstreamHTTPError
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        cls.return_value.search_companies.side_effect = UpstreamHTTPError(
            402, {"detail": "Not enough credits"}, service="theirstack")
        with pytest.raises(McpError, match="crédits"):
            _tool("theirstack_companies_search").fn(company_names=["X"])


def test_upstream_422_names_the_required_filters():
    from oto.tools.common.errors import UpstreamHTTPError
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        cls.return_value.search_jobs.side_effect = UpstreamHTTPError(
            422, {"detail": "at least one filter"}, service="theirstack")
        with pytest.raises(McpError, match="posted_at_max_age_days"):
            _tool("theirstack_jobs_search").fn(extra={"posted_at_max_age_days": None})


def test_verify_probe_uses_the_free_credit_balance_call():
    with patch("oto.tools.theirstack.client.TheirStackClient") as cls:
        from oto_mcp.tools import theirstack
        theirstack._verify({"key": "k"}, {})
    cls.assert_called_once_with(api_key="k")
    cls.return_value.credit_balance.assert_called_once_with()
    cls.return_value.search_companies.assert_not_called()
