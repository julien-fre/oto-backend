"""`lemlist_lead` by email: the ops whose route takes a lead id resolve it from
`campaign_id` + `email`, and a lookup by email that misses is an answer.

Seen in production, in a workspace with two lemlist accounts: `op="pause"` called
with `campaign_id` + `email` was refused ("`lead_id` required"), and an agent checking
which account holds a lead got "lemlist HTTP 404: Lead not found" as a FAILURE from
the account that does not — the expected answer to its question.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from oto_mcp.mcp_errors import McpError


def _appel(nom, **kwargs):
    from fastmcp import FastMCP

    from oto_mcp.tools import lemlist
    m = FastMCP("t")
    lemlist.register(m)
    fn = asyncio.run(m.get_tool(nom)).fn
    return fn(**kwargs)


@pytest.fixture
def client():
    with patch("oto_mcp.access.resolve_api_key", return_value=("k", False)), \
            patch("oto.tools.lemlist.LemlistClient") as C:
        yield C.return_value


def _not_found(body="Lead not found"):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(404, body, service="lemlist")


LEAD = {"_id": "lea_1", "campaignId": "cam_1", "email": "jane@site.example"}


# --- (b) a lookup by email that misses is an answer --------------------------------

@pytest.mark.parametrize("body", ["Lead not found", {"error": "Lead not found"},
                                  {"message": "lead not found"}])
def test_a_lookup_by_email_that_misses_answers_found_false(client, body):
    client.get_lead_by_email.side_effect = _not_found(body)
    with patch("oto_mcp.session_org.current_call_trace",
               return_value={"resolved_account": "second-workspace"}):
        out = _appel("lemlist_lead", op="get", email="jane@site.example")
    assert out["found"] is False
    assert out["email"] == "jane@site.example"
    assert out["account"] == "second-workspace", "a miss says WHERE it looked"


def test_a_found_lead_says_found_true_and_keeps_lemlist_s_record(client):
    client.get_lead_by_email.return_value = dict(LEAD)
    out = _appel("lemlist_lead", op="get", email="jane@site.example")
    assert out == {"found": True, **LEAD}


def test_another_404_on_a_lookup_still_raises(client):
    """An unknown route also answers 404: a refusal, never "not found"."""
    from oto.tools.common.errors import UpstreamHTTPError

    client.get_lead_by_email.side_effect = _not_found(
        {"statusCode": 404, "error": "Not Found", "message": "Route GET:/x not found"})
    with pytest.raises(UpstreamHTTPError):
        _appel("lemlist_lead", op="get", email="jane@site.example")


def test_a_lookup_by_id_that_misses_still_raises(client):
    """A lead id is not a question about an account: an unknown one is a wrong id."""
    from oto.tools.common.errors import UpstreamHTTPError

    client.get_lead.side_effect = _not_found()
    with pytest.raises(UpstreamHTTPError):
        _appel("lemlist_lead", op="get", lead_id="lea_x")


def test_a_write_on_an_email_with_no_lead_still_raises(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.get_lead_by_email.side_effect = _not_found()
    with pytest.raises(UpstreamHTTPError):
        _appel("lemlist_lead", op="pause", campaign_id="cam_1",
               email="jane@site.example")
    client.pause_lead.assert_not_called()


def test_delete_by_email_keeps_raising_on_404(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.delete_lead.side_effect = _not_found()
    with pytest.raises(UpstreamHTTPError):
        _appel("lemlist_lead", op="delete", campaign_id="cam_1",
               email="jane@site.example")


# --- (a) the ops that take a lead id accept campaign_id + email ----------------------

def test_pause_with_campaign_and_email_resolves_the_lead_of_that_campaign(client):
    client.get_lead_by_email.return_value = dict(LEAD)
    client.pause_lead.return_value = {"ok": True}
    out = _appel("lemlist_lead", op="pause", campaign_id="cam_1",
                 email="jane@site.example")
    client.get_lead_by_email.assert_called_once_with("jane@site.example")
    client.pause_lead.assert_called_once_with("lea_1", campaign_id="cam_1")
    assert out["lead_from_email"] == {"email": "jane@site.example", "lead_id": "lea_1"}


@pytest.mark.parametrize("op,kwargs,method,args", [
    ("resume", {}, "resume_lead", ("lea_1",)),
    ("update", {"fields": {"jobTitle": "CTO"}}, "update_lead",
     ("cam_1", "lea_1", {"jobTitle": "CTO"})),
    ("vars_update", {"variables": {"a": "1"}}, "update_lead_variables",
     ("lea_1", {"a": "1"})),
    ("vars_delete", {"variable_names": ["a"]}, "delete_lead_variables",
     ("lea_1", ["a"])),
])
def test_every_lead_id_op_resolves_from_campaign_and_email(client, op, kwargs, method,
                                                           args):
    client.get_lead_by_email.return_value = dict(LEAD)
    getattr(client, method).return_value = {}
    _appel("lemlist_lead", op=op, campaign_id="cam_1", email="jane@site.example",
           **kwargs)
    assert getattr(client, method).call_args.args == args


def test_a_list_answer_is_searched_for_the_campaign_asked(client):
    client.get_lead_by_email.return_value = [
        {"_id": "lea_0", "campaignId": "cam_0"}, dict(LEAD)]
    _appel("lemlist_lead", op="pause", campaign_id="cam_1", email="jane@site.example")
    assert client.pause_lead.call_args.args == ("lea_1",)


def test_another_campaign_s_lead_is_never_acted_on(client):
    client.get_lead_by_email.return_value = {"_id": "lea_9", "campaignId": "cam_9"}
    with pytest.raises(McpError) as e:
        _appel("lemlist_lead", op="pause", campaign_id="cam_1",
               email="jane@site.example")
    msg = str(e.value)
    assert 'lemlist_lead(op="list", campaign_id="cam_1")' in msg
    client.pause_lead.assert_not_called()


def test_an_email_without_a_campaign_is_refused_with_the_call_that_gives_the_id(client):
    with pytest.raises(McpError) as e:
        _appel("lemlist_lead", op="resume", email="jane@site.example")
    assert 'lemlist_lead(op="get", email="jane@site.example")' in str(e.value)
    client.get_lead_by_email.assert_not_called()
    client.resume_lead.assert_not_called()


def test_a_lead_id_given_is_used_as_is(client):
    _appel("lemlist_lead", op="pause", lead_id="lea_7", email="jane@site.example",
           campaign_id="cam_1")
    client.get_lead_by_email.assert_not_called()
    assert client.pause_lead.call_args.args == ("lea_7",)
