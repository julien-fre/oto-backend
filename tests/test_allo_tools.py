"""Tools `allo` / `allo_act` and the Allo probe.

What this file locks — the generic tripwires already cover registry, publisher,
logo, module derivation, served prose, the `<ns>`/`<ns>_act` convention and the
join to the client:
- the SURFACE (the pair) and the routing to the right client method, with the
  API key passed as is;
- intent refusals (an argument of another op, a missing required argument, a
  CRM field the record does not take, SMS sender ambiguity) — nothing is called;
- the translation of upstream refusals, Allo's own code and suggestion kept;
- the probe: 401/403 → `NonAutorise`, any other code unclassified.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.connectors import verify as cv
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def client(monkeypatch):
    inst, built = MagicMock(), []

    def fabrique(**kw):
        built.append(kw)
        return inst

    monkeypatch.setattr("oto.tools.allo.AlloClient", fabrique)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("ak_test", False))
    inst.built = built
    return inst


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import allo as A

    m = FastMCP("t")
    A.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def _upstream(status, body=None):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, body or {"error": {"type": "x"}}, service="allo")


# --- surface ------------------------------------------------------------------

def test_surface_is_the_pair(client):
    tools = {t.name: t for t in asyncio.run(_mcp().list_tools())}
    assert set(tools) == {"allo", "allo_act"}
    assert tools["allo"].annotations.readOnlyHint is True
    assert tools["allo_act"].annotations.readOnlyHint is False
    assert all(t.description for t in tools.values())


def test_registry_declares_one_api_key():
    from oto_mcp import providers
    c = providers.REGISTRY["allo"]
    assert c.kind == "tools" and c.secret_kind == "api_key"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})


def test_the_key_is_passed_as_is(client):
    _tool("allo")(op="me")
    assert client.built == [{"api_key": "ak_test"}]


# --- reads ----------------------------------------------------------------------

def test_search_maps_dates_type_and_transcript(client):
    _tool("allo")(op="search", search="pricing", item_type="CALL", date_from="2026-10-01",
                  date_to="2026-10-20", transcript=True, size=10)
    client.search_conversation_items.assert_called_once_with(
        allo_number=None, contact_number=None, user_id=None, direction=None, type="CALL",
        result=None, tags=None, unread=None, unresponded=None, search="pricing",
        date={"from": "2026-10-01", "to": "2026-10-20"}, sort=None, extend="transcript",
        page=None, size=10)


def test_notes_of_a_person_or_of_a_conversation(client):
    _tool("allo")(op="notes", person_id="per-1")
    client.list_person_notes.assert_called_once_with("per-1", page=None, size=None)
    _tool("allo")(op="notes", contact_number="+33612345678", allo_number="+33187000000")
    client.list_conversation_notes.assert_called_once_with(
        "+33612345678", "+33187000000", page=None, size=None)


def test_attributes_by_entity(client):
    _tool("allo")(op="attributes", entity="company")
    client.list_company_attributes.assert_called_once_with(page=None, size=None)
    client.list_person_attributes.assert_not_called()


def test_outbound_lists_a_stage_s_calls(client):
    _tool("allo")(op="outbound", date_from="2026-10-01", date_to="2026-10-20",
                  tags=["meeting_booked"], stage="CONVERSION", page=2)
    client.get_outbound_metrics.assert_called_once_with(
        {"from": "2026-10-01", "to": "2026-10-20"}, compare_date=None, user_ids=None,
        allo_numbers=None, tags=["meeting_booked"], granularity=None, extend="items",
        stage="CONVERSION", page=2, size=None)


def test_metrics_with_a_comparison(client):
    _tool("allo")(op="metrics", date_from="2026-10-12", date_to="2026-10-18",
                  compare_from="2026-10-05", compare_to="2026-10-11")
    client.get_team_metrics.assert_called_once_with(
        {"from": "2026-10-12", "to": "2026-10-18"},
        compare_date={"from": "2026-10-05", "to": "2026-10-11"}, user_ids=None,
        allo_numbers=None)


@pytest.mark.parametrize("kwargs,needle", [
    ({"op": "item"}, "`item_id` is required"),
    ({"op": "conversations"}, "`allo_number` is required"),
    ({"op": "person", "person_id": "per-1", "search": "x"}, "does not take `search`"),
    ({"op": "me", "transcript": True}, "does not take `transcript`"),
    ({"op": "notes"}, "needs `person_id`"),
    ({"op": "notes", "person_id": "per-1", "contact_number": "+3361"}, "not both"),
    ({"op": "search", "date_from": "2026-10-01"}, "go together"),
    ({"op": "outbound", "date_from": "2026-10-01", "date_to": "2026-10-02", "page": 2},
     "give `stage`"),
])
def test_read_refusals(client, kwargs, needle):
    with pytest.raises(McpError, match=needle):
        _tool("allo")(**kwargs)
    assert client.built == []


# --- writes ---------------------------------------------------------------------

def test_send_sms_from_a_sender_id(client):
    _tool("allo_act")(op="send_sms", to="+33612345678", message="Bonjour", sender_id="Acme")
    client.send_sms.assert_called_once_with("+33612345678", "Bonjour", from_number=None,
                                            sender_id="Acme")


@pytest.mark.parametrize("senders", [{}, {"from_number": "+1415", "sender_id": "Acme"}])
def test_send_sms_needs_exactly_one_sender(client, senders):
    with pytest.raises(McpError, match="exactly one"):
        _tool("allo_act")(op="send_sms", to="+33612345678", message="x", **senders)
    client.send_sms.assert_not_called()


def test_create_person_takes_numbers_out_of_fields(client):
    _tool("allo_act")(op="create_person", fields={"numbers": ["+33612345678"], "name": "Marie"})
    client.create_person.assert_called_once_with(["+33612345678"], name="Marie")


def test_create_company_takes_its_name_out_of_fields(client):
    _tool("allo_act")(op="create_company", fields={"name": "Acme", "industry": "Software"})
    client.create_company.assert_called_once_with("Acme", industry="Software")


def test_create_note_on_a_conversation(client):
    _tool("allo_act")(op="create_note", content="Call back Friday",
                      contact_number="+33612345678")
    client.create_conversation_note.assert_called_once_with(
        "+33612345678", "Call back Friday", allo_number=None, sender_id=None)


def test_queue_ops_route_to_the_dialing_queue(client):
    _tool("allo_act")(op="create_queue", name="October", email="rep@example.test")
    client.reset_dialing_queue.assert_called_once_with(name="October", user_id=None,
                                                       email="rep@example.test")
    entries = [{"number": "+33612345678", "name": "Marie"}]
    _tool("allo_act")(op="add_queue_numbers", entries=entries)
    client.add_queue_numbers.assert_called_once_with(entries, user_id=None, email=None)
    _tool("allo_act")(op="remove_queue_numbers", unassigned=True)
    client.remove_queue_numbers.assert_called_once_with(
        number=None, position=None, unassigned=True, user_id=None, email=None)


@pytest.mark.parametrize("kwargs,needle", [
    ({"op": "create_person", "fields": {"name": "Marie"}}, "fields.numbers"),
    ({"op": "create_person", "fields": {"numbers": ["+1"], "phone": "x"}}, "does not take `phone`"),
    ({"op": "update_company", "company_id": "com-1", "fields": {"numbers": ["+1"]}},
     "does not take `numbers`"),
    ({"op": "create_company", "fields": {"website": "x"}}, "fields.name"),
    ({"op": "add_tags", "item_id": "cll-1"}, "`tags` is required"),
    ({"op": "create_tag", "name": "Hot", "item_id": "cll-1"}, "does not take `item_id`"),
    ({"op": "create_note", "content": "x"}, "needs `person_id` or `contact_number`"),
    ({"op": "create_note", "content": "x", "person_id": "per-1", "contact_number": "+33"},
     "not both"),
])
def test_write_refusals(client, kwargs, needle):
    with pytest.raises(McpError, match=needle):
        _tool("allo_act")(**kwargs)
    assert client.built == []


# --- upstream refusals ------------------------------------------------------------

@pytest.mark.parametrize("status,needle", [
    (401, "refused the API key"), (403, "lacks the scope"), (404, "not found"),
    (429, "retry in 3 seconds"), (503, "temporarily unavailable"),
])
def test_upstream_refusals_translated(client, status, needle):
    client.get_person.side_effect = _upstream(status, {"error": {
        "code": "X_CODE", "message": "msg", "retry_after_seconds": 3}})
    with pytest.raises(McpError, match=needle):
        _tool("allo")(op="person", person_id="per-1")


def test_validation_error_keeps_allo_s_suggestion(client):
    client.create_tag.side_effect = _upstream(400, {"error": {
        "code": "VALIDATION_ERROR", "message": "name is blank",
        "suggestion": "Send a non-empty name."}})
    with pytest.raises(McpError, match="VALIDATION_ERROR name is blank Send a non-empty name."):
        _tool("allo_act")(op="create_tag", name=" ")


# --- probe ------------------------------------------------------------------------

def _probe_with(monkeypatch, error=None):
    inst = MagicMock()
    if error is not None:
        inst.probe.side_effect = error
    monkeypatch.setattr("oto.tools.allo.AlloClient", lambda **kw: inst)
    from oto_mcp.tools import allo as A
    return A._verify


def test_probe_ok(monkeypatch):
    _probe_with(monkeypatch)({"key": "ak_test"})


@pytest.mark.parametrize("status", [401, 403])
def test_probe_refused(monkeypatch, status):
    with pytest.raises(cv.NonAutorise):
        _probe_with(monkeypatch, _upstream(status))({"key": "ak_test"})


def test_probe_other_error_unclassified(monkeypatch):
    with pytest.raises(RuntimeError):
        _probe_with(monkeypatch, _upstream(500))({"key": "ak_test"})
