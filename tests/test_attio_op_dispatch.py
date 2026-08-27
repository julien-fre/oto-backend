"""Dispatch `op=` des 10 tools `attio_*` (ADR 0047 §Amendement, appliqué au
connecteur attio le 2026-08-11 : 56 tools → 10).

Attio n'avait AUCUN test : c'était le plus gros connecteur du catalogue, et sa
consolidation déplace le risque précisément là où rien ne regardait — une op mal
câblée appelle silencieusement la mauvaise méthode du client, et rien ne casse au
boot. D'où, pour chaque op : la méthode client appelée, le refus explicite d'une
op inconnue (jamais un fallback muet) et les arguments obligatoires.

⚠️ Ce connecteur ÉCRIT sur un CRM réel (create/update/delete sur des records,
notes, tâches, listes, entrées, commentaires de clients). Trois invariants sont
donc verrouillés ici en plus du routage :
- `test_default_op_never_writes` : le défaut de CHAQUE tool est une lecture ;
- chaque op d'écriture a son cas, qui vérifie la méthode appelée ET le mutisme
  des voisines dangereuses (aucune `create`/`update`/`delete` collatérale) ;
- un argument obligatoire manquant lève en NOMMANT l'op et l'argument.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError

# Les 10 tools, avec les arguments que leur SCHÉMA rend obligatoires (et, pour
# `attio_comment`, le filtre de parent qu'exige l'API sur la liste de threads).
_TOOLS = {
    "attio_record": {"object": "companies"},
    "attio_note": {},
    "attio_task": {},
    "attio_list": {},
    "attio_entry": {"list_id_or_slug": "l1"},
    "attio_workspace_member": {},
    "attio_comment": {"parent_object": "companies", "parent_record_id": "r1"},
    "attio_meeting": {},
    "attio_object": {},
    "attio_attribute": {"target": "objects", "identifier": "companies"},
}

_WRITE_VERBS = ("create", "update", "delete")


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import attio as A

    m = FastMCP("t")
    A.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _attr(mock, path: str):
    """`_attr(client, "companies.create")` → le sous-mock appelé par le tool."""
    for part in path.split("."):
        mock = getattr(mock, part)
    return mock


def _assert_no_stray_write(client, expected: str = ""):
    """Aucune écriture COLLATÉRALE : la seule méthode `create`/`update`/`delete`
    touchée est celle attendue. C'est le garde-fou du refactor — un dispatch qui
    part sur la mauvaise branche appellerait une voisine destructrice."""
    for name, _args, _kwargs in client.mock_calls:
        if name == expected:
            continue
        assert name.rsplit(".", 1)[-1] not in _WRITE_VERBS, (
            f"écriture collatérale : {name} (attendu : {expected or 'aucune'})")


@pytest.fixture
def client(monkeypatch):
    """Faux AttioClient + clé résolue. `register()` importe `AttioClient` à
    l'appel, donc patcher le module oto-core AVANT suffit."""
    import oto.tools.attio.client as core

    inst = MagicMock()
    monkeypatch.setattr(core, "AttioClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    # Défaut inoffensif : pas de membre ⟹ pas d'auto-remplissage d'`owner` sur la
    # création de deal (le cas nominal a son propre test).
    inst.workspace_members.list.return_value = {"data": []}
    return inst


# --- records : companies / people / deals -------------------------------------

@pytest.mark.parametrize("object", ["companies", "people", "deals"])
@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list"),
    ("get", {"record_id": "r1"}, "get"),
    ("search", {"query": "acme"}, "search"),
    ("create", {"attributes": {"name": "Acme"}}, "create"),
    ("update", {"record_id": "r1", "attributes": {"name": "Acme"}}, "update"),
    ("delete", {"record_id": "r1"}, "delete"),
])
def test_record_ops_route_to_the_right_object_and_method(
        client, object, op, kwargs, method):
    """Les 18 ex-tools `attio_{list,get,search,create,update,delete}_{company,
    person,deal}` : même verbe, l'objet devient un paramètre."""
    _tool("attio_record")(object=object, op=op, **kwargs)
    _attr(client, f"{object}.{method}").assert_called_once()


