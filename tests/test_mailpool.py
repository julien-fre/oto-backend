"""Mailpool connector — cold-email domains, DNS, mailboxes (wired 2026-10-05).

The generic tripwires already cover the registry, the publisher, the logo, the
served prose and the join to the oto-core client. This file locks what is
SPECIFIC to this module:

- **no credential reaches the caller**, from any tool and op, even when the
  client mock returns raw payloads with nested passwords (the allowlist is a
  second layer behind the client's own stripping);
- `mailpool_domain_fix`: dry run by default with a real diff, no write when
  nothing changes, the FULL set sent, a read-back that restores the previous
  set, DMARC published through the DMARC-email call;
- the DNS audit and plan rules (`mailpool_dns`);
- Mailpool's error envelopes translated (validations, 403, credits);
- arguments go through pydantic validation (`call_tool`, not `.fn`).
"""
import asyncio
import json
import re
from unittest.mock import patch

import pytest

from oto_mcp import providers
from oto_mcp.tools import mailpool_dns as dns


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("mp_k", False))
    monkeypatch.setattr("oto_mcp.tools.mailpool.time.sleep", lambda _s: None)


def _server():
    from fastmcp import FastMCP
    from oto_mcp.tools import mailpool

    m = FastMCP("t")
    mailpool.register(m)
    return m


def _call(name, args):
    res = asyncio.run(_server().call_tool(name, args))
    sc = getattr(res, "structured_content", None)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        return sc["result"]
    if sc is not None:
        return sc
    return json.loads(res.content[0].text)


SECRET = re.compile(r"pass|secret|auth_?code|api_?key|token", re.I)

OWNER = {"id": 9, "company": "Example Co", "email": "owner@example.com",
         "streetAddress1": "1 Example Street"}
MAILBOX = {
    "id": 5301, "email": "anna@example-outreach.com", "firstName": "Anna",
    "type": "shared", "status": "active", "password": "p1", "imapHost": "imap.example.net",
    "imapPassword": "p2", "smtpPassword": "p3", "secret": "s1", "avatar": "https://x/a.png",
    "admin": {"email": "admin@example-outreach.com", "password": "p4", "secret": "s2"},
    "domain": {"id": 1201, "domain": "example-outreach.com", "type": "shared",
               "domainOwner": OWNER},
}
DOMAIN = {"id": 1201, "domain": "example-outreach.com", "type": "google", "status": "active",
          "expireAt": "2027-01-01T00:00:00Z", "redirectUrl": "example.com",
          "domainOwner": OWNER, "nameservers": None}
RECORDS = [
    {"key": None, "type": "MX", "value": "mx.example.net", "priority": 1},
    {"key": None, "type": "TXT", "value": "v=spf1 include:_spf.example.net ~all"},
    {"key": "google._domainkey", "type": "TXT", "value": "v=DKIM1; k=rsa; p=AAA"},
    {"key": "_dmarc", "type": "TXT", "value": (
        "v=DMARC1; p=quarantine; fo=1; pct=100; rf=afrf; ri=86400; sp=quarantine; "
        "aspf=s; adkim=s; rua=mailto:old@example.com; ruf=mailto:old@example.com")},
]
SPAM = {"id": 880011, "createdAt": "2026-10-05T10:00:00Z", "state": "completed",
        "fromEmail": "anna@example-outreach.com", "mailbox": MAILBOX,
        "result": {"score": 97, "SPF": {"status": "passed"}, "DKIM": {"status": "passed"},
                   "spamAssassin": {"score": 0.9, "status": "warning"}}}


@pytest.fixture()
def client():
    with patch("oto.tools.mailpool.client.MailpoolClient") as cls:
        c = cls.return_value
        c.list_domains.return_value = {"data": [DOMAIN], "total": 1}
        c.get_domain.return_value = DOMAIN
        c.get_domain_dns.return_value = [dict(r) for r in RECORDS]
        c.list_mailboxes.return_value = {"data": [MAILBOX], "total": 1}
        c.get_mailbox.return_value = MAILBOX
        c.create_spam_check.return_value = SPAM
        c.get_spam_check.return_value = SPAM
        c.list_spam_checks.return_value = {"data": [SPAM], "total": 1}
        c.list_domain_owners.return_value = [OWNER]
        c.get_subscription_slots.return_value = {"slots": {"google": 4}}
        c.list_warmup_inboxes.return_value = {"data": [], "total": 0}
        yield c


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


