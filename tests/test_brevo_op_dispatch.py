"""Dispatch `op=` des tools `brevo_*` (ADR 0047 §Amendement, appliqué au connecteur
brevo : 30 tools → 9 dans `tools/brevo.py`).

Ce que ce fichier verrouille, et que `test_brevo_tools.py` ne pouvait plus couvrir :
ce dernier raisonne sur des NOMS de tools (« `brevo_delete_contact` n'existe pas »).
Après consolidation, le verbe n'est plus dans le nom mais dans `op=` — une écriture
destructive ou un envoi de masse s'ajouterait donc SANS créer de tool, invisible à ce
contrôle. On verrouille ici le grain op :

- pour chaque op, la méthode client réellement appelée (une op mal câblée appelle
  silencieusement la mauvaise méthode, et rien ne casse au boot) ;
- le refus d'une op inconnue, en NOMMANT les ops valides (jamais un repli muet sur
  le défaut : l'agent croirait sa demande honorée) ;
- les arguments obligatoires, avec un message qui nomme l'op ET l'argument ;
- **le défaut d'`op` est une LECTURE** sur les 5 dispatchers — ce connecteur envoie de
  vrais emails et écrit de vrais contacts, un appel sans `op` ne doit rien déclencher ;
- **chaque op qui écrit / envoie / consomme** a son cas propre, qui vérifie la méthode
  appelée ET le **mutisme des voisines dangereuses** ;
- les ops destructives volontairement absentes (`delete`, `send` d'une campagne) sont
  refusées comme n'importe quelle op inconnue.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
# Tout ce qui écrit, envoie un email réel, ou lance un job côté Brevo. Sert de
# « mutisme » à contrôler : aucune de ces méthodes ne doit partir sur une lecture.
DANGEROUS = (
    "send_email", "send_campaign_test", "create_campaign", "update_campaign",
    "import_contacts", "export_contacts", "upsert_contact", "update_contact",
    "create_list", "update_list", "add_to_list", "remove_from_list",
    "create_template", "update_template",
)


@pytest.fixture
def client(monkeypatch):
    """Faux `BrevoClient` + clé résolue.

    `register()` fait `from oto.tools.brevo import BrevoClient` puis capture le nom
    dans la closure `_client()` — le patch doit donc viser le module amont AVANT que
    `_tool()` n'appelle `register`.
    """
    import oto.tools.brevo as core

    inst = MagicMock()
    monkeypatch.setattr(core, "BrevoClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda *a, **k: ("test-key", None))
    return inst


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import brevo as B

    m = FastMCP("t")
    B.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _assert_silent(client, *skip: str):
    """Aucune méthode dangereuse n'a été appelée, hors celles explicitement attendues."""
    for m in DANGEROUS:
        if m not in skip:
            getattr(client, m).assert_not_called()


# --- l'invariant central : le défaut d'op ne déclenche JAMAIS une écriture --------

@pytest.mark.parametrize("tool,method", [
    ("brevo_contact", "list_contacts"),
    ("brevo_list", "list_lists"),
    ("brevo_template", "list_templates"),
    ("brevo_campaign", "list_campaigns"),
    ("brevo_transactional", "list_transactional_emails"),
])
def test_default_op_is_a_read(client, tool, method):
    _tool(tool)()
    getattr(client, method).assert_called_once()
    _assert_silent(client)


# --- contacts ------------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_contacts"),
    ("get", {"identifier": "a@b.c"}, "get_contact"),
    ("stats", {"identifier": "a@b.c"}, "contact_campaign_stats"),
    ("attributes", {}, "list_attributes"),
])
def test_contact_read_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("brevo_contact")(op=op, **kwargs)
    getattr(client, method).assert_called_once()
    _assert_silent(client)


def test_contact_list_passes_its_filters(client):
    """`list_ids`/`segment_id` sont exclusifs côté API, et `filter` n'accepte que
    `equals(...)` : les paramètres doivent arriver tels quels au client, sinon le
    filtre est ignoré EN SILENCE (la réponse reste 200)."""
    _tool("brevo_contact")(op="list", segment_id=7, filter='equals(FIRSTNAME,"Alex")',
                           limit=1000, sort="asc")
    kw = client.list_contacts.call_args.kwargs
    assert kw["segment_id"] == 7 and kw["filter"] == 'equals(FIRSTNAME,"Alex")'
    assert kw["limit"] == 1000 and kw["sort"] == "asc"


