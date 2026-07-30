"""Connecteur Spott — ATS/CRM des cabinets de recrutement (api.gospott.com).

Verrouille : l'entrée de registre (keyed byo-only, catégorie Recrutement), la
surface MCP curée sous le namespace `spott`, la jointure tool↔client oto-core
(garde version-skew), la sonde « tester la connexion », et les deux points où le
module fait autre chose que passer le plat : le routage de `spott_applications`
(par job / par candidat / liste) et la bascule liste↔recherche de `spott_clients`.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto_mcp import connector_verify, providers
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {
    "spott_candidates", "spott_candidate", "spott_search_candidates",
    "spott_create_candidate", "spott_update_candidate",
    "spott_jobs", "spott_job", "spott_search_jobs",
    "spott_applications", "spott_create_application", "spott_move_application",
    "spott_stages",
    "spott_notes", "spott_create_note",
    "spott_clients", "spott_client", "spott_client_contacts", "spott_placements",
    "spott_people", "spott_users",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    tools = asyncio.run(m._list_tools())
    return {t.name for t in tools}


def _tool(name):
    """Enregistre le module seul et rend le tool demandé."""
    from fastmcp import FastMCP
    from oto_mcp.tools import spott

    m = FastMCP("t")
    spott.register(m)
    return asyncio.run(m.get_tool(name))


# --- registre -----------------------------------------------------------------

def test_spott_is_keyed_byo_only_connector():
    c = providers.REGISTRY["spott"]
    assert c.kind == "tools"
    assert c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    # byo-only : pas de clé plateforme partagée pour un ATS client.
    assert "platform" not in c.auth_modes
    assert "spott" in providers.KEY_PROVIDERS
    assert c.category == "Recrutement"


def test_spott_has_onboarding_doc():
    kinds = {s.kind for s in providers.REGISTRY["spott"].doc_sections}
    assert {"prerequisite", "usage"} <= kinds


# --- surface MCP --------------------------------------------------------------

def test_spott_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= all_tools
    assert all(namespace_of(t) == "spott"
               for t in all_tools if t.startswith("spott_"))


def test_verify_probe_registered():
    assert connector_verify.supports("spott")


# --- jointure tool ↔ client oto-core (garde version-skew) ---------------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.spott.client import SpottClient
    for meth in ("list_candidates", "get_candidate", "search_candidates",
                 "create_candidate", "update_candidate",
                 "list_jobs", "get_job", "search_jobs",
                 "list_applications", "applications_by_candidate",
                 "applications_by_job", "create_application", "move_application",
                 "pipeline_stages", "list_notes", "create_note",
                 "list_clients", "get_client", "search_clients",
                 "list_client_contacts", "list_placements", "search_people",
                 "list_users"):
        assert callable(getattr(SpottClient, meth, None)), f"SpottClient.{meth} manquant"


# --- routage spott_applications ----------------------------------------------

def _with_fake_client():
    """Patche la résolution de clé + la classe client ; rend le mock d'instance."""
    key = patch("oto_mcp.access.resolve_api_key", return_value=("fake-key", False))
    cls = patch("oto.tools.spott.client.SpottClient")
    return key, cls


def test_applications_routes_by_job_candidate_or_listing():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("spott_applications")

        tool.fn(job_id="v1")
        inst.applications_by_job.assert_called_once_with("v1")

        tool.fn(candidate_id="c1")
        inst.applications_by_candidate.assert_called_once_with("c1")

        tool.fn(limit=10)
        assert inst.list_applications.call_args.kwargs["limit"] == 10


def test_applications_rejects_job_and_candidate_together():
    from mcp.shared.exceptions import McpError

    key, cls = _with_fake_client()
    with key, cls:
        tool = _tool("spott_applications")
        with pytest.raises(McpError):
            tool.fn(job_id="v1", candidate_id="c1")


# --- bascule liste ↔ recherche des clients ------------------------------------

def test_clients_switches_to_search_when_filters_given():
    key, cls = _with_fake_client()
    with key, cls as client_cls:
        inst = client_cls.return_value
        tool = _tool("spott_clients")

        tool.fn()
        inst.list_clients.assert_called_once()
        inst.search_clients.assert_not_called()

        flt = [{"type": "text", "operator": "contains",
                "path": "client.company.name", "value": "acme"}]
        tool.fn(filters=flt, page=1)
        inst.search_clients.assert_called_once_with(filters=flt, page=1, page_size=None)


# --- traduction des refus amont ----------------------------------------------

def test_upstream_401_becomes_a_readable_tool_error():
    from mcp.shared.exceptions import McpError
    from oto.tools.common.errors import UpstreamHTTPError

    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.list_users.side_effect = UpstreamHTTPError(
            401, {"message": "invalid api key"}, service="spott")
        tool = _tool("spott_users")
        with pytest.raises(McpError, match="clé API"):
            tool.fn()


def test_unknown_pipeline_is_a_param_error_not_a_crash():
    from mcp.shared.exceptions import McpError

    key, cls = _with_fake_client()
    with key, cls as client_cls:
        client_cls.return_value.pipeline_stages.side_effect = ValueError(
            "pipeline Spott inconnu : 'candidates'")
        tool = _tool("spott_stages")
        with pytest.raises(McpError, match="pipeline Spott inconnu"):
            tool.fn(entity="candidates")