# --- registry ---------------------------------------------------------------

def test_registry_entry():
    c = providers.REGISTRY["mailpool"]
    assert c.keyed and c.secret_kind == "api_key"
    assert c.auth_modes == {"byo_user", "byo_org"}


def test_tools_served_with_descriptions():
    tools = {t.name: t for t in asyncio.run(_server().list_tools())}
    assert set(tools) == {"mailpool_domains", "mailpool_domain_fix", "mailpool_mailboxes",
                          "mailpool_spam_checks", "mailpool_account"}
    assert all(t.description for t in tools.values())


def test_probe_reads_slots(client):
    from oto_mcp.tools.mailpool import _verify
    _verify({"key": "mp_k"})
    client.probe.assert_called_once()


# --- credentials ------------------------------------------------------------

@pytest.mark.parametrize("name,args", [
    ("mailpool_mailboxes", {"op": "list"}),
    ("mailpool_mailboxes", {"op": "get", "mailbox_id": 5301}),
    ("mailpool_spam_checks", {"op": "run", "mailbox_id": 5301}),
    ("mailpool_spam_checks", {"op": "get", "spam_check_id": 880011}),
    ("mailpool_spam_checks", {"op": "list"}),
    ("mailpool_domains", {"op": "list"}),
    ("mailpool_domains", {"op": "get", "domain_id": 1201}),
    ("mailpool_domains", {"op": "dns", "domain_id": 1201}),
    ("mailpool_domains", {"op": "audit"}),
    ("mailpool_domains", {"op": "owners"}),
    ("mailpool_domains", {"op": "availability", "domain": "example.com"}),
    ("mailpool_domains", {"op": "suggestions", "domain": "example"}),
    ("mailpool_domains", {"op": "google_workspace_check", "domain": "example.com"}),
    ("mailpool_domains", {"op": "microsoft_365_check", "domain": "example.com"}),
    ("mailpool_domain_fix", {"domain_id": 1201, "spf_hard_fail": True}),
    ("mailpool_spam_checks", {"op": "delete", "spam_check_id": 880011}),
    ("mailpool_account", {"op": "slots"}),
    ("mailpool_account", {"op": "warmup"}),
    ("mailpool_account", {"op": "warmup_inbox", "warmup_id": 77}),
    ("mailpool_account", {"op": "warmup_available"}),
])
def test_no_credential_reaches_the_caller(client, name, args):
    """Every op of every tool, with every client method returning payloads full
    of nested secrets — even ops that pass the response through."""
    for method in ("list_domain_owners", "get_domain_info", "get_domain_suggestions",
                   "check_google_workspace_availability", "check_microsoft_365_availability",
                   "delete_spam_check", "get_subscription_slots", "get_warmup_inbox"):
        getattr(client, method).return_value = {"data": [MAILBOX], **MAILBOX}
    for method in ("list_warmup_inboxes", "list_available_warmup_inboxes"):
        getattr(client, method).return_value = {"data": [MAILBOX, {"mailbox": MAILBOX}]}
    out = _call(name, args)
    assert [k for k in _keys(out) if SECRET.search(str(k))] == []
    assert "p1" not in json.dumps(out) and "s2" not in json.dumps(out)


def test_mailbox_is_an_allowlist(client):
    mb = _call("mailpool_mailboxes", {"op": "get", "mailbox_id": 5301})
    assert mb["email"] == "anna@example-outreach.com"
    assert mb["imapHost"] == "imap.example.net"
    assert mb["adminEmail"] == "admin@example-outreach.com"
    assert mb["domain"] == {"id": 1201, "domain": "example-outreach.com", "type": "shared"}
    assert "avatar" not in mb and "admin" not in mb


