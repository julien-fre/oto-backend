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
from oto.tools.common.errors import UpstreamHTTPError

# Méthodes mutantes du client Folk : aucune ne doit être touchée par une lecture,
# ni par une op mutante autre que la sienne, ni par un dry_run.
_MUTATORS = (
    "create_person", "create_company", "create_deal", "create_note",
    "create_interaction", "create_task", "create_reminder",
    "update_person", "update_company", "update_deal", "update_note",
    "update_interaction", "update_task", "update_reminder",
    "delete_person", "delete_company", "delete_deal", "delete_note",
    "delete_interaction", "delete_task", "delete_reminder",
    "mark_task_done", "mark_task_todo",
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
    ("interaction", {"entity_id": "per_A"}, "list_past_interactions"),
    ("task", {}, "list_tasks"),
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
    ("interaction", {"entity_id": "per_A"}, "get_interaction"),
    ("task", {}, "get_task"),
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


# --- entity="deal" : object_type auto-discovery -----------------------------
#
# "deals" (minuscule) est un défaut historique, pas une garantie Folk : l'objet
# deal est un objet CUSTOM que chaque client nomme lui-même (confirmé en live,
# 2026-08-17 — sur un vrai workspace `object_type="deals"` 404 quand l'objet
# s'appelle "Deals"). `_resolve_deal_object_type` sonde le nom, jamais ne le suppose.

def _entity_types_404(message: str) -> UpstreamHTTPError:
    return UpstreamHTTPError(404, {"error": {"message": message}}, service="folk")


def test_deal_object_type_omitted_resolves_via_probe(client):
    client.get_group_custom_fields.return_value = []  # le hint "deals" est direct valide
    client.create_deal.return_value = {"id": "dea_1"}
    _tool("folk_record")(entity="deal", op="create", group_id="grp_1", item={"name": "D"})
    client.get_group_custom_fields.assert_called_once_with("grp_1", entity_type="deals")
    client.create_deal.assert_called_once_with("grp_1", object_type="deals", name="D")


def test_deal_object_type_omitted_resolves_the_real_name_on_404(client):
    client.get_group_custom_fields.side_effect = _entity_types_404(
        'Object field "deals" not found in group "grp_1". Available entity types '
        'are: "person", "company", "Opportunities".')
    client.create_deal.return_value = {"id": "dea_1"}
    _tool("folk_record")(entity="deal", op="create", group_id="grp_1", item={"name": "D"})
    client.create_deal.assert_called_once_with("grp_1", object_type="Opportunities", name="D")


def test_deal_object_type_explicit_skips_discovery_entirely(client):
    client.list_deals.return_value = []
    _tool("folk_record")(entity="deal", op="search", group_id="grp_1",
                         object_type="Opportunities")
    client.get_group_custom_fields.assert_not_called()
    client.list_deals.assert_called_once_with("grp_1", object_type="Opportunities")


def test_deal_object_type_ambiguous_candidates_raises_actionable_error(client):
    client.get_group_custom_fields.side_effect = _entity_types_404(
        'Object field "deals" not found in group "grp_1". Available entity types '
        'are: "person", "company", "Deals", "Events".')
    with pytest.raises(McpError) as exc:
        _tool("folk_record")(entity="deal", op="create", group_id="grp_1",
                             item={"name": "D"})
    assert "Deals" in str(exc.value) and "Events" in str(exc.value)
    client.create_deal.assert_not_called()


def test_deal_object_type_no_custom_object_raises_actionable_error(client):
    client.get_group_custom_fields.side_effect = _entity_types_404(
        'Object field "deals" not found in group "grp_1". Available entity types '
        'are: "person", "company".')
    with pytest.raises(McpError, match="n'a pas d'objet deal"):
        _tool("folk_record")(entity="deal", op="search", group_id="grp_1")
    client.list_deals.assert_not_called()


def test_deal_object_type_404_without_enumeration_reraises_raw(client):
    """Un 404 qui ne porte PAS l'énumération Folk (ex. group_id lui-même
    introuvable) n'est pas notre 404 « entity_type invalide » — ne pas deviner
    dessus, remonter l'erreur d'origine telle quelle."""
    client.get_group_custom_fields.side_effect = _entity_types_404("Group not found.")
    with pytest.raises(UpstreamHTTPError):
        _tool("folk_record")(entity="deal", op="search", group_id="grp_bogus")


def test_deal_object_type_non_404_reraises_immediately(client):
    client.get_group_custom_fields.side_effect = UpstreamHTTPError(
        500, {"error": {"message": "boom"}}, service="folk")
    with pytest.raises(UpstreamHTTPError):
        _tool("folk_record")(entity="deal", op="search", group_id="grp_1")
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


def test_search_interaction_requires_its_parent_entity(client):
    """Ce test disait l'inverse : « Folk n'expose aucun `list_interactions` —
    `op="create"` reste le seul verbe ». C'était faux. Folk expose bien
    past/upcoming/get (open beta) ; c'est le connecteur qui ne les branchait
    pas, et cette croyance a fini écrite dans sa doc.

    Ce qui est vrai, et que ce test verrouille à la place : une interaction
    n'est pas adressable seule — sans `entity_id`, on refuse en le NOMMANT
    plutôt que de lister le workspace (ce qui n'existe pas)."""
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="interaction", op="search")
    client.list_past_interactions.assert_not_called()


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
     "'search', 'get', 'create', 'update', 'delete', 'add_to_group', "
     "'mark_done' ou 'mark_todo'"),
    ("folk_group", {}, "'list', 'create', 'update', 'custom_fields', "
     "'get_custom_field', 'create_custom_field', 'update_custom_field', "
     "'members', 'add_member', 'remove_member', 'update_member'"),
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