def test_contact_upsert_writes_and_only_that(client):
    """ÉCRITURE : crée/met à jour un contact réel."""
    _tool("brevo_contact")(op="upsert", email="a@b.c", attributes={"PRENOM": "Alex"},
                           list_ids=[3])
    client.upsert_contact.assert_called_once()
    kw = client.upsert_contact.call_args.kwargs
    assert kw["email"] == "a@b.c" and kw["update_enabled"] is True
    _assert_silent(client, "upsert_contact")


def test_contact_update_writes_and_carries_the_blacklist_flag(client):
    """ÉCRITURE : `email_blacklisted=True` coupe DÉFINITIVEMENT toute réception —
    le flag doit atteindre le client, jamais être avalé."""
    _tool("brevo_contact")(op="update", identifier="a@b.c", email_blacklisted=True,
                           unlink_list_ids=[3])
    client.update_contact.assert_called_once()
    kw = client.update_contact.call_args.kwargs
    assert kw["email_blacklisted"] is True and kw["unlink_list_ids"] == [3]
    _assert_silent(client, "update_contact")


def test_contact_upsert_refuses_without_email(client):
    with pytest.raises(McpError, match="email"):
        _tool("brevo_contact")(op="upsert", attributes={"PRENOM": "Alex"})
    _assert_silent(client)


@pytest.mark.parametrize("op", ["get", "stats", "update"])
def test_contact_ops_refuse_without_identifier(client, op):
    with pytest.raises(McpError, match="identifier"):
        _tool("brevo_contact")(op=op)
    _assert_silent(client)


# --- import / export : jobs asynchrones restés seuls ---------------------------

def test_import_contacts_is_a_bulk_write(client):
    """ÉCRITURE DE MASSE : la seule voie au-delà de 150 contacts."""
    _tool("brevo_import_contacts")(contacts=[{"email": "a@b.c"}], list_ids=[3])
    client.import_contacts.assert_called_once()
    assert client.import_contacts.call_args.kwargs["json_body"] == [{"email": "a@b.c"}]
    _assert_silent(client, "import_contacts")


def test_export_contacts_launches_a_job(client):
    """COÛTEUX : lance un job d'export côté Brevo (rend un processId, pas les données)."""
    _tool("brevo_export_contacts")(contact_filter={"listIds": [1]})
    client.export_contacts.assert_called_once()
    _assert_silent(client, "export_contacts")


# --- listes, dossiers, segments -------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_lists"),
    ("get", {"list_id": 3}, "get_list"),
    ("contacts", {"list_id": 3}, "list_contacts_of_list"),
    ("folders", {}, "list_folders"),
    ("segments", {}, "list_segments"),
])
def test_list_read_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("brevo_list")(op=op, **kwargs)
    getattr(client, method).assert_called_once()
    _assert_silent(client)


def test_list_create_writes_and_only_that(client):
    """ÉCRITURE : crée une liste réelle. `folder_id` est obligatoire côté API."""
    _tool("brevo_list")(op="create", name="Newsletter", folder_id=1)
    client.create_list.assert_called_once_with("Newsletter", 1)
    _assert_silent(client, "create_list")


def test_list_update_writes_and_only_that(client):
    _tool("brevo_list")(op="update", list_id=3, name="Clients")
    client.update_list.assert_called_once()
    _assert_silent(client, "update_list")


def test_list_add_writes_and_does_not_remove(client):
    """ÉCRITURE : `add` et `remove` sont voisines et symétriques — se tromper d'op
    VIDE une liste au lieu de la remplir."""
    _tool("brevo_list")(op="add", list_id=3, emails=["a@b.c"])
    client.add_to_list.assert_called_once()
    _assert_silent(client, "add_to_list")


def test_list_remove_writes_and_does_not_add(client):
    """ÉCRITURE DESTRUCTIVE : `all_contacts=True` vide la liste entière — le flag
    doit atteindre le client, et `add_to_list` rester muette."""
    _tool("brevo_list")(op="remove", list_id=3, all_contacts=True)
    client.remove_from_list.assert_called_once()
    assert client.remove_from_list.call_args.kwargs["all_contacts"] is True
    _assert_silent(client, "remove_from_list")