def test_spam_check_drops_the_embedded_mailbox(client):
    out = _call("mailpool_spam_checks", {"op": "run", "mailbox_id": 5301})
    assert "mailbox" not in out
    assert out["score"] == 97
    assert out["checks"] == {"SPF": "passed", "DKIM": "passed", "spamAssassin": "warning"}


def test_spam_check_run_waits_for_the_result(client):
    client.create_spam_check.return_value = {**SPAM, "state": "pending", "result": None}
    out = _call("mailpool_spam_checks", {"op": "run", "mailbox_id": 5301})
    assert out["state"] == "completed"
    client.get_spam_check.assert_called_with(880011)


# --- domain fix -------------------------------------------------------------

def test_fix_is_a_dry_run_by_default(client):
    out = _call("mailpool_domain_fix", {"domain_id": 1201, "spf_hard_fail": True,
                                        "dmarc_report_email": "dmarc@example.com"})
    assert out["dry_run"] is True and out["changed"] is True
    assert "TXT @ v=spf1 include:_spf.example.net -all" in out["added"]
    assert any("rua=mailto:dmarc@example.com; ruf=mailto:dmarc@example.com" in a
               and "fo=1" not in a for a in out["added"])
    client.update_domain_dns.assert_not_called()
    client.set_dmarc_email.assert_not_called()


def test_fix_applies_the_full_set_reads_back_and_publishes_dmarc(client):
    sent = {}

    def put(domain_id, records):
        sent["records"] = records
        client.get_domain_dns.return_value = records

    client.update_domain_dns.side_effect = put
    out = _call("mailpool_domain_fix", {
        "domain_id": 1201, "dmarc_report_email": "dmarc@example.com",
        "tracking_host": "track", "tracking_target": "custom.lemlist.com", "dry_run": False})
    assert out["applied"] == ["dns", "dmarc_published"]
    assert len(sent["records"]) == len(RECORDS) + 1
    assert {"type": "CNAME", "key": "track", "value": "custom.lemlist.com"} in sent["records"]
    assert any(r["type"] == "MX" for r in sent["records"])
    client.set_dmarc_email.assert_called_once_with(1201, "dmarc@example.com")


def test_fix_sends_nothing_when_already_matching(client):
    client.get_domain_dns.return_value = [
        {**r, "value": r["value"].replace("~all", "-all")} for r in RECORDS]
    out = _call("mailpool_domain_fix", {"domain_id": 1201, "spf_hard_fail": True,
                                        "dry_run": False})
    assert out["changed"] is False
    client.update_domain_dns.assert_not_called()


def test_fix_restores_the_previous_set_when_a_record_goes_missing(client):
    client.update_domain_dns.side_effect = lambda *_a: setattr(
        client.get_domain_dns, "return_value", RECORDS[:1])
    with pytest.raises(Exception, match="restored"):
        _call("mailpool_domain_fix", {"domain_id": 1201, "spf_hard_fail": True,
                                      "dry_run": False})
    assert client.update_domain_dns.call_count == 2
    restored = client.update_domain_dns.call_args_list[1].args[1]
    assert restored == RECORDS


def test_fix_settings_policy_and_redirect(client):
    out = _call("mailpool_domain_fix", {"domain_id": 1201, "dmarc_policy": "reject",
                                        "redirect_url": "example.com", "dry_run": False})
    assert out["settings"] == {"dmarc_policy": {"from": "quarantine", "to": "reject"}}
    client.set_dmarc_policy.assert_called_once_with(1201, "reject")
    client.set_redirect_url.assert_not_called()


def test_fix_arguments_are_validated(client):
    with pytest.raises(Exception):
        _call("mailpool_domain_fix", {"domain_id": "abc"})
    with pytest.raises(Exception):
        _call("mailpool_domain_fix", {"domain_id": 1201, "dmarc_policy": "strict"})
    with pytest.raises(Exception, match="go together"):
        _call("mailpool_domain_fix", {"domain_id": 1201, "tracking_host": "track"})


