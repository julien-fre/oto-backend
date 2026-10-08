"""Capability "Zoho server-based connection" — start + available modes.

ADR 0042 §Convergence of surfaces: a platform verb is born a **capability**, not a
hand-written REST route. These two verbs were written as pure REST on
2026-07-28 (a pattern inherited from folk/google, predating the convergence) — they
are brought here, and gain a **MCP face** along the way: the agent can build the
consent link and hand it to the user, which is precisely the useful gesture
in conversation.

What REMAINS a hand-written route (`api/zoho.py`): the **callback**.
Zoho redirects the BROWSER there — with no auth header, with a 302 response — which
a capability contract (JSON + authz) cannot express. This is declared as
such in `test_rest_modules_are_capabilities.py`.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import access
from ..auth import zoho as zoho_oauth
from ..connectors import flow as connector_flow
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class ZohoConnectInput(BaseModel):
    op: Literal["start", "modes"] = "start"
    connector: str                      # zoho | zohodesk | zohoanalytics
    # Region of the Zoho account — REQUIRED for `start`: the OAuth app and the token are
    # tied to their data center, an `.eu` client on `accounts.zoho.com` is rejected
    # with an opaque `invalid_client`. Unguessable, hence explicit.
    data_center: Optional[str] = None


def _guard(inp: ZohoConnectInput) -> None:
    if not zoho_oauth.supports(inp.connector):
        raise AuthzDenied(400, "unknown_zoho_connector",
                          f"\"{inp.connector}\" is not a Zoho connector.")


def _modes(ctx: ResolvedCtx, inp: ZohoConnectInput) -> dict:
    """What the front end (or the agent) uses to decide what to display: does the connector support
    server-based, and is an app ALREADY available (mine, my team's,
    my org's or the platform's — usual cascade)?"""
    _guard(inp)
    return {
        "connector": inp.connector,
        "self_client": True,            # always available
        "server_based": True,
        "has_app": zoho_oauth.has_app(inp.connector, ctx.sub, inp.data_center or ""),
        "scopes": list(zoho_oauth.SCOPES[inp.connector]),
    }


def start_for(ctx: ResolvedCtx, connector: str, data_center: str,
              return_app: str = "") -> connector_flow.FlowStart:
    """Consent URL to open. The app (client_id/secret) comes from the VAULT — never
    from an env variable: the org that brings its own wins, otherwise we take the
    region's PUBLISHER app (`credentials_store` §publisher app), which gives everyone
    the "one click".

    Shared with the generic flow (`connector_flow`, declared in tools/zoho.py): there
    must be only ONE way to start a Zoho consent, otherwise the two
    surfaces diverge — exactly what the convergence seeks to avoid."""
    # The TARGET connector (zoho, zohodesk, zohoanalytics) — the MCP face is named
    # under `zoho`, so the call guard alone does not see which one is asked.
    from ..connectors.activation_gate import exiger_connectable_capacite
    exiger_connectable_capacite(connector, ctx.sub)
    try:
        dc = (data_center or "").lower()
        url = zoho_oauth.build_auth_url(
            ctx.sub, ctx.org_id or 0, connector, dc,
            app=zoho_oauth.app_fields(connector, ctx.sub, dc),
            return_app=return_app)
    except zoho_oauth.ZohoOAuthError as e:
        raise AuthzDenied(400, "zoho_oauth_unavailable", str(e))
    # The connector echo is SPECIFIC to Zoho (three connectors share this flow):
    # it goes down into `details`, the top level remaining common to all flows.
    return connector_flow.FlowStart(auth_url=url, details={"connector": connector})


def _start(ctx: ResolvedCtx, inp: ZohoConnectInput) -> dict:
    _guard(inp)
    return start_for(ctx, inp.connector, inp.data_center or "").as_dict()


def _dispatch(ctx: ResolvedCtx, inp: ZohoConnectInput) -> dict:
    return _modes(ctx, inp) if inp.op == "modes" else _start(ctx, inp)


class ZohoVerbInput(BaseModel):
    """Input of the REST faces — per-verb, hence WITHOUT `op` (the path carries it)."""
    connector: str
    data_center: Optional[str] = None


class AnalyticsOrgsInput(BaseModel):
    pass


def _analytics_orgs(ctx: ResolvedCtx, inp: AnalyticsOrgsInput) -> dict:  # noqa: ARG001
    """The Analytics organizations of the connected account, so the user can CHOOSE.

    Zoho Analytics requires an organization on every call, and an account often sees
    several (shared workspaces). Without this list, all that was left was to
    ask for an eleven-digit identifier — which nobody knows by heart and which
    has to be looked up in the Zoho interface. Here we return NAMES.

    When only one organization exists, it was already set at consent
    (`zoho_oauth._derived_fields`): this surface therefore only serves the ambiguous case,
    and confirms it (`current` = the one that is saved)."""
    try:
        fields = access.resolve_credential(
            "zohoanalytics", want="byo", sub=ctx.sub, emit_on_failure=False).fields or {}
    # noqa: SILENT — declared debt: vault error read as "no credential" (#424, verdict C)
    except Exception:  # noqa: BLE001
        fields = {}
    if not fields.get("refresh_token"):
        raise AuthzDenied(400, "zoho_analytics_not_connected",
                          "connect Zoho Analytics first — the list of "
                          "organizations comes from your account.")
    try:
        orgs = zoho_oauth.analytics_orgs(fields)
    except Exception as e:  # noqa: BLE001
        raise AuthzDenied(502, "zoho_analytics_orgs_failed", str(e))
    return {"orgs": orgs, "current": fields.get("org_id") or None}


# `platform.instructions` pattern (ADR 0042): ONE op-aware capability for MCP +
# per-verb capabilities for REST, same handlers. The REST faces are
# idiomatic (one path = one verb) and MCP keeps a consolidated surface
# (ADR 0047, one tool per business object).
class AnalyticsOrgs(BaseModel):
    """The Zoho Analytics organizations visible to the current credential, and the one
    that is pinned. `current` is None until someone has chosen — this is what
    triggers the question to the user rather than an arbitrary choice."""
    orgs: list[dict]
    current: Optional[object] = None


CAPABILITIES += [
    Capability(
        key="me.zoho_connect",
        handler=_dispatch,
        Input=ZohoConnectInput,
        authz=ORG_MEMBER,
        # ⚠️ Named under ITS connector, not under the cross-cutting prefix: the
        # per-connector gate resolves on the name's namespace, so `oto_…` put this verb
        # in the toolbox of ALL accounts, including those that do not have
        # zoho. The old name is still served and callable until its removal date
        # (`deprecations.TOOLS`) — an org procedure still references it.
        mcp="zoho_connect",
        rest=None,
        description=(
            "Zoho \"server-based\" connection (CRM / Desk / Analytics): op='modes' "
            "says whether an OAuth app is already available and which scopes oto will request; "
            "op='start' (with `data_center`: eu, com, in, au, jp, ca) returns the consent "
            "URL to OPEN in a browser — on return, the refresh token "
            "is stored in the vault. Prerequisite: client_id + client_secret of the Zoho app "
            "set on the connector card (or shared by the org)."),
    ),
    Capability(
        key="me.zoho_analytics_orgs",
        handler=_analytics_orgs,
        Input=AnalyticsOrgsInput,
        Output=AnalyticsOrgs,
        authz=ORG_MEMBER,
        mcp="zohoanalytics_orgs",
        rest=RestBinding("GET", "/api/me/connectors/zohoanalytics/orgs"),
        description=(
            "Zoho Analytics organizations visible to your account (id, name, role) + "
            "the one currently saved. Analytics requires an organization on "
            "every call and an account often sees several (shared "
            "workspaces): this is where you choose it, from names rather than an "
            "identifier."),
    ),
    # `me.zoho_connect.start` was REMOVED: it only existed to carry the REST face
    # `/api/zoho/oauth/start`, now served by the fixed path
    # `/api/me/connectors/{name}/connect` (capability `me.connector_connect`), via the
    # same `start_for`. Start keeps its MCP face on `me.zoho_connect` op=start.
    # `me.zoho_connect.modes` was REMOVED likewise: its only consumer was the dashboard's
    # named widget, deleted with the generalization. The op is still served by
    # `me.zoho_connect` (op='modes') on the MCP side — an agent preparing a connection has
    # good reasons to ask "is an app already available?". A REST surface
    # with no caller, on the other hand, is debt paid at every reading of the code.
]
