"""Linear — issues, comments, projects, teams, cycles, labels, users, webhooks.

Wraps `oto.tools.linear.client.LinearClient` (GraphQL, single endpoint,
`Authorization: <key>` — no `Bearer` prefix, see the client's module
docstring). keyed `api_key`, **byo_org only** (no `byo_user`, no platform
key): a Linear API key is workspace-scoped by nature, and unlike a
per-vendor credit pool (AI Ark, cf. the retired `linkedin` connector,
oto-backend#279) there's no shared-pool rationale for a platform key here —
each org that wants Linear posts its own workspace key.

**8 tools, one per business object** (ADR 0047, silae) :
- `linear_issue` — the core object. op=list/get/search/create/update/
  archive/delete.
- `linear_comment` — comments on an issue. op=list/get/create/update/delete.
- `linear_project` — op=list/get/create/update.
- `linear_team` — a team + its workflow states (needed to resolve a
  `state_id` for `linear_issue(op="update")`). op=list/get/states.
- `linear_cycle` — sprints. op=list/get.
- `linear_label` — op=list/get/create.
- `linear_user` — op=list/get/viewer (the API key's own owner).
- `linear_webhook` — REAL GraphQL surface here, unlike Fireflies (dashboard-
  only there). op=list/create/update/delete.

**No param is silently ignored**: an op that doesn't use a given argument
REFUSES rather than dropping it (`_only`, allow-list per op — same contract
as Fireflies' `_refuse_ignored`, expressed as an allow-list instead of a
per-op deny-list since every op here has a small, disjoint param set).

⚠️ **No live key was available while building this** — see
`oto.tools.linear.client`'s module docstring for exactly what's unverified
(human-readable issue identifiers on `get`, `issueSearch`'s argument shape,
the full `resourceTypes` enum on webhooks). Treat this connector as
unverified until exercised against a real Linear workspace key.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _only(op: str, allowed: set, **provided: Any) -> None:
    """Un argument fourni hors de l'allow-list de CET op est une erreur
    d'intention, pas un détail — sinon un param mal placé serait
    silencieusement ignoré."""
    extra = sorted(k for k, v in provided.items() if v is not None and k not in allowed)
    if extra:
        raise _bad(f"op={op!r} n'utilise pas {', '.join(extra)}")


def _require(op: str, **required: Any) -> None:
    missing = sorted(k for k, v in required.items() if v is None)
    if missing:
        raise _bad(f"op={op!r} requiert {', '.join(missing)}")


def _page(first: Optional[int]) -> int:
    return first if first is not None else 50


def _upstream_message(e: Exception) -> str:
    from oto.tools.linear import LinearGraphQLError, LinearRateLimited
    from oto.tools.common.errors import UpstreamHTTPError

    if isinstance(e, UpstreamHTTPError):
        status = e.status_code
        if status in (401, 403):
            return (f"Linear a rejeté la clé API (HTTP {status}) — vérifie la clé posée "
                     "sur ce connecteur (linear.app/settings/api).")
        if status in (500, 502, 503, 504):
            return f"Linear est momentanément indisponible (HTTP {status}) — réessaie plus tard."
        return f"Linear a refusé la requête (HTTP {status}) : {e.body}"

    if isinstance(e, LinearRateLimited):
        reset = f" (réinitialisation à {e.reset_at} epoch ms UTC)" if e.reset_at else ""
        return ("Linear : quota horaire atteint (5 000 requêtes/h ou 3 000 000 points de "
                f"complexité/h){reset} — STOP, ne pas réessayer immédiatement.")

    if isinstance(e, LinearGraphQLError):
        return f"Linear a refusé la requête : {e.message if hasattr(e, 'message') else str(e)}"

    return str(e)


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : le profil du porteur de la clé, la
    plus légère des lectures (aucun filtre, un seul objet)."""
    from oto.tools.linear.client import LinearClient
    LinearClient(api_key=fields["key"]).get_viewer()