# --- folk_record : interactions en LECTURE ------------------------------------
#
# Le connecteur n'exposait que la création d'interaction — d'où l'affirmation,
# écrite dans sa doc et dans un test, que folk ne dit jamais CE qui s'est dit.
# Faux : `/interactions/past|upcoming|{id}` existent (open beta). Ces tests
# verrouillent le routage ET les deux pièges qui restent vrais.


def test_search_interaction_defaults_to_past_and_says_so(client):
    client.list_past_interactions.return_value = [{"id": "lit_1"}]
    out = _tool("folk_record")(entity="interaction", op="search", entity_id="per_A")
    client.list_past_interactions.assert_called_once_with("per_A", max_items=101)
    client.list_upcoming_interactions.assert_not_called()
    assert out["truncated"] is False
    # Le défaut masque l'à-venir : le reçu doit PORTER le bucket, sinon un
    # `count` se relit comme un total qu'il n'est pas.
    assert out["when"] == "past"
    assert out["count"] == 1


def test_search_interaction_upcoming(client):
    client.list_upcoming_interactions.return_value = [{"id": "lit_2"}]
    out = _tool("folk_record")(entity="interaction", op="search",
                               entity_id="per_A", when="upcoming")
    client.list_past_interactions.assert_not_called()
    assert out["when"] == "upcoming" and out["count"] == 1


def test_search_interaction_all_splits_the_two_counts(client):
    """Les deux listes viennent d'endpoints distincts et le record ne dit pas
    duquel il sort : un `count` agrégé sans détail ne se relit pas."""
    client.list_past_interactions.return_value = [{"id": "a"}, {"id": "b"}]
    client.list_upcoming_interactions.return_value = [{"id": "c"}]
    out = _tool("folk_record")(entity="interaction", op="search",
                               entity_id="per_A", when="all")
    assert (out["count"], out["past_count"], out["upcoming_count"]) == (3, 2, 1)
    assert out["truncated"] is False
    assert [r["id"] for r in out["results"]] == ["a", "b", "c"]


def test_when_is_refused_on_other_entities(client):
    """`when` n'a de sens que sur l'interaction — silencieusement ignoré
    ailleurs, il ferait croire à un filtre appliqué."""
    with pytest.raises(McpError, match="when"):
        _tool("folk_record")(entity="task", op="search", when="past")
    with pytest.raises(McpError, match="when"):
        _tool("folk_record")(entity="interaction", op="get", id="lit_1",
                             entity_id="per_A", when="past")


def test_entity_id_is_refused_where_it_would_be_ignored(client):
    """Un `entity_id` posé sur une recherche de personnes ne veut rien dire :
    l'avaler ferait croire à un filtre appliqué (le bon paramètre est
    `group_id`)."""
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="person", op="search", entity_id="per_A")
    client.list_people.assert_not_called()


def test_search_interaction_refuses_filters(client):
    with pytest.raises(McpError, match="aucun filtre"):
        _tool("folk_record")(entity="interaction", op="search",
                             entity_id="per_A", filters={"title": "café"})


def test_get_interaction_requires_entity_id(client):
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="interaction", op="get", id="lit_1")
    client.get_interaction.assert_not_called()


def test_get_interaction_passes_its_parent(client):
    _tool("folk_record")(entity="interaction", op="get", id="lit_1",
                         entity_id="per_A")
    client.get_interaction.assert_called_once_with("lit_1", "per_A")
    _assert_silent(client)


def test_update_interaction_rejects_the_create_vocabulary(client):
    """`type` à la création, `activityType` au PATCH : la même valeur sous deux
    clés. Sans ce garde, l'appelant lit `type` dans le docstring (rayon
    création) et récolte un 422 opaque."""
    with pytest.raises(McpError, match="activityType"):
        _tool("folk_record")(entity="interaction", op="update", id="lit_1",
                             entity_id="per_A", fields={"type": "coffee"})
    client.update_interaction.assert_not_called()


