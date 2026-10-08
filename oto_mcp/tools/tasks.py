"""Google Tasks — oto-core surface (TasksClient) exposed per user, multi-account.

Same substrate as Gmail: each user connects one or more Google accounts
on `https://app.oto.ninja/` (unified OAuth flow, `tasks` scope included). The
`tasks_*` tools act on the default account, or on the account targeted by
`account` (the email address). No platform key: strictly per-user access.

**Consolidated surface (ADR 0047 §Amendment, applied to the tasks product)**: one tool
per business OBJECT, the verb as an `op` parameter — 6 tools → 2.
- `tasks_task` = **the task**: `list` / `get` / `upsert` (create or update) /
  `set_status` (done / reopened) / `rm` (delete). All its ops share the same
  `(task_id, tasklist)` pair + `account`: maximal parameter overlap,
  which is the merge criterion.
- `tasks_lists` = **the task list**, and it stays ALONE: a different object, and no
  parameter in common with the task (neither `task_id` nor `tasklist` — it is what
  PRODUCES the `tasklist` ids the other consumes). Same case as `zoho_modules`.

⚠️ This module WRITES to the user's personal data: `op="upsert"`
creates/updates, `op="set_status"` updates, **`op="rm"` deletes** (irreversible). The
default `op="list"` is a READ — a call without `op` never writes or deletes.
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional, get_args

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..auth import google as google_oauth

# Ops of `tasks_task`, and the refusal message that NAMES them (single source: an op
# added here must appear in the message, otherwise the agent cannot correct itself).
# The `Literal` is that source: it serves both as annotation (⟹ `enum` in the JSON
# schema served to the model, which constrains generation) and as runtime guard via `get_args`.
_TaskOp = Literal["list", "get", "upsert", "set_status", "rm"]
_TASK_OPS = get_args(_TaskOp)
_UNKNOWN_OP = "op must be 'list', 'get', 'upsert', 'set_status' or 'rm'"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Required argument for THIS op — actionable error, never a fallback.

    The EMPTY string counts as absent: `op='rm'` with `task_id=""` would otherwise
    hit the API with an empty id, and the refusal must come from here, not from an
    opaque upstream 404."""
    if value is None or value == "":
        raise _bad(f"op='{op}' requires {name}")
    return value


def _client_for_user(account: Optional[str] = None):
    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account, service="tasks")
    except RuntimeError as e:
        raise _bad(str(e))
    from oto.tools.google.tasks.lib.tasks_client import TasksClient
    return TasksClient(credentials=creds)


_GOOGLE_CLIENT_TIMEOUT_S = 20
# oto-backend#867 batch 2 — see gmail.py::_client_for_user_async for the
# rationale (same token-refresh mechanism, same method).
async def _client_for_user_async(account: Optional[str] = None):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_client_for_user, account),
                                      timeout=_GOOGLE_CLIENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise _bad(f"Google did not respond within {_GOOGLE_CLIENT_TIMEOUT_S}s "
                   "(token refresh) — retry.")


def _normalize_due(due: Optional[str]) -> Optional[str]:
    """Expand a YYYY-MM-DD date to the RFC 3339 the Tasks API wants."""
    if due is None:
        return None
    if len(due) == 10 and due[4] == '-' and due[7] == '-':
        return f"{due}T00:00:00.000Z"
    return due


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def tasks_lists(create: Optional[str] = None, account: Optional[str] = None) -> dict:
        """List the user's Google Tasks lists — or create one.

        Returns {tasklists: [{id, title, updated}], count} when listing. Use a
        list `id` as the `tasklist` argument of `tasks_task`; omit it for '@default'.

        Args:
            create: if given (a title), CREATE a new task list and return it
                instead of listing.
            account: email of the Google account to use — same choice as `_account`.
        """
        client = await _client_for_user_async(account)
        if create:
            return await asyncio.to_thread(client.create_tasklist, create)
        tasklists = await asyncio.to_thread(client.list_tasklists)
        return {"tasklists": tasklists, "count": len(tasklists)}

    @mcp.tool()
    async def tasks_task(
        op: _TaskOp = "list",
        task_id: Optional[str] = None,
        tasklist: str = "@default",
        title: Optional[str] = None,
        notes: Optional[str] = None,
        due: Optional[str] = None,
        parent: Optional[str] = None,
        done: bool = True,
        completed: bool = False,
        max_results: int = 100,
        account: Optional[str] = None,
    ) -> dict:
        """A task inside a Google Tasks list — list, read, create/update, complete, delete.

        `op`:
        - **"list"** (default): list tasks in a task list.
        - **"get"**: get a single task by id.
        - **"upsert"**: create a task, or update an existing one.
        - **"set_status"**: complete (`done=True`) or reopen (`done=False`) a task.
        - **"rm"**: delete a task. Irreversible.

        Args:
            op: list (default) | get | upsert | set_status | rm.
            task_id: the task id — REQUIRED for op="get"/"set_status"/"rm". For
                op="upsert": when set, UPDATE that task instead of creating; pass
                any of title/notes/due to change.
            tasklist: task list id (default '@default'). Ids: `tasks_lists`.
            title: op="upsert" — task title. REQUIRED to create (omit `task_id`);
                optional to update.
            notes: op="upsert" — free-text notes.
            due: op="upsert" — due date, 'YYYY-MM-DD' or RFC 3339 (Tasks ignores
                the time).
            parent: op="upsert" — parent task id to nest under, same list (create only).
            done: op="set_status" — True = mark completed ; False = reopen (back to
                needsAction).
            completed: op="list" — include completed tasks (default false).
            max_results: op="list" — max tasks to return (default 100).
            account: email of the Google account to use — same choice as `_account`.
        """
        # Refuse BEFORE any credential resolution: an unknown op must be told
        # which ones are valid, not "no Google account connected".
        if op not in _TASK_OPS:
            raise _bad(_UNKNOWN_OP)

        client = await _client_for_user_async(account)

        if op == "list":
            tasks = await asyncio.to_thread(client.list_tasks, tasklist, completed, max_results)
            return {"tasks": tasks, "count": len(tasks)}
        if op == "get":
            return await asyncio.to_thread(
                client.get_task, _need(task_id, "task_id", op), tasklist
            )
        if op == "upsert":
            if task_id:
                if title is None and notes is None and due is None:
                    raise _bad("To update, provide title, notes or due.")
                return await asyncio.to_thread(
                    client.update_task, task_id, tasklist, title, notes, _normalize_due(due)
                )
            if not title:
                raise _bad("`title` is required to create a task (or provide `task_id` to update).")
            return await asyncio.to_thread(
                client.create_task, title, notes, _normalize_due(due), tasklist, parent
            )
        if op == "set_status":
            return await asyncio.to_thread(
                client.complete_task, _need(task_id, "task_id", op), tasklist, done
            )
        if op == "rm":
            return await asyncio.to_thread(
                client.delete_task, _need(task_id, "task_id", op), tasklist
            )
        raise _bad(_UNKNOWN_OP)   # unreachable (guard at the top) — safety net if `_TASK_OPS` grows
