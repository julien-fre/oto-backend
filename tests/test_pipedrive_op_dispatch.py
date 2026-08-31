"""Dispatch `op=` des 5 tools `pipedrive_*` (ADR 0047 §Amendement, appliqué au
connecteur pipedrive : 13 tools → 5).

Ce module n'avait AUCUN test de surface : le CRUD générique par `entity` passait
directement au client oto-core, donc une op mal câblée appellerait silencieusement
la mauvaise méthode (`update_record` au lieu de `delete_record`…) sans que rien ne
casse au boot — le seul filet était le garde-fou statique version-skew, qui vérifie
que la méthode EXISTE, pas qu'on appelle la bonne. D'où, pour chaque op : la méthode
client atteinte, le refus d'une op inconnue, et les arguments obligatoires par op.

S'y ajoutent les endroits où le module ne se contente PAS de passer le plat : les
filtres non fournis ne doivent pas partir (l'API rejette un `null` explicite), et
`op="create"` d'un lead assemble l'objet `value` à partir d'`amount`/`currency`.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp.connectors import verify as connector_verify

EXPECTED_TOOLS = {"pipedrive_record", "pipedrive_search", "pipedrive_note",
                  "pipedrive_lead", "pipedrive_users"}


def _register():
    from fastmcp import FastMCP
    from oto_mcp.tools import pipedrive as P

    m = FastMCP("t")
    P.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_register().get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    """Faux `PipedriveClient` + credential résolu.

    Le patch vise la CLASSE dans oto-core parce que `register()` l'importe dans sa
    portée locale : il faut donc patcher AVANT que `_tool()` n'enregistre le module.
    """
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.pipedrive.client.PipedriveClient",
                        lambda *a, **k: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider: {"api_token": "tok",
                                          "company_domain": "acme"})
    return inst


# --- la surface elle-même ------------------------------------------------------

def test_surface_is_exactly_the_five_consolidated_tools():
    """Un tool oublié en route (ou resté en double) se voit ici, pas en prod."""
    assert {t.name for t in asyncio.run(_register()._list_tools())} == EXPECTED_TOOLS


def test_the_connection_probe_stays_registered():
    _register()
    assert connector_verify.supports("pipedrive")


# --- records (API v2) : CRUD générique + schéma --------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_records"),
    ("get", {"record_id": 7}, "get_record"),
    ("create", {"data": {"title": "x"}}, "create_record"),
    ("update", {"record_id": 7, "data": {"title": "x"}}, "update_record"),
    ("delete", {"record_id": 7}, "delete_record"),
    ("fields", {}, "list_fields"),
])
def test_record_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("pipedrive_record")(entity="deals", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_record_list_forwards_only_the_filters_provided(client):
    """L'API rejette un `null` explicite : les filtres non fournis ne partent pas."""
    _tool("pipedrive_record")(entity="deals", op="list", status="won", limit=250,
                              cursor="c1")
    kwargs = client.list_records.call_args.kwargs
    assert client.list_records.call_args.args == ("deals",)
    assert kwargs["status"] == "won"
    assert kwargs["limit"] == 250 and kwargs["cursor"] == "c1"
    for absent in ("owner_id", "person_id", "org_id", "pipeline_id", "stage_id",
                   "filter_id", "updated_since", "sort_by", "sort_direction",
                   "include_fields", "custom_fields"):
        assert absent not in kwargs


def test_record_get_forwards_the_field_projection(client):
    _tool("pipedrive_record")(entity="persons", op="get", record_id=3,
                              include_fields="name", custom_fields="a" * 40)
    assert client.get_record.call_args.args == ("persons", 3)
    assert client.get_record.call_args.kwargs == {
        "include_fields": "name", "custom_fields": "a" * 40}


def test_record_write_ops_pass_the_body_through(client):
    _tool("pipedrive_record")(entity="deals", op="create", data={"title": "T"})
    assert client.create_record.call_args.args == ("deals", {"title": "T"})
    _tool("pipedrive_record")(entity="deals", op="update", record_id=9,
                              data={"value": 10})
    assert client.update_record.call_args.args == ("deals", 9, {"value": 10})


def test_record_fields_paginates_like_a_list(client):
    """op="fields" partage `limit`/`cursor` avec op="list" — c'est ce qui justifie
    de les fusionner, encore faut-il que les deux soient bien transmis."""
    _tool("pipedrive_record")(entity="deals", op="fields", limit=500, cursor="c2")
    assert client.list_fields.call_args.args == ("deals",)
    assert client.list_fields.call_args.kwargs == {"limit": 500, "cursor": "c2"}


