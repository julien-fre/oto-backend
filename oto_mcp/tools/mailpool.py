"""Mailpool tools — cold-email infrastructure: domains, DNS, mailboxes, spam
checks, warmup.

Wraps `oto.tools.mailpool.client.MailpoolClient` (API v1, `X-Api-Authorization`).
Five tools, one per business object:

- `mailpool_domains` — list, read, DNS records, deliverability audit,
  availability and suggestions for a new name, saved owners;
- `mailpool_domain_fix` — the only tool that edits DNS: SPF hard fail, DMARC
  report address, tracking CNAME, single records, redirect, DMARC policy.
  `dry_run=True` by default, with a real diff;
- `mailpool_mailboxes` — list, read;
- `mailpool_spam_checks` — run (free), read, list, delete;
- `mailpool_account` — subscription slots, warmup.

⚠️ **No credential ever reaches the caller.** Three layers: the client strips
every password/secret key, `_run` scrubs every result again, and mailboxes are
served through a field ALLOWLIST (`_slim_mailbox`), spam checks without their
embedded mailbox — a mailbox field Mailpool adds tomorrow is not served until it
is listed here.

⚠️ **Nothing billed is served**: registering, transferring or renewing a domain,
creating a mailbox, changing slots, enrolling in warmup, inbox placement.

DNS behaviour (observed live on 2026-10-05, encoded in `mailpool_dns`): a DNS
write REPLACES the whole record set — the fix tool always reads, sends the full
set, reads back and restores the previous set if anything went missing; the
`_dmarc` record only goes live through the DMARC-email call, which also writes
`ruf`. Changes reach public DNS within ~5 minutes.

Client calls are written out (`_client().get_domain_dns(…)`) so the version-skew
probe (`test_tools_client_methods_exist`) can check them.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import mailpool_dns


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, **provided) -> None:
    """A provided argument THIS op does not use is an intent error."""
    for name, value in provided.items():
        if value is not None and value is not False:
            raise _bad(f"op={op!r} does not use `{name}`.")


#: Mailbox fields served. Anything else (passwords, admin account, avatar…) is not.
MAILBOX_FIELDS = ("id", "email", "firstName", "lastName", "type", "status",
                  "forwardTo", "isAdmin", "imapHost", "imapPort", "imapTLS",
                  "smtpHost", "smtpPort", "smtpTLS", "error")
DOMAIN_FIELDS = ("id", "domain", "type", "status", "expireAt", "redirectUrl")

#: Spam checks are complete within seconds; `run` waits at most this long
#: (the REST tool-invoke path cuts at 45 s).
SPAM_CHECK_WAIT_S = 20
SPAM_CHECK_POLL_S = 4

#: `op="audit"` without `domain_id` reads one DNS set per domain.
AUDIT_MAX_DOMAINS = 20


def _slim_domain(d: Any) -> Any:
    if not isinstance(d, dict):
        return d
    out = {k: d.get(k) for k in DOMAIN_FIELDS if d.get(k) is not None}
    owner = d.get("domainOwner")
    if isinstance(owner, dict) and owner.get("company"):
        out["owner"] = owner.get("company")
    return out


def _slim_mailbox(m: Any) -> Any:
    if not isinstance(m, dict):
        return m
    out = {k: m.get(k) for k in MAILBOX_FIELDS if m.get(k) not in (None, "")}
    admin = m.get("admin")
    if isinstance(admin, dict) and admin.get("email"):
        out["adminEmail"] = admin["email"]
    if isinstance(m.get("domain"), dict):
        out["domain"] = {k: m["domain"].get(k) for k in ("id", "domain", "type")}
    return out


def _slim_spam_check(check: Any, full: bool) -> Any:
    if not isinstance(check, dict):
        return check
    result = check.get("result") if isinstance(check.get("result"), dict) else {}
    out = {k: check.get(k) for k in ("id", "createdAt", "state", "fromEmail")
           if check.get(k) is not None}
    if result:
        out["score"] = result.get("score")
        out["checks"] = {k: v.get("status") for k, v in result.items()
                         if isinstance(v, dict) and "status" in v}
        if full:
            out["result"] = result
    return out


WARMUP_FIELDS = ("id", "mailboxId", "email", "fullName", "type", "status",
                 "createdAt")


def _slim_warmup(w: Any) -> Any:
    """A warmup or available inbox, as an allowlist (spec `WarmupInbox` /
    `AvailableWarmupInbox`)."""
    if not isinstance(w, dict):
        return w
    return {k: w.get(k) for k in WARMUP_FIELDS if w.get(k) is not None}


def _page(res: Any, slim) -> Any:
    """`{count, items}`. Mailpool's own `total` is not served: on spam checks it
    still counts deleted ones (seen live: total 4, one item)."""
    if isinstance(res, dict) and isinstance(res.get("data"), list):
        items = [slim(x) for x in res["data"]]
        return {"count": len(items), "items": items}
    return res


def _upstream_message(e) -> str:
    """Mailpool refusals: `{message, error, statusCode}` or, on validation,
    `{validations: [{context, constraints}]}` — the field names are kept."""
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    detail = body.get("message") or e.body
    if isinstance(body.get("validations"), list):
        detail = "; ".join(
            f"`{v.get('context')}`: {', '.join((v.get('constraints') or {}).values())}"
            for v in body["validations"] if isinstance(v, dict))
    if status == 401:
        return ("Mailpool rejected the key (401) — check the workspace API key set on "
                "this connector (Mailpool → Settings → API Keys).")
    if status == 403:
        return (f"Mailpool refused (403): {detail} — this feature is enabled per "
                "workspace, on Mailpool's side.")
    if status == 404:
        return f"Mailpool: not found (404) — {detail}."
    if status == 400 and "credit" in str(detail).lower():
        return f"Mailpool: {detail} (400) — this action is billed in credits; nothing was done."
    if status == 429:
        return "Mailpool: too many requests (429) — retry later."
    if status in (500, 502, 503, 504):
        return f"Mailpool is temporarily unavailable (HTTP {status}) — retry later."
    return f"Mailpool refused the request (HTTP {status}): {detail}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test connection" probe: the subscription slots — no parameter, no
    personal data, a clean 401 if the key is wrong."""
    from oto.tools.mailpool.client import MailpoolClient
    MailpoolClient(api_key=fields["key"]).probe()


