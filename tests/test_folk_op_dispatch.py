"""Dispatch `op=` des 4 tools `folk_*` (ADR 0047 §Amendement, appliqué au connecteur
folk : 17 tools → 4).

Ce que ce fichier verrouille, et que `test_folk.py` ne couvrait PAS : celui-ci exerce
le COMPORTEMENT (bulk, dry_run, reçus, allow-list de champs) des quatre verbes
d'écriture, mais il ne dit rien du routage — quel `op` atteint quelle méthode du
client. Une consolidation par `op=` déplace précisément le risque là : une op mal
câblée appelle silencieusement la mauvaise méthode, et rien ne casse au boot. Sur un
connecteur CRM branché sur des données RÉELLES, « silencieusement » veut dire un
record écrasé ou supprimé.

D'où, pour chaque op : la méthode client appelée, le refus explicite d'une op inconnue
(message qui NOMME les ops valides), les arguments obligatoires (erreur qui nomme l'op
ET l'argument), et pour chaque op MUTANTE le mutisme de ses voisines dangereuses
(`assert_not_called` sur delete/update/create).

Invariant de sûreté vérifié ici en premier : **le défaut de chaque tool est une
LECTURE** — aucun `op` omis ne peut écrire.
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError

# Méthodes mutantes du client Folk : aucune ne doit être touchée par une lecture,
# ni par une op mutante autre que la sienne, ni par un dry_run.
_MUTATORS = (
    "create_person", "create_company", "create_deal", "create_note",
    "create_interaction", "create_reminder",
    "update_person", "update_company", "update_deal", "update_note",
    "update_reminder",
    "delete_person", "delete_company", "delete_deal", "delete_note",
    "delete_reminder",
    "create_webhook", "update_webhook",
)


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import folk as F

    m = FastMCP("t")
    F.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False)
    )


@pytest.fixture
def client():
    """Faux `FolkClient` — `register()` importe la classe à l'enregistrement, donc
    le patch doit porter sur le module d'origine (comme dans test_folk.py)."""
    with patch("oto.tools.folk.client.FolkClient") as cls:
        yield cls.return_value


def _assert_silent(client, *except_for: str):
    """Aucune écriture hors celles explicitement attendues."""
    for m in _MUTATORS:
        if m in except_for:
            continue
        getattr(client, m).assert_not_called()


# --- le défaut de chaque tool est une LECTURE ---------------------------------

def test_folk_record_default_op_searches_and_writes_nothing(client):
    client.list_people.return_value = []
    out = _tool("folk_record")(entity="person")
    client.list_people.assert_called_once()
    assert out["entity"] == "person"
    _assert_silent(client)


def test_folk_group_default_op_lists_and_writes_nothing(client):
    client.list_groups.return_value = [{"id": "grp_1"}]
    assert _tool("folk_group")() == {"groups": [{"id": "grp_1"}]}
    _assert_silent(client)


def test_folk_user_default_op_lists_and_writes_nothing(client):
    client.list_users.return_value = [{"id": "usr_1"}]
    assert _tool("folk_user")() == {"users": [{"id": "usr_1"}]}
    _assert_silent(client)


def test_folk_webhook_default_op_lists_and_writes_nothing(client):
    client.list_webhooks.return_value = [{"id": "wbk_1"}]
    assert _tool("folk_webhook")() == {"webhooks": [{"id": "wbk_1"}]}
    _assert_silent(client)


# --- folk_record : lectures ----------------------------------------------------

@pytest.mark.parametrize("entity,kwargs,method", [
    ("person", {}, "list_people"),
    ("company", {}, "list_companies"),
    ("deal", {"group_id": "grp_1"}, "list_deals"),
    ("note", {}, "list_notes"),
    ("reminder", {}, "list_reminders"),
])
def test_search_routes_to_the_right_client_method(client, entity, kwargs, method):
    getattr(client, method).return_value = []
    _tool("folk_record")(entity=entity, op="search", **kwargs)
    getattr(client, method).assert_called_once()
    _assert_silent(client)


