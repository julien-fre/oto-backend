"""Dispatch `op=` des tools `calendar_*` (ADR 0047 §Amendement, appliqué au produit
Google calendar : 4 tools → 2).

Ce module n'avait AUCUN test : les 4 tools passaient le plat au `CalendarClient`
oto-core, et le seul filet était le garde-fou statique version-skew — qui vérifie
que la méthode EXISTE sur la classe, jamais qu'on appelle la BONNE. Une
consolidation par `op=` déplace précisément le risque là.

Et ici le risque n'est pas cosmétique : **`op="create"` écrit dans l'agenda réel de
l'utilisateur**. D'où, en plus du routage par op :
- chaque op de LECTURE prouve que `create_event` n'est PAS appelée (`assert_not_called`
  sur la voisine dangereuse) ;
- l'op par défaut (appel sans `op`) est une lecture ;
- une op inconnue ne retombe jamais sur un défaut, et n'écrit rien ;
- les arguments obligatoires de l'écriture (`summary`, `start`) lèvent AVANT tout
  appel amont — jamais un événement inventé.

S'y ajoute la fidélité des arguments : le client oto-core prend ses paramètres en
POSITIONNEL, et `list_events` place `max_results` AVANT `query`. Une inversion à cet
endroit ne casse rien au boot et rend une recherche silencieusement fausse.
"""
import asyncio
import inspect
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
EXPECTED_TOOLS = {"calendar_calendars", "calendar_event"}


def _register():
    from fastmcp import FastMCP
    from oto_mcp.tools import calendar as C

    m = FastMCP("t")
    C.register(m)
    return m


def _tool(name: str):
    """La fonction nue du tool. Ces tools sont `async` → à passer à `asyncio.run`."""
    return asyncio.run(_register().get_tool(name)).fn


def _call(name: str, **kwargs):
    return asyncio.run(_tool(name)(**kwargs))


@pytest.fixture
def client(monkeypatch):
    """Faux `CalendarClient` + résolution de credential court-circuitée.

    `_client_for_user` est résolu dans les globals du module à CHAQUE appel (le tool
    ne le capture pas) : patcher l'attribut de module suffit, et on enregistre au
    passage l'`account` demandé pour vérifier qu'il est bien propagé.
    """
    from oto_mcp.tools import calendar as C

    inst = MagicMock()
    inst.list_calendars.return_value = []
    inst.list_events.return_value = []
    resolved: list = []

    def _fake(account=None):
        resolved.append(account)
        return inst

    monkeypatch.setattr(C, "_client_for_user", _fake)
    inst.resolved_accounts = resolved
    return inst


# --- la surface elle-même ------------------------------------------------------

def test_surface_is_exactly_the_two_consolidated_tools():
    """Un tool oublié en route (ou resté en double) se voit ici, pas en prod."""
    assert {t.name for t in asyncio.run(_register()._list_tools())} == EXPECTED_TOOLS


def test_discovery_tool_stays_alone_because_it_has_no_business_param():
    """`calendar_calendars` ne prend QUE `account` : ses paramètres ne recouvrent
    aucun de ceux de `calendar_event` (qui est toujours ciblé par `calendar_id`),
    et c'est lui qui PRODUIT ce `calendar_id`. Le fusionner ferait porter à un tool
    de découverte les 12 paramètres d'un tool d'événement — c'est la raison de le
    laisser seul, autant la figer."""
    assert set(inspect.signature(_tool("calendar_calendars")).parameters) == {"account"}


