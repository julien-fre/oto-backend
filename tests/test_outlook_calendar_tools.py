"""Tools `outlook_calendar_*` du connecteur Outlook Calendar (service du porteur
`microsoft`).

Ce que ce fichier verrouille :
- la SURFACE (2 tools), le jeton du service `outlook_calendar`, le refus nommé d'un
  compte qui ne l'a pas autorisé ;
- le dispatch des ops vers les VRAIES méthodes de `CalendarClient` (autospec) ;
- ⚠️ les invitations : Graph écrit LUI-MÊME aux participants. Une création avec
  participants, une modification ou une suppression (organisateur) d'un événement qui
  en a sont REFUSÉES sans `notify_attendees=True`, et rien n'est écrit ; sans
  participants, rien n'est exigé ;
- la vue resserrée par défaut, le brut avec `full=True`.
"""
import asyncio
from unittest.mock import create_autospec

import pytest
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def client(monkeypatch):
    import oto.tools.microsoft as coeur

    inst = create_autospec(coeur.CalendarClient, instance=True)
    jetons = []
    monkeypatch.setattr(coeur, "CalendarClient",
                        lambda jeton: jetons.append(jeton) or inst)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")

    def jeton(sub, service):
        assert service == "outlook_calendar"
        return f"AT-{sub}"

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", jeton)
    inst.jetons = jetons
    return inst


def _tool(name):
    from fastmcp import FastMCP
    from oto_mcp.tools import outlook_calendar as X

    m = FastMCP("t")
    X.register(m)
    return asyncio.run(m.get_tool(name)).fn


EVT = {"id": "e1", "subject": "Point", "start": {"dateTime": "2026-10-15T14:00:00",
                                                 "timeZone": "UTC"},
       "attendees": [{"emailAddress": {"address": "marc@fabrikam.example"},
                      "status": {"response": "accepted"}}],
       "isOrganizer": True, "onlineMeeting": {"joinUrl": "https://teams/x"},
       "body": {"content": "<html>lourd</html>"}}
SEUL = {"id": "e2", "subject": "Focus", "attendees": [], "isOrganizer": True}


def test_surface_et_calendriers(client):
    from fastmcp import FastMCP
    from oto_mcp.tools import outlook_calendar as X

    m = FastMCP("t")
    X.register(m)
    assert {t.name for t in asyncio.run(m.list_tools())} == {
        "outlook_calendar_calendars", "outlook_calendar_event"}
    client.list_calendars.return_value = [{"id": "c1", "name": "Calendar",
                                           "isDefaultCalendar": True,
                                           "owner": {"address": "moi@x"}, "color": "x"}]
    out = _tool("outlook_calendar_calendars")()
    assert out["calendars"] == [{"id": "c1", "name": "Calendar", "isDefaultCalendar": True,
                                 "canEdit": None, "owner": "moi@x"}]
    assert client.jetons == ["AT-sub-1"]


def test_un_compte_sans_le_calendrier_recoit_le_refus_du_porteur(client, monkeypatch):
    def refus(sub, service):
        raise RuntimeError("has not yet authorized Outlook Calendar")

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", refus)
    with pytest.raises(McpError, match="Outlook Calendar"):
        _tool("outlook_calendar_event")(start="a", end="b")
    assert client.jetons == []


def test_list_une_fenetre_vue_et_brut(client):
    client.list_events.return_value = [dict(EVT)]
    out = _tool("outlook_calendar_event")(start="2026-10-12T00:00:00Z",
                                          end="2026-10-17T00:00:00Z",
                                          timezone="Europe/Paris", limit=10)
    client.list_events.assert_called_once_with(
        start="2026-10-12T00:00:00Z", end="2026-10-17T00:00:00Z", calendar_id=None,
        limit=10, timezone="Europe/Paris")
    e = out["events"][0]
    assert e["attendees"] == [{"address": "marc@fabrikam.example", "response": "accepted"}]
    assert e["joinUrl"] == "https://teams/x" and "body" not in e
    assert _tool("outlook_calendar_event")(start="a", end="b", full=True)["events"] == [EVT]
    with pytest.raises(McpError, match="requires `end`"):
        _tool("outlook_calendar_event")(start="a")


