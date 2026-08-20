"""Dispatch `op=` des tools `webflow_*`.

Webflow a un VRAI endpoint batch (items[] en un seul appel HTTP) — au
contraire de Folk, le mode bulk de `webflow_items` n'est PAS une boucle
côté oto : un test le verrouille (`test_bulk_create_is_one_client_call`).
Couvre aussi : la validation `fieldData` contre le schéma de collection
AVANT tout appel réseau d'écriture, les diffs `dry_run` réels (pas un écho),
et la traduction d'erreur HTTP -> McpError actionnable.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError

from oto.tools.common.errors import UpstreamHTTPError


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import webflow as W

    m = FastMCP("t")
    W.register(m)
    return asyncio.run(m.get_tool(name)).fn


_COLLECTION_SCHEMA = {
    "id": "coll_1",
    "displayName": "Blog Posts",
    "fields": [
        {"slug": "summary", "displayName": "Summary", "type": "PlainText",
         "isRequired": False},
        {"slug": "author", "displayName": "Author", "type": "PlainText",
         "isRequired": True},
    ],
}


@pytest.fixture
def client(monkeypatch):
    """Faux `WebflowClient` + clé résolue. `register()` importe la classe à
    l'appel : patcher l'attribut du module oto-core avant `_tool()` suffit
    (même patron que test_cognism_op_dispatch.py). Le vrai client résout
    `site_id` lui-même (`GET /sites`) — hors de portée d'un mock du tool
    layer, couvert côté oto-core (`test_webflow_client.py`)."""
    inst = MagicMock()
    inst.get_collection.return_value = _COLLECTION_SCHEMA
    monkeypatch.setattr("oto.tools.webflow.client.WebflowClient",
                        lambda api_key=None: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("tok", False))
    return inst


# --- site / collections -----------------------------------------------------

def test_webflow_site_calls_get_site(client):
    client.get_site.return_value = {"id": "site_1"}
    assert _tool("webflow_site")() == {"id": "site_1"}


def test_collections_list(client):
    client.list_collections.return_value = [{"id": "coll_1"}]
    result = _tool("webflow_collections")(op="list")
    assert result == {"collections": [{"id": "coll_1"}]}


def test_collections_get_requires_collection_id(client):
    with pytest.raises(McpError):
        _tool("webflow_collections")(op="get")
    client.get_collection.assert_not_called()


def test_collections_get(client):
    result = _tool("webflow_collections")(op="get", collection_id="coll_1")
    assert result == _COLLECTION_SCHEMA
    client.get_collection.assert_called_once_with("coll_1")


# --- items: list / get -------------------------------------------------------

def test_items_list_params(client):
    client.list_items.return_value = {"items": [], "pagination": {"total": 0}}
    _tool("webflow_items")(op="list", collection_id="coll_1", offset=20,
                           max_results=50, sort_by="lastUpdated",
                           sort_order="desc", cms_locale_id="loc_fr")
    client.list_items.assert_called_once_with(
        "coll_1", offset=20, limit=50, sort_by="lastUpdated",
        sort_order="desc", cms_locale_id="loc_fr")


def test_items_list_caps_max_results_at_500(client):
    client.list_items.return_value = {"items": [], "pagination": {"total": 0}}
    _tool("webflow_items")(op="list", collection_id="coll_1", max_results=10_000)
    assert client.list_items.call_args.kwargs["limit"] == 500


def test_items_get_requires_id(client):
    with pytest.raises(McpError):
        _tool("webflow_items")(op="get", collection_id="coll_1")
    client.get_item.assert_not_called()


def test_items_get(client):
    client.get_item.return_value = {"id": "item_1"}
    result = _tool("webflow_items")(op="get", collection_id="coll_1", id="item_1")
    assert result == {"id": "item_1"}
    client.get_item.assert_called_once_with("coll_1", "item_1")


# --- items: create — validation contre le schéma -----------------------------

def test_create_rejects_unknown_field_before_any_write(client):
    with pytest.raises(McpError, match="foo"):
        _tool("webflow_items")(
            op="create", collection_id="coll_1",
            item={"fieldData": {"name": "Post", "slug": "post", "author": "J",
                                "foo": "bar"}})
    client.create_items.assert_not_called()


def test_create_rejects_missing_required_field(client):
    with pytest.raises(McpError, match="author"):
        _tool("webflow_items")(
            op="create", collection_id="coll_1",
            item={"fieldData": {"name": "Post", "slug": "post"}})
    client.create_items.assert_not_called()


def test_create_solo_requires_exactly_one_of_item_items(client):
    with pytest.raises(McpError):
        _tool("webflow_items")(op="create", collection_id="coll_1")
    with pytest.raises(McpError):
        _tool("webflow_items")(
            op="create", collection_id="coll_1",
            item={"fieldData": {"name": "A", "slug": "a", "author": "J"}},
            items=[{"fieldData": {"name": "B", "slug": "b", "author": "J"}}])
    client.create_items.assert_not_called()


def test_create_solo_returns_created_item(client):
    client.create_items.return_value = {"items": [{"id": "item_9", "fieldData": {}}]}
    result = _tool("webflow_items")(
        op="create", collection_id="coll_1",
        item={"fieldData": {"name": "Post", "slug": "post", "author": "J"}})
    assert result == {"id": "item_9", "fieldData": {}}
    client.create_items.assert_called_once_with(
        "coll_1", [{"fieldData": {"name": "Post", "slug": "post", "author": "J"}}])


def test_create_dry_run_makes_no_create_call(client):
    result = _tool("webflow_items")(
        op="create", collection_id="coll_1", dry_run=True,
        item={"fieldData": {"name": "Post", "slug": "post", "author": "J"}})
    assert result["dry_run"] is True
    assert result["would_create"]["fieldData"]["author"] == "J"
    client.create_items.assert_not_called()
    # la validation, elle, a bien lu le schéma
    client.get_collection.assert_called_once_with("coll_1")


def test_bulk_create_is_one_client_call(client):
    """Webflow a un VRAI endpoint batch : 3 items -> 1 seul appel HTTP, pas
    une boucle côté oto (contrairement à Folk)."""
    client.create_items.return_value = {"items": [
        {"id": "1"}, {"id": "2"}, {"id": "3"}]}
    items = [{"fieldData": {"name": n, "slug": n.lower(), "author": "J"}}
             for n in ("A", "B", "C")]
    result = _tool("webflow_items")(op="create", collection_id="coll_1", items=items)
    assert client.create_items.call_count == 1
    assert result == {"total": 3, "succeeded": 3,
                      "created": [{"index": 0, "id": "1"}, {"index": 1, "id": "2"},
                                  {"index": 2, "id": "3"}],
                      "failed": []}


def test_bulk_create_over_cap_rejected(client):
    items = [{"fieldData": {"name": str(i), "slug": str(i), "author": "J"}}
             for i in range(51)]
    with pytest.raises(McpError):
        _tool("webflow_items")(op="create", collection_id="coll_1", items=items)
    client.create_items.assert_not_called()


# --- items: update ------------------------------------------------------------

def test_update_solo_requires_exactly_one_of_id_items(client):
    with pytest.raises(McpError):
        _tool("webflow_items")(op="update", collection_id="coll_1")
    with pytest.raises(McpError):
        _tool("webflow_items")(op="update", collection_id="coll_1", id="i1",
                               items=[{"id": "i2"}])
    client.update_items.assert_not_called()


def test_update_solo_calls_update_items_with_id_merged(client):
    client.update_items.return_value = {"items": [{"id": "i1", "fieldData": {}}]}
    result = _tool("webflow_items")(
        op="update", collection_id="coll_1", id="i1",
        item={"fieldData": {"author": "New"}})
    client.update_items.assert_called_once_with(
        "coll_1", [{"id": "i1", "fieldData": {"author": "New"}}])
    assert result == {"id": "i1", "fieldData": {}}


def test_update_solo_dry_run_returns_real_diff_not_echo(client):
    client.get_item.return_value = {
        "id": "i1", "fieldData": {"author": "Old", "summary": "S"},
        "isDraft": True,
    }
    result = _tool("webflow_items")(
        op="update", collection_id="coll_1", id="i1", dry_run=True,
        item={"fieldData": {"author": "New"}, "isDraft": False})
    assert result["dry_run"] is True
    assert result["changes"]["author"] == {"from": "Old", "to": "New"}
    assert result["changes"]["isDraft"] == {"from": True, "to": False}
    client.update_items.assert_not_called()


def test_update_bulk_requires_id_on_each_item(client):
    with pytest.raises(McpError):
        _tool("webflow_items")(op="update", collection_id="coll_1",
                               items=[{"fieldData": {"author": "X"}}])
    client.update_items.assert_not_called()


def test_update_bulk_is_one_client_call(client):
    client.update_items.return_value = {"items": [{"id": "i1"}, {"id": "i2"}]}
    result = _tool("webflow_items")(
        op="update", collection_id="coll_1",
        items=[{"id": "i1", "fieldData": {"author": "A"}},
               {"id": "i2", "fieldData": {"author": "B"}}])
    assert client.update_items.call_count == 1
    assert result == {"total": 2, "succeeded": 2, "failed": []}


def test_update_bulk_dry_run_diffs_each_item(client):
    client.get_item.side_effect = [
        {"id": "i1", "fieldData": {"author": "Old1"}},
        {"id": "i2", "fieldData": {"author": "Old2"}},
    ]
    result = _tool("webflow_items")(
        op="update", collection_id="coll_1", dry_run=True,
        items=[{"id": "i1", "fieldData": {"author": "New1"}},
               {"id": "i2", "fieldData": {"author": "New2"}}])
    assert result["dry_run"] is True
    assert result["would_update"][0]["changes"]["author"] == {"from": "Old1", "to": "New1"}
    assert result["would_update"][1]["changes"]["author"] == {"from": "Old2", "to": "New2"}
    client.update_items.assert_not_called()


# --- items: delete -------------------------------------------------------------

def test_delete_requires_exactly_one_of_id_ids(client):
    with pytest.raises(McpError):
        _tool("webflow_items")(op="delete", collection_id="coll_1")
    with pytest.raises(McpError):
        _tool("webflow_items")(op="delete", collection_id="coll_1", id="i1",
                               ids=["i2"])
    client.delete_items.assert_not_called()


def test_delete_solo(client):
    result = _tool("webflow_items")(op="delete", collection_id="coll_1", id="i1")
    client.delete_items.assert_called_once_with("coll_1", ["i1"])
    assert result == {}


def test_delete_solo_dry_run_returns_would_delete_not_echo(client):
    client.get_item.return_value = {"id": "i1", "fieldData": {"author": "Old"}}
    result = _tool("webflow_items")(op="delete", collection_id="coll_1", id="i1",
                                    dry_run=True)
    assert result == {"dry_run": True, "id": "i1",
                      "would_delete": {"id": "i1", "fieldData": {"author": "Old"}}}
    client.delete_items.assert_not_called()


def test_delete_bulk_is_one_client_call(client):
    result = _tool("webflow_items")(op="delete", collection_id="coll_1",
                                    ids=["i1", "i2", "i3"])
    client.delete_items.assert_called_once_with("coll_1", ["i1", "i2", "i3"])
    assert result == {"total": 3, "succeeded": 3, "failed": []}


def test_delete_over_cap_rejected(client):
    with pytest.raises(McpError):
        _tool("webflow_items")(op="delete", collection_id="coll_1",
                               ids=[str(i) for i in range(51)])
    client.delete_items.assert_not_called()


# --- publish -------------------------------------------------------------------

def test_publish_requires_exactly_one_of_id_ids(client):
    with pytest.raises(McpError):
        _tool("webflow_publish")(collection_id="coll_1")
    with pytest.raises(McpError):
        _tool("webflow_publish")(collection_id="coll_1", id="i1", ids=["i2"])
    client.publish_items.assert_not_called()


def test_publish_solo(client):
    client.publish_items.return_value = {"publishedItemIds": ["i1"]}
    result = _tool("webflow_publish")(collection_id="coll_1", id="i1")
    client.publish_items.assert_called_once_with("coll_1", ["i1"])
    assert result == {"publishedItemIds": ["i1"]}


def test_publish_dry_run_shows_current_state_not_echo(client):
    """dry_run doit montrer isDraft/lastPublished COURANTS, pas juste
    confirmer l'id passé — sinon il ne protège de rien."""
    client.get_item.return_value = {
        "id": "i1", "isDraft": True, "lastPublished": None}
    result = _tool("webflow_publish")(collection_id="coll_1", id="i1", dry_run=True)
    assert result == {"dry_run": True,
                      "would_publish": {"id": "i1", "isDraft": True,
                                        "lastPublished": None}}
    client.publish_items.assert_not_called()


def test_publish_bulk_is_one_client_call(client):
    result = _tool("webflow_publish")(collection_id="coll_1", ids=["i1", "i2"])
    client.publish_items.assert_called_once_with("coll_1", ["i1", "i2"])


def test_publish_over_cap_rejected(client):
    with pytest.raises(McpError):
        _tool("webflow_publish")(collection_id="coll_1",
                                 ids=[str(i) for i in range(51)])
    client.publish_items.assert_not_called()


# --- traduction d'erreur HTTP ---------------------------------------------------

@pytest.mark.parametrize("status,fragment", [
    (401, "invalide"),
    (404, "introuvable"),
    (500, "indisponible"),
])
def test_upstream_errors_translated_to_actionable_message(client, status, fragment):
    client.get_site.side_effect = UpstreamHTTPError(status, {"msg": "x"}, service="webflow")
    with pytest.raises(McpError, match=fragment):
        _tool("webflow_site")()