# --- routage par op ------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_events"),
    ("get", {"event_id": "e1"}, "get_event"),
    ("create", {"summary": "Point", "start": "2026-08-12T10:00:00Z"}, "create_event"),
])
def test_event_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _call("calendar_event", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_calendars_lists_the_calendars(client):
    client.list_calendars.return_value = [
        {"id": "primary", "summary": "Alexis", "primary": True, "accessRole": "owner"}]
    assert _call("calendar_calendars") == {
        "calendars": [{"id": "primary", "summary": "Alexis", "primary": True,
                       "accessRole": "owner"}],
        "count": 1}


# --- l'op qui ÉCRIT ------------------------------------------------------------

def test_create_calls_only_the_write_method(client):
    """L'écriture ne doit toucher QUE `create_event` — ni lecture parasite, ni
    double appel (un événement créé deux fois est un incident, pas un doublon
    d'affichage)."""
    _call("calendar_event", op="create", summary="Point", start="2026-08-12T10:00:00Z")
    client.create_event.assert_called_once()
    client.list_events.assert_not_called()
    client.get_event.assert_not_called()
    client.list_calendars.assert_not_called()


@pytest.mark.parametrize("op,kwargs", [
    ("list", {}),
    ("get", {"event_id": "e1"}),
])
def test_read_ops_never_write(client, op, kwargs):
    """Aucune lecture n'atteint l'écriture — le mode de panne redouté d'une
    consolidation par `op=` (un `op` mal câblé qui glisse sur la voisine)."""
    _call("calendar_event", op=op, **kwargs)
    client.create_event.assert_not_called()


def test_default_op_is_a_read(client):
    """Un appel SANS `op` ne doit jamais écrire : le défaut est `list`."""
    _call("calendar_event")
    client.list_events.assert_called_once()
    client.create_event.assert_not_called()


@pytest.mark.parametrize("missing,kwargs", [
    ("summary", {"start": "2026-08-12T10:00:00Z"}),
    ("start", {"summary": "Point"}),
])
def test_create_refuses_a_missing_required_arg_without_writing(client, missing, kwargs):
    """Titre ou date manquants → refus actionnable nommant l'op ET l'argument, et
    surtout AUCUN appel amont : pas d'événement inventé à partir d'un défaut."""
    with pytest.raises(McpError, match=missing):
        _call("calendar_event", op="create", **kwargs)
    client.create_event.assert_not_called()


def test_get_refuses_without_an_event_id(client):
    with pytest.raises(McpError, match="event_id"):
        _call("calendar_event", op="get")
    client.get_event.assert_not_called()


# --- refus d'une op inconnue ---------------------------------------------------

def test_unknown_op_is_refused_with_the_allowed_list(client):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être") as e:
        _call("calendar_event", op="nope")
    msg = e.value.error.message
    assert "'list'" in msg and "'get'" in msg and "'create'" in msg


@pytest.mark.parametrize("op", ["metadata", "delete", "update", "CREATE", ""])
def test_unknown_op_never_reaches_the_write_path(client, op):
    """Le scénario redouté : un `op` que le module ne connaît pas (faute de frappe,
    op d'un autre connecteur, casse différente) ne doit atteindre NI l'écriture, ni
    une lecture par défaut."""
    with pytest.raises(McpError, match="op doit être"):
        _call("calendar_event", op=op, summary="Point", start="2026-08-12T10:00:00Z")
    client.create_event.assert_not_called()
    client.list_events.assert_not_called()
    client.get_event.assert_not_called()


# --- fidélité des arguments (le client prend du POSITIONNEL) -------------------

def test_list_forwards_the_range_in_the_client_positional_order(client):
    """`CalendarClient.list_events(calendar_id, time_min, time_max, max_results,
    query)` : `max_results` est AVANT `query`. Inverser rendrait une recherche
    silencieusement fausse (une plage bornée par un mot-clé)."""
    _call("calendar_event", op="list", calendar_id="team@x.com",
          time_min="2026-08-11T00:00:00Z", time_max="2026-08-18T00:00:00Z",
          query="point hebdo", max_results=5)
    assert client.list_events.call_args.args == (
        "team@x.com", "2026-08-11T00:00:00Z", "2026-08-18T00:00:00Z", 5,
        "point hebdo")


def test_list_leaves_the_bounds_open_when_omitted(client):
    """« Omit either bound to leave it open » : les bornes absentes partent en None
    (le client ne pose alors ni `timeMin` ni `timeMax`), pas en chaîne vide."""
    _call("calendar_event", op="list")
    assert client.list_events.call_args.args == ("primary", None, None, 20, None)


def test_list_returns_the_events_and_their_count(client):
    client.list_events.return_value = [{"id": "e1"}, {"id": "e2"}]
    assert _call("calendar_event", op="list") == {
        "events": [{"id": "e1"}, {"id": "e2"}], "count": 2}


def test_get_forwards_the_event_then_the_calendar(client):
    client.get_event.return_value = {"id": "e1", "summary": "Point"}
    assert _call("calendar_event", op="get", event_id="e1",
                 calendar_id="team@x.com") == {"id": "e1", "summary": "Point"}
    assert client.get_event.call_args.args == ("e1", "team@x.com")


def test_create_forwards_every_field_in_the_client_positional_order(client):
    """`create_event(summary, start, end, description, location, all_day,
    calendar_id)` — un décalage ici écrirait le lieu dans la description, ou le
    `calendar_id` dans le drapeau all-day."""
    client.create_event.return_value = {"id": "new"}
    out = _call("calendar_event", op="create", summary="Point", start="2026-08-12",
                end="2026-08-13", description="ODJ", location="Marseille",
                all_day=True, calendar_id="team@x.com")
    assert out == {"id": "new"}
    assert client.create_event.call_args.args == (
        "Point", "2026-08-12", "2026-08-13", "ODJ", "Marseille", True,
        "team@x.com")


def test_create_lets_the_client_default_the_end(client):
    """« If omitted, defaults to start + 1h » est une règle du CLIENT : le tool
    transmet `end=None`, il ne la recalcule pas (deux défauts divergeraient)."""
    _call("calendar_event", op="create", summary="Point",
          start="2026-08-12T10:00:00Z")
    assert client.create_event.call_args.args == (
        "Point", "2026-08-12T10:00:00Z", None, None, None, False, "primary")


# --- compte ciblé (multi-compte Google) ----------------------------------------

@pytest.mark.parametrize("name,kwargs", [
    ("calendar_calendars", {}),
    ("calendar_event", {"op": "list"}),
    ("calendar_event", {"op": "get", "event_id": "e1"}),
    ("calendar_event", {"op": "create", "summary": "P", "start": "2026-08-12"}),
])
def test_account_selects_the_google_account(client, name, kwargs):
    """`account` (email) reste un argument MÉTIER de chaque op : il choisit le
    compte Google dont on lit/écrit l'agenda. Le perdre ferait écrire dans le
    mauvais agenda sans la moindre erreur."""
    _call(name, account="alexis@otomata.tech", **kwargs)
    assert client.resolved_accounts == ["alexis@otomata.tech"]


def test_account_defaults_to_none(client):
    _call("calendar_event", op="list")
    assert client.resolved_accounts == [None]