# --- dns rules --------------------------------------------------------------

def test_audit_findings():
    a = dns.audit("example-outreach.com", RECORDS)
    checks = {(f["level"], f["check"]) for f in a["findings"]}
    assert ("warning", "spf") in checks
    assert ("info", "tracking") in checks
    assert ("info", "dmarc") in checks          # rua on another domain
    assert not any("ruf" in f["message"] for f in a["findings"])  # Mailpool always sets it
    assert a["ok"] is True
    assert a["findings"][0]["level"] == "warning"


def test_audit_errors_and_tracking():
    a = dns.audit("example-outreach.com", [
        {"key": "track", "type": "CNAME", "value": "custom.lemlist.com"}])
    assert a["ok"] is False
    assert {f["check"] for f in a["findings"] if f["level"] == "error"} == {
        "mx", "spf", "dkim", "dmarc"}
    assert a["tracking"] == [{"host": "track", "target": "custom.lemlist.com", "tool": "lemlist"}]


def test_plan_converges_on_what_mailpool_publishes():
    """Mailpool republishes `_dmarc` with ruf = rua: a second plan on the
    published state must find nothing to change."""
    first = dns.plan(RECORDS, dmarc_report_email="dmarc@example.com", spf_hard_fail=True)
    again = dns.plan(first["records"], dmarc_report_email="dmarc@example.com", spf_hard_fail=True)
    assert again["changed"] is False


def test_plan_never_drops_unrelated_records():
    p = dns.plan(RECORDS, set_records=[{"type": "TXT", "key": None, "value": "verify=1"}])
    assert len(p["records"]) == len(RECORDS) + 1
    assert p["removed"] == [] and p["publish_dmarc_email"] is None


def test_plan_remove_needs_an_exact_match():
    with pytest.raises(ValueError):
        dns.plan(RECORDS, remove_records=[{"type": "TXT", "key": None, "value": "nope"}])


def test_plan_refuses_a_cname_next_to_other_records():
    with pytest.raises(ValueError):
        dns.plan(RECORDS, tracking_host="_dmarc", tracking_target="custom.lemlist.com")


# --- errors and arguments ---------------------------------------------------

def test_timeout_is_a_clean_tool_error(client):
    import requests
    client.get_domain_info.side_effect = requests.ReadTimeout("slow")
    with pytest.raises(Exception, match="did not answer in time"):
        _call("mailpool_domains", {"op": "availability", "domain": "example.com"})


def test_lists_count_what_they_return(client):
    client.list_spam_checks.return_value = {"data": [SPAM], "total": 4}
    out = _call("mailpool_spam_checks", {"op": "list"})
    assert out["count"] == 1 and "total" not in out

def _upstream(status, body):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, body, service="mailpool")


def test_validation_envelope_names_the_field(client):
    client.get_domain_info.side_effect = _upstream(400, {"validations": [
        {"context": "domain", "constraints": {"isFqdn": "domain must be a valid domain"}}]})
    with pytest.raises(Exception, match="`domain`: domain must be a valid domain"):
        _call("mailpool_domains", {"op": "availability", "domain": "example.com"})


def test_feature_not_enabled_is_explained(client):
    client.check_google_workspace_availability.side_effect = _upstream(
        403, {"message": "Google Workspace domain-in-use check is not enabled for this workspace"})
    with pytest.raises(Exception, match="enabled per workspace"):
        _call("mailpool_domains", {"op": "google_workspace_check", "domain": "example.com"})


def test_ops_refuse_foreign_arguments(client):
    with pytest.raises(Exception, match="does not use `domain_id`"):
        _call("mailpool_domains", {"op": "list", "domain_id": 1201})
    with pytest.raises(Exception, match="does not use `mailbox_id`"):
        _call("mailpool_spam_checks", {"op": "get", "spam_check_id": 1, "mailbox_id": 2})
    with pytest.raises(Exception, match="`mailbox_id` is required"):
        _call("mailpool_mailboxes", {"op": "get"})
