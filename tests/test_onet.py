"""Connecteur O*NET (référentiel des métiers US) — verrouille : l'entrée registre
(keyed api_key, BYO seulement), la surface MCP (un outil `op=`, décrit), la jointure
tool↔client oto-core, et le tool layer sur un client MOCKÉ (la classe, pas
`requests`) : dispatch, refus des paramètres ignorés, projection par défaut vs
`full=True`, métier sans tâches (422) rendu `tasks: []`, erreurs amont traduites.
"""
import asyncio
from unittest.mock import patch

import pytest
from oto.tools.common.errors import UpstreamHTTPError
from oto_mcp import providers
from oto_mcp.mcp_errors import McpError
from oto_mcp.tool_visibility import namespace_of


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False))


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import onet as onet_tool

    m = FastMCP("t")
    onet_tool.register(m)
    return asyncio.run(m.get_tool("onet_occupation"))


@pytest.fixture()
def client():
    with patch("oto.tools.onet.client.ONetClient") as client_cls:
        yield client_cls


# --- registre -----------------------------------------------------------------

def test_onet_is_keyed_byo_only():
    c = providers.REGISTRY["onet"]
    assert c.kind == "tools" and c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert "onet" in providers.KEY_PROVIDERS
    assert c.platform_key_open is False and c.default_active is False


def test_onet_doc_is_served():
    kinds = [s.kind for s in providers.REGISTRY["onet"].doc_sections]
    assert {"prerequisite", "usage"} <= set(kinds)


# --- surface MCP --------------------------------------------------------------

def test_tool_registers_under_namespace_with_description():
    t = _tool()
    assert namespace_of(t.name) == "onet"
    # Régression du piège f-string-docstring (FastMCP sert alors un outil muet).
    assert t.description and "O*NET-SOC" in t.description
    assert "Args:" not in t.description


def test_client_exposes_methods_called_by_tools():
    from oto.tools.onet.client import ONetClient
    for meth in ("search_occupations", "get_occupation", "get_occupation_tasks"):
        assert callable(getattr(ONetClient, meth, None)), f"ONetClient.{meth} manquant"


# --- tool layer ---------------------------------------------------------------

_FOUND = {"start": 1, "end": 1, "total": 63, "occupation": [
    {"href": "https://api-v2.onetcenter.org/online/occupations/17-1011.00/",
     "code": "17-1011.00", "title": "Architects", "tags": {}}]}


def test_search_uses_the_resolved_key_and_drops_links(client):
    client.return_value.search_occupations.return_value = dict(_FOUND)
    out = _tool().fn(op="search", keyword=" architect ", limit=5)
    client.assert_called_with(api_key="k")
    client.return_value.search_occupations.assert_called_once_with("architect", end=5)
    assert out["total"] == 63
    assert out["occupation"] == [{"code": "17-1011.00", "title": "Architects", "tags": {}}]


def test_search_full_keeps_links(client):
    client.return_value.search_occupations.return_value = dict(_FOUND)
    out = _tool().fn(op="search", keyword="architect", full=True)
    assert out["occupation"][0]["href"].endswith("/17-1011.00/")


def test_get_merges_tasks_and_drops_navigation(client):
    inst = client.return_value
    inst.get_occupation.return_value = {
        "code": "17-2051.00", "title": "Civil Engineers", "description": "Perform…",
        "sample_of_reported_titles": ["City Engineer"],
        "also_see": [{"href": "https://x/", "code": "17-2051.01", "title": "Transportation Engineers"}],
        "summary_contents": [{"href": "https://x/", "title": "Tasks"}],
        "details_contents": [], "custom_contents": [], "updated": {"year": 2026}}
    inst.get_occupation_tasks.return_value = {
        "start": 1, "end": 2, "total": 17,
        "task": [{"id": "1", "title": "Direct engineering activities.", "related": "https://x/"},
                 {"id": "2", "title": "Manage construction.", "related": "https://x/"}]}
    out = _tool().fn(op="get", code="17-2051.00", limit=2)
    inst.get_occupation_tasks.assert_called_once_with("17-2051.00", end=2)
    assert out["tasks"] == ["Direct engineering activities.", "Manage construction."]
    assert out["tasks_total"] == 17
    assert out["also_see"] == [{"code": "17-2051.01", "title": "Transportation Engineers"}]
    assert not {"summary_contents", "details_contents", "custom_contents", "updated"} & set(out)
    assert out["sample_of_reported_titles"] == ["City Engineer"]


def test_get_occupation_without_tasks_is_not_an_error(client):
    inst = client.return_value
    inst.get_occupation.return_value = {"code": "11-1031.00", "title": "Legislators"}
    inst.get_occupation_tasks.side_effect = UpstreamHTTPError(
        422, {"error": "no data available"}, service="onet")
    out = _tool().fn(op="get", code="11-1031.00")
    assert out["tasks"] == [] and out["tasks_total"] == 0


@pytest.mark.parametrize("kwargs,fragment", [
    ({"op": "search"}, "exige `keyword`"),
    ({"op": "get"}, "exige `code`"),
    ({"op": "search", "keyword": "x", "code": "15-1299.08"}, "n'utilise pas `code`"),
    ({"op": "get", "code": "15-1299.08", "keyword": "x"}, "n'utilise pas `keyword`"),
    ({"op": "search", "keyword": "x", "limit": 0}, "`limit`"),
])
def test_bad_input_refused_before_any_call(client, kwargs, fragment):
    with pytest.raises(McpError) as e:
        _tool().fn(**kwargs)
    assert fragment in str(e.value)
    client.assert_not_called()


@pytest.mark.parametrize("status,fragment", [
    (401, "rejeté la clé"), (422, "inexistant ou obsolète"), (429, "saturé"),
    (503, "indisponible"),
])
def test_upstream_errors_are_translated(client, status, fragment):
    client.return_value.get_occupation.side_effect = UpstreamHTTPError(
        status, {"error": "x"}, service="onet")
    with pytest.raises(McpError) as e:
        _tool().fn(op="get", code="15-1299.08")
    assert fragment in str(e.value)


def test_invalid_code_from_client_is_a_clean_refusal(client):
    client.return_value.get_occupation.side_effect = ValueError("code O*NET-SOC invalide")
    with pytest.raises(McpError) as e:
        _tool().fn(op="get", code="civil engineer")
    assert "invalide" in str(e.value)