def register(mcp: FastMCP) -> None:
    from oto.tools.linear.client import LinearClient
    from oto.tools.linear import LinearError
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("linear", _verify)

    def _client() -> LinearClient:
        key, _ = access.resolve_api_key("linear")
        return LinearClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except (UpstreamHTTPError, LinearError) as e:
            raise _bad(_upstream_message(e))

    # ================================================================
    # Issues
    # ================================================================

    @mcp.tool()
    def linear_issue(
        op: Literal["list", "get", "search", "create", "update", "archive", "delete"] = "list",
        issue_id: Optional[str] = None,
        team_id: Optional[str] = None,
        project_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        state_id: Optional[str] = None,
        query: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[int] = None,
        label_ids: Optional[List[str]] = None,
        due_date: Optional[str] = None,
        estimate: Optional[int] = None,
        parent_id: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """A Linear issue — list/filter, full-text search, fetch one,
        create, update, archive (reversible), or delete (moves to trash).

        Args:
            op: "list" (default, filter by ids) | "get" | "search"
                (full-text, title+description) | "create" | "update" |
                "archive" | "delete".
            issue_id: REQUIRED by "get"/"update"/"archive"/"delete".
            team_id/project_id/cycle_id/assignee_id/state_id: op="list"
                only — filters, combinable. `state_id` comes from
                `linear_team(op="states", team_id=...)`.
            query: REQUIRED by "search".
            title: REQUIRED by "create". Also settable on "update".
            description/assignee_id/state_id/priority/label_ids/
                project_id/cycle_id/due_date/estimate: op="create"/"update"
                fields. `priority`: 0=none, 1=urgent, 2=high, 3=normal,
                4=low (Linear's own scale). `due_date`: ISO 8601 date.
            parent_id: op="create" only — makes this a sub-issue.
            first/after: op="list"/"search" only — cursor pagination
                (`after` = previous call's `pageInfo.endCursor`).
        """
        c = _client()
        if op == "list":
            _only(op, {"team_id", "project_id", "cycle_id", "assignee_id",
                        "state_id", "first", "after"},
                  issue_id=issue_id, query=query, title=title, description=description,
                  priority=priority, label_ids=label_ids, due_date=due_date,
                  estimate=estimate, parent_id=parent_id)
            return _run(lambda: c.list_issues(
                team_id=team_id, project_id=project_id, cycle_id=cycle_id,
                assignee_id=assignee_id, state_id=state_id, first=_page(first), after=after))
        if op == "get":
            _require(op, issue_id=issue_id)
            _only(op, {"issue_id"}, team_id=team_id, project_id=project_id, cycle_id=cycle_id,
                  assignee_id=assignee_id, state_id=state_id, query=query, title=title,
                  description=description, priority=priority, label_ids=label_ids,
                  due_date=due_date, estimate=estimate, parent_id=parent_id,
                  first=first, after=after)
            return _run(lambda: c.get_issue(issue_id))
        if op == "search":
            _require(op, query=query)
            _only(op, {"query", "team_id", "first", "after"},
                  issue_id=issue_id, project_id=project_id, cycle_id=cycle_id,
                  assignee_id=assignee_id, state_id=state_id, title=title,
                  description=description, priority=priority, label_ids=label_ids,
                  due_date=due_date, estimate=estimate, parent_id=parent_id)
            return _run(lambda: c.search_issues(query, team_id=team_id, first=_page(first), after=after))
        if op == "create":
            _require(op, title=title, team_id=team_id)
            _only(op, {"title", "team_id", "description", "assignee_id", "state_id",
                        "priority", "label_ids", "project_id", "cycle_id", "parent_id",
                        "due_date", "estimate"},
                  issue_id=issue_id, query=query, first=first, after=after)
            return _run(lambda: c.create_issue(
                title, team_id, description=description, assignee_id=assignee_id,
                state_id=state_id, priority=priority, label_ids=label_ids,
                project_id=project_id, cycle_id=cycle_id, parent_id=parent_id,
                due_date=due_date, estimate=estimate))
        if op == "update":
            _require(op, issue_id=issue_id)
            _only(op, {"issue_id", "title", "description", "assignee_id", "state_id",
                        "priority", "label_ids", "project_id", "cycle_id",
                        "due_date", "estimate"},
                  team_id=team_id, query=query, parent_id=parent_id, first=first, after=after)
            return _run(lambda: c.update_issue(
                issue_id, title=title, description=description, assignee_id=assignee_id,
                state_id=state_id, priority=priority, label_ids=label_ids,
                project_id=project_id, cycle_id=cycle_id, due_date=due_date,
                estimate=estimate))
        if op == "archive":
            _require(op, issue_id=issue_id)
            _only(op, {"issue_id"}, team_id=team_id, project_id=project_id, cycle_id=cycle_id,
                  assignee_id=assignee_id, state_id=state_id, query=query, title=title,
                  description=description, priority=priority, label_ids=label_ids,
                  due_date=due_date, estimate=estimate, parent_id=parent_id,
                  first=first, after=after)
            return _run(lambda: c.archive_issue(issue_id))
        if op == "delete":
            _require(op, issue_id=issue_id)
            _only(op, {"issue_id"}, team_id=team_id, project_id=project_id, cycle_id=cycle_id,
                  assignee_id=assignee_id, state_id=state_id, query=query, title=title,
                  description=description, priority=priority, label_ids=label_ids,
                  due_date=due_date, estimate=estimate, parent_id=parent_id,
                  first=first, after=after)
            return _run(lambda: c.delete_issue(issue_id))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Comments
    # ================================================================

    @mcp.tool()
    def linear_comment(
        op: Literal["list", "get", "create", "update", "delete"] = "list",
        comment_id: Optional[str] = None,
        issue_id: Optional[str] = None,
        body: Optional[str] = None,
        parent_id: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """Comments on a Linear issue.

        Args:
            op: "list" (all comments on one issue) | "get" | "create" |
                "update" | "delete".
            comment_id: REQUIRED by "get"/"update"/"delete".
            issue_id: REQUIRED by "list"/"create".
            body: REQUIRED by "create"/"update" — the comment text.
            parent_id: op="create" only — threads a reply under another comment.
            first/after: op="list" only — cursor pagination.
        """
        c = _client()
        if op == "list":
            _require(op, issue_id=issue_id)
            _only(op, {"issue_id", "first", "after"},
                  comment_id=comment_id, body=body, parent_id=parent_id)
            return _run(lambda: c.list_comments(issue_id, first=_page(first), after=after))
        if op == "get":
            _require(op, comment_id=comment_id)
            _only(op, {"comment_id"}, issue_id=issue_id, body=body, parent_id=parent_id,
                  first=first, after=after)
            return _run(lambda: c.get_comment(comment_id))
        if op == "create":
            _require(op, issue_id=issue_id, body=body)
            _only(op, {"issue_id", "body", "parent_id"}, comment_id=comment_id,
                  first=first, after=after)
            return _run(lambda: c.create_comment(issue_id, body, parent_id=parent_id))
        if op == "update":
            _require(op, comment_id=comment_id, body=body)
            _only(op, {"comment_id", "body"}, issue_id=issue_id, parent_id=parent_id,
                  first=first, after=after)
            return _run(lambda: c.update_comment(comment_id, body))
        if op == "delete":
            _require(op, comment_id=comment_id)
            _only(op, {"comment_id"}, issue_id=issue_id, body=body, parent_id=parent_id,
                  first=first, after=after)
            return _run(lambda: c.delete_comment(comment_id))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Projects
    # ================================================================

    @mcp.tool()
    def linear_project(
        op: Literal["list", "get", "create", "update"] = "list",
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
        team_ids: Optional[List[str]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        state: Optional[str] = None,
        lead_id: Optional[str] = None,
        target_date: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """A Linear project (spans one or more teams).

        Args:
            op: "list" (optionally scoped to one team) | "get" | "create" |
                "update".
            project_id: REQUIRED by "get"/"update".
            team_id: op="list" only — filter to projects visible to one team.
            team_ids: REQUIRED by "create" — a project belongs to ≥1 team.
            name: REQUIRED by "create". Also settable on "update".
            description/state/lead_id/target_date: op="create"/"update" fields.
            first/after: op="list" only — cursor pagination.
        """
        c = _client()
        if op == "list":
            _only(op, {"team_id", "first", "after"},
                  project_id=project_id, team_ids=team_ids, name=name,
                  description=description, state=state, lead_id=lead_id,
                  target_date=target_date)
            return _run(lambda: c.list_projects(team_id=team_id, first=_page(first), after=after))
        if op == "get":
            _require(op, project_id=project_id)
            _only(op, {"project_id"}, team_id=team_id, team_ids=team_ids, name=name,
                  description=description, state=state, lead_id=lead_id, target_date=target_date,
                  first=first, after=after)
            return _run(lambda: c.get_project(project_id))
        if op == "create":
            _require(op, name=name, team_ids=team_ids)
            _only(op, {"name", "team_ids", "description", "state", "lead_id", "target_date"},
                  project_id=project_id, team_id=team_id, first=first, after=after)
            return _run(lambda: c.create_project(
                name, team_ids, description=description, state=state,
                lead_id=lead_id, target_date=target_date))
        if op == "update":
            _require(op, project_id=project_id)
            _only(op, {"project_id", "name", "description", "state", "lead_id", "target_date"},
                  team_id=team_id, team_ids=team_ids, first=first, after=after)
            return _run(lambda: c.update_project(
                project_id, name=name, description=description, state=state,
                lead_id=lead_id, target_date=target_date))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Teams & workflow states
    # ================================================================

    @mcp.tool()
    def linear_team(
        op: Literal["list", "get", "states"] = "list",
        team_id: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """A Linear team, or its workflow states (statuses).

        Args:
            op: "list" (every team in the workspace) | "get" | "states"
                (this team's status list — Backlog/Todo/In Progress/Done/
                Cancelled buckets — resolve a `state_id` for
                `linear_issue(op="update")` here).
            team_id: REQUIRED by "get"/"states".
            first/after: op="list"/"states" only — cursor pagination.
        """
        c = _client()
        if op == "list":
            _only(op, {"first", "after"}, team_id=team_id)
            return _run(lambda: c.list_teams(first=_page(first), after=after))
        if op == "get":
            _require(op, team_id=team_id)
            _only(op, {"team_id"}, first=first, after=after)
            return _run(lambda: c.get_team(team_id))
        if op == "states":
            _require(op, team_id=team_id)
            _only(op, {"team_id", "first", "after"})
            return _run(lambda: c.list_workflow_states(team_id, first=_page(first), after=after))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Cycles
    # ================================================================

    @mcp.tool()
    def linear_cycle(
        op: Literal["list", "get"] = "list",
        cycle_id: Optional[str] = None,
        team_id: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """A Linear cycle (sprint).

        Args:
            op: "list" (optionally scoped to one team) | "get".
            cycle_id: REQUIRED by "get".
            team_id: op="list" only.
            first/after: op="list" only — cursor pagination.
        """
        c = _client()
        if op == "list":
            _only(op, {"team_id", "first", "after"}, cycle_id=cycle_id)
            return _run(lambda: c.list_cycles(team_id=team_id, first=_page(first), after=after))
        if op == "get":
            _require(op, cycle_id=cycle_id)
            _only(op, {"cycle_id"}, team_id=team_id, first=first, after=after)
            return _run(lambda: c.get_cycle(cycle_id))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Labels
    # ================================================================

    @mcp.tool()
    def linear_label(
        op: Literal["list", "get", "create"] = "list",
        label_id: Optional[str] = None,
        team_id: Optional[str] = None,
        name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """An issue label.

        Args:
            op: "list" (optionally scoped to one team; workspace labels
                have no team) | "get" | "create".
            label_id: REQUIRED by "get".
            team_id: op="list" filter. On "create", scopes the new label to
                a team — omit for a workspace-level label.
            name: REQUIRED by "create".
            color/description: op="create" only.
            first/after: op="list" only — cursor pagination.
        """
        c = _client()
        if op == "list":
            _only(op, {"team_id", "first", "after"},
                  label_id=label_id, name=name, color=color, description=description)
            return _run(lambda: c.list_labels(team_id=team_id, first=_page(first), after=after))
        if op == "get":
            _require(op, label_id=label_id)
            _only(op, {"label_id"}, team_id=team_id, name=name, color=color, description=description,
                  first=first, after=after)
            return _run(lambda: c.get_label(label_id))
        if op == "create":
            _require(op, name=name)
            _only(op, {"name", "team_id", "color", "description"}, label_id=label_id,
                  first=first, after=after)
            return _run(lambda: c.create_label(
                name, team_id=team_id, color=color, description=description))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Users
    # ================================================================

    @mcp.tool()
    def linear_user(
        op: Literal["list", "get", "viewer"] = "viewer",
        user_id: Optional[str] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """A workspace member.

        Args:
            op: "viewer" (default — the user who owns the API key) |
                "list" (every member) | "get".
            user_id: REQUIRED by "get".
            first/after: op="list" only — cursor pagination.
        """
        c = _client()
        if op == "viewer":
            _only(op, set(), user_id=user_id, first=first, after=after)
            return _run(lambda: c.get_viewer())
        if op == "list":
            _only(op, {"first", "after"}, user_id=user_id)
            return _run(lambda: c.list_users(first=_page(first), after=after))
        if op == "get":
            _require(op, user_id=user_id)
            _only(op, {"user_id"}, first=first, after=after)
            return _run(lambda: c.get_user(user_id))
        raise _bad(f"op inconnu: {op!r}")

    # ================================================================
    # Webhooks
    # ================================================================

    @mcp.tool()
    def linear_webhook(
        op: Literal["list", "create", "update", "delete"] = "list",
        webhook_id: Optional[str] = None,
        url: Optional[str] = None,
        team_id: Optional[str] = None,
        resource_types: Optional[List[str]] = None,
        secret: Optional[str] = None,
        enabled: Optional[bool] = None,
        all_public_teams: Optional[bool] = None,
        first: Optional[int] = None,
        after: Optional[str] = None,
    ) -> object:
        """A webhook subscription (real GraphQL surface on Linear, unlike
        Fireflies where webhook management is dashboard-only).

        Args:
            op: "list" (optionally scoped to one team) | "create" |
                "update" | "delete".
            webhook_id: REQUIRED by "update"/"delete".
            url: REQUIRED by "create" — the receiving endpoint. Settable on
                "update".
            team_id: op="list" filter; op="create" scopes the subscription
                to one team (use `all_public_teams` instead to cover every
                public team).
            resource_types: op="create"/"update" — event types, e.g.
                `["Issue", "Comment"]`. Linear's documented values include
                "Issue", "Comment", "IssueLabel", "Project", "Cycle",
                "ProjectUpdate", "Reaction" — passed through raw, validated
                server-side (full enum unconfirmed here).
            secret: op="create" only — signs delivered payloads.
            enabled: op="create" (default True) / "update".
            all_public_teams: op="create" only — subscribe across every
                public team instead of one `team_id`.
            first/after: op="list" only — cursor pagination.
        """
        c = _client()
        if op == "list":
            _only(op, {"team_id", "first", "after"},
                  webhook_id=webhook_id, url=url, resource_types=resource_types,
                  secret=secret, enabled=enabled, all_public_teams=all_public_teams)
            return _run(lambda: c.list_webhooks(team_id=team_id, first=_page(first), after=after))
        if op == "create":
            _require(op, url=url)
            _only(op, {"url", "team_id", "resource_types", "secret", "enabled", "all_public_teams"},
                  webhook_id=webhook_id, first=first, after=after)
            return _run(lambda: c.create_webhook(
                url, team_id=team_id, resource_types=resource_types, secret=secret,
                enabled=enabled if enabled is not None else True,
                all_public_teams=bool(all_public_teams)))
        if op == "update":
            _require(op, webhook_id=webhook_id)
            _only(op, {"webhook_id", "url", "resource_types", "enabled"},
                  team_id=team_id, secret=secret, all_public_teams=all_public_teams,
                  first=first, after=after)
            return _run(lambda: c.update_webhook(
                webhook_id, url=url, resource_types=resource_types, enabled=enabled))
        if op == "delete":
            _require(op, webhook_id=webhook_id)
            _only(op, {"webhook_id"}, url=url, team_id=team_id, resource_types=resource_types,
                  secret=secret, enabled=enabled, all_public_teams=all_public_teams,
                  first=first, after=after)
            return _run(lambda: c.delete_webhook(webhook_id))
        raise _bad(f"op inconnu: {op!r}")
