"""Capability "Salesforce connection" — builds the consent link.

ADR 0042 §Convergence of surfaces: a platform verb is born a **capability**, not a
hand-written REST route. Modeled on `zoho_connect.py` (same shape, same reason) —
and like it, gains a **MCP face**: the agent can build the link and hand it to
the user, which is the useful gesture in conversation.

What REMAINS a hand-written route (`api/salesforce.py`): the **callback**.
Salesforce redirects the BROWSER there — with no auth header, with a 302 response — which
a capability contract (JSON + authz) cannot express. Declared as such in
`test_rest_modules_are_capabilities.py`.

Salesforce particularity (cf. `salesforce_oauth.py`): the OAuth client is
**per-customer** (each org creates its own Connected App), so `start` reads the
client_id/client_secret/login_url triplet ALREADY set on the connector card — it is a
prerequisite, not a platform constant.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ..auth import salesforce as salesforce_oauth
from ..connectors import flow as connector_flow
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx
from .registry import CAPABILITIES


class SalesforceConnectInput(BaseModel):
    op: Literal["start"] = "start"
    # Level at which to STORE the credential: the member (default), the whole org, or the active
    # team. The right is checked when the link is built AND re-checked on return
    # (the state lives 10 min — cf. the callback).
    scope: Optional[Literal["member", "org", "group"]] = "member"


def start_for(ctx: ResolvedCtx, scope: str,
              return_app: Optional[str] = None) -> connector_flow.FlowStart:
    """Consent URL to open, for the requested level. Shared with the generic
    flow (`connector_flow`, declared in tools/salesforce.py): one single way
    to start, two surfaces.

    `return_app`: only carried by the generic REST path (a front end's browser
    knows who it is); the MCP path below (`_start`) never passes it
    — a Claude agent has no browser to redirect."""
    try:
        auth_url = salesforce_oauth.build_auth_url(ctx.sub, scope or "member", return_app)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_scope_param", str(e))
    except PermissionError as e:
        raise AuthzDenied(403, "org_admin_required", str(e))
    except LookupError as e:
        raise AuthzDenied(400, "missing_credentials", str(e))
    except RuntimeError as e:
        raise AuthzDenied(400, "oauth_misconfigured", str(e))
    # The RETAINED tier is specific to Salesforce (other flows have none): it
    # goes down into `details`. It carries information — it is the effective scope, default
    # resolved — but no generic client needs to know it in order to open the URL.
    return connector_flow.FlowStart(auth_url=auth_url, details={"scope": scope or "member"})


def _start(ctx: ResolvedCtx, inp: SalesforceConnectInput) -> dict:
    return start_for(ctx, inp.scope or "member").as_dict()


CAPABILITIES += [
    Capability(
        key="me.salesforce_connect",
        handler=_start,
        Input=SalesforceConnectInput,
        authz=ORG_MEMBER,
        # ⚠️ Named under ITS connector, not under the cross-cutting prefix: the
        # per-connector gate resolves on the name's namespace, so `oto_…` put this verb
        # in the toolbox of ALL accounts, including those that do not have
        # salesforce. The old name is still served and callable until its removal date
        # (`deprecations.TOOLS`) — an org procedure still references it.
        mcp="salesforce_connect",
        # No NAMED REST face any more: the fixed path `/api/me/connectors/{name}/connect`
        # (capability `me.connector_connect`) now serves it, via the SAME `start_for`.
        # The MCP face stays — an agent knows the connector it is connecting.
        rest=None,
        description=(
            "Connect Salesforce. op='start' returns the consent URL to OPEN in a "
            "browser — on return, the refresh token is stored in the vault. "
            "Prerequisite: the Connected App's Consumer Key + Consumer Secret + Login "
            "URL must already be saved on the connector card (Salesforce's OAuth "
            "client is per-customer: each org creates its own Connected App). "
            "`scope`: member (default) | org | group — org/group require admin."),
    ),
]