def test_record_refuses_an_unknown_object(client):
    """Attio a des objets CUSTOM, mais le client n'expose que les trois standard :
    un objet inconnu doit être nommé, pas silencieusement traité."""
    with pytest.raises(McpError, match="object doit être"):
        _tool("attio_record")(object="products", op="list")
    _assert_no_stray_write(client)


def test_record_list_is_paginated(client):
    _tool("attio_record")(object="people", op="list", limit=10, offset=20)
    assert client.people.list.call_args.kwargs == {"limit": 10, "offset": 20}


def test_record_create_deal_autofills_the_owner(client):
    """`owner` est obligatoire côté workspace pour un deal : sans lui la création
    échoue, et l'agent ne peut pas le deviner."""
    client.workspace_members.list.return_value = {
        "data": [{"id": {"workspace_member_id": "wm-1"}}]}
    _tool("attio_record")(object="deals", op="create", attributes={"name": "D"})
    assert client.deals.create.call_args.kwargs["owner"] == [{
        "referenced_actor_type": "workspace-member",
        "referenced_actor_id": "wm-1",
    }]


def test_record_create_deal_keeps_an_explicit_owner(client):
    client.workspace_members.list.return_value = {
        "data": [{"id": {"workspace_member_id": "wm-1"}}]}
    _tool("attio_record")(object="deals", op="create",
                          attributes={"name": "D", "owner": ["explicite"]})
    assert client.deals.create.call_args.kwargs["owner"] == ["explicite"]


def test_record_create_only_autofills_owner_for_deals(client):
    """L'auto-remplissage est une spécificité de l'objet deals — une company ne
    doit pas hériter d'un champ `owner` inventé."""
    client.workspace_members.list.return_value = {
        "data": [{"id": {"workspace_member_id": "wm-1"}}]}
    _tool("attio_record")(object="companies", op="create", attributes={"name": "A"})
    assert "owner" not in client.companies.create.call_args.kwargs


# --- notes / tâches / listes / entrées / membres / commentaires ----------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "notes.list"),
    ("get", {"note_id": "n1"}, "notes.get"),
    ("create", {"parent_object": "companies", "parent_record_id": "r1",
                "title": "T", "content": "corps"}, "notes.create"),
    ("delete", {"note_id": "n1"}, "notes.delete"),
])
def test_note_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_note")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


def test_note_list_scopes_on_the_parent_when_given(client):
    # Les bornes de page (`limit`/`offset`, ajoutées pour les signaux #586/#597)
    # voyagent aussi : on n'assertionne donc que le SCOPE, pas le dict entier —
    # cf. tests/test_attio_listing_window.py pour la fenêtre elle-même.
    _tool("attio_note")(op="list", parent_object="deals", parent_record_id="r1")
    kwargs = client.notes.list.call_args.kwargs
    assert kwargs["parent_object"] == "deals" and kwargs["parent_record_id"] == "r1"


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "tasks.list"),
    ("get", {"task_id": "t1"}, "tasks.get"),
    ("create", {"content": "rappeler Acme"}, "tasks.create"),
    ("update", {"task_id": "t1", "is_completed": True}, "tasks.update"),
    ("delete", {"task_id": "t1"}, "tasks.delete"),
])
def test_task_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_task")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


def test_task_completed_filters_the_listing_and_is_not_a_setter(client):
    """`completed` (filtre de liste) et `is_completed` (écriture) coexistent dans
    la signature fusionnée : les confondre ferait cocher une tâche en croyant la
    chercher."""
    _tool("attio_task")(op="list", completed=False)
    assert client.tasks.list.call_args.kwargs["completed"] is False
    client.tasks.update.assert_not_called()


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "lists.list"),
    ("get", {"list_id_or_slug": "l1"}, "lists.get"),
    ("views", {"list_id_or_slug": "l1"}, "lists.views"),
    ("create", {"name": "Prospects", "parent_object": "companies"}, "lists.create"),
    ("update", {"list_id_or_slug": "l1", "attributes": {"name": "N"}}, "lists.update"),
])
def test_list_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_list")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