@pytest.mark.parametrize("op", ["get", "contacts", "update", "add", "remove"])
def test_list_ops_refuse_without_list_id(client, op):
    with pytest.raises(McpError, match="list_id"):
        _tool("brevo_list")(op=op)
    _assert_silent(client)


@pytest.mark.parametrize("kwargs,missing", [
    ({}, "name"),
    ({"name": "Newsletter"}, "folder_id"),
])
def test_list_create_names_the_missing_arg(client, kwargs, missing):
    with pytest.raises(McpError, match=missing):
        _tool("brevo_list")(op="create", **kwargs)
    _assert_silent(client)


# --- transactionnel : envoi unitaire (tool à part) + délivrabilité ---------------

def test_send_email_sends_for_real(client):
    """ENVOI RÉEL d'un email transactionnel — le seul tool de ce module qui envoie
    sans passer par une campagne. Reste SEUL (params de rédaction disjoints)."""
    _tool("brevo_send_email")(to=[{"email": "a@b.c"}], subject="Hi",
                              html_content="<p>Hi</p>",
                              sender={"email": "me@otomata.tech"})
    client.send_email.assert_called_once()
    assert client.send_email.call_args.kwargs["to"] == [{"email": "a@b.c"}]
    _assert_silent(client, "send_email")


@pytest.mark.parametrize("op,kwargs,method", [
    ("logs", {}, "list_transactional_emails"),
    ("content", {"uuid": "u1"}, "get_transactional_email_content"),
    ("events", {"event": "hardBounces"}, "transactional_events"),
    ("report", {}, "transactional_report"),
    ("blocked", {}, "list_blocked"),
    ("blocked_domains", {}, "list_blocked"),
])
def test_transactional_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("brevo_transactional")(op=op, **kwargs)
    getattr(client, method).assert_called_once()
    _assert_silent(client)


def test_transactional_blocked_switches_contacts_vs_domains(client):
    """Deux ops pour deux endpoints (`/smtp/blockedContacts` vs `/smtp/blockedDomains`)
    — la seconde n'est pas paginée."""
    _tool("brevo_transactional")(op="blocked")
    assert client.list_blocked.call_args.kwargs["domains"] is False
    _tool("brevo_transactional")(op="blocked_domains")
    assert client.list_blocked.call_args.kwargs["domains"] is True


def test_transactional_content_refuses_without_uuid(client):
    with pytest.raises(McpError, match="uuid"):
        _tool("brevo_transactional")(op="content")


# --- templates ------------------------------------------------------------------

def test_template_list_can_target_one(client):
    _tool("brevo_template")(op="list", template_id=12)
    assert client.list_templates.call_args.kwargs["template_id"] == 12
    _assert_silent(client)


def test_template_create_writes_and_defaults_to_active(client):
    """ÉCRITURE : `is_active` valait `True` par défaut avant consolidation — le
    défaut est porté ici, `None` ne doit pas devenir « inactif »."""
    _tool("brevo_template")(op="create", template_name="Relance", subject="Hello",
                            sender={"email": "me@otomata.tech"})
    client.create_template.assert_called_once()
    assert client.create_template.call_args.kwargs["is_active"] is True
    _assert_silent(client, "create_template")


def test_template_update_writes_and_only_that(client):
    _tool("brevo_template")(op="update", template_id=12, subject="Hello")
    client.update_template.assert_called_once()
    _assert_silent(client, "update_template")


@pytest.mark.parametrize("kwargs,missing", [
    ({}, "template_name"),
    ({"template_name": "Relance"}, "subject"),
    ({"template_name": "Relance", "subject": "Hello"}, "sender"),
])
def test_template_create_names_the_missing_arg(client, kwargs, missing):
    with pytest.raises(McpError, match=missing):
        _tool("brevo_template")(op="create", **kwargs)
    _assert_silent(client)


def test_template_update_refuses_without_template_id(client):
    with pytest.raises(McpError, match="template_id"):
        _tool("brevo_template")(op="update", subject="Hello")
    _assert_silent(client)


# --- campagnes ------------------------------------------------------------------

