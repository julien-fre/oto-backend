"""Brevo — automations (marketing workflows) via the vendor's PRIVATE API.

⚠️ Undocumented private API: `workflow-apis.brevo.com/v1`, auth = **live browser
session** (httpOnly `auth` cookie). Reverse-engineered from the v5 editor
(o-browser exploration of 2026-06-24). May break without notice on Brevo's side. NOT
to be confused with the PUBLIC v3 API (`api.brevo.com/v3`, `api-key` key) which handles
transactional / contacts / campaigns — but NOT automation authoring (hence this
separate connector).

Execution — **Browserbase** (`oto_mcp/browserbase.py`). The Brevo token is only accepted
from a **live browser session**; a raw `httpx`/curl is rejected (403),
and a session **cannot be transplanted** by cookie export. So we rent a remote
Chrome: the user logs in ONCE via the **Live View** (they handle SSO/captcha/2FA),
their session persists in a Browserbase **Context** (= the per-user credential, `brevoauto`
vault), and each `workflow-apis` call runs as a `fetch()` INSIDE an ephemeral
session of the Context (see `browserbase.run_fetch`). Proven 200 on 2026-06-24. Platform
creds = env `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID`.

Surface = **empirically verified** endpoints:
- onboarding: `brevoauto_connect_start` (→ Live View) / `brevoauto_connect_status` (persists);
- read: `listing`, full workflow (triggers + steps + wiring), catalog;
- write: create / configure / delete trigger & step (with `prev` +
  `condition_node` + `next_steps`), activate.

NOT exposed (separate, heavy API): **email template creation**
(`/editor-api/*` + `/email/templates/{id}`). A `send_email` step references an
existing `template_id`; the content is designed in the Brevo UI (or via the v3 API).

**Consolidated surface (ADR 0047 §Amendment, applied to the brevoauto connector on
2026-08-11: 13 tools → 5)**: one tool per business OBJECT, the verb as an `op` parameter —
`brevoauto_automation` (list/get/catalog/create/status: the scenario itself, all
scoped by `workflow_id` except `list`/`create`), `brevoauto_trigger` (add/configure/
delete: an entry point, designated by `trigger_point_id`) and `brevoauto_step`
(add/configure/delete: a step, designated by `step_id`). Trigger and step remain
TWO tools: distinct objects, distinct identifiers (`trigger_point_id` vs
`step_id`), distinct endpoints — merging them would only share `workflow_id`.

The pair `brevoauto_connect_start` / `brevoauto_connect_status` stays ALONE: it is the
platform pattern of two-step connection flows (`*_connect_start` /
`*_connect_status` on unipile, pennylaneged, crunchbase — *handles* in the MRTR sense),
its parameters (`context_id`, `session_id`, returned by the start) overlap NO
parameter of the automation domain, and its convergence target is the cross-cutting capability
`oto_connector op=connect`, not a per-connector merge.

⚠️ **This module WRITES to the real Brevo account** (create / configure / delete a
scenario, a trigger, a step; activate a scenario that will send real emails).
Two safeguards: `brevoauto_trigger` and `brevoauto_step` have ONLY write ops →
their `op` is **required**, no default, so no write is reachable by
omission; `brevoauto_automation` defaults to `op="list"`, a **READ**. An unknown
op is refused BEFORE the credential is resolved — it never reaches Brevo.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastmcp import Context, FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from .. import access, browser_session, browserbase
from ..auth.hooks import current_user_sub_from_token

logger = logging.getLogger(__name__)

# (Private API, origin page) pair specific to Brevo — passed to the generic substrate
# `browserbase.run_fetch`. The `fetch` is same-origin with the app (app.brevo.com)
# to carry the session cookie; the workflow-apis.* API is a subdomain of
# brevo.com reachable with this cookie.
_API = "https://workflow-apis.brevo.com/v1"
_APP = "https://app.brevo.com/"

# Ops of each tool, reads first. Single source: input validation, the refusal
# message AND the `Literal[…]` annotation of `op` (hence the JSON-schema `enum`
# served to the model) all derive from it — an op cannot be added and accepted without being
# announced (nor the reverse). `brevoauto_trigger`/`brevoauto_step` have no read:
# their `op` therefore has NO default (see module docstring).
# ⚠️ These constants are subscripted in a `Literal[…]`: keep TUPLES (a
# list is unhashable → `Literal[[…]]` raises when annotations are resolved).
_AUTOMATION_READ_OPS = ("list", "get", "catalog")
_AUTOMATION_WRITE_OPS = ("create", "status")
_AUTOMATION_OPS = _AUTOMATION_READ_OPS + _AUTOMATION_WRITE_OPS
_AUTOMATION_OPS_ERROR = (
    "op must be 'list', 'get', 'catalog', 'create' or 'status'")

_TRIGGER_OPS = ("add", "configure", "delete")
_TRIGGER_OPS_ERROR = "op must be 'add', 'configure' or 'delete'"

_STEP_OPS = ("add", "configure", "delete")
_STEP_OPS_ERROR = "op must be 'add', 'configure' or 'delete'"

# Scenario statuses, as the API accepts them.
_STATUSES = ("active", "paused", "draft")


async def _verify_session(session_id: str) -> browser_session.Verdict:
    """Brevo login confirmed? Checks on the LIVE session for the presence of the
    httpOnly `auth` cookie (set by the Brevo login). Shared by the two connection
    surfaces (REST dashboard + MCP) via `browser_session`. Returns a `Verdict`: a
    refusal STATES its reason, without which the calling agent has no other course than to
    start over in a loop (see the header of `browser_session`)."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(browserbase.connect_url(session_id))
        try:
            c = b.contexts[0] if b.contexts else await b.new_context()
            cks = await c.cookies()
            if any(x["name"] == "auth" for x in cks):
                return browser_session.Verdict(True, browser_session.LOGGED_IN)
            return browser_session.Verdict(
                False, browser_session.NO_SESSION,
                "The Brevo session cookie (`auth`) is missing: the login was not "
                "completed. Finish it in the Live View, then rerun "
                "`brevoauto_connect_status` with the same session identifiers.")
        finally:
            await b.close()


