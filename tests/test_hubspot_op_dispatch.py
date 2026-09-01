"""Dispatch `op=` du tool `hubspot_object` (ADR 0047 §Amendement, appliqué au
connecteur hubspot : 9 tools → 2).

Ce module n'avait AUCUN test de surface : les 9 tools appelaient chacun une méthode
du client, et rien ne vérifiait laquelle. La consolidation par `op=` déplace
précisément le risque là — une op mal câblée appelle silencieusement la mauvaise
méthode (`delete_object` au lieu d'`update_object` : la donnée part à la corbeille
sans qu'aucun test ne rougisse). D'où, pour CHAQUE op : la méthode client appelée
et les arguments transmis, le refus d'une op inconnue, et les arguments
obligatoires manquants.

S'y ajoute ce qui est PROPRE à cette fusion : `properties` et `associations` sont
des homonymes dont le type dépend de l'op (liste de noms en lecture / dict et
objets d'association en écriture). C'est le seul endroit où la consolidation peut
faire perdre du sens — la mauvaise forme doit lever ici, pas produire un 400
opaque chez HubSpot.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
@pytest.fixture
def client(monkeypatch):
    """Faux HubSpotClient + clé résolue.

    `register()` importe `HubSpotClient` à l'appel : patcher l'attribut du module
    oto-core AVANT `register` suffit, pas besoin de toucher au closure `_client`.
    """
    import oto.tools.hubspot.client as hs

    inst = MagicMock()
    monkeypatch.setattr(hs, "HubSpotClient", lambda *a, **k: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    return inst


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import hubspot as H

    m = FastMCP("t")
    H.register(m)
    return asyncio.run(m.get_tool(name)).fn


# --- surface : ce qui reste monté ----------------------------------------------

def test_the_object_surface_stays_one_tool(client):
    """Les huit verbes portant `object_type` restent fusionnés dans
    `hubspot_object` ; `hubspot_list`/`hubspot_property` sont d'autres domaines
    (ils ne partagent aucun paramètre d'enregistrement CRM) et sont couverts par
    `test_hubspot_list_property.py`."""
    from fastmcp import FastMCP
    from oto_mcp.tools import hubspot as H

    m = FastMCP("t")
    H.register(m)
    names = {t.name for t in asyncio.run(m._list_tools())}
    assert "hubspot_object" in names
    assert not [n for n in names if n.startswith("hubspot_object_")]


def test_owners_stays_its_own_tool(client):
    """Aucun paramètre d'objet CRM en commun (ni object_type, ni object_id, ni
    properties) : le fusionner n'aurait factorisé aucun paramètre."""
    _tool("hubspot_owners")()
    client.list_owners.assert_called_once_with()


# --- dispatch op → méthode client ----------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("search", {"object_type": "contacts"}, "search_objects"),
    ("list", {"object_type": "companies"}, "list_objects"),
    ("get", {"object_type": "deals", "object_id": "1"}, "get_object"),
    ("create", {"object_type": "contacts", "properties": {"email": "a@b.c"}},
     "create_object"),
    ("update", {"object_type": "deals", "object_id": "1",
                "properties": {"amount": "10"}}, "update_object"),
    ("delete", {"object_type": "tickets", "object_id": "1"}, "delete_object"),
    ("associations", {"object_type": "contacts", "object_id": "1",
                      "to_object_type": "deals"}, "list_associations"),
    ("add_note", {"object_type": "contacts", "object_id": "1", "body": "coucou"},
     "create_note"),
])
def test_each_op_routes_to_the_right_client_method(client, op, kwargs, method):
    _tool("hubspot_object")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_search_is_the_default_op(client):
    _tool("hubspot_object")(object_type="contacts")
    client.search_objects.assert_called_once()


# --- les arguments transmis (une fusion ne doit rien perdre en route) -----------

