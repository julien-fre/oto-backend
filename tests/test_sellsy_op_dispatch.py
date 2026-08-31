"""Dispatch `op=` / `kind=` de la surface `sellsy_*` (ADR 0047 §Amendement).

Ce que ce fichier verrouille, et que `test_sellsy.py` ne couvrait qu'en pointillé :
la table de routage complète. Tout passe par un helper unique (`_crud`) qui reçoit
la RESSOURCE en chaîne — donc une op mal câblée n'échoue pas au boot, elle appelle
silencieusement la bonne méthode du client sur la MAUVAISE ressource (un
`op="delete"` de tiers qui viserait `invoices`), ou la mauvaise méthode sur la
bonne ressource. Rien ne casserait avant la prod, chez le client, sur ses données.

D'où, pour CHAQUE op de CHAQUE tool : la méthode client appelée **et** la ressource
visée ; le refus d'une op inconnue (qui doit nommer les ops valides) ; le refus d'un
`kind` inconnu ; et chaque argument obligatoire manquant, nommé avec son op — jamais
un fallback muet.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import sellsy

    m = FastMCP("t")
    sellsy.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    """Faux SellsyClient + credential résolu.

    Le patch doit être posé AVANT `register()` : le module fait
    `from oto.tools.sellsy import SellsyClient` dans `register`, et `_client()`
    ferme sur ce nom.
    """
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.sellsy.SellsyClient",
                        MagicMock(return_value=inst))
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider, **kw: {"client_id": "id",
                                                "client_secret": "sec"})
    return inst


# Les 10 (tool, kind) qui partagent le socle CRUD, avec la ressource attendue.
_CRUD_SURFACES = [
    ("sellsy_third_party", {"kind": "company"}, "companies"),
    ("sellsy_third_party", {"kind": "individual"}, "individuals"),
    ("sellsy_contact", {}, "contacts"),
    ("sellsy_opportunity", {}, "opportunities"),
    ("sellsy_document", {"kind": "estimate"}, "estimates"),
    ("sellsy_document", {"kind": "order"}, "orders"),
    ("sellsy_document", {"kind": "invoice"}, "invoices"),
    ("sellsy_document", {"kind": "credit_note"}, "credit-notes"),
    ("sellsy_item", {}, "items"),
    ("sellsy_task", {}, "tasks"),
]

_CRUD_OPS = [
    ("list", {}, "list_records"),
    ("search", {"filters": {"name": "acme"}}, "search_records"),
    ("get", {"record_id": 1}, "get_record"),
    ("create", {"data": {"name": "Acme"}}, "create_record"),
    ("update", {"record_id": 1, "data": {"name": "Acme"}}, "update_record"),
    ("delete", {"record_id": 1}, "delete_record"),
    ("custom_fields", {"record_id": 1}, "get_custom_fields"),
]


# --- socle CRUD : la bonne méthode SUR LA BONNE RESSOURCE ---------------------

@pytest.mark.parametrize("tool,fixed,resource", _CRUD_SURFACES)
@pytest.mark.parametrize("op,kwargs,method", _CRUD_OPS)
def test_crud_ops_route_to_the_right_method_and_resource(
        client, tool, fixed, resource, op, kwargs, method):
    _tool(tool)(op=op, **fixed, **kwargs)

    call = getattr(client, method)
    assert call.call_count == 1, f"{tool}{fixed} op={op} : {method} non appelée"
    assert call.call_args.args[0] == resource


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_records"),
    ("search", {"filters": {"status": ["paid"]}}, "search_records"),
    ("get", {"record_id": 1}, "get_record"),
    ("delete", {"record_id": 1}, "delete_record"),
    ("custom_fields", {"record_id": 1}, "get_custom_fields"),
])
def test_payment_ops_route_to_the_payments_resource(client, op, kwargs, method):
    """L'encaissement se LIT et se supprime ici ; il s'enregistre sur le tiers
    (`sellsy_third_party(op="record_payment")`) — ce tool n'a donc pas de `data`."""
    _tool("sellsy_payment")(op=op, **kwargs)

    call = getattr(client, method)
    assert call.call_count == 1
    assert call.call_args.args[0] == "payments"


def test_search_passes_the_filters_and_the_paging(client):
    _tool("sellsy_third_party")(kind="individual", op="search",
                                filters={"email": "a@b.fr"}, limit=50,
                                offset="cur", order="created", direction="desc")

    args, kwargs = client.search_records.call_args
    assert args[0] == "individuals" and args[1] == {"email": "a@b.fr"}
    assert kwargs["limit"] == 50 and kwargs["offset"] == "cur"
    assert kwargs["order"] == "created" and kwargs["direction"] == "desc"