@pytest.mark.parametrize("entity,kwargs,method", [
    ("person", {}, "get_person"),
    ("company", {}, "get_company"),
    ("deal", {"group_id": "grp_1"}, "get_deal"),
    ("reminder", {}, "get_reminder"),
])
def test_get_routes_to_the_right_client_method(client, entity, kwargs, method):
    """`op="get", entity="reminder"` remplace l'ex-`folk_get_reminder` (rmd_…) — même
    méthode client, un paramètre de moins à la surface."""
    _tool("folk_record")(entity=entity, op="get", id="x1", **kwargs)
    getattr(client, method).assert_called_once()
    _assert_silent(client)


def test_get_refuses_note_because_folk_has_no_get_by_id(client):
    """Gap PERMANENT de l'API Folk, pas un raccourci d'implémentation : le refus doit
    orienter vers `op="search"` plutôt que laisser croire à une panne."""
    with pytest.raises(McpError, match="get-par-id"):
        _tool("folk_record")(entity="note", op="get", id="nte_1")
    client.list_notes.assert_not_called()


def test_search_person_by_group_translates_to_a_membership_filter(client):
    """`group_id` sur person/company = LISTER LES MEMBRES du groupe (le client le
    traduit en filter[groups][in][id]), pas un scope de deal."""
    client.list_people.return_value = []
    _tool("folk_record")(entity="person", op="search", group_id="grp_1")
    assert client.list_people.call_args.kwargs == {"groups": "grp_1"}


def test_search_truncates_but_count_reports_the_real_total(client):
    """`count` est le total RÉEL : un `count` au-dessus du nombre de `results` est le
    seul signal que la liste a été coupée."""
    client.list_people.return_value = [{"id": i} for i in range(5)]
    out = _tool("folk_record")(entity="person", op="search", max_results=2)
    assert out["count"] == 5 and len(out["results"]) == 2


def test_search_deal_requires_group_id(client):
    with pytest.raises(McpError, match="group_id"):
        _tool("folk_record")(entity="deal", op="search")
    client.list_deals.assert_not_called()


def test_search_note_refuses_an_unknown_filter(client):
    """`list_notes` a une signature FERMÉE (`entity_id` seul) : un filtre inconnu
    lèverait un TypeError rendu en « erreur interne ». Refus nommé à la place."""
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="note", op="search", filters={"fullName": "X"})
    client.list_notes.assert_not_called()


def test_search_reminder_refuses_group_id_rather_than_ignoring_it(client):
    """Folk ne filtre pas les rappels par groupe : l'accepter en silence ferait croire
    à l'agent que son filtre s'applique (cf. « filtre d'API ignoré en silence »)."""
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="reminder", op="search", group_id="grp_1")
    client.list_reminders.assert_not_called()


def test_search_refuses_interaction(client):
    """Folk n'expose aucun `list_interactions` — `op="create"` reste le seul verbe."""
    with pytest.raises(McpError, match="entity doit être"):
        _tool("folk_record")(entity="interaction", op="search")


# --- folk_record : écritures ---------------------------------------------------
#
# Chaque op mutante a SON cas : la méthode appelée, et le silence des voisines
# (une op mal câblée sur des données CRM réelles écrase ou supprime).

@pytest.mark.parametrize("entity,item,method", [
    ("person", {"first_name": "Ada"}, "create_person"),
    ("company", {"name": "Acme"}, "create_company"),
    ("note", {"entity_id": "per_1", "content": "x"}, "create_note"),
    ("interaction", {"entity_id": "per_1", "type": "call", "title": "t"},
     "create_interaction"),
    ("reminder", {"entity_id": "per_1", "name": "n", "recurrence_rule": "r"},
     "create_reminder"),
])
def test_create_routes_to_the_right_client_method(client, entity, item, method):
    _tool("folk_record")(entity=entity, op="create", item=item)
    getattr(client, method).assert_called_once()
    _assert_silent(client, method)