def register(mcp: FastMCP) -> None:
    import requests
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.mailpool.client import MailpoolClient, strip_secrets

    connector_verify.register("mailpool", _verify)

    def _client() -> MailpoolClient:
        key, _ = access.resolve_api_key("mailpool")
        return MailpoolClient(api_key=key)

    def _run(fn):
        """Every client call goes through here: errors become tool errors, and
        the result is scrubbed of secret-looking keys a second time — the single
        choke point, whatever the op does with it afterwards."""
        try:
            return strip_secrets(fn())
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        except (requests.Timeout, requests.ConnectionError):
            raise _bad("Mailpool did not answer in time — retry later. Nothing "
                       "is known to have changed.")

    def _need(value, name: str, op: str):
        if value is None or value == "":
            raise _bad(f"op={op!r}: `{name}` is required.")
        return value

    # --- domains ---------------------------------------------------------------

    @mcp.tool()
    def mailpool_domains(
        op: Literal["list", "get", "dns", "audit", "availability", "suggestions",
                    "google_workspace_check", "microsoft_365_check", "owners"] = "list",
        domain_id: Optional[int] = None,
        domain: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Any:
        """Mailpool domains: read them, audit their deliverability setup, look up a new name.

        Ops:
        - `list` — the workspace's domains (type google/outlook/shared/private,
          status, expiry, redirect). `limit` (1-100) / `offset` paginate.
        - `get` — one domain (`domain_id`).
        - `dns` — the DNS records Mailpool holds for a domain (`domain_id`), as
          `{type, key, value, priority}`; `key` is the host label, null = apex.
        - `audit` — findings on SPF, DKIM, DMARC, MX and custom tracking domain,
          worst first, for one domain (`domain_id`) or the first page of domains
          (at most 20). Read from Mailpool's stored configuration, not a live DNS
          lookup. Fix what it finds with `mailpool_domain_fix`.
        - `availability` — can `domain` be registered, and at what price.
          Nothing is bought (registration is not served by this connector).
        - `suggestions` — available names close to `domain`, with price.
        - `google_workspace_check` / `microsoft_365_check` — is `domain` already
          used by a Google Workspace / Microsoft 365 account (enabled per
          Mailpool workspace, otherwise refused).
        - `owners` — saved registrant contacts.

        Args:
            op: what to do (see above).
            domain_id: Mailpool domain id (from `op="list"`).
            domain: domain name, for availability/suggestions/checks.
            limit: page size, 1-100.
            offset: items to skip.
        """
        if op in ("list", "suggestions", "owners"):
            _refuse_ignored(op, domain_id=domain_id)
        if op == "list":
            _refuse_ignored(op, domain=domain)
            return _page(_run(lambda: _client().list_domains(limit=limit, offset=offset)),
                         _slim_domain)
        if op == "get":
            _refuse_ignored(op, domain=domain)
            return _slim_domain(_run(lambda: _client().get_domain(_need(domain_id, "domain_id", op))))
        if op == "dns":
            _refuse_ignored(op, domain=domain)
            return {"domain_id": domain_id,
                    "records": _run(lambda: _client().get_domain_dns(_need(domain_id, "domain_id", op))),
                    "source": "Mailpool's stored configuration, not a live DNS lookup"}
        if op == "audit":
            _refuse_ignored(op, domain=domain)
            if domain_id is not None:
                targets = [_run(lambda: _client().get_domain(domain_id))]
            else:
                page = _run(lambda: _client().list_domains(
                    limit=min(limit, AUDIT_MAX_DOMAINS), offset=offset))
                targets = page.get("data", []) if isinstance(page, dict) else []
            audits = []
            for d in targets:
                records = _run(lambda: _client().get_domain_dns(d["id"]))
                audits.append({"domain_id": d["id"], **mailpool_dns.audit(d["domain"], records)})
            return audits[0] if domain_id is not None else {"domains": audits}
        if op == "availability":
            return _run(lambda: _client().get_domain_info(_need(domain, "domain", op)))
        if op == "suggestions":
            return _run(lambda: _client().get_domain_suggestions(
                _need(domain, "domain", op), limit=limit, offset=offset))
        if op == "google_workspace_check":
            return _run(lambda: _client().check_google_workspace_availability(_need(domain, "domain", op)))
        if op == "microsoft_365_check":
            return _run(lambda: _client().check_microsoft_365_availability(_need(domain, "domain", op)))
        _refuse_ignored(op, domain=domain)
        return _run(lambda: _client().list_domain_owners())

    @mcp.tool()
    def mailpool_domain_fix(
        domain_id: int,
        spf_hard_fail: bool = False,
        dmarc_report_email: Optional[str] = None,
        tracking_host: Optional[str] = None,
        tracking_target: Optional[str] = None,
        set_records: Optional[List[Dict[str, Any]]] = None,
        remove_records: Optional[List[Dict[str, Any]]] = None,
        dmarc_policy: Optional[Literal["none", "quarantine", "reject"]] = None,
        redirect_url: Optional[str] = None,
        dry_run: bool = True,
    ) -> Any:
        """Edit a Mailpool domain's DNS and DMARC settings — preview first, then apply.

        `dry_run=True` (the default) reads the current records and returns the
        exact diff (`added`, `removed`) without writing anything. Re-call with
        `dry_run=False` to apply. Nothing is sent when there is no difference.

        Changes, combinable:
        - `spf_hard_fail=True` — end the apex SPF record in `-all` instead of
          `~all`. Only once every service sending as this domain is listed.
        - `dmarc_report_email` — where DMARC reports go. The record is also
          normalised: `fo`, `rf`, `ri` are dropped, policy and alignment kept.
          Mailpool always publishes the address as `rua` AND `ruf`.
        - `tracking_host` + `tracking_target` — a custom tracking domain, e.g.
          `track` → `custom.lemlist.com` (the target comes from the sending tool).
          Replaces any CNAME already on that host.
        - `set_records` / `remove_records` — single records
          `{type, key, value, priority?}` (`key` = host label, null = apex), e.g.
          a verification TXT a sending tool asks for.
        - `dmarc_policy` — none / quarantine / reject.
        - `redirect_url` — where a web visit to the domain goes.
        ⚠️ `dmarc_policy` and a NEW `redirect_url` are not live-verified yet:
        re-run the audit after applying them.

        Safety: a DNS write replaces the WHOLE record set, so the full set is
        always sent, read back, and the previous set restored if a record went
        missing. Public DNS follows within ~5 minutes.

        Args:
            domain_id: Mailpool domain id.
            spf_hard_fail: switch SPF to `-all`.
            dmarc_report_email: DMARC report address.
            tracking_host: tracking subdomain label (e.g. `track`).
            tracking_target: its CNAME target, given by the sending tool.
            set_records: records to add.
            remove_records: records to delete (exact match).
            dmarc_policy: DMARC policy.
            redirect_url: web redirect target.
            dry_run: preview only (default True).
        """
        current = _run(lambda: _client().get_domain_dns(domain_id))
        try:
            p = mailpool_dns.plan(
                current, spf_hard_fail=spf_hard_fail, dmarc_report_email=dmarc_report_email,
                tracking_host=tracking_host, tracking_target=tracking_target,
                set_records=set_records, remove_records=remove_records)
        except ValueError as e:
            raise _bad(str(e))
        settings = {}
        if dmarc_policy is not None or redirect_url is not None:
            dom = _run(lambda: _client().get_domain(domain_id))
            dmarc_rec = [r for r in current if (r.get("key") or "") == "_dmarc"]
            cur_policy = (mailpool_dns.parse_dmarc(dmarc_rec[0]["value"]).get("p")
                          if dmarc_rec else None)
            if dmarc_policy is not None and dmarc_policy != cur_policy:
                settings["dmarc_policy"] = {"from": cur_policy, "to": dmarc_policy}
            if redirect_url is not None and redirect_url.strip() != (dom.get("redirectUrl") or ""):
                settings["redirect_url"] = {"from": dom.get("redirectUrl"), "to": redirect_url.strip()}
        preview = {"domain_id": domain_id, "added": p["added"], "removed": p["removed"],
                   "settings": settings, "notes": p["notes"]}
        if not p["changed"] and not settings:
            return {**preview, "dry_run": dry_run, "changed": False,
                    "message": "Nothing to change: the domain already matches."}
        if dry_run:
            return {**preview, "dry_run": True, "changed": True}

        done: List[str] = []
        if p["changed"]:
            _run(lambda: _client().update_domain_dns(domain_id, p["records"]))
            check = mailpool_dns.missing_after(
                p["records"], _run(lambda: _client().get_domain_dns(domain_id)))
            if check["missing"]:
                _run(lambda: _client().update_domain_dns(domain_id, current))
                raise _bad(f"Mailpool did not keep the full record set (missing: "
                           f"{check['missing']}); the previous records were restored.")
            done.append("dns")
            if p["publish_dmarc_email"]:
                _run(lambda: _client().set_dmarc_email(domain_id, p["publish_dmarc_email"]))
                done.append("dmarc_published")
        if "dmarc_policy" in settings:
            _run(lambda: _client().set_dmarc_policy(domain_id, dmarc_policy))
            done.append("dmarc_policy")
        if "redirect_url" in settings:
            _run(lambda: _client().set_redirect_url(domain_id, redirect_url))
            done.append("redirect_url")
        return {**preview, "dry_run": False, "changed": True, "applied": done,
                "message": "Applied. Public DNS follows within ~5 minutes."}

    # --- mailboxes -------------------------------------------------------------

    @mcp.tool()
    def mailpool_mailboxes(
        op: Literal["list", "get"] = "list",
        mailbox_id: Optional[int] = None,
        domain_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Any:
        """Mailpool mailboxes: address, name, type, status, domain, IMAP/SMTP hosts.

        Credentials (mailbox, IMAP/SMTP and admin passwords) are never returned;
        they stay in the Mailpool dashboard.

        Ops:
        - `list` — the workspace's mailboxes, optionally of one `domain_id`.
          `limit` (1-100) / `offset` paginate.
        - `get` — one mailbox (`mailbox_id`).

        Args:
            op: list or get.
            mailbox_id: Mailpool mailbox id (op="get").
            domain_id: only the mailboxes of this domain (op="list").
            limit: page size, 1-100.
            offset: items to skip.
        """
        if op == "get":
            _refuse_ignored(op, domain_id=domain_id)
            return _slim_mailbox(_run(lambda: _client().get_mailbox(
                _need(mailbox_id, "mailbox_id", op))))
        _refuse_ignored(op, mailbox_id=mailbox_id)
        return _page(_run(lambda: _client().list_mailboxes(
            limit=limit, offset=offset, domain_id=domain_id)), _slim_mailbox)

    # --- spam checks -----------------------------------------------------------

    @mcp.tool()
    def mailpool_spam_checks(
        op: Literal["run", "get", "list", "delete"] = "list",
        mailbox_id: Optional[int] = None,
        spam_check_id: Optional[int] = None,
        wait: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> Any:
        """Mailpool spam checks: send a test email from a mailbox and score it.

        Ops:
        - `run` — check `mailbox_id`. Free. With `wait=True` (default) waits up
          to 20 s for the result (usually a few seconds); otherwise returns the
          pending check to read later with `op="get"`. Some mailbox types are
          refused by Mailpool (404).
        - `get` — one check (`spam_check_id`) with its detailed results.
        - `list` — past checks with score and per-check status.
        - `delete` — remove one check (`spam_check_id`) from the history.

        Results: `score` (0-100) and `checks` — SPF, DKIM, DMARK (DMARC),
        domainAge, reverseDNS, ipBlackList, domainBlackList, spamAssassin,
        shortUrls, brokenLinks — each passed / warning / error.

        Args:
            op: run, get, list or delete.
            mailbox_id: mailbox to check (op="run").
            spam_check_id: check id (op="get"/"delete").
            wait: op="run" — wait for the result.
            limit: page size, 1-100 (op="list").
            offset: items to skip (op="list").
        """
        if op == "list":
            _refuse_ignored(op, mailbox_id=mailbox_id, spam_check_id=spam_check_id)
            return _page(_run(lambda: _client().list_spam_checks(limit=limit, offset=offset)),
                         lambda x: _slim_spam_check(x, full=False))
        if op == "run":
            _refuse_ignored(op, spam_check_id=spam_check_id)
            check = _run(lambda: _client().create_spam_check(_need(mailbox_id, "mailbox_id", op)))
            deadline = time.monotonic() + SPAM_CHECK_WAIT_S
            while wait and check.get("state") == "pending" and time.monotonic() < deadline:
                time.sleep(SPAM_CHECK_POLL_S)
                check = _run(lambda: _client().get_spam_check(check["id"]))
            return _slim_spam_check(check, full=True)
        _refuse_ignored(op, mailbox_id=mailbox_id)
        sid = _need(spam_check_id, "spam_check_id", op)
        if op == "get":
            return _slim_spam_check(_run(lambda: _client().get_spam_check(sid)), full=True)
        _run(lambda: _client().delete_spam_check(sid))
        return {"deleted": sid}

    # --- account ---------------------------------------------------------------

    @mcp.tool()
    def mailpool_account(
        op: Literal["slots", "warmup", "warmup_inbox", "warmup_available"] = "slots",
        warmup_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Any:
        """Mailpool workspace: subscription slots and warmup.

        Ops:
        - `slots` — mailboxes used vs paid, per type, and warmup slots. A new
          mailbox needs a free slot (buying slots is not served here).
        - `warmup` — inboxes enrolled in Mailpool warmup, with status.
        - `warmup_inbox` — one enrolled inbox (`warmup_id`): settings, counters.
        - `warmup_available` — active mailboxes not enrolled yet.

        Args:
            op: what to read.
            warmup_id: warmup inbox id (op="warmup_inbox").
            limit: page size, 1-100.
            offset: items to skip.
        """
        if op == "warmup_inbox":
            return _run(lambda: _client().get_warmup_inbox(_need(warmup_id, "warmup_id", op)))
        _refuse_ignored(op, warmup_id=warmup_id)
        if op == "warmup":
            return _page(_run(lambda: _client().list_warmup_inboxes(limit=limit, offset=offset)),
                         lambda w: _slim_warmup(w))
        if op == "warmup_available":
            return _page(_run(lambda: _client().list_available_warmup_inboxes(
                limit=limit, offset=offset)), lambda w: _slim_warmup(w))
        return _run(lambda: _client().get_subscription_slots())
