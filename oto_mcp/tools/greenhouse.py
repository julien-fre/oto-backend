"""Greenhouse Harvest API — ATS (candidates, jobs, applications, notes).

Wraps `oto.tools.greenhouse.GreenhouseClient` (Harvest API key, Basic auth). Key
resolved per call via `access.resolve_api_key("greenhouse")` — byo (user key on
/account or the org's shared credential). No platform key.

⚠️ Greenhouse requires an **`on_behalf_of`** (id of a Greenhouse user) on
writes (candidate creation, note) — get an id via `greenhouse_users`.

**Consolidated surface (ADR 0047 §Amendment, applied to the greenhouse connector)**:
one tool per business OBJECT, the verb as an `op` parameter — `greenhouse_candidate`
(list/get/create/add_note), `greenhouse_job` (list/get), `greenhouse_application`
(list/get). What does NOT merge, and why:

- **`greenhouse_users` stays ALONE**: it is the connector's only "user" object,
  it has a single verb (list) and serves as the DIRECTORY for the other tools'
  writes (`on_behalf_of` / `user_id`). A single-valued `op=` would homogenize
  nothing — same case as `zoho_modules` / `gmail_list_accounts`.
- **job and application do not merge with each other** despite almost identical
  parameters (`per_page`/`page`/`job_id`/`status`): they are two distinct business
  objects (`/jobs` vs `/applications`, and `status` doesn't even have the same
  value domain there — open/closed/draft vs active/rejected/hired). Conflating them
  behind a `kind=` would make the schema less readable, not more.

⚠️ This module WRITES to the ATS: `greenhouse_candidate(op="create")` creates a
candidate record, `op="add_note"` posts a note in its activity feed (read by the
recruiting team). The default of EVERY tool is `op="list"` — a READ: a call
without `op` can neither create nor annotate anything.

⚠️ An id that only `op="get"` consumes (`candidate_id`, `job_id` of
`greenhouse_job`, `application_id`) is REFUSED under `op="list"` rather than ignored:
before the consolidation, `greenhouse_candidate(candidate_id=456)` read ONE record;
silently accepting it under the new default would return the whole list while
making the agent believe its request was honored.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /v1/users` (`list_users`, already in the client), `per_page=1` — the
    smallest format available, since Greenhouse exposes neither `/me` nor a
    balance. Basic auth (key as username, empty password), read with no side
    effect. No mention of any particular cost or rate limit for this call.

    **Authenticated ≠ usable** (oto#69 class): doesn't distinguish scope —
    Greenhouse has no per-key permission beyond the key's own global Harvest
    scope.
    """
    from oto.tools.greenhouse.client import GreenhouseClient

    GreenhouseClient(api_key=fields["key"]).list_users(per_page=1)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Mandatory argument for THIS op — actionable error, never a fallback.

    An EMPTY value counts as absent: `candidate={}` would create an empty record
    in the ATS and `body=""` would post a blank note in a candidate's activity
    feed — two real writes that would pass for a success.
    """
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        raise _bad(f"op='{op}' requires {name}")
    return value


def _not_for(value, name: str, op: str, right_op: str) -> None:
    """An id that ONLY `op=<right_op>` consumes, passed under another op → refused.

    Greenhouse can't filter a list by the object's id: letting it through
    would return the whole page under the pretense of having answered the question.
    """
    if value is not None:
        raise _bad(f"op='{op}' does not filter by {name} — use op='{right_op}' "
                   f"to read an object by its id")


def register(mcp: FastMCP) -> None:
    from oto.tools.greenhouse.client import GreenhouseClient

    connector_verify.register("greenhouse", _verify)

    def _client() -> GreenhouseClient:
        key, _ = access.resolve_api_key("greenhouse")
        return GreenhouseClient(api_key=key)

    @mcp.tool()
    def greenhouse_candidate(
        op: Literal["list", "get", "create", "add_note"] = "list",
        candidate_id: Optional[int] = None,
        per_page: int = 50,
        page: int = 1,
        job_id: Optional[int] = None,
        email: Optional[str] = None,
        created_after: Optional[str] = None,
        updated_after: Optional[str] = None,
        candidate: Optional[dict] = None,
        on_behalf_of: Optional[int] = None,
        body: Optional[str] = None,
        user_id: Optional[int] = None,
        visibility: str = "public",
    ) -> Any:
        """A Greenhouse candidate — list, read, create, annotate.

        `op`:
        - **"list"** (default): list candidates, paginated. Filters: `job_id` (only
          candidates with an application on this job), `email` (exact match),
          `created_after` / `updated_after` (ISO 8601 timestamps).
        - **"get"**: fetch one candidate by id (`candidate_id`), with their
          applications.
        - **"create"**: create a candidate/prospect. **WRITES** to the ATS.
        - **"add_note"**: add a note to a candidate's activity feed. **WRITES** —
          the note is readable by the hiring team at the chosen `visibility`.

        ⚠️ Greenhouse requires an **acting user id** on every write (`On-Behalf-Of`
        header): `on_behalf_of` for op="create", `user_id` for op="add_note" (the
        note's author doubles as the acting user). Get an id from `greenhouse_users`.

        Args:
            op: list (default) | get | create | add_note.
            candidate_id: op="get"/"add_note" — the candidate.
            per_page: op="list" — page size (default 50, capped at 500 upstream).
            page: op="list" — 1-based page number.
            job_id: op="list" — only candidates with an application on this job.
            email: op="list" — filter by exact email.
            created_after: op="list" — ISO 8601 timestamp.
            updated_after: op="list" — ISO 8601 timestamp.
            candidate: op="create" — Greenhouse candidate object (first_name,
                last_name, email_addresses, phone_numbers, applications, …).
            on_behalf_of: op="create" — Greenhouse user id to act as (required for
                writes — see greenhouse_users).
            body: op="add_note" — the note text.
            user_id: op="add_note" — Greenhouse user id authoring the note.
            visibility: op="add_note" — admin_only | private | public.
        """
        client = _client()

        if op == "list":
            _not_for(candidate_id, "candidate_id", op, "get")
            return client.list_candidates(
                per_page=per_page, page=page, job_id=job_id, email=email,
                created_after=created_after, updated_after=updated_after)

        if op == "get":
            return client.get_candidate(_need(candidate_id, "candidate_id", op))

        if op == "create":
            return client.add_candidate(
                _need(candidate, "candidate", op),
                on_behalf_of=_need(on_behalf_of, "on_behalf_of", op))

        if op == "add_note":
            return client.add_note(
                _need(candidate_id, "candidate_id", op),
                _need(body, "body", op),
                _need(user_id, "user_id", op),
                visibility=visibility)

        raise _bad("op must be 'list', 'get', 'create' or 'add_note'")

    @mcp.tool()
    def greenhouse_job(
        op: Literal["list", "get"] = "list",
        job_id: Optional[int] = None,
        per_page: int = 50,
        page: int = 1,
        status: Optional[str] = None,
    ) -> Any:
        """A Greenhouse job (an open position) — list or read one.

        `op`:
        - **"list"** (default): list jobs, paginated. `status` filters
          open | closed | draft.
        - **"get"**: fetch one job by id (`job_id`).

        Args:
            op: list (default) | get.
            job_id: op="get" — the job.
            per_page: op="list" — page size (default 50, capped at 500 upstream).
            page: op="list" — 1-based page number.
            status: op="list" — open | closed | draft.
        """
        client = _client()

        if op == "list":
            _not_for(job_id, "job_id", op, "get")
            return client.list_jobs(per_page=per_page, page=page, status=status)

        if op == "get":
            return client.get_job(_need(job_id, "job_id", op))

        raise _bad("op must be 'list' or 'get'")

    @mcp.tool()
    def greenhouse_application(
        op: Literal["list", "get"] = "list",
        application_id: Optional[int] = None,
        per_page: int = 50,
        page: int = 1,
        job_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Any:
        """A Greenhouse application (a candidate on a job) — list or read one.

        `op`:
        - **"list"** (default): list applications, paginated. Filters: `job_id`,
          `status` (active | rejected | hired).
        - **"get"**: fetch one application by id (`application_id`).

        Args:
            op: list (default) | get.
            application_id: op="get" — the application.
            per_page: op="list" — page size (default 50, capped at 500 upstream).
            page: op="list" — 1-based page number.
            job_id: op="list" — only applications on this job.
            status: op="list" — active | rejected | hired.
        """
        client = _client()

        if op == "list":
            _not_for(application_id, "application_id", op, "get")
            return client.list_applications(
                per_page=per_page, page=page, job_id=job_id, status=status)

        if op == "get":
            return client.get_application(
                _need(application_id, "application_id", op))

        raise _bad("op must be 'list' or 'get'")

    @mcp.tool()
    def greenhouse_users(per_page: int = 50, page: int = 1) -> list:
        """List Greenhouse users (recruiters) — get an id for `on_behalf_of`.

        Paginated (`per_page` default 50, capped at 500 upstream). This is the
        directory the writes of `greenhouse_candidate` (op="create" / "add_note")
        read their acting user id from.
        """
        return _client().list_users(per_page=per_page, page=page)
