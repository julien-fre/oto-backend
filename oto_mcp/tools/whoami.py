"""Whoami — the identity under which Claude acts when it calls the tools.

`oto_whoami()` answers the question « for whom / in what context am I
acting? »: the **account** (Logto sub + email + platform role) crossed with the
**active org** and the possible **active group** — exactly what governs
credential resolution and the data scope (see the « MCP identity » badge of the
dashboard). Read-only, best-effort (never an exception on a DB hiccup).

Spine: loaded explicitly in `register_all`, outside the activation gate, always
visible (`PROTECTED_TOOLS`). No external dependency.
"""
from __future__ import annotations

import logging
import os

from fastmcp import Context, FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, db, org_store, session_org, tenant_vault
from ..auth.hooks import current_user_sub_from_token
from .. import config

logger = logging.getLogger(__name__)

# ⚠️ NOT a module constant: the address depends on the account's TENANT, hence on the
# call. Freezing it at load time would serve ours to everyone — including to a
# partner's users, to whom it offers a product that is not theirs.


def _require_sub() -> str:
    # An identity failure BUBBLES UP (the seam logs it with its reason, #464): only
    # a call truly without a token is « unauthenticated ».
    sub = current_user_sub_from_token()
    if not sub:
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message="Auth required — these tools only work on the authenticated HTTP transport.",
        ))
    return sub


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def oto_whoami(ctx: Context) -> dict:
        """Current MCP identity: under which account and in which org/group you act.

        Call it when you need to know FOR WHOM you are working, or before a
        sensitive action (CRM write, message send, credit spend) to
        confirm the context. It is this pair **account × active org × active group**
        that determines which API keys are resolved and which data you access.

        Returns: `account` (sub, email, name, platform role), `tenant` (the slug of the
        partner your account depends on, if it has one — `None` for an ordinary oto
        account; the tenant is ABOVE your org, not below it: it is the
        top-level account at the provider, called « hébergeur » in the public
        docs), `org` (active org —
        id, name, role; you are ALWAYS in an org), `group` (possible active group),
        `connectors` (summary of the configured connectors
        — including `platform_quotas`, the day's quota `{used, limit,
        remaining}` of the platform connectors with a capped quota: look at it before
        a batch of calls that spend, to decide without discovering the limit in the
        middle of the batch), and a readable `summary`. Read-only.

        To act under another org/team/project: pass the token `_org=` /
        `_group=` / `_project=` directly on each work call (no session
        state, ADR 0038) — `oto_whoami(org=X)` shows the resulting context.
        The DEFAULT (home) org/team can only be changed in the dashboard —
        the agent never mutates the default.
        """
        sub = _require_sub()

        # Slug of the tenant (partner) this account depends on, `None` for an ordinary
        # oto account — same resolution as the vault key `tenant_key`
        # (`instances_tenant.py`).
        #
        # ⚠️ NO fail-open here, unlike the DB blocks that follow, and it is the served
        # description that requires it: it gives `None` for a FACT (« ordinary oto
        # account »). Swallowing the failure would render that fact on a resolution that
        # did not happen — a hosted account would be told it is not, and the agent
        # would have no way to tell the difference. `rung_tenant` does NO I/O anyway
        # (prefix classification in the process registry): there is no
        # « hiccup » to absorb, only a broken registry, which must be seen.
        tenant = tenant_vault.rung_tenant(sub)

        user = {}
        try:
            user = db.get_user(sub) or {}
        except Exception as e:
            logger.warning("whoami: get_user failed: %s", e)
        try:
            role = access.get_user_role(sub)
        # noqa: SILENT — role unreadable ⇒ not displayed, never guessed
        except Exception:
            role = None

        # EFFECTIVE org under which you act (ADR 0038) = call token ?? home.
        # `scope`='call' = org pinned by THIS call's token (org=/project=/group=);
        # 'home' = your home org (default of any call without a token). 0/None = personal.
        org_block = None
        active_org = None
        try:
            active_org = access.current_org(sub)
            has_call_pin = session_org.current_call_org() is not None
            if active_org is not None:
                o = org_store.get_org(active_org)
                org_block = {
                    "id": active_org,
                    "name": o["name"] if o else None,
                    "role": org_store.get_org_role(active_org, sub),
                    "scope": "call" if has_call_pin else "home",
                    # The org's mandatory MFA (the 2nd factor is imposed at members'
                    # login, enforced by Logto via the mirror org — see mfa_mirror).
                    "require_mfa": org_store.get_org_mfa(active_org)["require_mfa"],
                }
        except Exception as e:
            logger.warning("whoami: org lookup failed: %s", e)

        # Active group (sub-tier ADR 0012) — invariant: belongs to the active org.
        group_block = None
        try:
            from .. import group_store, roles
            active_group = access.current_group(sub)
            if active_group is not None:
                g = group_store.get_group(active_group)
                group_block = {
                    "id": active_group,
                    "name": g["name"] if g else None,
                    "role": roles.effective_group_role(sub, active_group),
                }
        except Exception as e:
            logger.warning("whoami: group lookup failed: %s", e)

        # The call's project (token project= — the session wristband is removed, ADR 0038 B3b).
        project_block = None
        try:
            active_project = access.current_project()
            if active_project is not None:
                p = db.get_project_by_id(active_project)
                if p is not None:
                    project_block = {"id": active_project, "name": p.get("name")}
        except Exception as e:
            logger.warning("whoami: project lookup failed: %s", e)

        # Configured connectors (summary, not the key details). `platform_quotas`
        # reuses the computation already done by `status_for` (no extra step):
        # for a connector in platform mode whose day quota is CAPPED
        # (e.g. apollo — see `access.platform_quota_hint`), looking here BEFORE a
        # batch of calls that spend avoids discovering the limit in the middle of a
        # batch (oto-backend#710). `over_quota` stays listed — hiding the connector
        # once exhausted would say « not configured » to someone who only has that exhausted.
        configured: list[str] = []
        platform_ready: list[str] = []
        platform_quotas: dict[str, dict] = {}
        try:
            providers = access.status_for(sub).get("providers", {})
            for name, st in sorted(providers.items()):
                mode = st.get("mode")
                if mode in ("user", "group", "org"):
                    configured.append(name)
                elif mode in ("platform", "over_quota"):
                    platform_ready.append(name)
                    limit = st.get("quota_daily")
                    if limit:
                        used = st.get("quota_used_today") or 0
                        platform_quotas[name] = {
                            "used": used, "limit": limit,
                            "remaining": max(0, limit - used),
                        }
        except Exception as e:
            logger.warning("whoami: status_for failed: %s", e)

        # ⚠️ No more `knowledge` field (removed on 10/09/2026 with the verb `oto_kb`): it
        # returned the id of the former « knowledge base » project, and an agent that reads
        # « your KB is project N » writes to it — a recruitment by the RESPONSE, the very
        # defect that got the verb removed. That project remains an ordinary project.

        who = user.get("name") or user.get("email") or sub
        if org_block:
            scope = f"org « {org_block['name']} » (role {org_block['role']})"
            if group_block:
                scope += f", group « {group_block['name']} »"
        else:
            scope = "personal space (no active org)"
        if org_block and org_block["scope"] == "call":
            scope += " — pinned by THIS call's token (org=/project=/group=)"
        if project_block:
            scope += f" — active project « {project_block['name']} »"
        summary = f"You are acting for {who} in {scope}."

        return {
            "account": {
                "sub": sub,
                "email": user.get("email"),
                "name": user.get("name"),
                "role": role,
            },
            "tenant": tenant,
            "org": org_block,
            "group": group_block,
            "project": project_block,
            "connectors": {
                "configured": configured,
                "platform_available": platform_ready,
                # {name: {used, limit, remaining}} for only the platform connectors
                # with a CAPPED quota today — absent otherwise (unlimited
                # quota, or org on an `unmetered` plan, ADR 0043).
                "platform_quotas": platform_quotas,
            },
            "summary": summary,
            "dashboard_url": config.dashboard_url_for(sub),
        }