def test_update_interaction_routes(client):
    _tool("folk_record")(entity="interaction", op="update", id="lit_1",
                         entity_id="per_A", fields={"activityType": "coffee"})
    client.update_interaction.assert_called_once_with(
        "lit_1", "per_A", activityType="coffee")
    _assert_silent(client, "update_interaction")


def test_update_interaction_requires_entity_id(client):
    """La spec OpenAPI ne marque pas `entity` requis dans le corps du PATCH.
    Live 2026-08-27 : Folk répond quand même 422 `path: ['entity'], Required`.
    On l'exige donc AVANT l'appel, avec un message qui le nomme."""
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="interaction", op="update", id="lit_1",
                             fields={"activityType": "coffee"})
    client.update_interaction.assert_not_called()


def test_delete_interaction_requires_entity_id_even_in_dry_run(client):
    """Exigé AVANT l'aperçu : sinon le dry_run rendrait un `would_delete: None`
    rassurant, et l'appel réel échouerait juste après."""
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="interaction", op="delete", id="lit_1",
                             dry_run=True)
    with pytest.raises(McpError, match="entity_id"):
        _tool("folk_record")(entity="interaction", op="delete", id="lit_1")
    client.delete_interaction.assert_not_called()


def test_delete_interaction_routes(client):
    _tool("folk_record")(entity="interaction", op="delete", id="lit_1",
                         entity_id="per_A")
    client.delete_interaction.assert_called_once_with("lit_1", "per_A")
    _assert_silent(client, "delete_interaction")


# --- folk_record : tâches (successeur des rappels) ----------------------------


def test_search_task_maps_entity_id_onto_the_entity_filter(client):
    client.list_tasks.return_value = []
    _tool("folk_record")(entity="task", op="search", entity_id="per_A",
                         filters={"completedAt": {"empty": True}})
    client.list_tasks.assert_called_once_with(
        {"completedAt": {"empty": True}, "entity": "per_A"})
    _assert_silent(client)


def test_search_task_refuses_both_spellings_of_the_parent(client):
    with pytest.raises(McpError, match="pas les deux"):
        _tool("folk_record")(entity="task", op="search", entity_id="per_A",
                             filters={"entity": "per_B"})


def test_search_task_surfaces_the_client_filter_refusal(client):
    """Le client valide champs ET opérateurs contre la doc Folk. Sa ValueError
    nomme déjà ce qui existe : elle doit ressortir telle quelle, pas en
    « erreur interne »."""
    client.list_tasks.side_effect = ValueError("filtre de tâche inconnu : 'title'")
    with pytest.raises(McpError, match="filtre de tâche inconnu"):
        _tool("folk_record")(entity="task", op="search", filters={"title": "x"})


def test_create_task_routes(client):
    _tool("folk_record")(entity="task", op="create",
                         item={"entity_id": "per_A", "title": "Relancer",
                               "due_at": "2026-09-01"})
    client.create_task.assert_called_once_with(
        entity_id="per_A", title="Relancer", due_at="2026-09-01")
    _assert_silent(client, "create_task")


def test_create_task_rejects_the_reminder_vocabulary(client):
    """Porter un rappel vers une tâche renomme les champs (name→title,
    recurrence_rule→due_at + recurrence_frequency) : le refus doit LISTER les
    champs acceptés, pas laisser passer un payload muet."""
    with pytest.raises(McpError, match="champ\\(s\\) inconnu\\(s\\)"):
        _tool("folk_record")(entity="task", op="create",
                             item={"entity_id": "per_A", "name": "Relancer",
                                   "recurrence_rule": "RRULE:FREQ=WEEKLY"})
    client.create_task.assert_not_called()


def test_create_refuses_entity_id_at_the_call_level(client):
    """À la création, l'entité porteuse est un CHAMP du record (elle peut
    différer d'un item à l'autre dans un lot) : l'accepter en paramètre la
    ferait taire silencieusement."""
    with pytest.raises(McpError, match="CHAMP du record"):
        _tool("folk_record")(entity="task", op="create", entity_id="per_A",
                             item={"title": "T", "due_at": "2026-09-01"})
    client.create_task.assert_not_called()


def test_update_task_rejects_completed_at(client):
    """`completedAt` s'écrit à la création mais PAS au PATCH
    (`additionalProperties: false` chez Folk) — compléter, c'est `mark_done`."""
    with pytest.raises(McpError, match="mark_done"):
        _tool("folk_record")(entity="task", op="update", id="tsk_1",
                             fields={"completedAt": "2026-08-27T10:00:00.000Z"})
    client.update_task.assert_not_called()