def test_campaign_list_switches_to_one_when_id_given(client):
    _tool("brevo_campaign")(op="list")
    client.list_campaigns.assert_called_once()
    client.get_campaign.assert_not_called()
    _tool("brevo_campaign")(op="list", campaign_id=9, statistics="globalStats")
    client.get_campaign.assert_called_once()
    _assert_silent(client)


@pytest.mark.parametrize("op,method", [
    ("report", "campaign_shared_url"),
    ("ab_test", "campaign_ab_test_result"),
])
def test_campaign_report_ops_route_to_the_right_client_method(client, op, method):
    _tool("brevo_campaign")(op=op, campaign_id=9)
    getattr(client, method).assert_called_once()
    _assert_silent(client)


def test_campaign_create_writes_a_draft_and_sends_nothing(client):
    """ÉCRITURE : crée un BROUILLON. Aucun envoi ne doit partir (`sendNow` n'est pas
    exposé, et l'envoi de test est une op distincte)."""
    _tool("brevo_campaign")(op="create", name="Août", sender={"email": "me@otomata.tech"},
                            recipients={"listIds": [1]})
    client.create_campaign.assert_called_once()
    _assert_silent(client, "create_campaign")


def test_campaign_update_writes_and_only_that(client):
    _tool("brevo_campaign")(op="update", campaign_id=9, fields={"subject": "Hello"})
    client.update_campaign.assert_called_once_with(9, subject="Hello")
    _assert_silent(client, "update_campaign")


def test_campaign_test_sends_for_real_and_touches_nothing_else(client):
    """ENVOI RÉEL : part vers les adresses données (qui doivent exister comme
    contacts). Aucune autre écriture — surtout pas `send_email`."""
    _tool("brevo_campaign")(op="test", campaign_id=9, email_to=["me@otomata.tech"])
    client.send_campaign_test.assert_called_once_with(9, ["me@otomata.tech"])
    _assert_silent(client, "send_campaign_test")


@pytest.mark.parametrize("op,kwargs,missing", [
    ("create", {}, "name"),
    ("create", {"name": "Août"}, "sender"),
    ("update", {"fields": {"subject": "x"}}, "campaign_id"),
    ("update", {"campaign_id": 9}, "fields"),
    ("test", {"email_to": ["a@b.c"]}, "campaign_id"),
    ("test", {"campaign_id": 9}, "email_to"),
    ("report", {}, "campaign_id"),
    ("ab_test", {}, "campaign_id"),
])
def test_campaign_missing_required_arg_names_the_op_and_the_arg(
        client, op, kwargs, missing):
    with pytest.raises(McpError, match=missing):
        _tool("brevo_campaign")(op=op, **kwargs)
    _assert_silent(client)


# --- refus ----------------------------------------------------------------------

@pytest.mark.parametrize("tool", [
    "brevo_contact", "brevo_list", "brevo_template", "brevo_campaign",
    "brevo_transactional",
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool):
    with pytest.raises(McpError, match="op doit être"):
        _tool(tool)(op="nope")
    _assert_silent(client)


@pytest.mark.parametrize("tool,op", [
    # Les suppressions restent dans l'UI Brevo (cf. docstring du module) : le verbe
    # ayant migré du NOM vers `op=`, c'est ici qu'on garde la décision.
    ("brevo_contact", "delete"),
    ("brevo_list", "delete"),
    ("brevo_template", "delete"),
    ("brevo_campaign", "delete"),
    # L'envoi de MASSE d'une campagne (`sendNow`) n'est pas exposé non plus.
    ("brevo_campaign", "send"),
    ("brevo_campaign", "send_now"),
    # Purge des hard bounces.
    ("brevo_transactional", "delete_hardbounces"),
])
def test_destructive_ops_are_not_reachable(client, tool, op):
    with pytest.raises(McpError, match="op doit être"):
        _tool(tool)(op=op)
    _assert_silent(client)


def test_unknown_op_never_falls_back_to_the_default(client):
    """Le piège de la consolidation : retomber sur l'op par défaut ferait croire à
    l'agent que sa demande a été honorée."""
    with pytest.raises(McpError):
        _tool("brevo_contact")(op="serach")
    client.list_contacts.assert_not_called()