def test_search_without_filters_sends_an_empty_dict(client):
    """`None` remonterait tel quel au client : on envoie `{}` (pas de filtre),
    jamais un `None` que l'amont interpréterait à sa façon."""
    _tool("sellsy_task")(op="search")
    assert client.search_records.call_args.args[1] == {}


@pytest.mark.parametrize("op,extra", [("list", {}),
                                      ("search", {"filters": {"name": "acme"}})])
def test_all_pages_switches_to_the_paginating_helper(client, op, extra):
    _tool("sellsy_document")(kind="invoice", op=op, all_pages=True, max_pages=3,
                             **extra)

    assert client.list_all.call_args.args[0] == "invoices"
    assert client.list_all.call_args.kwargs["max_pages"] == 3
    assert not client.list_records.called and not client.search_records.called


def test_dry_run_maps_to_the_api_verify_flag(client):
    """`verify=true` côté Sellsy : le payload est validé, rien n'est persisté."""
    _tool("sellsy_item")(op="create", data={"type": "product", "reference": "R1"},
                         dry_run=True)
    assert client.create_record.call_args.kwargs["verify"] is True


def test_custom_fields_writes_only_when_data_is_given(client):
    tool = _tool("sellsy_opportunity")

    tool(op="custom_fields", record_id=42)
    assert client.get_custom_fields.called and not client.set_custom_fields.called

    tool(op="custom_fields", record_id=42,
         data={"custom_fields": [{"id": 12, "value": "x"}]})
    assert client.set_custom_fields.call_args.args[:2] == ("opportunities", 42)
    assert client.set_custom_fields.call_args.args[2] == [{"id": 12, "value": "x"}]


# --- sous-ressources ----------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,expected", [
    ("sellsy_third_party", {"kind": "company", "op": "contacts", "record_id": 1},
     ("companies", 1, "contacts")),
    ("sellsy_third_party", {"kind": "individual", "op": "contacts", "record_id": 1},
     ("individuals", 1, "contacts")),
    ("sellsy_contact", {"op": "companies", "record_id": 2}, ("contacts", 2, "companies")),
    ("sellsy_item", {"op": "prices", "record_id": 3}, ("items", 3, "prices")),
    ("sellsy_document", {"kind": "invoice", "op": "payments", "record_id": 4},
     ("invoices", 4, "payments")),
    ("sellsy_document", {"kind": "invoice", "op": "linked", "record_id": 5},
     ("invoices", 5, "credit-notes")),
    ("sellsy_document", {"kind": "credit_note", "op": "linked", "record_id": 6},
     ("credit-notes", 6, "invoices")),
])
def test_sub_resource_ops_list_under_their_parent(client, tool, kwargs, expected):
    """`linked` est le cas qui se croise le plus facilement : les avoirs d'une
    facture, les factures d'un avoir — inverser rendrait une liste plausible."""
    _tool(tool)(**kwargs)
    assert client.list_sub.call_args.args[:3] == expected


# --- verbes d'action (POST/PATCH/PUT dédiés) ----------------------------------

@pytest.mark.parametrize("kind,resource", [("company", "companies"),
                                           ("individual", "individuals")])
def test_convert_targets_the_client_status_by_default(client, kind, resource):
    """« prospect → client » est irréversible côté Sellsy : la cible par défaut
    doit être explicite dans le payload, pas laissée à l'amont."""
    _tool("sellsy_third_party")(kind=kind, op="convert", record_id=7)

    assert client.act.call_args.args[:3] == (resource, 7, "convert")
    assert client.act.call_args.kwargs["payload"] == {"target": "client"}


def test_convert_honours_an_explicit_payload(client):
    _tool("sellsy_third_party")(kind="company", op="convert", record_id=7,
                                data={"target": "supplier"})
    assert client.act.call_args.kwargs["payload"] == {"target": "supplier"}


@pytest.mark.parametrize("kind,resource", [("company", "companies"),
                                           ("individual", "individuals")])
def test_record_payment_posts_on_the_third_party(client, kind, resource):
    payload = {"amount": {"value": "120.00", "currency": "EUR"}, "type": "credit"}
    _tool("sellsy_third_party")(kind=kind, op="record_payment", record_id=8,
                                data=payload)

    assert client.act.call_args.args[:3] == (resource, 8, "payments")
    assert client.act.call_args.kwargs["payload"] == payload


def test_link_and_unlink_contact_use_the_dedicated_endpoints(client):
    tool = _tool("sellsy_third_party")

    tool(kind="company", op="link_contact", record_id=1, contact_id=9,
         data={"position": "DAF"})
    assert client.link_contact_to_company.call_args.args[:2] == (1, 9)
    assert client.link_contact_to_company.call_args.kwargs["payload"] == {
        "position": "DAF"}

    tool(kind="company", op="unlink_contact", record_id=1, contact_id=9)
    assert client.unlink_contact_from_company.call_args.args[:2] == (1, 9)


