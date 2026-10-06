"""Lucca — FR HR (read): directory, absences, expense claims, organization.

Credential = static API key + tenant subdomain, two secrets. Resolved
per call via `access.resolve_credential_fields("lucca")` — generic
multi-field model (ADR 0011), same family as silae. byo_user: each
firm/employer sets their own Lucca key; their data is only visible to
them.

**Consolidated surface (ADR 0047 §Amendment)**: one tool per business OBJECT, the
verb as an `op` parameter — `lucca_employee` (list/get), `lucca_absence`
(list/get, `date` REQUIRED by Lucca on list), `lucca_leave_request`
(list/get), `lucca_expense_claim` (list ONLY — Lucca exposes no detail
by id on this resource, see `oto.tools.lucca.LuccaClient`), `lucca_department`
(list/get) and `lucca_establishment` (list ONLY, base URL and pagination
different from the other five — see the client).

⚠️ **Read-only**: the oto-core client carries NO write for Lucca —
nothing to omit here, the boundary is already the client's. The 1:1 symmetry
above (one tool per resource, no arbitration over what we expose) holds
AS LONG AS the upstream client stays read-only: a first write
method (posting a leave, approving an expense claim) reopens the question of
what we expose — the same symmetry would then make a write tool appear
by default, which is not of the same order as a read.

⚠️ **No redaction policy set by default.** Field masking
(IBAN, social security number, name…) is available at the tools boundary
(`FieldRedactionMiddleware`, policy resolved by NAMESPACE `lucca` —
insensitive to tool names), but `field_filter_defaults.SERVER_DEFAULTS` carries
nothing for `lucca`: nothing is redacted until the org sets its
own policy. The directory (`lucca_employee`) and the expense claims
(`lucca_expense_claim`) are the two surfaces most likely to
carry personal data.
"""
from __future__ import annotations

from typing import Literal, Optional

from .. import output_projection

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, egress
from ..connectors import verify as connector_verify


def _base_url(domain: str) -> str:
    """The URL the client will build itself — recomputed HERE so that the egress
    guard (`oto_mcp/egress.py`) judges the REAL destination before any network
    byte. `domain` comes from an org's credential: this is exactly the case
    that an eighth free-host connector must guard (`tests/test_egress_guard.py`)."""
    return f"https://{domain}.ilucca.net"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Required argument for THIS op — actionable error, never a fallback."""
    if value is None or value == "":
        raise _bad(f"op='{op}' requires {name}")
    return value


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """A supplied argument that THIS op doesn't use is an intent error,
    not a detail — same reason as silae/ahrefs: silence would return a
    plausible result that is beside the request."""
    for name, value in provided.items():
        if value is not None and value != "":
            raise _bad(f"op='{op}' does not use {name} — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return f"Lucca: access denied (HTTP {status}) — invalid API key or subdomain."
    if status == 429:
        return "Lucca: too many requests (429) — retry in a moment."
    if status in (500, 502, 503, 504):
        return f"Lucca is temporarily unavailable (HTTP {status}) — retry later."
    return f"Lucca rejected the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe (otomata-tech/oto#69).

    `list_departments()`: the smallest client call with no required
    parameter — unlike `list_leaves` (requires `date`) or
    `list_establishments` (different base URL, could be outside the key's
    scope without it being an auth fault). An EMPTY list is a
    normal state (freshly created account), never a refusal."""
    from oto.tools.lucca import LuccaClient
    from oto.tools.common.errors import UpstreamHTTPError

    egress.check_url(_base_url(fields["domain"]), connector="lucca")
    try:
        LuccaClient(api_key=fields["api_key"], domain=fields["domain"]).list_departments()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Lucca HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Lucca: {e.body}")