def test_mark_done_routes_and_defaults_the_timestamp_to_the_client(client):
    _tool("folk_record")(entity="task", op="mark_done", id="tsk_1")
    client.mark_task_done.assert_called_once_with("tsk_1", completed_at=None)
    _assert_silent(client, "mark_task_done")


def test_mark_done_accepts_an_explicit_timestamp(client):
    _tool("folk_record")(entity="task", op="mark_done", id="tsk_1",
                         fields={"completedAt": "2026-08-26T10:00:00.000Z"})
    client.mark_task_done.assert_called_once_with(
        "tsk_1", completed_at="2026-08-26T10:00:00.000Z")


def test_mark_done_refuses_stray_fields(client):
    with pytest.raises(McpError, match="completedAt"):
        _tool("folk_record")(entity="task", op="mark_done", id="tsk_1",
                             fields={"title": "nope"})
    client.mark_task_done.assert_not_called()


def test_mark_todo_takes_no_fields(client):
    with pytest.raises(McpError, match="aucun champ"):
        _tool("folk_record")(entity="task", op="mark_todo", id="tsk_1",
                             fields={"completedAt": "2026-08-26T10:00:00.000Z"})
    _tool("folk_record")(entity="task", op="mark_todo", id="tsk_1")
    client.mark_task_todo.assert_called_once_with("tsk_1")


def test_mark_bulk_closes_many_and_reports_per_item(client):
    """Le cas d'usage qui a motivé tout ça : refermer d'un coup les tâches
    posées sur un contact."""
    client.mark_task_done.side_effect = [
        {"id": "tsk_1"},
        UpstreamHTTPError(422, {"error": {"message": "boom"}}, service="folk"),
        {"id": "tsk_3"}]
    out = _tool("folk_record")(entity="task", op="mark_done",
                               ids=["tsk_1", "tsk_2", "tsk_3"])
    assert out["total"] == 3 and out["succeeded"] == 2
    assert [f["id"] for f in out["failed"]] == ["tsk_2"]


def test_mark_dry_run_reads_but_writes_nothing(client):
    client.get_task.return_value = {"id": "tsk_1", "completedAt": None}
    out = _tool("folk_record")(entity="task", op="mark_done", id="tsk_1",
                               dry_run=True)
    assert out["dry_run"] is True and out["would_mark"] == "done"
    assert out["current"]["completedAt"] is None
    _assert_silent(client)


def test_mark_is_task_only(client):
    """Un rappel se marque « déclenché » tout seul, il ne se termine pas : lui
    proposer mark_done mentirait sur ce que fait l'appel."""
    with pytest.raises(McpError, match="entity doit être"):
        _tool("folk_record")(entity="reminder", op="mark_done", id="rmd_1")
    _assert_silent(client)


def test_reminder_entity_still_works_deprecated_is_not_broken(client):
    """Folk a déprécié /reminders (retrait annoncé février 2027), pas coupé :
    l'entité doit continuer de répondre tant que l'endpoint répond."""
    client.list_reminders.return_value = []
    _tool("folk_record")(entity="reminder", op="search")
    client.list_reminders.assert_called_once()


def test_search_interaction_stops_early_and_admits_it(client):
    """Folk ne filtre pas les interactions et les sert par pages de 30 : une
    fiche active en porte des centaines (>360 mesuré). La recherche s'arrête
    donc à `max_results` au lieu de vider la collection — et le dit, sinon un
    `count` de 5 se lit comme « il y en a cinq »."""
    client.list_past_interactions.return_value = [{"id": f"lit_{i}"} for i in range(6)]
    out = _tool("folk_record")(entity="interaction", op="search",
                               entity_id="per_A", max_results=5)
    # max_results + 1 : de quoi SAVOIR qu'il en reste, sans payer une page de plus.
    client.list_past_interactions.assert_called_once_with("per_A", max_items=6)
    assert out["count"] == 5 and len(out["results"]) == 5
    assert out["truncated"] is True


def test_search_interaction_all_counts_follow_the_truncation(client):
    """En `when="all"` les deux compteurs doivent décrire ce qui est RENDU :
    si la troncature tombe au milieu du seau `past`, `upcoming_count` est 0,
    pas le nombre d'interactions à venir dans folk."""
    client.list_past_interactions.return_value = [{"id": f"p{i}"} for i in range(4)]
    client.list_upcoming_interactions.return_value = [{"id": "u1"}]
    out = _tool("folk_record")(entity="interaction", op="search",
                               entity_id="per_A", when="all", max_results=2)
    assert (out["count"], out["past_count"], out["upcoming_count"]) == (2, 2, 0)
    assert out["truncated"] is True