@pytest.mark.parametrize("op", ["link_contact", "unlink_contact"])
def test_linking_a_contact_is_refused_on_an_individual(client, op):
    """Le rattachement de contact n'existe que côté société : laisser passer
    produirait un 404 opaque sur `/individuals/{id}/contacts/{cid}`."""
    with pytest.raises(McpError, match="company"):
        _tool("sellsy_third_party")(kind="individual", op=op, record_id=1,
                                    contact_id=9)
    assert not client.link_contact_to_company.called
    assert not client.unlink_contact_from_company.called


def test_moving_an_opportunity_uses_step_rank_in_patch(client):
    """Changer d'étape passe par `step-rank` (PATCH) — un `update` ne la bouge pas."""
    _tool("sellsy_opportunity")(op="move", record_id=7, step=299, before_sibling=57)

    assert client.act.call_args.args[:3] == ("opportunities", 7, "step-rank")
    assert client.act.call_args.kwargs["payload"] == {"step": 299,
                                                      "before_sibling": 57}
    assert client.act.call_args.kwargs["method"] == "PATCH"


def test_moving_without_a_sibling_omits_the_rank(client):
    """Sans `before_sibling`, l'opportunité se pose en dernier rang de l'étape :
    la clé ne doit pas partir à None (l'amont la lirait comme une position)."""
    _tool("sellsy_opportunity")(op="move", record_id=7, step=299)
    assert client.act.call_args.kwargs["payload"] == {"step": 299}


@pytest.mark.parametrize("kind,resource", [("invoice", "invoices"),
                                           ("credit_note", "credit-notes")])
def test_validate_is_reserved_to_the_accounting_documents(client, kind, resource):
    """Valider est IRRÉVERSIBLE (numéro définitif, document comptable) : réservé à
    la facture et à l'avoir. Un devis change d'état par `op="status"`."""
    _tool("sellsy_document")(kind=kind, op="validate", record_id=1)
    assert client.act.call_args.args[:3] == (resource, 1, "validate")


@pytest.mark.parametrize("kind", ["estimate", "order"])
def test_validate_is_refused_on_the_other_documents(client, kind):
    with pytest.raises(McpError, match="validate"):
        _tool("sellsy_document")(kind=kind, op="validate", record_id=1)
    assert not client.act.called


def test_status_is_reserved_to_the_estimate(client):
    _tool("sellsy_document")(kind="estimate", op="status", record_id=2,
                             status="accepted")
    assert client.act.call_args.args[:3] == ("estimates", 2, "status")
    assert client.act.call_args.kwargs["payload"] == {"status": "accepted"}
    assert client.act.call_args.kwargs["method"] == "PUT"


@pytest.mark.parametrize("kind", ["invoice", "order", "credit_note"])
def test_status_is_refused_on_the_other_documents(client, kind):
    with pytest.raises(McpError, match="status"):
        _tool("sellsy_document")(kind=kind, op="status", record_id=1, status="sent")


@pytest.mark.parametrize("kind", ["estimate", "order"])
def test_linked_is_refused_where_there_is_nothing_to_link(client, kind):
    """`linked` ne relie que facture ↔ avoir."""
    with pytest.raises(McpError, match="linked"):
        _tool("sellsy_document")(kind=kind, op="linked", record_id=1)
    assert not client.list_sub.called


# --- référentiels -------------------------------------------------------------

@pytest.mark.parametrize("kind,path", [
    ("staffs", "staffs"),
    ("custom_fields", "custom-fields"),
    ("pipelines", "opportunities/pipelines"),
    ("sources", "opportunities/sources"),
    ("categories", "opportunities/categories"),
    ("payment_methods", "payments/methods"),
    ("taxes", "taxes"),
    ("units", "units"),
    ("currencies", "currencies"),
    ("countries", "countries"),
    ("rate_categories", "rate-categories"),
    ("accounting_codes", "accounting-codes"),
    ("task_labels", "tasks/labels"),
    ("document_layouts", "document-layouts"),
])
def test_ref_kinds_map_to_their_api_path(client, kind, path):
    """Un chemin faux rendrait un 404 là où l'agent attend des ids à ne PAS deviner."""
    _tool("sellsy_ref")(kind=kind, limit=5)
    assert client.list_records.call_args.args[0] == path
    assert client.list_records.call_args.kwargs["limit"] == 5


def test_ref_steps_are_nested_under_their_pipeline(client):
    _tool("sellsy_ref")(kind="steps", pipeline_id=5)
    assert client.list_records.call_args.args[0] == "opportunities/pipelines/5/steps"


def test_ref_smart_tags_use_the_autocomplete_endpoint(client):
    _tool("sellsy_ref")(kind="smart_tags", linked_type="opportunity")
    assert client.smart_tags_autocomplete.call_args.args[0] == "opportunity"
    assert not client.list_records.called