def test_creer_sans_participants_n_exige_rien(client):
    client.create_event.return_value = dict(SEUL)
    _tool("outlook_calendar_event")(op="create", subject="Focus", start="2026-10-15T14:00:00",
                                    end="2026-10-15T15:00:00", timezone="Europe/Paris",
                                    body="**ordre du jour**", online_meeting=True)
    kw = client.create_event.call_args.kwargs
    assert kw["attendees"] == () and kw["online_meeting"] is True
    assert "<strong>ordre du jour</strong>" in kw["body_html"]
    assert kw["timezone"] == "Europe/Paris"


def test_creer_avec_participants_refuse_sans_notify(client):
    with pytest.raises(McpError) as e:
        _tool("outlook_calendar_event")(op="create", subject="Point", start="a", end="b",
                                        attendees=["marc@fabrikam.example"])
    assert "notify_attendees=True" in str(e.value) and "marc@fabrikam.example" in str(e.value)
    assert "Nothing was done" in str(e.value)
    client.create_event.assert_not_called()
    assert client.jetons == [], "refus AVANT tout appel"
    client.create_event.return_value = dict(EVT)
    _tool("outlook_calendar_event")(op="create", subject="Point", start="a", end="b",
                                    attendees=["marc@fabrikam.example"],
                                    notify_attendees=True)
    assert client.create_event.call_args.kwargs["attendees"] == ["marc@fabrikam.example"]


def test_modifier_un_evenement_avec_participants_refuse_sans_notify(client):
    client.get_event.return_value = dict(EVT)
    with pytest.raises(McpError, match="notify_attendees=True"):
        _tool("outlook_calendar_event")(op="update", event_id="e1", start="c", end="d")
    client.update_event.assert_not_called()
    client.update_event.return_value = dict(EVT)
    _tool("outlook_calendar_event")(op="update", event_id="e1", start="c", end="d",
                                    timezone="Europe/Paris", notify_attendees=True)
    client.update_event.assert_called_once_with("e1", {
        "start": {"dateTime": "c", "timeZone": "Europe/Paris"},
        "end": {"dateTime": "d", "timeZone": "Europe/Paris"}})


def test_ajouter_des_participants_a_un_evenement_seul_refuse_sans_notify(client):
    client.get_event.return_value = dict(SEUL)
    with pytest.raises(McpError, match="new@x.example"):
        _tool("outlook_calendar_event")(op="update", event_id="e2",
                                        attendees=["new@x.example"])
    client.update_event.assert_not_called()
    client.update_event.return_value = dict(SEUL)
    _tool("outlook_calendar_event")(op="update", event_id="e2", subject="Focus 2")
    client.update_event.assert_called_once_with("e2", {"subject": "Focus 2"})


def test_modifier_exige_un_champ_et_les_deux_bornes(client):
    with pytest.raises(McpError, match="at least one field"):
        _tool("outlook_calendar_event")(op="update", event_id="e1")
    with pytest.raises(McpError, match="AND `end`"):
        _tool("outlook_calendar_event")(op="update", event_id="e1", start="c")


def test_supprimer_une_reunion_organisee_refuse_sans_notify(client):
    client.get_event.return_value = dict(EVT)
    with pytest.raises(McpError, match="deletion would email"):
        _tool("outlook_calendar_event")(op="rm", event_id="e1")
    client.delete_event.assert_not_called()
    out = _tool("outlook_calendar_event")(op="rm", event_id="e1", notify_attendees=True)
    client.delete_event.assert_called_once_with("e1")
    assert out["deleted"] == "e1" and out["subject"] == "Point"


def test_supprimer_une_invitation_recue_ou_un_evenement_seul(client):
    client.get_event.return_value = {**EVT, "isOrganizer": False}
    _tool("outlook_calendar_event")(op="rm", event_id="e1")
    client.get_event.return_value = dict(SEUL)
    _tool("outlook_calendar_event")(op="rm", event_id="e2")
    assert client.delete_event.call_count == 2
