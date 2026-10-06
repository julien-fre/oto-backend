"""Call guard for a connector's activation — named refusal `connector_disabled`.

Session visibility hides the tools of a disabled connector, but it is
**fail-open** (display governance, ADR 0031) and `oto_call` crosses it by
construction (ADR 0036): measured on 24/09/2026 (oto-backend#1064), a tool of a
disabled connector — platform master OFF, or org override OFF — was SERVED by
`oto_call`, handler executed. This guard is the call-time control that was missing.

**A single passage point for both paths**: it plays once the call context
is set (axes `_org=`/`_group=`/`_project=`, the run's org), hence
against the org and team under which the target will RESOLVE — not the home org.
The context middleware (`CallContextMiddleware`, direct call) and `oto_call`
(which replays that context outside the chain) call it at the same place: right after
`run_org.pin_for_call`, right before the handler.

**Fail-closed**: an activation read that fails makes the call fail, it
does not let it through. Without a sub (local stdio), nothing is guarded: only the
multi-user surface is targeted.
Platform tools (`oto_*`, `data_*`, `run_*`…) have no connector in the
registry: activation never guards them.

**The SUSPENDED org** (`org_suspension`) goes through here too, and BEFORE activation:
it is the same seam (the org under which the call resolves, `_org` and run included), for
both paths. Named refusal `org_suspended`. It ALSO guards hand-written platform
tools (`org_suspension.outil_garde`): without that, `data_write` or
`run_start` kept going in a suspended org, directly as well as through `oto_call`.
Capabilities, for their part, are guarded in their adapter.
"""
from __future__ import annotations

from typing import Optional

from mcp.types import INVALID_PARAMS, ErrorData
from starlette.concurrency import run_in_threadpool

from .. import access, call_axes, org_suspension, providers
from ..mcp_errors import McpError
from ..tool_visibility import namespace_of
from . import activation

CODE = "connector_disabled"


def _refus_suspendue(org: Optional[int], **cible) -> Optional[ErrorData]:
    """The error to return if the org under which the call resolves is suspended."""
    if (texte := org_suspension.refus(org)):
        return ErrorData(code=INVALID_PARAMS, message=f"Refusal `{org_suspension.CODE}`: {texte}",
                         data={"code": org_suspension.CODE, "retryable": False,
                               "org_id": org, **cible})
    return None


def _refus_outil_plateforme(tool_name: str) -> Optional[ErrorData]:
    """Sync (threadpool): the suspension alone, for a tool without a connector."""
    sub = call_axes.current_user_sub_from_token()
    if not sub:
        return None
    return _refus_suspendue(access.current_org(sub), tool=tool_name)


def _refus(connector: str) -> Optional[ErrorData]:
    """Sync (threadpool): the error to return if `connector` is disabled for the org and
    team under which the call resolves, otherwise `None`. The identity is read here, not
    in the loop: canonicalizing it may touch the database (alias drain)."""
    sub = call_axes.current_user_sub_from_token()
    if not sub:
        return None
    org = access.current_org(sub)
    if (suspendue := _refus_suspendue(org, connector=connector)) is not None:
        return suspendue
    group = access.current_group(sub)
    cran = activation.cran_qui_coupe(connector, org, group)
    if cran is None:
        return None
    ou = f"organization {org}" if org is not None else "your account (no active organization)"
    if cran == "org":
        pourquoi = f"disabled for {ou} by an organization setting"
        geste = (f"an org admin enables it: oto_connector_activation(op='set', "
                 f"scope='org', org_id={org}, name='{connector}', enabled=true).")
    elif cran == "tenant":
        pourquoi = "disabled by your organization's host, for its entire offering"
        geste = ("only an admin of that host (the tenant) reopens it, from their "
                 "dashboard; no organization or team can.")
    elif cran == "group":
        pourquoi = f"disabled by your team {group}"
        geste = (f"a team lead removes the cut: oto_connector_activation("
                 f"op='clear', scope='group', group_id={group}, name='{connector}').")
    else:
        pourquoi = f"disabled by the platform for {ou}"
        geste = ("only a platform admin can open it: an organization cannot, "
                 "the platform ceiling is never relaxed.")
    return ErrorData(
        code=INVALID_PARAMS,
        message=(f"Refusal `{CODE}`: the connector `{connector}` is {pourquoi}. The call "
                 f"is served neither directly nor through oto_call. To enable it, {geste}"),
        data={"code": CODE, "retryable": False, "connector": connector,
              "org_id": org, "group_id": group, "scope": cran},
    )


async def require_active(tool_name: str) -> None:
    """Raises `connector_disabled` if the tool `tool_name` belongs to a connector
    disabled for the current call. To be called AFTER the call context is set."""
    con = providers.connector_for_namespace(namespace_of(tool_name))
    if con is None:
        if org_suspension.outil_garde(tool_name):
            refus = await run_in_threadpool(_refus_outil_plateforme, tool_name)
            if refus is not None:
                raise McpError(refus)
        return
    refus = await run_in_threadpool(_refus, con.name)
    if refus is not None:
        raise McpError(refus)