# Declares Brevo as a browser-session connector (generic start + this verify) —
# feeds the REST (dashboard) AND MCP connection flows. At module import.
browser_session.register("brevoauto", _verify_session, login_url=_APP)


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _need(value, name: str, op: str):
    """Argument required for THIS op — actionable error that NAMES the op and
    the argument, never a fallback.

    An empty string counts as absent (`name=""` would create an unnamed scenario,
    `step_name=""` would put the config block under an empty key). A `config={}` stays
    valid: configuring with an empty block is a legitimate API case — it is the
    ABSENCE of `config` that we refuse, because the merge by `op=` turned this
    parameter from "required by the schema" into "optional", and a silent write that
    overwrites a step's config with just the defaults would be undetectable.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _err(f"op='{op}' requires {name}")
    return value


def _sub() -> str:
    # An identity failure BUBBLES UP (the seam logs it with its reason, #464): only
    # a call truly without a token is "unauthenticated".
    sub = current_user_sub_from_token()
    if not sub:
        raise _err("Auth required — this tool only works on the authenticated HTTP transport.")
    return sub


def _context_id() -> str:
    """The user's Browserbase Context (= their logged-in Brevo session), resolved from the
    vault. Raises an actionable McpError if Brevo is not connected."""
    try:
        return access.resolve_credential("brevoauto", want="byo").key
    except McpError:
        raise _err("Brevo not connected. Run `brevoauto_connect_start` to log in "
                   "(once) via the Live View.")


async def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    """Runs a `workflow-apis` call in the user's Browserbase session.
    Returns the decoded `data`. Otherwise raises an actionable McpError."""
    if not browserbase.is_configured():
        raise _err("Browserbase not configured on the platform side "
                   "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
    ctx_id = _context_id()
    try:
        res = await browserbase.run_fetch(ctx_id, method, path, body, base=_API, app=_APP)
    except browserbase.BrowserbaseError as e:
        raise _err(f"Browserbase execution failed: {e}", code=INTERNAL_ERROR)
    st = res.get("status")
    if st in (401, 403):
        raise _err("Brevo session expired / disconnected — rerun `brevoauto_connect_start`.")
    if not (200 <= (st or 0) < 300):
        raise _err(f"Brevo returned {st}: {str(res.get('data'))[:200]}", code=INTERNAL_ERROR)
    return res["data"]


def register(mcp: FastMCP) -> None:

    # --- Onboarding (Live View) --------------------------------------------
    @mcp.tool()
    def brevoauto_connect_start(ctx: Context) -> dict:
        """Starts the connection to Brevo (automations). Opens a remote browser
        and returns a **`live_view_url`**: open it, log in to Brevo
        as usual (email/password, Google SSO, captcha — you handle everything in
        that window). Then call `brevoauto_connect_status(context_id, session_id)`
        with the returned values to finalize (your session is remembered; redo it
        only when it expires).
        """
        sub = _sub()
        try:
            out = browser_session.start(sub, "brevoauto")
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["instructions"] = ("Open `live_view_url`, log in to Brevo, then call "
                               "`brevoauto_connect_status` with context_id + session_id.")
        return out

    @mcp.tool()
    async def brevoauto_connect_status(ctx: Context, context_id: str,
                                   session_id: str) -> dict:
        """Finalizes the Brevo connection. Checks that you really logged in in the Live
        View; if so, **remembers** your session (the Context) for the next
        calls. Returns `{connected}`. Call it again if `connected=false` (not yet
        logged in)."""
        sub = _sub()
        try:
            res = await browser_session.finalize(sub, "brevoauto", context_id, session_id)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not res.connected:
            # `reason` + `retry` first and foremost: a refusal without a reason leaves the agent
            # only a reconnection loop (see the header of `browser_session`).
            return {"connected": False, "reason": res.reason, "retry": res.retry,
                    "hint": res.detail or "Not logged in yet — log in in the Live "
                                          "View then rerun."}
        out = {"connected": True, "context_id": context_id, "reason": res.reason,
               "login_verified": not res.warning}
        if res.warning:
            out["warning"], out["retry"] = res.warning, False
        return out

    # --- The scenario itself -----------------------------------------------
    @mcp.tool()
    async def brevoauto_automation(ctx: Context,
                                   op: Literal[_AUTOMATION_OPS] = "list",
                                   workflow_id: Optional[int] = None,
                                   name: Optional[str] = None,
                                   description: str = "",
                                   status: str = "active") -> dict:
        """An automation (marketing scenario) of the Brevo account — list, read its
        structure, read the editor's palette, create, activate / pause.

        `op`:
        - **"list"** (default): lists the automations (marketing scenarios) of the
          connected Brevo account. Returns `workflows[]` with `id`, `scenario_name`, `status`,
          `created_at`/`updated_at`. Use the `id` as the `workflow_id` of the
          following ops.
        - **"get"**: full structure of an automation: triggers (entry
          points), steps (MAP keyed by id) and the graph wiring
          (`next`/`prev`, `is_condition`, `condition_node`). Includes the compiled DSL of
          conditions (`fe_query` / `dsl`).
        - **"catalog"**: catalog of available triggers (the editor's palette),
          grouped by source (contacts / email / WhatsApp…). Each entry carries its
          `internal_action_id`, `action_type`, label — to pass to
          `brevoauto_trigger(op="add")` / `brevoauto_step(op="add")`. `workflow_id`
          serves as context for the palette and stays optional.
        - **"create"** — ⚠️ WRITES: creates an EMPTY automation scenario and returns
          `{workflow_id}`. Build step 1: then `brevoauto_trigger(op="add")`
          (entry point), then `brevoauto_step(op="add")` +
          `brevoauto_step(op="configure")`, then
          `brevoauto_automation(op="status", status="active")`.
        - **"status"** — ⚠️ WRITES: activates / pauses a scenario. `status` ∈
          `active` | `paused` | `draft`. Call it last, once all nodes are created AND
          configured.

        Args:
            op: list (default) | get | catalog | create | status.
            workflow_id: op="get"/"status" — the scenario id (see op="list");
                op="catalog" — optional palette context.
            name: op="create" — scenario name (required).
            description: op="create" — free description (optional).
            status: op="status" — `active` | `paused` | `draft` (default `active`).
        """
        # Refusal BEFORE any credential resolution: an unknown op never reaches
        # Brevo — so never, via a derived path, a write.
        if op not in _AUTOMATION_OPS:
            raise _err(_AUTOMATION_OPS_ERROR)

        # ---- reads -----------------------------------------------------------
        if op == "list":
            return await _api("GET", "/workflow/listing")

        if op == "get":
            wid = int(_need(workflow_id, "workflow_id", op))
            return await _api("GET", f"/workflow/{wid}")

        if op == "catalog":
            # `workflow_id` absent (or 0) → 1: the palette is the editor's,
            # any scenario serves as its context.
            wid = int(workflow_id or 0) or 1
            return await _api("GET", f"/workflow/getCategoryData?workflow_id={wid}")

        # ---- writes ----------------------------------------------------------
        if op == "create":
            libelle = str(_need(name, "name", op)).strip()
            return await _api("POST", "/workflow/createcustom", {
                "workflow_name": libelle, "workflow_desc": description or "",
                "multiple_trigger": False, "is_default": True,
            })

        # op == "status"
        wid = int(_need(workflow_id, "workflow_id", op))
        st = (status or "").strip().lower()
        if st not in _STATUSES:
            raise _err("`status` must be active | paused | draft.")
        return await _api("PUT", f"/workflow/{wid}/status", {"status": st})

    # --- Entry points (triggers) -------------------------------------------
    @mcp.tool()
    async def brevoauto_trigger(ctx: Context, op: Literal[_TRIGGER_OPS],
                                workflow_id: int,
                                trigger_point_id: Optional[int] = None,
                                trigger_name: Optional[str] = None,
                                internal_action_id: Optional[int] = None,
                                event_name: Optional[str] = None,
                                config: Optional[dict] = None,
                                source: str = "contacts") -> dict:
        """An entry point (trigger) of a scenario — add it, configure it, delete
        it. ⚠️ All THREE ops WRITE: `op` is required, it has no
        default.

        `op`:
        - **"add"**: adds an entry point (trigger) to a scenario. Returns
          `{start_point_id}`. `trigger_name`/`internal_action_id`/`source` come from
          `brevoauto_automation(op="catalog")` (e.g. segment =
          `contact_match_one_segment`, id 19, source `contacts`). The fine-grained condition is
          then set via `op="configure"`.
        - **"configure"**: configures an already-added entry point. `config` =
          specific settings that get merged (e.g. segment trigger:
          `config={"segment_id":1,"segment_name":"Segment A","is_bulk":True,
          "schedule":{"interval":"daily","schedule_time":"14:00",
          "timezone":"Europe/Paris"}}`). Returns `{status}`.
        - **"delete"**: deletes a trigger from a scenario. Returns `{status}`.

        Args:
            op: add | configure | delete (required — all write).
            workflow_id: the target scenario id (required for all three ops).
            trigger_point_id: op="configure"/"delete" — the entry point id
                (returned by op="add" as `start_point_id`, or read from
                `brevoauto_automation(op="get")`).
            trigger_name: op="add" — trigger name in the catalog (e.g.
                `contact_match_one_segment`).
            internal_action_id: op="add"/"configure" — catalog id of the trigger
                (e.g. 19).
            event_name: op="configure" — name of the configured event.
            config: op="configure" — settings block, merged into the request
                body.
            source: catalog source (`contacts` by default, e.g. `messaging`).
        """
        if op not in _TRIGGER_OPS:
            raise _err(_TRIGGER_OPS_ERROR)
        wid = int(workflow_id)

        if op == "add":
            return await _api("POST", f"/workflow/{wid}/trigger?platform=web", {
                "trigger_name": _need(trigger_name, "trigger_name", op),
                "multiple_entry": False,
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
                "source": source,
            })

        if op == "configure":
            body: dict = {
                "trigger_point_id": int(
                    _need(trigger_point_id, "trigger_point_id", op)),
                "workflow_id": wid,
                "trigger_point_type": "start_workflow",
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
                "source": source,
                "event_name": _need(event_name, "event_name", op),
            }
            body.update(_need(config, "config", op))
            return await _api("PUT", "/workflow/update/trigger", body)

        # op == "delete"
        return await _api("DELETE", "/workflow/trigger", {
            "trigger_point_id": int(_need(trigger_point_id, "trigger_point_id", op)),
            "workflow_id": wid})

    # --- Steps --------------------------------------------------------------
    @mcp.tool()
    async def brevoauto_step(ctx: Context, op: Literal[_STEP_OPS],
                             workflow_id: int,
                             step_id: Optional[int] = None,
                             step_type: Optional[str] = None,
                             step_name: Optional[str] = None,
                             internal_action_id: Optional[int] = None,
                             config: Optional[dict] = None,
                             is_condition: bool = False,
                             prev: Optional[int] = None,
                             next: int = 0,
                             condition_node: Optional[str] = None,
                             next_steps: Optional[list] = None,
                             source: Optional[str] = None) -> dict:
        """A step (action or condition) of a scenario — add it, configure it,
        delete it. ⚠️ All THREE ops WRITE: `op` is required, it has no
        default.

        `op`:
        - **"add"**: adds a step (action or condition) and returns `{step_id}`.
          Wiring: `prev` = id of the previous node; to attach UNDER a condition,
          `prev` = id of the condition node + `condition_node` = "0" (yes) / "1" (no);
          `is_condition=True` for a branch node (e.g.
          `if_else_bool_segmentation`, id 18). Creates the node WITHOUT its config
          (→ `op="configure"`).
        - **"configure"**: configures an already-created step (the write that carries the
          real data). `config` = the settings block, under a key named
          `step_name`. Examples:
          - **wait**: `step_name="wait_until"`, id 21,
            `config={"wait_for":[{"unit":"Hours","delay":"2"}]}`;
          - **email**: `step_name="send_email"`, id 1, `source="messaging"`,
            `config={"template_id":<existing id>,"subject":"…","from_name":"…",
            "from_email":"…","preview_text":"…"}`;
          - **condition**: `step_name="if_else_bool_segmentation"`, id 18,
            `is_condition=True`, `config={"branches":[{"fe_query":"<DSL json string>"},
            {"is_last_branch":True}]}` + **`next_steps=[<yes-branch step>,<no-branch
            step>]`** (wiring of the outputs).
          The `send_email` references an **existing** `template_id` (template creation
          = separate API, not exposed).
        - **"delete"**: deletes a step from a scenario. Returns `{status}`.

        Args:
            op: add | configure | delete (required — all write).
            workflow_id: the target scenario id (required for all three ops).
            step_id: op="configure"/"delete" — the step id (returned by op="add").
            step_type: op="add" — node type (`type` on the API side, e.g. `send_email`).
            step_name: op="configure" — name of the setting, AND the key under which
                `config` is placed in the body (e.g. `wait_until`, `send_email`).
            internal_action_id: op="add"/"configure" — catalog id of the action
                (e.g. 21 for wait, 1 for email, 18 for condition).
            config: op="configure" — the settings block (see examples above).
            is_condition: op="add" — branch node; op="configure" — only sent
                if True.
            prev: op="add" — id of the previous node (None = start of the graph).
            next: op="add" — id of the next node (0 = none).
            condition_node: op="add" — branch of the condition parent, "0" (yes) /
                "1" (no). Only sent if provided.
            next_steps: op="configure" — wiring of a condition node's outputs
                (`[<yes branch>, <no branch>]`). Only sent if provided.
            source: catalog source. ⚠️ Asymmetry kept from the original
                surface: op="add" ALWAYS sends it (default `contacts` if omitted),
                op="configure" ONLY sends it if provided (e.g. `messaging` for
                a `send_email`).
        """
        if op not in _STEP_OPS:
            raise _err(_STEP_OPS_ERROR)
        wid = int(workflow_id)

        if op == "add":
            body: dict = {
                "next": int(next), "prev": (int(prev) if prev is not None else None),
                "type": _need(step_type, "step_type", op),
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
                "is_condition": bool(is_condition),
                # `source` is optional in the merged signature (op="configure"
                # only sends it if provided): here the historical default
                # `contacts` is restored, the API always expects it.
                "source": source or "contacts",
            }
            if condition_node is not None:
                body["condition_node"] = str(condition_node)
            return await _api("POST", f"/workflow/{wid}/step?platform=web", body)

        if op == "configure":
            nom = str(_need(step_name, "step_name", op))
            body = {
                "step_id": int(_need(step_id, "step_id", op)),
                "step_name": nom, "step_type": "",
                nom: _need(config, "config", op), "workflowId": wid,
                "internal_action_id": int(
                    _need(internal_action_id, "internal_action_id", op)),
            }
            if is_condition:
                body["is_condition"] = True
            if source is not None:
                body["source"] = source
            if next_steps is not None:
                body["next_steps"] = next_steps
            return await _api("PUT", f"/workflow/{wid}/step", body)

        # op == "delete"
        return await _api("DELETE", f"/workflow/{wid}/step",
                          {"step_id": int(_need(step_id, "step_id", op))})