@pytest.mark.parametrize("op,kwargs,method", [
    ("query", {}, "entries.query"),
    ("get", {"entry_id": "e1"}, "entries.get"),
    ("create", {"parent_object": "companies", "parent_record_id": "r1"},
     "entries.create"),
    ("update", {"entry_id": "e1", "entry_values": {"stage": "x"}}, "entries.update"),
    ("delete", {"entry_id": "e1"}, "entries.delete"),
])
def test_entry_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_entry")(list_id_or_slug="l1", op=op, **kwargs)
    _attr(client, method).assert_called_once()
    assert _attr(client, method).call_args.args[0] == "l1"


def test_entry_update_defaults_to_patch(client):
    """PATCH ajoute aux multiselect, PUT écrase : le défaut doit rester le moins
    destructeur des deux."""
    _tool("attio_entry")(list_id_or_slug="l1", op="update", entry_id="e1",
                         entry_values={"a": 1})
    assert client.entries.update.call_args.kwargs["overwrite_multiselect"] is False


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "workspace_members.list"),
    ("get", {"workspace_member_id": "wm1"}, "workspace_members.get"),
])
def test_workspace_member_ops_route_to_the_right_client_method(
        client, op, kwargs, method):
    _tool("attio_workspace_member")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


@pytest.mark.parametrize("op,kwargs,method", [
    ("threads", {"parent_object": "companies", "parent_record_id": "r1"},
     "threads.list"),
    ("thread", {"thread_id": "th1"}, "threads.get"),
    ("get", {"comment_id": "c1"}, "comments.get"),
    ("create", {"content": "salut", "author_id": "wm1", "thread_id": "th1"},
     "comments.create"),
    ("delete", {"comment_id": "c1"}, "comments.delete"),
])
def test_comment_ops_route_to_the_right_client_method(client, op, kwargs, method):
    """Threads et comments = un seul objet (le fil), même tuple d'ancrage."""
    _tool("attio_comment")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


def test_comment_threads_refuses_an_unfiltered_listing(client):
    """Gotcha empirique : `GET /threads` sans filtre répond 400 — autant le dire
    ici, actionnable, plutôt que de laisser remonter l'erreur opaque."""
    with pytest.raises(McpError, match="parent_object"):
        _tool("attio_comment")(op="threads")
    client.threads.list.assert_not_called()


def test_comment_threads_accepts_a_list_entry_anchor(client):
    """L'ancrage par entrée de liste est l'AUTRE filtre valide : le refus ne doit
    pas exiger le couple parent_* et rejeter un appel qui marche."""
    _tool("attio_comment")(op="threads", list_id="l1", entry_id="e1")
    client.threads.list.assert_called_once()


# --- meetings / meta ----------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "meetings.list"),
    ("get", {"meeting_id": "m1"}, "meetings.get"),
    ("recordings", {"meeting_id": "m1"}, "call_recordings.list"),
    ("recording", {"meeting_id": "m1", "call_recording_id": "cr1"},
     "call_recordings.get"),
    ("transcript", {"meeting_id": "m1", "call_recording_id": "cr1"},
     "call_recordings.transcript"),
])
def test_meeting_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_meeting")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "objects.list"),
    ("get", {"object_id_or_slug": "companies"}, "objects.get"),
    ("views", {"object_id_or_slug": "companies"}, "objects.views"),
])
def test_object_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_object")(op=op, **kwargs)
    _attr(client, method).assert_called_once()


@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "attributes.list"),
    ("get", {"attribute": "name"}, "attributes.get"),
    ("options", {"attribute": "stage"}, "attributes.options"),
    ("statuses", {"attribute": "stage"}, "attributes.statuses"),
])
def test_attribute_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("attio_attribute")(target="objects", identifier="companies", op=op, **kwargs)
    called = _attr(client, method)
    called.assert_called_once()
    assert called.call_args.args[:2] == ("objects", "companies")


def test_attribute_target_can_be_lists(client):
    """Le schéma d'une LISTE se lit par le même tool — c'est pour ça que
    `target` existe (et pourquoi objets et attributs ne sont pas fusionnés)."""
    _tool("attio_attribute")(target="lists", identifier="l1", op="list")
    assert client.attributes.list.call_args.args == ("lists", "l1")