# --- recherche : mono-entité vs transverse -------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("entity", {"entity": "deals"}, "search"),
    ("all", {}, "search_all"),
])
def test_search_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("pipedrive_search")(term="acme", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_search_entity_forwards_the_linked_record_filters(client):
    _tool("pipedrive_search")(term="acme", entity="deals", status="open",
                              person_id=5, exact_match=True)
    assert client.search.call_args.args == ("deals", "acme")
    kwargs = client.search.call_args.kwargs
    assert kwargs["status"] == "open" and kwargs["person_id"] == 5
    assert kwargs["exact_match"] is True
    assert "organization_id" not in kwargs


def test_search_entity_omits_the_filters_not_provided(client):
    _tool("pipedrive_search")(term="acme", entity="persons")
    kwargs = client.search.call_args.kwargs
    for absent in ("person_id", "organization_id", "status"):
        assert absent not in kwargs


def test_search_all_forwards_its_own_two_parameters(client):
    """`item_types` et `search_for_related_items` n'existent QUE sur op="all" —
    ils ne doivent pas se perdre dans la fusion."""
    _tool("pipedrive_search")(term="acme", op="all", item_types="deal,person",
                              search_for_related_items=True)
    kwargs = client.search_all.call_args.kwargs
    assert kwargs["item_types"] == "deal,person"
    assert kwargs["search_for_related_items"] is True


# --- notes (API v1, pagination offset) -----------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_notes"),
    ("create", {"content": "<b>hi</b>", "deal_id": 1}, "create_note"),
])
def test_note_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("pipedrive_note")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_note_list_paginates_by_offset(client):
    _tool("pipedrive_note")(op="list", person_id=4, limit=50, start=100)
    kwargs = client.list_notes.call_args.kwargs
    assert kwargs["person_id"] == 4 and kwargs["limit"] == 50
    assert kwargs["start"] == 100


def test_note_create_requires_a_target(client):
    """Une note sans objet lié est rejetée : autant le dire ici, plutôt que de
    laisser partir un appel que l'API refusera."""
    with pytest.raises(McpError, match="deal_id"):
        _tool("pipedrive_note")(op="create", content="hi")
    client.create_note.assert_not_called()


# --- leads (CRUD resté en v1) --------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_leads"),
    ("create", {"title": "T", "person_id": 1}, "create_lead"),
])
def test_lead_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("pipedrive_lead")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_lead_list_forwards_its_filters(client):
    _tool("pipedrive_lead")(op="list", owner_id=2, filter_id=8,
                            archived_status="not_archived", start=20)
    kwargs = client.list_leads.call_args.kwargs
    assert kwargs["owner_id"] == 2 and kwargs["filter_id"] == 8
    assert kwargs["archived_status"] == "not_archived" and kwargs["start"] == 20


def test_lead_create_builds_the_value_object(client):
    """`amount`/`currency` sont assemblés côté tool en `value` (devise par défaut
    EUR) — un pliage qui se perdrait sans test."""
    _tool("pipedrive_lead")(op="create", title="T", person_id=1, amount=1000)
    assert client.create_lead.call_args.kwargs["value"] == {"amount": 1000,
                                                            "currency": "EUR"}


def test_lead_create_without_amount_sends_no_value(client):
    _tool("pipedrive_lead")(op="create", title="T", organization_id=2,
                            expected_close_date="2026-09-01")
    kwargs = client.create_lead.call_args.kwargs
    assert kwargs["value"] is None
    assert kwargs["expected_close_date"] == "2026-09-01"
    assert client.create_lead.call_args.args == ("T",)


def test_lead_create_requires_a_person_or_an_organization(client):
    with pytest.raises(McpError, match="person_id ou organization_id"):
        _tool("pipedrive_lead")(op="create", title="T")
    client.create_lead.assert_not_called()


# --- utilisateurs ---------------------------------------------------------------

def test_users_lists_account_users(client):
    _tool("pipedrive_users")()
    client.list_users.assert_called_once()


# --- refus -----------------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs", [
    ("pipedrive_record", {"entity": "deals"}),
    ("pipedrive_search", {"term": "acme"}),
    ("pipedrive_note", {}),
    ("pipedrive_lead", {}),
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, kwargs):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être"):
        _tool(tool)(op="nope", **kwargs)


@pytest.mark.parametrize("tool,kwargs,missing", [
    ("pipedrive_record", {"entity": "deals", "op": "get"}, "record_id"),
    ("pipedrive_record", {"entity": "deals", "op": "update", "data": {}}, "record_id"),
    ("pipedrive_record", {"entity": "deals", "op": "delete"}, "record_id"),
    ("pipedrive_record", {"entity": "deals", "op": "create"}, "data"),
    ("pipedrive_record", {"entity": "deals", "op": "update", "record_id": 1}, "data"),
    ("pipedrive_search", {"term": "acme", "op": "entity"}, "entity"),
    ("pipedrive_note", {"op": "create", "deal_id": 1}, "content"),
    ("pipedrive_lead", {"op": "create", "person_id": 1}, "title"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, tool, kwargs, missing):
    with pytest.raises(McpError, match=missing):
        _tool(tool)(**kwargs)