def test_create_deal_routes_to_create_deal_within_its_group(client):
    _tool("folk_record")(entity="deal", op="create", group_id="grp_1",
                         item={"name": "Deal A"})
    client.create_deal.assert_called_once_with("grp_1", object_type="deals",
                                               name="Deal A")
    _assert_silent(client, "create_deal")


@pytest.mark.parametrize("entity,method", [
    ("person", "update_person"),
    ("company", "update_company"),
    ("note", "update_note"),
    ("reminder", "update_reminder"),
])
def test_update_routes_to_the_right_client_method(client, entity, method):
    _tool("folk_record")(entity=entity, op="update", id="x1", fields={"a": 1})
    getattr(client, method).assert_called_once_with("x1", a=1)
    _assert_silent(client, method)


def test_update_deal_routes_to_update_deal_within_its_group(client):
    _tool("folk_record")(entity="deal", op="update", id="dea_1", group_id="grp_1",
                         fields={"name": "N"})
    client.update_deal.assert_called_once_with("grp_1", "dea_1", object_type="deals",
                                               name="N")
    _assert_silent(client, "update_deal")


@pytest.mark.parametrize("entity,kwargs,method", [
    ("person", {}, "delete_person"),
    ("company", {}, "delete_company"),
    ("note", {}, "delete_note"),
    ("reminder", {}, "delete_reminder"),
    ("deal", {"group_id": "grp_1"}, "delete_deal"),
])
def test_delete_routes_to_the_right_client_method(client, entity, kwargs, method):
    """Irréversible : l'op `delete` ne doit jamais atteindre une autre entité que
    celle demandée, ni un autre verbe."""
    _tool("folk_record")(entity=entity, op="delete", id="x1", **kwargs)
    getattr(client, method).assert_called_once()
    _assert_silent(client, method)


def test_add_to_group_reads_then_writes_the_union(client):
    """L'écriture passe par `update_person` (le champ `groups` de Folk est replace-all
    sur PATCH) : la lecture préalable est ce qui empêche d'effacer les autres groupes."""
    client.get_person.return_value = {"groups": [{"id": "g1"}]}
    _tool("folk_record")(entity="person", op="add_to_group", id="per_1",
                         group_id="g2")
    client.update_person.assert_called_once_with(
        "per_1", groups=[{"id": "g1"}, {"id": "g2"}])
    _assert_silent(client, "update_person")


def test_add_to_group_requires_the_target_group(client):
    with pytest.raises(McpError, match="group_id"):
        _tool("folk_record")(entity="person", op="add_to_group", id="per_1")
    _assert_silent(client)


# --- dry_run : la validation tourne, l'appel mutant est sauté ------------------

@pytest.mark.parametrize("kwargs,method", [
    ({"op": "create", "item": {"first_name": "Ada"}}, "create_person"),
    ({"op": "update", "id": "per_1", "fields": {"jobTitle": "CEO"}}, "update_person"),
    ({"op": "delete", "id": "per_1"}, "delete_person"),
    ({"op": "add_to_group", "id": "per_1", "group_id": "g2"}, "update_person"),
])
def test_dry_run_never_reaches_a_mutating_method(client, kwargs, method):
    client.get_person.return_value = {"id": "per_1", "groups": []}
    out = _tool("folk_record")(entity="person", dry_run=True, **kwargs)
    assert out["dry_run"] is True
    _assert_silent(client)


# --- folk_group / folk_user ----------------------------------------------------

def test_group_custom_fields_routes_and_passes_the_entity_type(client):
    client.get_group_custom_fields.return_value = [{"name": "Status"}]
    out = _tool("folk_group")(op="custom_fields", group_id="grp_1",
                              entity_type="company")
    assert out == {"custom_fields": [{"name": "Status"}]}
    client.get_group_custom_fields.assert_called_once_with("grp_1", "company")


def test_group_custom_fields_requires_group_id(client):
    with pytest.raises(McpError, match="group_id"):
        _tool("folk_group")(op="custom_fields")
    client.get_group_custom_fields.assert_not_called()