# --- écritures : la méthode attendue, et AUCUNE voisine dangereuse ------------

@pytest.mark.parametrize("tool,kwargs,path", [
    ("attio_record", {"object": "companies", "op": "create",
                      "attributes": {"name": "Acme"}}, "companies.create"),
    ("attio_record", {"object": "companies", "op": "update", "record_id": "r1",
                      "attributes": {"name": "Acme"}}, "companies.update"),
    ("attio_record", {"object": "companies", "op": "delete",
                      "record_id": "r1"}, "companies.delete"),
    ("attio_record", {"object": "people", "op": "delete",
                      "record_id": "r1"}, "people.delete"),
    ("attio_record", {"object": "deals", "op": "delete",
                      "record_id": "r1"}, "deals.delete"),
    ("attio_note", {"op": "create", "parent_object": "companies",
                    "parent_record_id": "r1", "title": "T",
                    "content": "c"}, "notes.create"),
    ("attio_note", {"op": "delete", "note_id": "n1"}, "notes.delete"),
    ("attio_task", {"op": "create", "content": "faire"}, "tasks.create"),
    ("attio_task", {"op": "update", "task_id": "t1",
                    "is_completed": True}, "tasks.update"),
    ("attio_task", {"op": "delete", "task_id": "t1"}, "tasks.delete"),
    ("attio_list", {"op": "create", "name": "L",
                    "parent_object": "companies"}, "lists.create"),
    ("attio_list", {"op": "update", "list_id_or_slug": "l1",
                    "attributes": {"name": "N"}}, "lists.update"),
    ("attio_entry", {"list_id_or_slug": "l1", "op": "create",
                     "parent_object": "companies",
                     "parent_record_id": "r1"}, "entries.create"),
    ("attio_entry", {"list_id_or_slug": "l1", "op": "update", "entry_id": "e1",
                     "entry_values": {"a": 1}}, "entries.update"),
    ("attio_entry", {"list_id_or_slug": "l1", "op": "delete",
                     "entry_id": "e1"}, "entries.delete"),
    ("attio_comment", {"op": "create", "content": "hop", "author_id": "wm1",
                       "thread_id": "th1"}, "comments.create"),
    ("attio_comment", {"op": "delete", "comment_id": "c1"}, "comments.delete"),
])
def test_write_ops_call_exactly_one_write_method(client, tool, kwargs, path):
    """Une op d'écriture appelle SA méthode, et rien d'autre qui écrive."""
    _tool(tool)(**kwargs)
    _attr(client, path).assert_called_once()
    _assert_no_stray_write(client, expected=path)


@pytest.mark.parametrize("tool,minimal", sorted(_TOOLS.items()))
def test_default_op_never_writes(client, tool, minimal):
    """Invariant central du refactor : `op` a un défaut, et ce défaut est une
    LECTURE. Un appel sans `op` ne peut ni écrire ni supprimer dans le CRM."""
    _tool(tool)(**minimal)
    assert client.mock_calls, f"{tool} n'a appelé aucune méthode client"
    _assert_no_stray_write(client)


# --- refus --------------------------------------------------------------------

@pytest.mark.parametrize("tool,minimal", sorted(_TOOLS.items()))
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, minimal):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être"):
        _tool(tool)(op="nope", **minimal)
    assert not client.mock_calls, "une op inconnue ne doit RIEN appeler"