def test_search_forwards_query_filters_projection_and_pagination(client):
    filters = [{"propertyName": "email", "operator": "CONTAINS_TOKEN",
                "value": "acme"}]
    _tool("hubspot_object")(
        op="search", object_type="contacts", query="acme", filters=filters,
        properties=["email", "firstname"], limit=25, after="cur")
    args, kw = client.search_objects.call_args
    assert args[0] == "contacts"
    assert kw == {"query": "acme", "filters": filters,
                  "properties": ["email", "firstname"], "limit": 25, "after": "cur"}


def test_list_forwards_projection_and_pagination(client):
    _tool("hubspot_object")(op="list", object_type="companies",
                            properties=["name"], limit=10, after="cur")
    args, kw = client.list_objects.call_args
    assert args[0] == "companies"
    assert kw == {"properties": ["name"], "limit": 10, "after": "cur"}


def test_get_forwards_projection_and_association_types(client):
    _tool("hubspot_object")(op="get", object_type="contacts", object_id="42",
                            properties=["email"],
                            associations=["companies", "deals"])
    args, kw = client.get_object.call_args
    assert args == ("contacts", "42")
    assert kw == {"properties": ["email"],
                  "associations": ["companies", "deals"]}


def test_create_forwards_properties_and_v3_association_objects(client):
    assoc = [{"to": {"id": "7"}, "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                            "associationTypeId": 279}]}]
    _tool("hubspot_object")(op="create", object_type="deals",
                            properties={"dealname": "X", "amount": "10"},
                            associations=assoc)
    args, kw = client.create_object.call_args
    assert args == ("deals", {"dealname": "X", "amount": "10"})
    assert kw == {"associations": assoc}


def test_update_forwards_id_and_properties_positionally(client):
    _tool("hubspot_object")(op="update", object_type="deals", object_id="1",
                            properties={"amount": "99"})
    assert client.update_object.call_args.args == ("deals", "1", {"amount": "99"})


def test_delete_targets_the_object(client):
    _tool("hubspot_object")(op="delete", object_type="tickets", object_id="9")
    assert client.delete_object.call_args.args == ("tickets", "9")


def test_associations_keeps_the_source_then_target_order(client):
    """`object_type`/`to_object_type` inversés rendraient un résultat plausible mais
    faux (les contacts d'un deal au lieu des deals d'un contact)."""
    _tool("hubspot_object")(op="associations", object_type="contacts",
                            object_id="1", to_object_type="deals")
    assert client.list_associations.call_args.args == ("contacts", "1", "deals")


def test_add_note_passes_the_body_first_then_the_target(client):
    """Signature oto-core : `create_note(body, object_type, object_id)`. Une
    permutation attacherait la note au mauvais objet, ou écrirait un id en corps."""
    _tool("hubspot_object")(op="add_note", object_type="contacts", object_id="42",
                            body="rappeler lundi")
    assert client.create_note.call_args.args == ("rappeler lundi", "contacts", "42")


# --- refus : op inconnue -------------------------------------------------------

def test_unknown_op_is_refused_with_the_allowed_list(client):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être"):
        _tool("hubspot_object")(op="nope", object_type="contacts")
    for m in ("search", "list", "get", "create", "update", "delete",
              "associations", "add_note"):
        assert not getattr(client, {
            "search": "search_objects", "list": "list_objects", "get": "get_object",
            "create": "create_object", "update": "update_object",
            "delete": "delete_object", "associations": "list_associations",
            "add_note": "create_note"}[m]).called


def test_unknown_op_message_lists_every_op(client):
    with pytest.raises(McpError) as e:
        _tool("hubspot_object")(op="nope")
    msg = str(e.value)
    for op in ("search", "list", "get", "create", "update", "delete",
               "associations", "add_note"):
        assert f"'{op}'" in msg


# --- refus : arguments obligatoires manquants ----------------------------------

@pytest.mark.parametrize("op,other", [
    ("search", {}), ("list", {}), ("get", {"object_id": "1"}),
    ("create", {"properties": {"a": "b"}}),
    ("update", {"object_id": "1", "properties": {"a": "b"}}),
    ("delete", {"object_id": "1"}),
    ("associations", {"object_id": "1", "to_object_type": "deals"}),
    ("add_note", {"object_id": "1", "body": "x"}),
])
def test_object_type_is_required_by_every_op(client, op, other):
    """`object_type` est l'axe de TOUTES les op : aucune ne doit se replier sur un
    type par défaut (un `contacts` implicite écrirait dans le mauvais objet)."""
    with pytest.raises(McpError, match="object_type"):
        _tool("hubspot_object")(op=op, **other)


@pytest.mark.parametrize("op,kwargs,missing", [
    ("get", {"object_type": "contacts"}, "object_id"),
    ("update", {"object_type": "contacts", "properties": {"a": "b"}}, "object_id"),
    ("delete", {"object_type": "contacts"}, "object_id"),
    ("associations", {"object_type": "contacts", "to_object_type": "deals"},
     "object_id"),
    ("associations", {"object_type": "contacts", "object_id": "1"},
     "to_object_type"),
    ("add_note", {"object_type": "contacts", "object_id": "1"}, "body"),
    ("add_note", {"object_type": "contacts", "body": "x"}, "object_id"),
    ("create", {"object_type": "contacts"}, "properties"),
    ("update", {"object_type": "contacts", "object_id": "1"}, "properties"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, op, kwargs, missing):
    with pytest.raises(McpError, match=missing) as e:
        _tool("hubspot_object")(op=op, **kwargs)
    assert f"op='{op}'" in str(e.value)


# --- les paramètres homonymes (le seul vrai coût de la fusion) ------------------

@pytest.mark.parametrize("op,kwargs", [
    ("search", {"object_type": "contacts"}),
    ("list", {"object_type": "contacts"}),
    ("get", {"object_type": "contacts", "object_id": "1"}),
])
def test_read_ops_refuse_a_write_shaped_properties(client, op, kwargs):
    """`properties` en lecture = des NOMS. Un dict passé là est la forme d'écriture :
    HubSpot le sérialiserait en `properties=a,b` et rendrait n'importe quoi."""
    with pytest.raises(McpError, match="properties"):
        _tool("hubspot_object")(op=op, properties={"email": "a@b.c"}, **kwargs)


def test_get_refuses_association_objects_where_types_are_expected(client):
    with pytest.raises(McpError, match="associations"):
        _tool("hubspot_object")(
            op="get", object_type="contacts", object_id="1",
            associations=[{"to": {"id": "7"}}])
    client.get_object.assert_not_called()


@pytest.mark.parametrize("op,kwargs", [
    ("create", {"object_type": "contacts"}),
    ("update", {"object_type": "contacts", "object_id": "1"}),
])
def test_write_ops_refuse_a_read_shaped_properties(client, op, kwargs):
    """Une liste de noms sur create/update = un objet créé VIDE côté HubSpot."""
    with pytest.raises(McpError, match="properties"):
        _tool("hubspot_object")(op=op, properties=["email", "firstname"], **kwargs)
    client.create_object.assert_not_called()
    client.update_object.assert_not_called()


def test_create_refuses_association_type_names_where_objects_are_expected(client):
    with pytest.raises(McpError, match="associations"):
        _tool("hubspot_object")(op="create", object_type="deals",
                                properties={"dealname": "X"},
                                associations=["contacts"])
    client.create_object.assert_not_called()


def test_none_stays_none_on_both_shapes(client):
    """Les projections/associations optionnelles ne doivent pas devenir [] : le
    client omet le paramètre quand il est None (sinon HubSpot renvoie les objets
    sans aucune propriété)."""
    _tool("hubspot_object")(op="get", object_type="contacts", object_id="1")
    kw = client.get_object.call_args.kwargs
    assert kw["properties"] is None and kw["associations"] is None