def test_global_search_passes_its_facets(client):
    _tool("sellsy_search")(q="acme", types=["company"], limit=10, archived=True)

    assert client.global_search.call_args.args[0] == "acme"
    assert client.global_search.call_args.kwargs == {"types": ["company"],
                                                     "limit": 10,
                                                     "archived": True}


# --- refus : op inconnue ------------------------------------------------------

@pytest.mark.parametrize("tool,fixed,expected_ops", [
    ("sellsy_third_party", {"kind": "company"},
     ("contacts", "convert", "link_contact", "unlink_contact", "record_payment")),
    ("sellsy_contact", {}, ("companies",)),
    ("sellsy_opportunity", {}, ("move",)),
    ("sellsy_document", {"kind": "invoice"},
     ("validate", "status", "payments", "linked")),
    ("sellsy_payment", {}, ()),
    ("sellsy_item", {}, ("prices",)),
    ("sellsy_task", {}, ()),
])
def test_unknown_op_is_refused_and_names_the_accepted_ones(
        client, tool, fixed, expected_ops):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur `list` (l'agent croirait son écriture honorée)."""
    with pytest.raises(McpError, match="op inconnu") as e:
        _tool(tool)(op="archive", **fixed)

    msg = str(e.value)
    for op in ("list", "search", "get", "create", "update", "delete",
               "custom_fields", *expected_ops):
        assert op in msg, f"{tool} : l'erreur ne mentionne pas l'op '{op}'"


# --- refus : kind inconnu -----------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,expected", [
    ("sellsy_third_party", {"kind": "prospect", "op": "list"},
     ("company", "individual")),
    ("sellsy_document", {"kind": "quote", "op": "list"},
     ("estimate", "invoice", "order", "credit_note")),
    ("sellsy_ref", {"kind": "widgets"}, ("staffs", "steps", "smart_tags")),
])
def test_unknown_kind_is_refused_and_names_the_accepted_ones(
        client, tool, kwargs, expected):
    with pytest.raises(McpError, match="kind") as e:
        _tool(tool)(**kwargs)

    msg = str(e.value)
    for kind in expected:
        assert kind in msg, f"{tool} : l'erreur ne mentionne pas kind '{kind}'"
    assert not client.list_records.called


# --- refus : argument obligatoire manquant ------------------------------------

@pytest.mark.parametrize("tool,kwargs,missing", [
    # le socle commun, sur une surface de chaque famille
    ("sellsy_third_party", {"kind": "company", "op": "get"}, "record_id"),
    ("sellsy_third_party", {"kind": "company", "op": "create"}, "data"),
    ("sellsy_third_party", {"kind": "individual", "op": "update", "record_id": 1},
     "data"),
    ("sellsy_third_party", {"kind": "company", "op": "delete"}, "record_id"),
    ("sellsy_third_party", {"kind": "company", "op": "custom_fields"}, "record_id"),
    # les verbes propres au tiers
    ("sellsy_third_party", {"kind": "company", "op": "contacts"}, "record_id"),
    ("sellsy_third_party", {"kind": "company", "op": "convert"}, "record_id"),
    ("sellsy_third_party", {"kind": "company", "op": "link_contact", "record_id": 1},
     "contact_id"),
    ("sellsy_third_party", {"kind": "company", "op": "unlink_contact",
                            "record_id": 1}, "contact_id"),
    ("sellsy_third_party", {"kind": "company", "op": "record_payment",
                            "record_id": 1}, "data"),
    ("sellsy_third_party", {"kind": "company", "op": "record_payment"}, "record_id"),
    # les autres tools
    ("sellsy_contact", {"op": "companies"}, "record_id"),
    ("sellsy_opportunity", {"op": "move", "record_id": 1}, "step"),
    ("sellsy_opportunity", {"op": "move", "step": 2}, "record_id"),
    ("sellsy_document", {"kind": "estimate", "op": "status", "record_id": 1},
     "status"),
    ("sellsy_document", {"kind": "invoice", "op": "validate"}, "record_id"),
    ("sellsy_item", {"op": "get"}, "record_id"),
    ("sellsy_item", {"op": "prices"}, "record_id"),
    ("sellsy_payment", {"op": "delete"}, "record_id"),
    ("sellsy_task", {"op": "create"}, "data"),
    ("sellsy_ref", {"kind": "steps"}, "pipeline_id"),
    ("sellsy_ref", {"kind": "smart_tags"}, "linked_type"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, tool, kwargs, missing):
    """Jamais de fallback silencieux : l'erreur dit QUEL argument manque POUR QUELLE
    op — c'est ce qui permet à l'agent de se corriger sans deviner."""
    with pytest.raises(McpError, match=missing):
        _tool(tool)(**kwargs)