@pytest.mark.parametrize("tool,op,kwargs,missing", [
    ("attio_record", "get", {"object": "companies"}, "record_id"),
    ("attio_record", "search", {"object": "companies"}, "query"),
    ("attio_record", "create", {"object": "companies"}, "attributes"),
    ("attio_record", "update", {"object": "companies"}, "record_id"),
    ("attio_record", "update", {"object": "companies", "record_id": "r1"},
     "attributes"),
    ("attio_record", "delete", {"object": "companies"}, "record_id"),
    ("attio_note", "get", {}, "note_id"),
    ("attio_note", "create", {}, "parent_object"),
    ("attio_note", "create", {"parent_object": "companies",
                              "parent_record_id": "r1"}, "title"),
    ("attio_note", "delete", {}, "note_id"),
    ("attio_task", "get", {}, "task_id"),
    ("attio_task", "create", {}, "content"),
    ("attio_task", "update", {}, "task_id"),
    ("attio_task", "delete", {}, "task_id"),
    ("attio_list", "get", {}, "list_id_or_slug"),
    ("attio_list", "views", {}, "list_id_or_slug"),
    ("attio_list", "create", {}, "name"),
    ("attio_list", "create", {"name": "L"}, "parent_object"),
    ("attio_list", "update", {"list_id_or_slug": "l1"}, "attributes"),
    ("attio_entry", "get", {"list_id_or_slug": "l1"}, "entry_id"),
    ("attio_entry", "create", {"list_id_or_slug": "l1"}, "parent_record_id"),
    ("attio_entry", "update", {"list_id_or_slug": "l1", "entry_id": "e1"},
     "entry_values"),
    ("attio_entry", "delete", {"list_id_or_slug": "l1"}, "entry_id"),
    ("attio_workspace_member", "get", {}, "workspace_member_id"),
    ("attio_comment", "thread", {}, "thread_id"),
    ("attio_comment", "get", {}, "comment_id"),
    ("attio_comment", "create", {}, "content"),
    ("attio_comment", "create", {"content": "hop"}, "author_id"),
    ("attio_comment", "delete", {}, "comment_id"),
    ("attio_meeting", "get", {}, "meeting_id"),
    ("attio_meeting", "recordings", {}, "meeting_id"),
    ("attio_meeting", "recording", {"meeting_id": "m1"}, "call_recording_id"),
    ("attio_meeting", "transcript", {"meeting_id": "m1"}, "call_recording_id"),
    ("attio_object", "get", {}, "object_id_or_slug"),
    ("attio_object", "views", {}, "object_id_or_slug"),
    ("attio_attribute", "get", {"target": "objects", "identifier": "companies"},
     "attribute"),
    ("attio_attribute", "options", {"target": "objects", "identifier": "companies"},
     "attribute"),
    ("attio_attribute", "statuses", {"target": "objects", "identifier": "companies"},
     "attribute"),
])
def test_missing_required_arg_names_the_op_and_the_arg(
        client, tool, op, kwargs, missing):
    """Argument obligatoire absent → erreur qui NOMME l'op et l'argument, jamais
    un fallback (sur une écriture, un fallback muet toucherait le mauvais
    record)."""
    with pytest.raises(McpError, match=f"op='{op}' requiert {missing}"):
        _tool(tool)(op=op, **kwargs)
    _assert_no_stray_write(client)


@pytest.mark.parametrize("tool,op,kwargs", [
    ("attio_record", "create", {"object": "companies", "attributes": {}}),
    ("attio_record", "update", {"object": "companies", "record_id": "r1",
                                "attributes": {}}),
    ("attio_entry", "update", {"list_id_or_slug": "l1", "entry_id": "e1",
                               "entry_values": {}}),
    ("attio_list", "update", {"list_id_or_slug": "l1", "attributes": {}}),
])
def test_empty_payload_counts_as_missing_on_writes(client, tool, op, kwargs):
    """Un dict VIDE sur une écriture = rien à écrire : créerait un record vide,
    ou passerait un PATCH sans effet pour un succès."""
    with pytest.raises(McpError, match="requiert"):
        _tool(tool)(op=op, **kwargs)
    _assert_no_stray_write(client)


# --- comptage d'usage plateforme (comportement préservé) ----------------------

def test_platform_usage_is_recorded_only_on_the_platform_key(monkeypatch):
    """La clé plateforme se compte, la clé BYO non — invariant d'avant le
    refactor, qui vit maintenant dans 10 tools au lieu de 56."""
    import oto.tools.attio.client as core

    inst = MagicMock()
    monkeypatch.setattr(core, "AttioClient", lambda **kw: inst)
    recorded = []
    monkeypatch.setattr("oto_mcp.access.record_platform_usage", recorded.append)

    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", True))
    _tool("attio_record")(object="companies", op="list")
    assert recorded == ["attio"]

    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    _tool("attio_record")(object="companies", op="list")
    assert recorded == ["attio"]
