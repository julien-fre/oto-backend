"""Capability "publisher's OAuth app" — setting oto's app at a provider.

Why it exists: on a consent connector (Zoho & co), the user had to
create an OAuth app THEMSELVES at the provider ("Self Client" mode) before being able to
connect anything — a developer console to get through, scopes to tick by
hand, and three incidents of badly chosen scopes (#190, #202, Desk articles-only). With
a publisher app set here, only the gesture that matters remains: consent.

**What is set is not an access key.** `client_id`/`client_secret` identify
the PUBLISHER that asks for access; the data only opens with the
`refresh_token` born of the user's consent, stored in THEIR name. The invariant that
guarantees this separation is documented in `credentials_store` §publisher app.

**REST only, super admin**: the MCP face is deliberately absent — a raw secret
as a tool argument would transit through the model's context (repo rule, cf. the setting
of org secrets).

**The TENANT face is elsewhere** (`tenant_apps`, 23/09/2026): a tenant admin sets
THEIR app under THEIR slug from `/api/admin/tenants/{slug}/apps/{connector}` — here, the key
is free (zoho region, `tenant:<slug>` for a tenant's app) and the gesture remains the
operator's.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import credentials_store
from ..connectors import flow as connector_flow
from ._authz import SUPER_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


def _guard(connector: str) -> str:
    """The connector must have a consent flow — setting a publisher app on
    an API-key connector would make no sense (nothing would consume it)."""
    name = (connector or "").strip()
    if not connector_flow.supports(name):
        raise AuthzDenied(400, "no_consent_flow",
                          f"\"{name}\" has no consent-based connection flow: "
                          "a publisher app would be of no use there.")
    return name


class ListInput(BaseModel):
    connector: Optional[str] = None


class SetInput(BaseModel):
    connector: str
    # The region is part of the KEY, not a setting: an OAuth app is registered
    # in its data center and rejected by the others.
    data_center: str
    client_id: str
    client_secret: str


class DeleteInput(BaseModel):
    connector: str
    data_center: str


def _list(ctx: ResolvedCtx, inp: ListInput) -> dict:  # noqa: ARG001
    return {"apps": credentials_store.list_editor_apps(inp.connector or None)}


def _set(ctx: ResolvedCtx, inp: SetInput) -> dict:
    name = _guard(inp.connector)
    try:
        credentials_store.set_editor_app(
            name, inp.data_center,
            {"client_id": inp.client_id.strip(), "client_secret": inp.client_secret.strip()},
            set_by=ctx.sub)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_editor_app", str(e))
    key = inp.data_center.strip().lower()
    return {"connector": name, "data_center": key,
            "callback_url": connector_flow.callback_url(name, host=_tenant_host(key))}


def _tenant_host(key: str) -> Optional[str]:
    """When the app's key designates a TENANT (`tenant:<slug>`,
    `credentials_store.tenant_app_key`), the callback the admin must register at the
    provider is the tenant's (cf. `google_oauth.app_for`) — giving them ours
    would make them declare a URL the flow will never send. A REGION key
    returns `None` (the instance's callback), even if a tenant bears the same name: a
    region is never read as a slug (review of #1063)."""
    from .. import tenancy
    slug = credentials_store.tenant_of_app_key(key)
    return tenancy.current().callback_host(slug) if slug else None


def _delete(ctx: ResolvedCtx, inp: DeleteInput) -> dict:  # noqa: ARG001
    if not credentials_store.clear_editor_app(inp.connector, inp.data_center):
        raise AuthzDenied(404, "unknown_editor_app",
                          "no publisher app for this connector and this region.")
    return {"ok": True, "connector": inp.connector,
            "data_center": inp.data_center.strip().lower()}


CAPABILITIES += [
    Capability(
        key="platform.editor_app.list", handler=_list, Input=ListInput,
        authz=SUPER_ADMIN, mcp=None,
        rest=RestBinding("GET", "/api/admin/editor-apps"),
        description="Publisher OAuth apps that are set (connector × region), without secrets.",
    ),
    Capability(
        key="platform.editor_app.set", handler=_set, Input=SetInput,
        authz=SUPER_ADMIN, mcp=None,
        rest=RestBinding("POST", "/api/admin/editor-apps"),
        description="Set/rotate oto's OAuth app for a connector and a region.",
    ),
    Capability(
        key="platform.editor_app.delete", handler=_delete, Input=DeleteInput,
        authz=SUPER_ADMIN, mcp=None,
        rest=RestBinding("DELETE", "/api/admin/editor-apps/{connector}/{data_center}"),
        description="Remove a connector's publisher OAuth app for a region.",
    ),
]
