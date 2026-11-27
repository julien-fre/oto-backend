"""`cognism_redeem` cuts the company's job-posting events, never the contact.

A hiring company's `account.hiringEvent` can run to hundreds of entries: the redeemed
record became one line too long for any reader to page, and the email and phone the
credit paid for were out of reach. The list is cut to `max_hiring_events` (default 5)
and `hiringEvent_total` keeps the real count.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import cognism as C

    m = FastMCP("t")
    C.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.cognism.client.CognismClient",
                        lambda api_key=None: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("k", False))
    return inst


def _events(n):
    return [{"jobTitle": f"role {i}", "department": "R&D"} for i in range(n)]


def test_contact_keeps_email_and_phone_with_five_events(client):
    client.redeem_contacts.return_value = {"total": 1, "result": [{
        "email": {"address": "a@b.example"}, "mobilePhoneNumbers": [{"number": "+33"}],
        "account": {"name": "Acme", "hiringEvent": _events(300)},
    }]}
    out = _tool("cognism_redeem")(op="contact", ids=["c1"])
    rec = out["result"][0]
    assert rec["email"] == {"address": "a@b.example"}
    assert rec["mobilePhoneNumbers"] == [{"number": "+33"}]
    assert len(rec["account"]["hiringEvent"]) == 5
    assert rec["account"]["hiringEvent_total"] == 300
    assert out["total"] == 1


def test_account_record_is_cut_at_its_top_level(client):
    client.redeem_accounts.return_value = {"total": 1, "result": [{
        "name": "Acme", "hiringEvent": _events(40)}]}
    out = _tool("cognism_redeem")(op="account", ids=["a1"], max_hiring_events=2)
    assert len(out["result"][0]["hiringEvent"]) == 2
    assert out["result"][0]["hiringEvent_total"] == 40


def test_none_keeps_every_event(client):
    client.redeem_contacts.return_value = {"total": 1, "result": [{
        "account": {"hiringEvent": _events(30)}}]}
    out = _tool("cognism_redeem")(op="contact", ids=["c1"], max_hiring_events=None)
    assert len(out["result"][0]["account"]["hiringEvent"]) == 30
    assert "hiringEvent_total" not in out["result"][0]["account"]


@pytest.mark.parametrize("payload", [
    {"total": 1, "result": [{"account": {"hiringEvent": _events(3)}}]},
    {"total": 1, "result": [{"account": {"hiringEvent": {"department": ["R&D"]}}}]},
    {"total": 1, "result": [{"account": None}, "not a record"]},
    {"total": 0},
    [],
])
def test_other_shapes_pass_untouched(client, payload):
    import copy

    client.redeem_contacts.return_value = copy.deepcopy(payload)
    assert _tool("cognism_redeem")(op="contact", ids=["c1"]) == payload


def test_negative_limit_is_refused_before_any_credit_is_spent(client):
    with pytest.raises(McpError, match="max_hiring_events"):
        _tool("cognism_redeem")(op="contact", ids=["c1"], max_hiring_events=-1)
    client.redeem_contacts.assert_not_called()
