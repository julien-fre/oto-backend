"""Brevo — emailing & CRM via the PUBLIC v3 API (`api-key` key).

Wraps `oto.tools.brevo.BrevoClient`. Key resolved per call via
`access.resolve_api_key("brevo")` — byo (the member's key or the org's shared
credential). No platform key: a Brevo account = its owner's contacts.

⚠️ **Distinct from the `brevoauto` connector** (automations, private API + browser
session). Same vendor, disjoint surfaces: the v3 key does not open automation
authoring, and the browser session does not open these tools.

**Dangerous writes deliberately absent**: sending a campaign
(`sendNow` / `sent` status), deleting a contact / list / campaign / template,
purging hard bounces. We design, we measure, we send ourselves a test — launching
a mass send and deletions stay in the Brevo UI. `brevo_send_email` stays
exposed: it is single transactional sending, explicit recipients.

**Consolidated surface (ADR 0047 §Amendment, applied to the brevo connector)**: one
tool per business OBJECT, the verb in the `op` parameter — `brevo_contact`, `brevo_list`
(lists + folders + segments), `brevo_template`, `brevo_campaign`,
`brevo_transactional`. The default `op` is ALWAYS a read: this connector
sends real emails, a call without `op` must trigger nothing.

Three tools stay STANDALONE, their parameters not overlapping those of their neighbors:
- `brevo_send_email` — 11 drafting parameters (`to`/`cc`/`bcc`/`sender`/
  `html_content`/`scheduled_at`…) of which only one (`template_id`) exists elsewhere;
- `brevo_import_contacts` / `brevo_export_contacts` — **asynchronous** jobs returning
  a `{"processId"}` (not data), on batch parameters (`contacts`,
  `file_url`, `new_list`, `contact_filter`, `export_attributes`) that no other
  op uses. Merging them would only stack disjoint variants.
`brevo_account` stays standalone too: a single boolean, it is the account sheet.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: probe contract, unused here)
    """"Test the connection" probe: does the key really authenticate?

    `GET /account` has no side effect and is refused (401) by an invalid key.
    Raises — the message bubbles up to the UI as is.
    """
    from oto.tools.brevo import BrevoClient
    BrevoClient(api_key=fields["key"]).get_account()


def register(mcp: FastMCP) -> None:
    from oto.tools.brevo import BrevoClient

    connector_verify.register("brevo", _verify)

    def _client() -> BrevoClient:
        key, _ = access.resolve_api_key("brevo")
        return BrevoClient(api_key=key)

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Mandatory argument for THIS op — actionable error, never a fallback."""
        if value is None:
            raise _bad(f"op='{op}' requires {name}")
        return value

    # --- Account --------------------------------------------------------------

    @mcp.tool()
    def brevo_account(senders: bool = True) -> dict:
        """Brevo account: company, plan, remaining email/SMS credits.

        Args:
            senders: include the verified senders — their `email` is required
                to send an email or create a campaign.
        """
        client = _client()
        out: dict[str, Any] = {"account": client.get_account()}
        if senders:
            out["senders"] = client.list_senders()
        return out

    # --- Contacts -------------------------------------------------------------

    @mcp.tool()
    def brevo_contact(
        op: Literal["list", "get", "stats", "attributes", "upsert",
                    "update"] = "list",
        identifier: Optional[str] = None,
        identifier_type: Optional[str] = None,
        email: Optional[str] = None,
        attributes: Optional[dict] = None,
        list_ids: Optional[list[int]] = None,
        unlink_list_ids: Optional[list[int]] = None,
        email_blacklisted: Optional[bool] = None,
        update_enabled: bool = True,
        ext_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        segment_id: Optional[int] = None,
        modified_since: Optional[str] = None,
        created_since: Optional[str] = None,
        filter: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> dict:
        """A Brevo contact — list, read, create/update, statistics, schema.

        `op`:
        - **"list"** (default): lists contacts (paginated, **max 1000 per call**).
        - **"get"**: a contact's sheet (attributes, lists, sending statistics).
        - **"stats"**: a contact's campaign statistics (opens, clicks,
          bounces).
        - **"attributes"**: contact attributes declared on the account (name, category,
          type). **Read before writing `attributes`**: an unknown attribute is
          rejected. No parameters.
        - **"upsert"**: creates a contact, or updates it if it exists
          (`update_enabled`). Returns `{"id": …}` on creation, an empty object on
          an update.
        - **"update"**: updates an **existing** contact. Returns an empty object on
          success. The way to **unsubscribe from a list** (`unlink_list_ids`) or
          **blacklist** (`email_blacklisted=True`, the contact will receive nothing anymore).

        Bulk import/export = `brevo_import_contacts` / `brevo_export_contacts`
        (asynchronous jobs). Reading blocked contacts =
        `brevo_transactional(op="blocked")`.

        Args:
            op: list (default) | get | stats | attributes | upsert | update.
            identifier: op="get"/"stats"/"update" — email by default; otherwise id,
                phone or EXT_ID.
            identifier_type: `email_id` | `contact_id` | `phone_id` | `ext_id`.
            email: op="upsert" — the email of the contact to create/update.
            attributes: op="upsert"/"update" — Brevo attributes in UPPERCASE
                (`{"PRENOM": "Alex", "NOM": "Laporte"}`) — they must exist on the
                account (see op="attributes").
            list_ids: op="upsert"/"update" — lists to subscribe the contact to;
                op="list" — restrict to some lists. **Mutually exclusive with `segment_id`.**
            unlink_list_ids: op="update" — lists to unsubscribe it from.
            email_blacklisted: op="update" — `True` = blacklist.
            update_enabled: op="upsert" — update if the contact already exists.
            ext_id: op="upsert" — external identifier.
            limit: op="list" — page size (max 1000).
            offset: op="list" — pagination.
            segment_id: op="list" — restrict to a segment. **Mutually exclusive with
                `list_ids`.**
            modified_since: op="list" — ISO 8601 UTC (`2026-01-31T00:00:00.000Z`).
            created_since: op="list" — ISO 8601 UTC.
            filter: op="list" — filter on attributes, `equals` operator ONLY —
                e.g. `equals(FIRSTNAME,"Alex")`. No `contains` or `>`.
            sort: op="list" — `asc` | `desc` (default `desc`, by creation date).
        """
        client = _client()

        if op == "list":
            return client.list_contacts(
                limit=limit, offset=offset, list_ids=list_ids, segment_id=segment_id,
                modified_since=modified_since, created_since=created_since,
                filter=filter, sort=sort)
        if op == "get":
            return client.get_contact(_need(identifier, "identifier", op),
                                      identifier_type=identifier_type)
        if op == "stats":
            return client.contact_campaign_stats(_need(identifier, "identifier", op))
        if op == "attributes":
            return client.list_attributes()
        if op == "upsert":
            return client.upsert_contact(
                email=_need(email, "email", op), attributes=attributes,
                list_ids=list_ids, update_enabled=update_enabled, ext_id=ext_id)
        if op == "update":
            return client.update_contact(
                _need(identifier, "identifier", op), attributes=attributes,
                list_ids=list_ids, unlink_list_ids=unlink_list_ids,
                identifier_type=identifier_type, email_blacklisted=email_blacklisted)
        raise _bad("op must be 'list', 'get', 'stats', 'attributes', 'upsert' "
                   "or 'update'")

    @mcp.tool()
    def brevo_import_contacts(
        contacts: Optional[list[dict]] = None,
        list_ids: Optional[list[int]] = None,
        file_url: Optional[str] = None,
        update_existing_contacts: bool = True,
        new_list: Optional[dict] = None,
    ) -> dict:
        """**Asynchronous** bulk import. Returns `{"processId": …}` (not the contacts).

        **The way to go beyond 150 contacts** — `brevo_list(op="add")` caps there.

        Args:
            contacts: `[{"email": …, "attributes": {…}}, …]`.
            file_url: alternative — remote CSV (`;` separator).
            new_list: `{"listName": …, "folderId": …}` to create the list on the fly.
        """
        return _client().import_contacts(
            json_body=contacts, list_ids=list_ids, file_url=file_url,
            update_existing_contacts=update_existing_contacts, new_list=new_list)

    @mcp.tool()
    def brevo_export_contacts(contact_filter: Optional[dict] = None,
                              export_attributes: Optional[list[str]] = None) -> dict:
        """**Asynchronous** contact export. Returns `{"processId": …}`, not the data.

        To read contacts directly, prefer `brevo_contact(op="list")` (paginated).

        Args:
            contact_filter: `{"listIds": [1]}` | `{"segmentId": 2}` |
                `{"emailBlacklisted": true}`. Default = all active contacts.
        """
        return _client().export_contacts(
            contact_filter=contact_filter, export_attributes=export_attributes)

    # --- Lists, folders, segments ---------------------------------------------

    @mcp.tool()
    def brevo_list(
        op: Literal["list", "get", "contacts", "create", "update", "add",
                    "remove", "folders", "segments"] = "list",
        list_id: Optional[int] = None,
        name: Optional[str] = None,
        folder_id: Optional[int] = None,
        emails: Optional[list[str]] = None,
        ids: Optional[list[int]] = None,
        all_contacts: bool = False,
        modified_since: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """A Brevo contact list — and the folders/segments around it.

        `op`:
        - **"list"** (default): the account's contact lists, or those of a folder if
          `folder_id`.
        - **"get"**: a list's details — name, folder, number of contacts and of
          blacklisted ones.
        - **"contacts"**: a list's contacts (paginated, **max 500 per call**).
        - **"create"**: creates a list. `folder_id` is **mandatory**
          (see op="folders").
        - **"update"**: renames a list, or moves it to another folder.
        - **"add"** / **"remove"**: adds or removes **EXISTING** contacts
          to/from a list. ⚠️ **Max 150 contacts per call**, and a SINGLE identifier
          type (`emails` OR `ids`) — beyond that, the API refuses: use
          `brevo_import_contacts`, which also creates missing contacts. Returns
          `{contacts: {success: [...], failure: [...]}}` — **read `failure`**, an
          unknown contact fails without failing the call. `all_contacts=True`
          (op="remove" only) empties the entire list.
        - **"folders"**: list folders. Their `id` is required for op="create".
        - **"segments"**: segments (dynamic lists defined by a filter).
          Read-only, and a segment's `id` is passed to
          `brevo_contact(op="list", segment_id=…)`.

        Args:
            op: list (default) | get | contacts | create | update | add | remove |
                folders | segments.
            list_id: op="get"/"contacts"/"update"/"add"/"remove" — the targeted list.
            name: op="create"/"update" — the list name.
            folder_id: op="create" (mandatory) / "update" (move) /
                "list" (filter on a folder).
            emails: op="add"/"remove" — contacts by email (max 150).
            ids: op="add"/"remove" — contacts by Brevo id (max 150). Mutually exclusive
                with `emails`.
            all_contacts: op="remove" — empties the entire list.
            modified_since: op="contacts" — ISO 8601 UTC.
            limit: page size (list, contacts, folders, segments).
            offset: pagination (list, contacts, folders, segments).
        """
        client = _client()

        if op == "list":
            return client.list_lists(limit=limit, offset=offset, folder_id=folder_id)
        if op == "get":
            return client.get_list(_need(list_id, "list_id", op))
        if op == "contacts":
            return client.list_contacts_of_list(
                _need(list_id, "list_id", op), limit=limit, offset=offset,
                modified_since=modified_since)
        if op == "create":
            return client.create_list(_need(name, "name", op),
                                      _need(folder_id, "folder_id", op))
        if op == "update":
            return client.update_list(_need(list_id, "list_id", op), name=name,
                                      folder_id=folder_id)
        if op == "add":
            return client.add_to_list(_need(list_id, "list_id", op), emails=emails,
                                      ids=ids)
        if op == "remove":
            return client.remove_from_list(_need(list_id, "list_id", op),
                                           emails=emails, ids=ids,
                                           all_contacts=all_contacts)
        if op == "folders":
            return client.list_folders(limit=limit, offset=offset)
        if op == "segments":
            return client.list_segments(limit=limit, offset=offset)
        raise _bad("op must be 'list', 'get', 'contacts', 'create', 'update', "
                   "'add', 'remove', 'folders' or 'segments'")

    # --- Transactional email ---------------------------------------------------

    @mcp.tool()
    def brevo_send_email(
        to: list[dict],
        subject: Optional[str] = None,
        html_content: Optional[str] = None,
        sender: Optional[dict] = None,
        template_id: Optional[int] = None,
        params: Optional[dict] = None,
        cc: Optional[list[dict]] = None,
        bcc: Optional[list[dict]] = None,
        reply_to: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        scheduled_at: Optional[str] = None,
    ) -> dict:
        """**Actually sends** a transactional email. Returns `{"messageId": …}`.

        Two mutually exclusive modes:
        - **template**: `template_id` + `params` (variables `{{params.NOM}}`);
        - **direct**: `subject` + `html_content` + `sender`.

        For a mass send to a list, that is a campaign — not this tool.

        Args:
            to: `[{"email": "a@b.c", "name": "Alex"}]` — max 99 recipients.
            sender: `{"email": …, "name": …}`. Must be a **verified** sender
                of the account (see `brevo_account`), otherwise Brevo refuses the send.
            scheduled_at: ISO 8601 UTC, up to 72 h in the future.
        """
        return _client().send_email(
            to=to, subject=subject, html_content=html_content, sender=sender,
            template_id=template_id, params=params, cc=cc, bcc=bcc,
            reply_to=reply_to, tags=tags, scheduled_at=scheduled_at)

    @mcp.tool()
    def brevo_transactional(
        op: Literal["logs", "content", "events", "report", "blocked",
                    "blocked_domains"] = "logs",
        email: Optional[str] = None,
        template_id: Optional[int] = None,
        message_id: Optional[str] = None,
        uuid: Optional[str] = None,
        event: Optional[str] = None,
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        by_day: bool = False,
        tag: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Sent transactional email and its deliverability (`/smtp/*` routes).

        `op`:
        - **"logs"** (default): sent transactional emails. Dates in `YYYY-MM-DD`
          format. To know what an email BECAME (delivered, opened,
          bounce), use op="events".
        - **"content"**: the HTML content of a specific send (`uuid`, returned by
          op="logs").
        - **"events"**: deliverability events — **the source of truth per
          email**.
        - **"report"**: aggregated transactional counters (sent, delivered,
          opened, clicks…). `by_day=False` (default) = one total over the period;
          `by_day=True` = one row per day.
        - **"blocked"**: blocked contacts (hard bounce, spam complaint,
          unsubscribe). Deliverability diagnostic: **a blocked contact
          receives nothing anymore, silently.**
        - **"blocked_domains"**: the account's blocked domains (simple list,
          no pagination).

        Args:
            op: logs (default) | content | events | report | blocked | blocked_domains.
            email: op="logs"/"events" — filter on a recipient.
            template_id: op="logs"/"events" — filter on a template.
            message_id: op="logs" — filter on a message.
            uuid: op="content" — the uuid of the send whose HTML you want.
            event: op="events" — `delivered` | `opened` | `clicks` | `hardBounces` |
                `softBounces` | `spam` | `blocked` | `unsubscribed` | `invalid` |
                `deferred` | `requests` | `error`. Omitted = all.
            days: op="events"/"report" — sliding window in days (alternative
                to the dates).
            start_date: `YYYY-MM-DD`.
            end_date: `YYYY-MM-DD`.
            by_day: op="report" — one row per day instead of a total.
            tag: op="report" — restrict to a send tag.
            limit: page size (logs, events, blocked).
            offset: pagination (logs, events, blocked).
        """
        client = _client()

        if op == "logs":
            return client.list_transactional_emails(
                email=email, template_id=template_id, message_id=message_id,
                start_date=start_date, end_date=end_date, limit=limit, offset=offset)
        if op == "content":
            return client.get_transactional_email_content(_need(uuid, "uuid", op))
        if op == "events":
            return client.transactional_events(
                event=event, email=email, template_id=template_id, days=days,
                start_date=start_date, end_date=end_date, limit=limit, offset=offset)
        if op == "report":
            return client.transactional_report(
                by_day=by_day, days=days, start_date=start_date, end_date=end_date,
                tag=tag)
        if op == "blocked":
            return client.list_blocked(domains=False, limit=limit, offset=offset)
        if op == "blocked_domains":
            return client.list_blocked(domains=True)
        raise _bad("op must be 'logs', 'content', 'events', 'report', 'blocked' "
                   "or 'blocked_domains'")

    @mcp.tool()
    def brevo_template(
        op: Literal["list", "create", "update"] = "list",
        template_id: Optional[int] = None,
        template_name: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[dict] = None,
        html_content: Optional[str] = None,
        reply_to: Optional[str] = None,
        tag: Optional[str] = None,
        is_active: Optional[bool] = None,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """A Brevo transactional template.

        `op`:
        - **"list"** (default): the account's templates. `template_id` → just one,
          with its HTML.
        - **"create"**: creates a template. Returns `{"id": …}`.
        - **"update"**: updates a template (provided fields only).

        Args:
            op: list (default) | create | update.
            template_id: op="list" — return only one (with its HTML);
                op="update" — the targeted template (mandatory).
            template_name: op="create" (mandatory) / "update" — the name.
            subject: op="create" (mandatory) / "update" — the email subject.
            sender: op="create" (mandatory) / "update" — `{"email": …, "name": …}`,
                **verified** sender of the account (see `brevo_account`).
            html_content: HTML of the body. Variables: `{{params.NOM}}`,
                `{{contact.PRENOM}}`.
            reply_to: op="create" — reply address.
            tag: op="create" — template tag.
            is_active: op="create" (default `True`) / "update" — active or not.
            active_only: op="list" — list only active templates.
            limit: op="list" — page size.
            offset: op="list" — pagination.
        """
        client = _client()

        if op == "list":
            return client.list_templates(
                template_id=template_id, active_only=active_only or None,
                limit=limit, offset=offset)
        if op == "create":
            return client.create_template(
                template_name=_need(template_name, "template_name", op),
                subject=_need(subject, "subject", op),
                sender=_need(sender, "sender", op),
                html_content=html_content, reply_to=reply_to, tag=tag,
                is_active=True if is_active is None else is_active)
        if op == "update":
            return client.update_template(
                _need(template_id, "template_id", op), template_name=template_name,
                subject=subject, sender=sender, html_content=html_content,
                is_active=is_active)
        raise _bad("op must be 'list', 'create' or 'update'")

    # --- Email campaigns ----------------------------------------------------------

    @mcp.tool()
    def brevo_campaign(
        op: Literal["list", "create", "update", "test", "report",
                    "ab_test"] = "list",
        campaign_id: Optional[int] = None,
        status: Optional[str] = None,
        statistics: Optional[str] = None,
        name: Optional[str] = None,
        sender: Optional[dict] = None,
        subject: Optional[str] = None,
        html_content: Optional[str] = None,
        template_id: Optional[int] = None,
        recipients: Optional[dict] = None,
        preview_text: Optional[str] = None,
        reply_to: Optional[str] = None,
        fields: Optional[dict] = None,
        email_to: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """A Brevo email campaign — design, measure, send yourself a test.

        `op`:
        - **"list"** (default): email campaigns. `campaign_id` → a single
          campaign. **The HTML is excluded from responses** (volume); it remains readable
          in the UI.
        - **"create"**: creates a campaign as a **draft**. Returns `{"id": …}`.
          Does not send it: sending (`sendNow`) is deliberately not exposed —
          the send is triggered from the Brevo UI, after review. Use
          op="test" to send it to yourself first.
        - **"update"**: updates a campaign **not yet sent**.
        - **"test"**: ⚠️ **actually sends** a test of the campaign to the given
          addresses (not to the recipients). These addresses must **exist as
          contacts** of the Brevo account, otherwise the API refuses.
        - **"report"**: public share URL of a sent campaign.
        - **"ab_test"**: A/B test result of a campaign.

        Args:
            op: list (default) | create | update | test | report | ab_test.
            campaign_id: op="list" (a single campaign) / "update" / "test" /
                "report" / "ab_test" — the targeted campaign.
            status: op="list" — `draft` | `sent` | `queued` | `suspended` |
                `archive` | `inProcess`.
            statistics: op="list" — `globalStats` | `linksStats` | `statsByDomain` |
                `statsByDevice` | `statsByBrowser` — attaches the stats.
            name: op="create" (mandatory) — the campaign name.
            sender: op="create" (mandatory) — `{"email": …, "name": …}`,
                **verified** sender (see `brevo_account`).
            subject: op="create" — the email subject.
            html_content: op="create" — the HTML of the body.
            template_id: op="create" — start from a template rather than an
                `html_content`.
            recipients: op="create" — `{"listIds": [1,2], "exclusionListIds": [3]}`.
            preview_text: op="create" — the pre-header.
            reply_to: op="create" — reply address.
            fields: op="update" — Brevo camelCase keys: `name`, `subject`,
                `htmlContent`, `sender`, `recipients`, `previewText`.
            email_to: op="test" — the test addresses (existing contacts).
            limit: op="list" — page size.
            offset: op="list" — pagination.
        """
        client = _client()

        if op == "list":
            if campaign_id is not None:
                return client.get_campaign(campaign_id, statistics=statistics)
            return client.list_campaigns(
                status=status, statistics=statistics, limit=limit, offset=offset)
        if op == "create":
            return client.create_campaign(
                name=_need(name, "name", op), sender=_need(sender, "sender", op),
                subject=subject, html_content=html_content, template_id=template_id,
                recipients=recipients, preview_text=preview_text, reply_to=reply_to)
        if op == "update":
            return client.update_campaign(_need(campaign_id, "campaign_id", op),
                                          **_need(fields, "fields", op))
        if op == "test":
            return client.send_campaign_test(_need(campaign_id, "campaign_id", op),
                                             _need(email_to, "email_to", op))
        if op == "report":
            return client.campaign_shared_url(_need(campaign_id, "campaign_id", op))
        if op == "ab_test":
            return client.campaign_ab_test_result(
                _need(campaign_id, "campaign_id", op))
        raise _bad("op must be 'list', 'create', 'update', 'test', 'report' "
                   "or 'ab_test'")
