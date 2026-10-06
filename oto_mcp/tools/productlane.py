"""Productlane tools — customer feedback, roadmap, changelogs, help center.

Wraps `oto.tools.productlane.client.ProductlaneClient` (API **v2**, Bearer).
Eight tools, one per upstream API family.

Three things to know before reading a result:

- ⚠️ **The roadmap is backed by Linear.** Projects and issues are born in Linear
  then mirrored here; `team_id`, `state_id`, `assignee_id` and
  `linear_status_id` are LINEAR identifiers. Above all: a write can
  succeed locally while the Linear sync fails — the vendor logs it on its
  side and does not surface it. A success therefore does not prove that
  Linear followed.

- ⚠️ **Only one gesture writes to third parties**: `productlane_changelogs
  op='broadcast'`, which sends an email to subscribed contacts and posts to
  Slack. It is **dry-run by default**, like `lightfield`'s email send
  and `origami`'s campaign launch — the only three calls in the repo that
  leave the organization.

- **Cursor pagination**, never by page number: each list returns
  `{data, page:{cursor, has_more}}`, and `has_more` says whether anything
  remains — not the size of `data`, which a last page can return empty.

Client calls are written out in plain form (`_client().list_threads(…)`): that is
what makes them verifiable by the version-skew probe
(`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    """Translate a Productlane refusal into an actionable message.

    The v2 error envelope carries `{error: {code, message, request_id}}`: the
    `code` and the `request_id` are what lets the customer find the call in
    their logs, so they are passed through as-is.
    """
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = err.get("code") or body.get("code")
    rid = err.get("request_id") or body.get("request_id")
    suffixe = f" [request_id {rid}]" if rid else ""
    if status == 401:
        return ("Productlane rejected the key (401) — check the key configured "
                "on this connector. ⚠️ A **v1** key does not work here: the v2 API "
                "is separate, and its key is created separately." + suffixe)
    if status == 403:
        return (f"Productlane denied access (403{f', {code}' if code else ''}) "
                "— the key exists but lacks the scope for this operation, "
                "or the workspace plan does not cover it (snippets and some "
                "tags require Pro or Scale)."
                + suffixe)
    if status == 404:
        return f"Productlane: resource not found (404) — check the identifier.{suffixe}"
    if status in (400, 422):
        if code == "validation_failed":
            return ("Productlane rejected the request (validation_failed) — when "
                    "sending a message, this often means that the integration "
                    "for the inferred channel (email, Slack, Teams) is not "
                    "configured for this workspace, not that the "
                    f"content is bad.{suffixe}")
        return (f"Productlane rejected the request (HTTP {status}"
                f"{f', {code}' if code else ''}): {e.body}{suffixe}")
    if status == 429:
        return ("Productlane: too many requests (429) — 1000 reads/minute and "
                "60 writes/minute per key. Try again in a moment." + suffixe)
    if status in (500, 502, 503, 504):
        return (f"Productlane is temporarily unavailable (HTTP {status}) — "
                f"try again later.{suffixe}")
    return f"Productlane rejected the request (HTTP {status}): {e.body}{suffixe}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: `GET /me`.

    Callable by ANY authenticated key, whatever its scopes — that is what
    makes it the right probe: it separates "invalid key" (401) from "valid
    key but without the requested right" (403 elsewhere). Probing a resource
    would conflate the two and show red on a healthy but deliberately
    restricted key.

    It also returns the granted scopes, so there is enough to explain a refusal
    BEFORE provoking it.
    """
    from oto.tools.productlane.client import ProductlaneClient
    ProductlaneClient(api_key=fields["key"]).me()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.productlane.client import ProductlaneClient

    connector_verify.register("productlane", _verify)

    def _client() -> ProductlaneClient:
        key, _ = access.resolve_api_key("productlane")
        return ProductlaneClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _need(value, nom: str, op: str):
        if not value:
            raise _bad(f"op='{op}': `{nom}` is required.")
        return value

    def _bad_op(op: str, attendus: str):
        return _bad(f"`op` invalid: {op!r} (expected: {attendus}).")

    # --- threads -------------------------------------------------------------

    @mcp.tool()
    def productlane_threads(
        op: Literal["search", "get", "create", "update", "delete",
                    "messages", "send", "comments", "comment",
                    "update_comment", "delete_comment", "link"] = "search",
        thread_id: Optional[str] = None,
        comment_id: Optional[str] = None,
        status: Optional[str] = None,
        tab: Optional[str] = None,
        pain_level: Optional[str] = None,
        origin: Optional[str] = None,
        contact_id: Optional[str] = None,
        company_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        tag_id: Optional[str] = None,
        issue_ids: Optional[list[str]] = None,
        project_ids: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        content: Optional[str] = None,
        fields: Optional[dict] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — customer feedback threads: what customers have said.

        This is the product's inbox. A thread carries a conversation
        (messages, all channels merged), internal comments, a pain level
        (`pain_level`) and links to the roadmap.

        ⚠️ **Two planes that must not be confused**: `send` sends a message
        TO THE CUSTOMER, through the channel the thread came from (email, Slack, live chat,
        Teams) — it is a real outbound communication. `comment` writes a
        note visible to the team only. Nothing in the shape of the two calls
        tells you so: it is the name of the `op` that does.

        `op`:
        - `search` — list threads (filters: status, tab, pain, origin,
          contact, company, assignee, tag, date window).
        - `get` — one thread; `expand=['messages','comments']` inlines the conversation.
        - `create` — open a thread and **upsert its contact by email**
          (`fields`: text, pain_level, contact_email required).
        - `update` — update (`fields`). ⚠️ `tag_ids` REPLACES the tags.
        - `delete` — soft-delete.
        - `messages` — the thread's conversation, oldest to newest.
        - `send` — ⚠️ **sends a message to the customer** (`content`).
        - `comments` / `comment` / `update_comment` / `delete_comment` —
          the thread's INTERNAL notes.
        - `link` — link the thread to Linear issues and/or projects
          (`issue_ids`/`project_ids`): this is the gesture that turns feedback
          into a tracked request on the roadmap, and that raises a project's score.

        Args:
            op: the operation, see above.
            thread_id: the target thread (all ops except search and create).
            comment_id: the target comment (update_comment, delete_comment).
            status: op='search'/'create'/'update' — open | snoozed | done.
            tab: op='search' — open | new | needs-response | my | snoozed | done.
            pain_level: UNKNOWN | LOW | MEDIUM | HIGH.
            origin: op='search'/'create' — origin channel (email, slack, portal…).
            contact_id: op='search' — filter by contact.
            company_id: op='search' — filter by company.
            assignee_id: op='search' — filter by assignee.
            tag_id: op='search' — filter by tag.
            issue_ids: op='link' — Linear issues to link.
            project_ids: op='link' — Linear projects to link.
            expand: op='get' — messages and/or comments to inline.
            content: op='send'/'comment'/'update_comment' — the text.
            fields: op='create'/'update' — the thread body.
            created_after: op='search' — lower bound (ISO 8601).
            created_before: op='search' — upper bound (ISO 8601).
            cursor: next page (returned as `page.cursor`).
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_threads(
                limit=limit, cursor=cursor, status=status, tab=tab,
                pain_level=pain_level, origin=origin, contact_id=contact_id,
                company_id=company_id, assignee_id=assignee_id, tag_id=tag_id,
                created_after=created_after, created_before=created_before))
        if op == "get":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.get_thread(thread_id, expand=expand))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_thread(fields))
        if op == "update":
            _need(thread_id, "thread_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_thread(thread_id, fields))
        if op == "delete":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.delete_thread(thread_id))
        if op == "messages":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.list_messages(thread_id, limit=limit,
                                                cursor=cursor))
        if op == "send":
            _need(thread_id, "thread_id", op)
            _need(content, "content", op)
            return _run(lambda: c.send_message(thread_id,
                                               dict(fields or {}, content=content)))
        if op == "comments":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.list_comments(thread_id, limit=limit,
                                                cursor=cursor))
        if op == "comment":
            _need(thread_id, "thread_id", op)
            _need(content, "content", op)
            return _run(lambda: c.post_comment(thread_id, content))
        if op == "update_comment":
            _need(thread_id, "thread_id", op)
            _need(comment_id, "comment_id", op)
            _need(content, "content", op)
            return _run(lambda: c.update_comment(thread_id, comment_id,
                                                 {"content": content}))
        if op == "delete_comment":
            _need(thread_id, "thread_id", op)
            _need(comment_id, "comment_id", op)
            return _run(lambda: c.delete_comment(thread_id, comment_id))
        if op == "link":
            _need(thread_id, "thread_id", op)
            return _run(lambda: c.link_thread(thread_id, issue_ids=issue_ids,
                                              project_ids=project_ids))
        raise _bad_op(op, "search | get | create | update | delete | messages | "
                          "send | comments | comment | update_comment | "
                          "delete_comment | link")

    # --- contacts -------------------------------------------------------------

    @mcp.tool()
    def productlane_contacts(
        op: Literal["search", "get", "create", "update", "delete",
                    "companies", "add_company", "remove_company",
                    "issues", "projects",
                    "blocked", "block", "unblock"] = "search",
        contact_id: Optional[str] = None,
        company_id: Optional[str] = None,
        blocked_id: Optional[str] = None,
        email: Optional[str] = None,
        name_contains: Optional[str] = None,
        external_id: Optional[str] = None,
        block_type: Optional[str] = None,
        value: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — the people who wrote in, and their companies.

        A contact links a conversation to a customer organization: that is what
        lets you say "this feedback comes from a customer at such a tier".

        ⚠️ **Blocking cuts communication without telling the person concerned.** A blocked
        sender can no longer open a thread or write on an existing thread,
        and is not told. `block_type='DOMAIN'` cuts off **an entire
        organization** in a single call — not to be confused with `'EMAIL'`, which
        targets only one address.

        `op`:
        - `search` / `get` / `create` / `update` / `delete` — the contact.
          ⚠️ `update` with `is_subscribed: false` **unsubscribes** from changelog
          broadcasts: it is a communication preference, not a neutral field.
        - `companies` / `add_company` / `remove_company` — its memberships.
          Adding is idempotent and becomes the primary one if it had none.
        - `issues` / `projects` — what its threads are linked to on the roadmap.
        - `blocked` / `block` / `unblock` — blocked senders.

        Args:
            op: the operation, see above.
            contact_id: the target contact.
            company_id: the company (add_company, remove_company).
            blocked_id: the blocked entry to remove (unblock).
            email: op='search' — filter by exact email.
            name_contains: op='search' — filter by partial name.
            external_id: op='search' — filter by external identifier.
            block_type: op='block'/'blocked' — EMAIL (one address) or DOMAIN (a whole domain).
            value: op='block' — the address or domain to block.
            fields: op='create'/'update' — the contact body (email required on creation).
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_contacts(
                limit=limit, cursor=cursor, email=email,
                name_contains=name_contains, company_id=company_id,
                external_id=external_id))
        if op == "get":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.get_contact(contact_id))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_contact(fields))
        if op == "update":
            _need(contact_id, "contact_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_contact(contact_id, fields))
        if op == "delete":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.delete_contact(contact_id))
        if op == "companies":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.list_contact_companies(contact_id))
        if op == "add_company":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.add_contact_to_company(
                contact_id, company_id=company_id,
                company_name=(fields or {}).get("company_name"),
                company_external_id=(fields or {}).get("company_external_id")))
        if op == "remove_company":
            _need(contact_id, "contact_id", op)
            _need(company_id, "company_id", op)
            return _run(lambda: c.remove_contact_from_company(contact_id,
                                                              company_id))
        if op == "issues":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.list_contact_issues(contact_id, limit=limit,
                                                      cursor=cursor))
        if op == "projects":
            _need(contact_id, "contact_id", op)
            return _run(lambda: c.list_contact_projects(contact_id, limit=limit,
                                                        cursor=cursor))
        if op == "blocked":
            return _run(lambda: c.list_blocked_senders(limit=limit,
                                                       cursor=cursor,
                                                       type=block_type))
        if op == "block":
            _need(block_type, "block_type", op)
            _need(value, "value", op)
            return _run(lambda: c.block_sender(block_type, value))
        if op == "unblock":
            _need(blocked_id, "blocked_id", op)
            return _run(lambda: c.unblock_sender(blocked_id))
        raise _bad_op(op, "search | get | create | update | delete | companies | "
                          "add_company | remove_company | issues | projects | "
                          "blocked | block | unblock")

    # --- entreprises ----------------------------------------------------------

    @mcp.tool()
    def productlane_companies(
        op: Literal["search", "get", "create", "update", "delete",
                    "merge", "linear_options"] = "search",
        company_id: Optional[str] = None,
        source_id: Optional[str] = None,
        domain: Optional[str] = None,
        name_contains: Optional[str] = None,
        external_id: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — customer companies, paired with Linear customers.

        ⚠️ **The Linear mirror is asynchronous**: the customer is provisioned once
        a domain is set, identity changes propagate there later,
        and a deletion takes effect there afterwards. Seeing nothing on the Linear side right
        after a call is a delay, not an outage.

        ⚠️ `merge` is **irreversible, and direction matters**: the company
        `company_id` SURVIVES, the one in `source_id` is deleted. Its threads,
        contacts and votes move to the survivor, of which only EMPTY properties
        are filled in.

        `op`: `search` | `get` | `create` | `update` | `delete` (soft) |
        `merge` | `linear_options` (available Linear statuses and tiers —
        returns `null` if Linear is not connected, which is an answer and not
        an error).

        Args:
            op: the operation, see above.
            company_id: the target company — on merge, the one that SURVIVES.
            source_id: op='merge' — the ABSORBED company (deleted).
            domain: op='search' — filter by domain.
            name_contains: op='search' — filter by partial name.
            external_id: op='search' — filter by external identifier.
            fields: op='create'/'update' — the body (name required on creation).
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_companies(
                limit=limit, cursor=cursor, domain=domain,
                name_contains=name_contains, external_id=external_id))
        if op == "get":
            _need(company_id, "company_id", op)
            return _run(lambda: c.get_company(company_id))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_company(fields))
        if op == "update":
            _need(company_id, "company_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_company(company_id, fields))
        if op == "delete":
            _need(company_id, "company_id", op)
            return _run(lambda: c.delete_company(company_id))
        if op == "merge":
            _need(company_id, "company_id", op)
            _need(source_id, "source_id", op)
            return _run(lambda: c.merge_company(company_id, source_id))
        if op == "linear_options":
            return _run(lambda: c.linear_customer_options())
        raise _bad_op(op, "search | get | create | update | delete | merge | "
                          "linear_options")

    # --- roadmap --------------------------------------------------------------

    @mcp.tool()
    def productlane_roadmap(
        op: Literal["projects", "project", "create_project", "update_project",
                    "delete_project", "statuses",
                    "issues", "issue", "create_issue", "update_issue",
                    "delete_issue", "workflows"] = "projects",
        project_id: Optional[str] = None,
        issue_id: Optional[str] = None,
        team_id: Optional[str] = None,
        state: Optional[str] = None,
        status: Optional[str] = None,
        name_contains: Optional[str] = None,
        sort: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — the roadmap: projects and issues, **backed by Linear**.

        ⚠️ **Linear must be connected**, and three asymmetries follow:
        CREATION starts from Linear (without it, it fails); update and
        deletion succeed locally **even if the Linear sync fails**
        (the vendor logs it and does not surface it); and `team_id`,
        `state_id`, `assignee_id`, `linear_status_id` are LINEAR
        identifiers, to be read via `op='workflows'` / `op='statuses'` or via the
        Linear connector.

        ⚠️ `sort='total_score'` ranks by the weight of attached customer feedback —
        it answers the real question "what do our customers ask for the most?",
        which ordering by date does not.

        ⚠️ On an issue, `status` is NOT a fixed enum: these are the
        workflow states of the Linear team, specific to each workspace.
        Read them via `op='workflows'` rather than hardcoding one.

        ⚠️ `priority` follows Linear numbering: `0` = none, `1` = urgent,
        then 2, 3, 4 in decreasing urgency. It is not an ascending scale.

        Args:
            op: the operation, see above.
            project_id: the target project.
            issue_id: the target issue.
            team_id: op='workflows' (required) and creation — the LINEAR team.
            state: op='projects'/'create_project' — backlog | planned | started |
                completed | canceled.
            status: op='issues' — Linear workflow state (see op='workflows').
            name_contains: filter by partial name.
            sort: created_at | total_score.
            fields: creation or update body.
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        if op == "projects":
            return _run(lambda: c.list_projects(
                limit=limit, cursor=cursor, state=state,
                name_contains=name_contains, sort=sort))
        if op == "project":
            _need(project_id, "project_id", op)
            return _run(lambda: c.get_project(project_id))
        if op == "create_project":
            _need(fields, "fields", op)
            return _run(lambda: c.create_project(fields))
        if op == "update_project":
            _need(project_id, "project_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_project(project_id, fields))
        if op == "delete_project":
            _need(project_id, "project_id", op)
            return _run(lambda: c.delete_project(project_id))
        if op == "statuses":
            return _run(lambda: c.list_project_statuses())
        if op == "issues":
            return _run(lambda: c.list_issues(
                limit=limit, cursor=cursor, project_id=project_id,
                status=status, name_contains=name_contains, sort=sort))
        if op == "issue":
            _need(issue_id, "issue_id", op)
            return _run(lambda: c.get_issue(issue_id))
        if op == "create_issue":
            _need(fields, "fields", op)
            return _run(lambda: c.create_issue(fields))
        if op == "update_issue":
            _need(issue_id, "issue_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_issue(issue_id, fields))
        if op == "delete_issue":
            _need(issue_id, "issue_id", op)
            return _run(lambda: c.delete_issue(issue_id))
        if op == "workflows":
            _need(team_id, "team_id", op)
            return _run(lambda: c.list_workflow_states(team_id))
        raise _bad_op(op, "projects | project | create_project | update_project | "
                          "delete_project | statuses | issues | issue | "
                          "create_issue | update_issue | delete_issue | workflows")

    # --- changelogs -----------------------------------------------------------

    @mcp.tool()
    def productlane_changelogs(
        op: Literal["search", "get", "create", "update", "delete", "broadcast",
                    "tags", "create_tag", "update_tag",
                    "delete_tag"] = "search",
        changelog_id: Optional[str] = None,
        tag_id: Optional[str] = None,
        published: Optional[bool] = None,
        language: Optional[str] = None,
        title_contains: Optional[str] = None,
        fields: Optional[dict] = None,
        email: bool = False,
        slack: bool = False,
        subject: Optional[str] = None,
        message: Optional[str] = None,
        sender_name: Optional[str] = None,
        from_email: Optional[str] = None,
        dry_run: bool = True,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — release notes, and their broadcast to subscribers.

        ⚠️ **`op='broadcast'` is the only call in this connector that writes to
        third parties**: email to subscribed contacts and/or a post to the configured
        Slack channels. It cannot be cancelled or recalled. It is therefore
        **dry-run by default** — `dry_run=false` to really send.

        ⚠️ **Publishing and broadcasting are two distinct gestures**, and the vendor is
        explicit: broadcasting NEVER touches `published`. An unpublished
        changelog can therefore be broadcast, and recipients would receive a link to
        an invisible page. Publish = `op='update'` with `{"published": true}`.

        `op`: `search` | `get` | `create` | `update` | `delete` (soft) |
        `broadcast` | `tags` | `create_tag` | `update_tag` | `delete_tag`.

        ⚠️ Changelog tags are NOT thread tags
        (`productlane_tags`): two distinct families upstream, and one
        requires the Scale plan. `delete_tag` is a HARD delete, which also detaches
        the tag from all changelogs.

        Args:
            op: the operation, see above.
            changelog_id: the target changelog.
            tag_id: the target changelog tag.
            published: op='search' — filter published / unpublished.
            language: op='search'/'get' — serves the translation row.
            title_contains: op='search' — filter by partial title.
            fields: op='create'/'update'/'update_tag' — the body.
            email: op='broadcast' — write to subscribed contacts.
            slack: op='broadcast' — post to the Slack channels.
            subject: op='broadcast' — email subject.
            message: op='broadcast' — accompanying text.
            sender_name: op='broadcast' — displayed sender name.
            from_email: op='broadcast' — sending address.
            dry_run: op='broadcast' — True (default) describes the send without doing it.
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        if op == "search":
            return _run(lambda: c.list_changelogs(
                limit=limit, cursor=cursor, published=published,
                language=language, title_contains=title_contains))
        if op == "get":
            _need(changelog_id, "changelog_id", op)
            return _run(lambda: c.get_changelog(changelog_id, language=language))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_changelog(fields))
        if op == "update":
            _need(changelog_id, "changelog_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_changelog(changelog_id, fields))
        if op == "delete":
            _need(changelog_id, "changelog_id", op)
            return _run(lambda: c.delete_changelog(changelog_id))
        if op == "broadcast":
            _need(changelog_id, "changelog_id", op)
            if not email and not slack:
                raise _bad("op='broadcast': choose at least one channel — "
                           "`email=true` (subscribed contacts) and/or `slack=true`.")
            if dry_run:
                return {
                    "dry_run": True,
                    "would": "broadcast this changelog to third parties",
                    "changelog_id": changelog_id,
                    "canaux": [n for n, v in (("email", email),
                                              ("slack", slack)) if v],
                    "subject": subject, "sender_name": sender_name,
                    "from_email": from_email, "message": message,
                    "avertissement": ("sending is irreversible and does NOT modify "
                                      "`published` — an unpublished changelog "
                                      "would be broadcast to an invisible page"),
                    "pour_envoyer": "call again with dry_run=false",
                }
            return _run(lambda: c.broadcast_changelog(
                changelog_id, email=email or None, slack=slack or None,
                message=message, subject=subject, sender_name=sender_name,
                from_email=from_email))
        if op == "tags":
            return _run(lambda: c.list_changelog_tags())
        if op == "create_tag":
            _need(fields, "fields", op)
            return _run(lambda: c.create_changelog_tag(
                fields.get("name"), color=fields.get("color"),
                icon=fields.get("icon")))
        if op == "update_tag":
            _need(tag_id, "tag_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_changelog_tag(tag_id, fields))
        if op == "delete_tag":
            _need(tag_id, "tag_id", op)
            return _run(lambda: c.delete_changelog_tag(tag_id))
        raise _bad_op(op, "search | get | create | update | delete | broadcast | "
                          "tags | create_tag | update_tag | delete_tag")

    # --- centre d'aide --------------------------------------------------------

    @mcp.tool()
    def productlane_docs(
        op: Literal["articles", "article", "create", "update", "delete", "move",
                    "groups", "create_group", "update_group", "delete_group",
                    "drafts", "draft", "create_draft", "accept",
                    "decline"] = "articles",
        article_id: Optional[str] = None,
        group_id: Optional[str] = None,
        draft_id: Optional[str] = None,
        article_ids: Optional[list[str]] = None,
        visibility: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        published: Optional[bool] = None,
        title_contains: Optional[str] = None,
        language: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — the help center: articles, groups, and review.

        Two write paths, which do not serve the same purpose: DIRECT writes
        (`create` / `update` / `delete`) apply immediately; the
        DRAFT (`create_draft` then `accept` or `decline`) proposes a
        change for review.

        ⚠️ **`accept` can answer `superseded` instead of `accepted`**: the
        draft no longer applies cleanly because the article moved
        underneath it. It is an HTTP success that **applied nothing** — read the returned
        status, not just the absence of an error.

        ⚠️ Visibility is not binary: `public`, `agent` (visible to
        AI agents), `internal`, `unlisted`. `all` only exists as a list filter
        — an article cannot "be" of visibility `all`.

        ⚠️ `update` with `allow_image_removal` allows the rewrite to
        DELETE images absent from the new content; without it, they are
        kept. It is a vendor safeguard against loss through partial
        re-copying — turning it off is a decision.

        Args:
            op: the operation, see above.
            article_id: the target article.
            group_id: the target group (or the target of a move; null ungroups).
            draft_id: the target draft (draft, accept, decline).
            article_ids: op='move' — the articles to move.
            visibility: public | agent | internal | unlisted (+ all as a filter).
            kind: op='articles' — doc | link | all. op='create_draft' — edit | create | delete.
            status: op='drafts' — draft | open | accepted | rejected | superseded.
            published: op='articles' — filter published / unpublished.
            title_contains: op='articles' — filter by partial title.
            language: serves or writes a translation row.
            fields: creation or update body (content in markdown).
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        if op == "articles":
            return _run(lambda: c.list_articles(
                limit=limit, cursor=cursor, group_id=group_id,
                published=published, visibility=visibility, kind=kind,
                title_contains=title_contains, language=language))
        if op == "article":
            _need(article_id, "article_id", op)
            return _run(lambda: c.get_article(article_id, language=language))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_article(fields))
        if op == "update":
            _need(article_id, "article_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_article(article_id, fields))
        if op == "delete":
            _need(article_id, "article_id", op)
            return _run(lambda: c.delete_article(article_id))
        if op == "move":
            _need(article_ids, "article_ids", op)
            return _run(lambda: c.move_articles(article_ids, group_id))
        if op == "groups":
            return _run(lambda: c.list_groups())
        if op == "create_group":
            _need(fields, "fields", op)
            return _run(lambda: c.create_group(
                fields.get("name"),
                portal_instance_id=fields.get("portal_instance_id")))
        if op == "update_group":
            _need(group_id, "group_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_group(group_id, fields))
        if op == "delete_group":
            _need(group_id, "group_id", op)
            return _run(lambda: c.delete_group(group_id))
        if op == "drafts":
            return _run(lambda: c.list_drafts(
                limit=limit, cursor=cursor, kind=kind, status=status,
                article_id=article_id, group_id=group_id))
        if op == "draft":
            _need(draft_id, "draft_id", op)
            return _run(lambda: c.get_draft(draft_id))
        if op == "create_draft":
            _need(fields, "fields", op)
            return _run(lambda: c.create_draft(fields))
        if op == "accept":
            _need(draft_id, "draft_id", op)
            return _run(lambda: c.accept_draft(draft_id))
        if op == "decline":
            _need(draft_id, "draft_id", op)
            return _run(lambda: c.decline_draft(draft_id))
        raise _bad_op(op, "articles | article | create | update | delete | move | "
                          "groups | create_group | update_group | delete_group | "
                          "drafts | draft | create_draft | accept | decline")

    # --- thread tags ----------------------------------------------------------

    @mcp.tool()
    def productlane_tags(
        op: Literal["list", "get", "create", "update", "delete",
                    "groups", "group", "create_group", "update_group",
                    "delete_group"] = "list",
        tag_id: Optional[str] = None,
        group_id: Optional[str] = None,
        name_contains: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — THREAD tags and their groups.

        ⚠️ These are not changelog tags (`productlane_changelogs
        op='tags'`): two distinct families upstream, with different
        rules. These always live in a group — `tag_group_id`
        is mandatory on creation, along with `name`, `color` and `icon`.

        `op`: `list` | `get` | `create` | `update` | `delete` (soft — the tag
        is removed from all threads) | `groups` | `group` | `create_group` |
        `update_group` | `delete_group` (the group must be EMPTY).

        Args:
            op: the operation, see above.
            tag_id: the target tag.
            group_id: the target tag group.
            name_contains: op='list' — filter by partial name.
            fields: the body — tag creation: name, color, icon,
                tag_group_id (all four required); group: name, color.
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        f = fields or {}
        if op == "list":
            return _run(lambda: c.list_tags(limit=limit, cursor=cursor,
                                            name_contains=name_contains,
                                            tag_group_id=group_id))
        if op == "get":
            _need(tag_id, "tag_id", op)
            return _run(lambda: c.get_tag(tag_id))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_tag(
                f.get("name"), f.get("color"), f.get("icon"),
                f.get("tag_group_id") or group_id))
        if op == "update":
            _need(tag_id, "tag_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_tag(tag_id, fields))
        if op == "delete":
            _need(tag_id, "tag_id", op)
            return _run(lambda: c.delete_tag(tag_id))
        if op == "groups":
            return _run(lambda: c.list_tag_groups(limit=limit, cursor=cursor))
        if op == "group":
            _need(group_id, "group_id", op)
            return _run(lambda: c.get_tag_group(group_id))
        if op == "create_group":
            _need(fields, "fields", op)
            return _run(lambda: c.create_tag_group(f.get("name"), f.get("color")))
        if op == "update_group":
            _need(group_id, "group_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_tag_group(group_id, fields))
        if op == "delete_group":
            _need(group_id, "group_id", op)
            return _run(lambda: c.delete_tag_group(group_id))
        raise _bad_op(op, "list | get | create | update | delete | groups | "
                          "group | create_group | update_group | delete_group")

    # --- espace de travail ----------------------------------------------------

    @mcp.tool()
    def productlane_workspace(
        op: Literal["me", "roadmap", "portal", "instances",
                    "snippets", "snippet", "create_snippet", "update_snippet",
                    "delete_snippet", "folders", "import_file"] = "me",
        snippet_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        contact_email: Optional[str] = None,
        language: Optional[str] = None,
        title_contains: Optional[str] = None,
        url: Optional[str] = None,
        file_name: Optional[str] = None,
        fields: Optional[dict] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Any:
        """Productlane — key identity, the public portal, snippets.

        `op='me'` is the useful starting point: it returns the scopes granted to
        the key and the workspace's Linear team selection. That is enough to
        explain a refusal BEFORE provoking it, and it requires no
        scope.

        `op`:
        - `me` — key identity, scopes, Linear teams.
        - `roadmap` — the PUBLIC roadmap as the portal renders it;
          `contact_email` renders it from a contact's point of view.
        - `portal` — what a contact sees in their support portal
          (`contact_email` required, Scale plan).
        - `instances` — portal instances. ⚠️ The **Main (Root) portal is
          implicit**: it is not listed, and is designated elsewhere by a null
          `portal_instance_id`. An empty list does not mean "no portal".
        - `snippets` / `snippet` / `create_snippet` / `update_snippet` /
          `delete_snippet` / `folders` — reply templates (Pro plan).
          ⚠️ Their body is **HTML**, not markdown.
        - `import_file` — store a file from a public URL and return a
          CDN URL reusable in a changelog or an article.

        Args:
            op: the operation, see above.
            snippet_id: the target snippet.
            folder_id: op='snippets' — filter by folder; creation — the target folder.
            contact_email: op='roadmap'/'portal' — a contact's point of view.
            language: op='roadmap' — the language served.
            title_contains: op='snippets' — filter by partial title.
            url: op='import_file' — the public URL of the file to store.
            file_name: op='import_file' — name given to the stored file.
            fields: op='create_snippet'/'update_snippet' — title and html.
            cursor: next page.
            limit: rows per page (1-200, default 50).
        """
        c = _client()
        f = fields or {}
        if op == "me":
            return _run(lambda: c.me())
        if op == "roadmap":
            return _run(lambda: c.get_roadmap(contact_email=contact_email,
                                              language=language))
        if op == "portal":
            _need(contact_email, "contact_email", op)
            return _run(lambda: c.get_customer_portal(contact_email))
        if op == "instances":
            return _run(lambda: c.list_portal_instances())
        if op == "snippets":
            return _run(lambda: c.list_snippets(limit=limit, cursor=cursor,
                                                title_contains=title_contains,
                                                folder_id=folder_id))
        if op == "snippet":
            _need(snippet_id, "snippet_id", op)
            return _run(lambda: c.get_snippet(snippet_id))
        if op == "create_snippet":
            _need(fields, "fields", op)
            return _run(lambda: c.create_snippet(f.get("title"), f.get("html"),
                                                 folder_id=folder_id))
        if op == "update_snippet":
            _need(snippet_id, "snippet_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_snippet(snippet_id, fields))
        if op == "delete_snippet":
            _need(snippet_id, "snippet_id", op)
            return _run(lambda: c.delete_snippet(snippet_id))
        if op == "folders":
            return _run(lambda: c.list_snippet_folders(limit=limit, cursor=cursor))
        if op == "import_file":
            _need(url, "url", op)
            return _run(lambda: c.import_file(url=url, file_name=file_name))
        raise _bad_op(op, "me | roadmap | portal | instances | snippets | "
                          "snippet | create_snippet | update_snippet | "
                          "delete_snippet | folders | import_file")