def register(mcp: FastMCP) -> None:
    from oto.tools.lucca import LuccaClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("lucca", _verify)

    def _client() -> LuccaClient:
        creds = access.resolve_credential_fields("lucca")
        # No explicit `field_filter`: the client's default reads a LOCAL YAML
        # file (~/.otomata/config.yaml) that doesn't exist on the server,
        # hence a no-op — redaction for the backend goes through
        # `FieldRedactionMiddleware` (see module docstring), not here.
        egress.check_url(_base_url(creds.get("domain") or ""), connector="lucca")
        return LuccaClient(api_key=creds.get("api_key"), domain=creds.get("domain"))

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- Directory ---

    @mcp.tool()
    def lucca_employee(
        op: Literal["list", "get"] = "list",
        user_id: Optional[str] = None,
        fields: Optional[str] = None,
        mail: Optional[str] = None,
        login: Optional[str] = None,
        former_employees: Optional[bool] = None,
        offset: int = 0,
        limit: int = 1000,
    ) -> dict:
        """An employee of the Lucca directory — the roster, or one employee.

        `op`:
        - **"list"** (default): employees reachable with the API key.
        - **"get"**: one employee by id (`user_id`).

        Args:
            op: list (default) | get.
            user_id: op="get" only — the employee's Lucca id.
            fields: OData-style field selection (both ops), e.g.
                "id,firstName,lastName,legalEntity[id,name]".
            mail: op="list" only — exact match, or "like,..." for partial match.
            login: op="list" only — exact match.
            former_employees: op="list" only — include former employees
                (default: current only).
            offset / limit: op="list" only — pagination (Lucca caps limit at 1000).
        """
        client = _client()

        if op == "list":
            rows = _run(lambda: client.list_users(
                offset=offset, limit=limit, fields=fields, mail=mail, login=login,
                former_employees=former_employees))
            return {"employees": rows}
        if op == "get":
            _refuse_ignored(op, "only exists on op='list'",
                            mail=mail, login=login, former_employees=former_employees)
            row = _run(lambda: client.get_user(
                _need(user_id, "user_id", op), fields=fields))
            return {"employee": row}
        raise _bad("op must be 'list' or 'get'")

    # --- Absences (Timmi Absences) ---

    @mcp.tool()
    def lucca_absence(
        op: Literal["list", "get"] = "list",
        leave_id: Optional[str] = None,
        date: Optional[str] = None,
        owner_id: Optional[list] = None,
        department_id: Optional[list] = None,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[list] = None,
    ) -> dict:
        """A posted absence (half-day leave record) — the list, or one record.

        `op`:
        - **"list"** (default): leaves in a date range. `date` is REQUIRED by
          Lucca — there is no unfiltered "all leaves". `comment` (Lucca's free-text
          field on a leave, no documented length limit) is DROPPED by default —
          `comment_length` says how much was cut. Pass `fields=["*"]` for the raw
          record, or name the fields you want (`comment` included) to get exactly
          those.
        - **"get"**: one leave by id (`leave_id`) — always the full record.

        Args:
            op: list (default) | get.
            leave_id: op="get" only — the leave's Lucca id.
            date: op="list" only, REQUIRED — "yyyy-mm-dd" (exact day),
                "since,yyyy-mm-dd", "until,yyyy-mm-dd", or
                "between,yyyy-mm-dd,yyyy-mm-dd".
            owner_id: op="list" only — filter by employee id(s).
            department_id: op="list" only — filter by department id(s).
            offset / limit: op="list" only — pagination (Lucca caps limit at 1000).
            fields: op="list" only — omit for the trimmed view (no `comment`);
                `["*"]` for the raw record; a list of names for exactly those.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "only exists on op='get'", leave_id=leave_id)
            rows = _run(lambda: client.list_leaves(
                _need(date, "date", op), offset=offset, limit=limit,
                owner_id=owner_id, department_id=department_id))
            rows, notice = output_projection.summarize(
                rows, body_fields=("comment",), fields=fields, always=("id",))
            return {"leaves": rows, **({"projection": notice} if notice else {})}
        if op == "get":
            _refuse_ignored(op, "only exists on op='list'",
                            date=date, owner_id=owner_id, department_id=department_id)
            row = _run(lambda: client.get_leave(_need(leave_id, "leave_id", op)))
            return {"leave": row}
        raise _bad("op must be 'list' or 'get'")

    # --- Leave requests (the approval workflow, distinct from absences) ---

    @mcp.tool()
    def lucca_leave_request(
        op: Literal["list", "get"] = "list",
        leave_request_id: Optional[str] = None,
    ) -> dict:
        """A leave REQUEST — the workflow object behind an absence, distinct
        from the posted leave record itself (`lucca_absence`).

        `op`:
        - **"list"** (default): all leave requests. ⚠️ Lucca's own OpenAPI spec
          documents NO query parameters on this endpoint at all — no paging,
          no filter (verified against developers.luccasoftware.com,
          2026-09-15). It always returns everything the API key reaches.
        - **"get"**: one leave request by id (`leave_request_id`).

        Args:
            op: list (default) | get.
            leave_request_id: op="get" only — the leave request's Lucca id.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "only exists on op='get'",
                            leave_request_id=leave_request_id)
            rows = _run(lambda: client.list_leave_requests())
            return {"leave_requests": rows}
        if op == "get":
            row = _run(lambda: client.get_leave_request(
                _need(leave_request_id, "leave_request_id", op)))
            return {"leave_request": row}
        raise _bad("op must be 'list' or 'get'")

    # --- Expense claims (Cleemy Expenses) ---

    @mcp.tool()
    def lucca_expense_claim(
        owner_id: Optional[list] = None,
        status_id: Optional[str] = None,
        declared_on: Optional[str] = None,
        order_by: Optional[str] = None,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[list] = None,
    ) -> dict:
        """Expense claims (notes de frais). List only — Lucca's legacy v3 API
        documents no detail-by-id endpoint for this resource (verified
        2026-09-15): adding a "get" op would promise something Lucca doesn't have.

        ⚠️ Unlike `lucca_absence`, this resource's schema (verified against
        Lucca's own OpenAPI spec, 2026-09-15) has NO free-text field — only ids,
        dates, enums and a `name` capped at 255 chars. `fields` is offered for
        symmetry and to let a caller restrict to exactly the columns it needs,
        but nothing is dropped by default: there is no verbose field to cut.

        Args:
            owner_id: Filter by employee id(s).
            status_id: Numeric id (1-9) or name — Created, PartiallyApproved,
                Approved, Controlled, ApprovedAndControlled, PaymentInitiated,
                Paid, Refused, Cancelled.
            declared_on: `"{comparator},{date}"`, e.g. "between,2026-01-01,2026-01-31".
            order_by: `"{field},{'asc'|'desc'}"`, e.g. "declaredOn,desc".
            offset / limit: pagination (Lucca caps limit at 1000).
            fields: restrict to exactly these fields (no default reduction — see above).
        """
        rows = _run(lambda: _client().list_expense_claims(
            offset=offset, limit=limit, owner_id=owner_id, status_id=status_id,
            declared_on=declared_on, order_by=order_by))
        rows, notice = output_projection.summarize(
            rows, body_fields=(), fields=fields, always=("id",))
        return {"expense_claims": rows, **({"projection": notice} if notice else {})}

    # --- Organization: departments ---

    @mcp.tool()
    def lucca_department(
        op: Literal["list", "get"] = "list",
        department_id: Optional[str] = None,
        head_id: Optional[int] = None,
        parent_id: Optional[int] = None,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[list] = None,
    ) -> dict:
        """A department of the org chart — the list, or one department.

        `op`:
        - **"list"** (default): departments reachable with the API key. The
          member rosters Lucca embeds on each department (`users`,
          `currentUsers` — every employee of that department, nested in full)
          are DROPPED by default — `users_length`/`currentUsers_length` say
          how many were cut. Pass `fields=["*"]` for the raw record (rosters
          included), or name the fields you want.
        - **"get"**: one department by id (`department_id`) — always the full
          record, rosters included.

        Args:
            op: list (default) | get.
            department_id: op="get" only — the department's Lucca id.
            head_id: op="list" only — filter by department head's employee id.
            parent_id: op="list" only — filter by parent department id.
            offset / limit: op="list" only — pagination (Lucca caps limit at 1000).
            fields: op="list" only — omit for the trimmed view (no rosters);
                `["*"]` for the raw record; a list of names for exactly those.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "only exists on op='get'", department_id=department_id)
            rows = _run(lambda: client.list_departments(
                offset=offset, limit=limit, head_id=head_id, parent_id=parent_id))
            rows, notice = output_projection.summarize(
                rows, body_fields=("users", "currentUsers"), fields=fields,
                always=("id", "name"))
            return {"departments": rows, **({"projection": notice} if notice else {})}
        if op == "get":
            _refuse_ignored(op, "only exists on op='list'",
                            head_id=head_id, parent_id=parent_id)
            row = _run(lambda: client.get_department(
                _need(department_id, "department_id", op)))
            return {"department": row}
        raise _bad("op must be 'list' or 'get'")

    # --- Organization: establishments ---

    @mcp.tool()
    def lucca_establishment(
        ids: Optional[list] = None,
        legal_unit_id: Optional[list] = None,
        search: Optional[str] = None,
        is_archived: Optional[bool] = None,
        page: int = 1,
        limit: int = 10,
        fields: Optional[list] = None,
    ) -> dict:
        """Establishments (legal entities / sites). List only — Lucca documents
        no detail-by-id endpoint for this resource (verified 2026-09-15).

        ⚠️ Different from the other tools of this module: this endpoint lives
        under a different base path (organization structure API, not
        `/api/v3/...`) and paginates by PAGE (1-indexed, default size 10),
        not by offset.

        The nested `legalUnit` object (the establishment's legal entity, with
        its own id/name/code/activity code…) is DROPPED by default —
        `legalUnit_length` says how much was cut, and `legalUnitId` is kept so
        you can still address it. Pass `fields=["*"]` for the raw record.

        Args:
            ids: Establishment id(s).
            legal_unit_id: Legal unit id(s).
            search: Name search.
            is_archived: Filter archived / active establishments (omitted =
                Lucca's own default).
            page / limit: 1-indexed pagination — Lucca's default page size
                for this endpoint is 10, not 1000 like the rest of the client.
            fields: omit for the trimmed view (no `legalUnit`); `["*"]` for
                the raw record; a list of names for exactly those.
        """
        rows = _run(lambda: _client().list_establishments(
            page=page, limit=limit, ids=ids, legal_unit_id=legal_unit_id,
            search=search, is_archived=is_archived))
        rows, notice = output_projection.summarize(
            rows, body_fields=("legalUnit",), fields=fields,
            always=("id", "name", "legalUnitId"))
        return {"establishments": rows, **({"projection": notice} if notice else {})}