def test_user_get_defaults_to_the_authenticated_user(client):
    client.get_user.return_value = {"id": "usr_me"}
    assert _tool("folk_user")(op="get") == {"id": "usr_me"}
    client.get_user.assert_called_once_with("me")


# --- folk_webhook --------------------------------------------------------------

def test_webhook_create_routes_to_create_webhook(client):
    events = [{"eventType": "person.created"}]
    client.create_webhook.return_value = {"id": "wbk_1"}
    _tool("folk_webhook")(op="create", name="n", target_url="https://x/h",
                          subscribed_events=events)
    client.create_webhook.assert_called_once_with("n", "https://x/h", events)
    _assert_silent(client, "create_webhook")


def test_webhook_update_routes_to_update_webhook(client):
    _tool("folk_webhook")(op="update", webhook_id="wbk_1",
                          fields={"status": "inactive"})
    client.update_webhook.assert_called_once_with("wbk_1", status="inactive")
    _assert_silent(client, "update_webhook")


@pytest.mark.parametrize("kwargs,missing", [
    ({"op": "create", "target_url": "https://x/h",
      "subscribed_events": [{"eventType": "person.created"}]}, "name"),
    ({"op": "create", "name": "n",
      "subscribed_events": [{"eventType": "person.created"}]}, "target_url"),
    ({"op": "create", "name": "n", "target_url": "https://x/h"}, "subscribed_events"),
    ({"op": "update", "fields": {"status": "inactive"}}, "webhook_id"),
    ({"op": "update", "webhook_id": "wbk_1"}, "fields"),
])
def test_webhook_missing_required_arg_names_the_op_and_the_arg(client, kwargs, missing):
    with pytest.raises(McpError, match=missing):
        _tool("folk_webhook")(**kwargs)
    _assert_silent(client)


@pytest.mark.parametrize("kwargs", [
    {"op": "create", "name": "n", "target_url": "https://x/h",
     "subscribed_events": [{"eventType": "person.made_up"}]},
    {"op": "update", "webhook_id": "wbk_1",
     "fields": {"subscribedEvents": [{"eventType": "person.made_up"}]}},
])
def test_webhook_rejects_an_invalid_event_type_on_both_write_ops(client, kwargs):
    with pytest.raises(McpError, match="person.made_up"):
        _tool("folk_webhook")(**kwargs)
    _assert_silent(client)


def test_webhook_dry_run_never_writes(client):
    client.get_webhook.return_value = {"status": "active"}
    for kwargs in ({"op": "create", "name": "n", "target_url": "https://x/h",
                    "subscribed_events": [{"eventType": "person.created"}]},
                   {"op": "update", "webhook_id": "wbk_1",
                    "fields": {"status": "inactive"}}):
        out = _tool("folk_webhook")(dry_run=True, **kwargs)
        assert out["dry_run"] is True
    _assert_silent(client)


# --- refus d'une op inconnue ---------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,expected", [
    ("folk_record", {"entity": "person"},
     "'search', 'get', 'create', 'update', 'delete' ou 'add_to_group'"),
    ("folk_group", {}, "'list' ou 'custom_fields'"),
    ("folk_user", {}, "'list' ou 'get'"),
    ("folk_webhook", {}, "'list', 'create' ou 'update'"),
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, kwargs, expected):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError) as e:
        _tool(tool)(op="nope", **kwargs)
    assert expected in str(e.value)
    _assert_silent(client)


@pytest.mark.parametrize("op,kwargs,missing", [
    ("get", {}, "id"),
    ("create", {}, "item"),
    ("update", {}, "id"),
    ("delete", {}, "id"),
    ("add_to_group", {"group_id": "g1"}, "id"),
])
def test_record_missing_required_arg_names_the_op_and_the_arg(client, op, kwargs,
                                                              missing):
    with pytest.raises(McpError) as e:
        _tool("folk_record")(entity="person", op=op, **kwargs)
    msg = str(e.value)
    assert missing in msg and op in msg
    _assert_silent(client)
