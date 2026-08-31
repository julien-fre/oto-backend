"""Connecteur PromptWatch — verrouille : l'entrée registre (credential 3
champs — api_key secret + project_id optionnel non-secret —, résolu via
resolve_credential_fields comme lighton ; BYO user/org SEULEMENT, pas de mode
plateforme, pas d'accord commercial Otomata↔PromptWatch), la surface MCP
consolidée (19 tools `op=`, ADR 0047 — couverture COMPLÈTE de l'API v2, plus
de domaine déféré), la jointure tool↔client oto-core (garde version-skew —
couverte aussi par test_tools_client_methods_exist.py), et le contrat
requis/optionnel par `op` du tool layer (mock du client CLASSE, pas de
`requests` — cf. test_folk.py pour le patron)."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp import providers
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {
    "promptwatch_project",
    "promptwatch_monitor",
    "promptwatch_prompt",
    "promptwatch_response",
    "promptwatch_visibility",
    "promptwatch_citation",
    "promptwatch_content",
    "promptwatch_taxonomy",
    "promptwatch_persona",
    "promptwatch_brand",
    "promptwatch_publishing",
    "promptwatch_content_agent",
    "promptwatch_ads",
    "promptwatch_shopping",
    "promptwatch_sitemap",
    "promptwatch_page_tracker",
    "promptwatch_models",
    "promptwatch_actions",
    "promptwatch_social",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    tools = asyncio.run(m._list_tools())
    return {t.name: t for t in tools}


# --- registre -----------------------------------------------------------------

def test_promptwatch_is_fields_credential_connector():
    c = providers.REGISTRY["promptwatch"]
    assert c.kind == "tools"
    assert c.mount_url is None
    assert not c.keyed
    assert c.secret_kind == "fields"
    field_names = {f.name for f in c.credential_fields}
    assert field_names == {"api_key", "project_id"}
    api_key_field = next(f for f in c.credential_fields if f.name == "api_key")
    project_id_field = next(f for f in c.credential_fields if f.name == "project_id")
    assert api_key_field.secret is True and api_key_field.required is True
    assert project_id_field.secret is False and project_id_field.required is False


def test_promptwatch_is_byo_only_no_platform_mode():
    c = providers.REGISTRY["promptwatch"]
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert "platform" not in c.auth_modes


def test_promptwatch_deny_by_default():
    assert providers.REGISTRY["promptwatch"].default_active is False


def test_promptwatch_not_a_mount():
    assert all(c.name != "promptwatch" for c in providers.MOUNT_CONNECTORS)


# --- surface MCP --------------------------------------------------------------

def test_promptwatch_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= set(all_tools)
    assert all(namespace_of(t) == "promptwatch"
               for t in all_tools if t.startswith("promptwatch_"))


def test_promptwatch_tools_all_have_descriptions(all_tools):
    # Régression du piège f-string-docstring (f"""...""" n'alimente PAS
    # __doc__ -> FastMCP l'expose sans description).
    for name in EXPECTED_TOOLS:
        assert all_tools[name].description, f"{name} has no description"


# --- jointure tool ↔ client oto-core (garde version-skew) ---------------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.promptwatch.client import PromptWatchClient
    for meth in (
        "list_projects", "list_monitors", "get_monitor", "create_monitor",
        "update_monitor", "delete_monitor", "list_prompts", "get_prompt",
        "create_prompt", "update_prompt", "delete_prompt",
        "bulk_create_prompts", "bulk_delete_prompts", "activate_prompts",
        "deactivate_prompts", "attach_tags", "attach_topics",
        "bulk_attach_tags", "bulk_attach_topics", "list_responses",
        "get_response", "response_summary", "sentiment_distribution",
        "response_sentiment_time_series", "mentions_time_series",
        "top_competitors", "visibility_time_series", "sentiment_time_series",
        "prompt_visibility_time_series", "competitor_heatmap", "citations",
        "citation_rank_analysis", "citation_domains_over_time",
        "citation_domains_by_llm", "citation_grouped", "citation_llm_sources",
        "citation_self_frequency", "citation_top_pages", "list_content",
        "get_content", "create_content", "content_gap_stats",
        "content_gap_prompts", "content_gap_latest",
        "content_gap_recommendations", "list_tags", "create_tags",
        "delete_tag", "rename_tag", "list_topics", "create_topics",
        "delete_topic", "rename_topic", "list_brands", "create_brand",
        "update_brand", "list_personas", "get_persona", "create_persona",
        "update_persona", "delete_persona",
        "list_cms_connections", "get_content_publish_status",
        "set_content_publication", "clear_content_publication",
        "push_content_draft_to_cms", "publish_content_live",
        "get_content_agent_settings", "update_content_agent_settings",
        "list_content_agent_slots", "get_content_agent_slot",
        "update_content_agent_slot", "accept_content_agent_slot",
        "decline_content_agent_slot", "publish_content_agent_slot_now",
        "list_ads", "list_prompts_with_ads", "list_ad_domains",
        "ad_domain_analytics", "list_shopping_items", "get_shopping_item",
        "shopping_products_over_time", "shopping_product_position_analytics",
        "shopping_top_merchant_domains", "shopping_top_products",
        "list_tracked_products", "add_tracked_products",
        "update_tracked_product", "delete_tracked_product",
        "site_health_pages", "sitemap_crawl_progress", "list_sitemap_urls",
        "list_tracked_pages", "add_tracked_pages", "get_tracked_page",
        "delete_tracked_page", "list_tracked_page_prompts",
        "list_tracked_page_responses", "list_models", "list_action_items",
        "update_action_item", "list_query_fanouts", "list_reddit_citations",
        "list_youtube_citations",
    ):
        assert callable(getattr(PromptWatchClient, meth, None)), \
            f"PromptWatchClient.{meth} manquant"


# --- dispatch du tool layer (mock du client) -----------------------------------

@pytest.fixture(autouse=True)
def _fake_creds(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_credential_fields",
        lambda provider, account=None: {"api_key": "k", "project_id": ""},
    )


@pytest.fixture
def client_cls():
    with patch("oto.tools.promptwatch.client.PromptWatchClient") as cls:
        yield cls


def _call(tool_name, **kwargs):
    from fastmcp import FastMCP
    from oto_mcp.tools import promptwatch as pw_tool

    m = FastMCP("t")
    pw_tool.register(m)
    fn = asyncio.run(m.get_tool(tool_name)).fn
    return fn(**kwargs)


def test_project_list(client_cls):
    inst = client_cls.return_value
    inst.list_projects.return_value = [{"id": "p1", "name": "Example"}]
    result = _call("promptwatch_project")
    assert result == [{"id": "p1", "name": "Example"}]


def test_project_id_passed_from_credential(client_cls):
    with patch("oto_mcp.access.resolve_credential_fields",
               return_value={"api_key": "k", "project_id": "proj-1"}):
        _call("promptwatch_project")
    client_cls.assert_called_once_with(api_key="k", project_id="proj-1")


def test_monitor_get_requires_monitor_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_monitor", op="get")
    client_cls.return_value.get_monitor.assert_not_called()


def test_monitor_create_requires_name_and_models(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_monitor", op="create", name="M1")
    client_cls.return_value.create_monitor.assert_not_called()


def test_monitor_create_dispatch(client_cls):
    inst = client_cls.return_value
    inst.create_monitor.return_value = {"id": "mon-1"}
    result = _call("promptwatch_monitor", op="create", name="M1", models=["openai/gpt-4.1"])
    assert result == {"id": "mon-1"}
    inst.create_monitor.assert_called_once()
    args, kwargs = inst.create_monitor.call_args
    assert args == ("M1", ["openai/gpt-4.1"])


def test_monitor_update_requires_at_least_one_field(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_monitor", op="update", monitor_id="mon-1")
    client_cls.return_value.update_monitor.assert_not_called()


def test_monitor_update_camelcases_fields(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_monitor", op="update", monitor_id="mon-1", prompt_frequency="WEEKLY")
    inst.update_monitor.assert_called_once_with("mon-1", promptFrequency="WEEKLY")


def test_prompt_create_requires_prompt_monitor_and_type(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_prompt", op="create", prompt="hello")
    client_cls.return_value.create_prompt.assert_not_called()


def test_prompt_create_dispatch(client_cls):
    inst = client_cls.return_value
    inst.create_prompt.return_value = {"id": "p1"}
    result = _call("promptwatch_prompt", op="create", prompt="hello",
                    llm_monitor_id="mon-1", type="ORGANIC")
    assert result == {"id": "p1"}
    inst.create_prompt.assert_called_once_with(
        "hello", "mon-1", "ORGANIC", intent=None, language_code=None,
        keywords=None, tags=None, is_active=None,
    )


def test_prompt_update_requires_type_and_intent(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_prompt", op="update", prompt_id="p1", type="ORGANIC")
    client_cls.return_value.update_prompt.assert_not_called()


def test_prompt_update_dispatch(client_cls):
    inst = client_cls.return_value
    inst.update_prompt.return_value = {"id": "p1"}
    result = _call("promptwatch_prompt", op="update", prompt_id="p1",
                    type="BRAND_SPECIFIC", intent="BRANDED")
    assert result == {"id": "p1"}
    inst.update_prompt.assert_called_once_with("p1", "BRAND_SPECIFIC", "BRANDED")


def test_prompt_create_rejects_empty_llm_monitor_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_prompt", op="create", prompt="hello",
              llm_monitor_id="", type="ORGANIC")
    client_cls.return_value.create_prompt.assert_not_called()


def test_prompt_bulk_delete_rejects_empty_prompt_ids(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_prompt", op="bulk_delete", prompt_ids=[])
    client_cls.return_value.bulk_delete_prompts.assert_not_called()


def test_prompt_bulk_delete_requires_prompt_ids(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_prompt", op="bulk_delete")
    client_cls.return_value.bulk_delete_prompts.assert_not_called()


def test_prompt_bulk_delete_dispatch(client_cls):
    inst = client_cls.return_value
    inst.bulk_delete_prompts.return_value = {"deleted": 2}
    result = _call("promptwatch_prompt", op="bulk_delete", prompt_ids=["a", "b"])
    assert result == {"deleted": 2}
    inst.bulk_delete_prompts.assert_called_once_with(["a", "b"])


def test_prompt_attach_topics_uses_prompt_id_and_topics(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_prompt", op="attach_topics", prompt_id="p1", topics=["pricing"])
    inst.attach_topics.assert_called_once_with("p1", ["pricing"])


def test_prompt_unknown_op_rejected(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_prompt", op="bogus")


def test_response_get_requires_response_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_response", op="get")


def test_response_list_dispatch(client_cls):
    inst = client_cls.return_value
    inst.list_responses.return_value = {"responses": [], "total": 0}
    result = _call("promptwatch_response", op="list", date_from="2026-01-01")
    assert result == {"responses": [], "total": 0}
    _, kwargs = inst.list_responses.call_args
    assert kwargs["from_"] == "2026-01-01"


def test_visibility_prompt_time_series_requires_prompt_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_visibility", op="prompt_time_series")
    client_cls.return_value.prompt_visibility_time_series.assert_not_called()


def test_visibility_sentiment_time_series_dispatch(client_cls):
    inst = client_cls.return_value
    inst.sentiment_time_series.return_value = [{"date": "2026-08-01", "value": 0.5}]
    result = _call("promptwatch_visibility", op="sentiment_time_series",
                    start_date="2026-07-01", end_date="2026-08-01")
    assert result == [{"date": "2026-08-01", "value": 0.5}]
    inst.sentiment_time_series.assert_called_once_with(
        start_date="2026-07-01", end_date="2026-08-01", range=None,
        models=None, prompt_id=None, llm_monitor_id=None,
    )


def test_citation_extra_params_merged(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_citation", op="llm_sources", prompt_id="p1",
          extra_params={"customFilter": "x"})
    _, kwargs = inst.citation_llm_sources.call_args
    assert kwargs["promptId"] == "p1"
    assert kwargs["customFilter"] == "x"


def test_content_create_requires_mode_prompt_and_persona(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_content", op="create")
    client_cls.return_value.create_content.assert_not_called()


def test_content_create_dispatch(client_cls):
    inst = client_cls.return_value
    inst.create_content.return_value = {"id": "c1", "status": "PENDING"}
    result = _call("promptwatch_content", op="create", mode="CREATE",
                    prompt_id="p1", persona_id="per1", type="BLOG_POST",
                    content_length="MEDIUM")
    assert result == {"id": "c1", "status": "PENDING"}


def test_content_gap_latest_requires_prompt_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_content", op="gap_latest")
    client_cls.return_value.content_gap_latest.assert_not_called()


def test_taxonomy_create_tags_requires_names(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_taxonomy", op="create_tags")
    client_cls.return_value.create_tags.assert_not_called()


def test_taxonomy_rename_tag_requires_id_and_name(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_taxonomy", op="rename_tag", id="tag-1", name="New name")
    inst.rename_tag.assert_called_once_with("tag-1", "New name")


def test_persona_update_requires_at_least_one_field(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_persona", op="update", persona_id="per-1")
    client_cls.return_value.update_persona.assert_not_called()


def test_persona_update_camelcases_fields(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_persona", op="update", persona_id="per-1", age_range="25-34")
    inst.update_persona.assert_called_once_with("per-1", ageRange="25-34")


def test_brand_create_requires_name_url_relation(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_brand", op="create", name="Acme")
    client_cls.return_value.create_brand.assert_not_called()


def test_brand_create_dispatch(client_cls):
    inst = client_cls.return_value
    inst.create_brand.return_value = {"id": "b1"}
    result = _call("promptwatch_brand", op="create", name="Acme",
                    url="https://acme.com", relation="SELF")
    assert result == {"id": "b1"}
    inst.create_brand.assert_called_once_with("Acme", "https://acme.com", "SELF")


def test_401_maps_to_actionable_message(client_cls):
    inst = client_cls.return_value
    err = Exception("promptwatch HTTP 401: {'valid': False}")
    err.status_code = 401
    err.body = {"valid": False}
    inst.list_projects.side_effect = err
    with pytest.raises(McpError) as exc:
        _call("promptwatch_project")
    assert "401" in str(exc.value)


def test_5xx_maps_to_retry_message(client_cls):
    inst = client_cls.return_value
    err = Exception("promptwatch HTTP 503: ...")
    err.status_code = 503
    inst.list_projects.side_effect = err
    with pytest.raises(McpError) as exc:
        _call("promptwatch_project")
    assert "503" in str(exc.value)


def test_content_query_fanouts_op(client_cls):
    inst = client_cls.return_value
    inst.list_query_fanouts.return_value = {"prompts": []}
    result = _call("promptwatch_content", op="query_fanouts", page=1, size=5)
    assert result == {"prompts": []}
    inst.list_query_fanouts.assert_called_once_with(page=1, size=5)


def test_publishing_set_requires_content_id_and_url(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_publishing", op="set", content_id="c1")
    client_cls.return_value.set_content_publication.assert_not_called()


def test_publishing_status_requires_content_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_publishing", op="status")
    client_cls.return_value.get_content_publish_status.assert_not_called()


def test_publishing_list_connections_needs_no_content_id(client_cls):
    inst = client_cls.return_value
    inst.list_cms_connections.return_value = []
    result = _call("promptwatch_publishing", op="list_connections")
    assert result == []


def test_content_agent_update_settings_requires_at_least_one_field(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_content_agent", op="update_settings")
    client_cls.return_value.update_content_agent_settings.assert_not_called()


def test_content_agent_update_settings_camelcases_fields(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_content_agent", op="update_settings", max_per_day=5)
    inst.update_content_agent_settings.assert_called_once_with(maxPerDay=5)


def test_content_agent_update_slot_requires_slot_fields(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_content_agent", op="update_slot", slot_id="s1")
    client_cls.return_value.update_content_agent_slot.assert_not_called()


def test_content_agent_update_slot_dispatch(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_content_agent", op="update_slot", slot_id="s1",
          slot_fields={"priorityScore": 5})
    inst.update_content_agent_slot.assert_called_once_with("s1", priorityScore=5)


def test_ads_domain_analytics_dispatch(client_cls):
    inst = client_cls.return_value
    inst.ad_domain_analytics.return_value = {"topDomains": [], "daily": []}
    result = _call("promptwatch_ads", op="domain_analytics", domains=["hubspot.com"])
    assert result == {"topDomains": [], "daily": []}
    _, kwargs = inst.ad_domain_analytics.call_args
    assert kwargs["domains"] == ["hubspot.com"]


def test_ads_list_ads_dispatch(client_cls):
    inst = client_cls.return_value
    inst.list_ads.return_value = {"items": []}
    result = _call("promptwatch_ads", op="list_ads", date_from="2026-01-01")
    assert result == {"items": []}
    _, kwargs = inst.list_ads.call_args
    assert kwargs["from_"] == "2026-01-01"


def test_shopping_add_tracked_requires_products(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_shopping", op="add_tracked")
    client_cls.return_value.add_tracked_products.assert_not_called()


def test_shopping_update_tracked_requires_name_or_description(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_shopping", op="update_tracked", tracked_product_id="p1")
    client_cls.return_value.update_tracked_product.assert_not_called()


def test_shopping_delete_tracked_dispatch(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_shopping", op="delete_tracked", tracked_product_id="p1")
    inst.delete_tracked_product.assert_called_once_with("p1")


def test_sitemap_health_dispatch(client_cls):
    inst = client_cls.return_value
    inst.site_health_pages.return_value = {"pages": []}
    result = _call("promptwatch_sitemap", op="health", issue_types=["noH1"])
    assert result == {"pages": []}
    _, kwargs = inst.site_health_pages.call_args
    assert kwargs["issue_types"] == ["noH1"]


def test_page_tracker_add_requires_urls(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_page_tracker", op="add")
    client_cls.return_value.add_tracked_pages.assert_not_called()


def test_page_tracker_get_requires_page_id(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_page_tracker", op="get")
    client_cls.return_value.get_tracked_page.assert_not_called()


def test_models_no_args(client_cls):
    inst = client_cls.return_value
    inst.list_models.return_value = ["openai/gpt-4.1"]
    result = _call("promptwatch_models")
    assert result == ["openai/gpt-4.1"]


def test_actions_update_requires_action_id_and_status(client_cls):
    with pytest.raises(McpError):
        _call("promptwatch_actions", op="update")
    client_cls.return_value.update_action_item.assert_not_called()


def test_actions_update_dispatch(client_cls):
    inst = client_cls.return_value
    _call("promptwatch_actions", op="update", action_id="a1", status="DISMISSED")
    inst.update_action_item.assert_called_once_with("a1", "DISMISSED")


def test_social_reddit_dispatch(client_cls):
    inst = client_cls.return_value
    inst.list_reddit_citations.return_value = {"items": []}
    result = _call("promptwatch_social", op="reddit", subreddit_name="crm")
    assert result == {"items": []}
    _, kwargs = inst.list_reddit_citations.call_args
    assert kwargs["subreddit_name"] == "crm"


def test_social_youtube_dispatch(client_cls):
    inst = client_cls.return_value
    inst.list_youtube_citations.return_value = {"items": []}
    result = _call("promptwatch_social", op="youtube", channel_name="Folk")
    assert result == {"items": []}
    _, kwargs = inst.list_youtube_citations.call_args
    assert kwargs["channel_name"] == "Folk"
