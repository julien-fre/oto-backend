"""HubSpot — the one-click "Connect HubSpot" (OAuth), next to the pasted key.

**Why.** The pasted key (a service key, or a legacy private app's token) makes the
customer tick about fifteen scopes by hand, and they get them wrong: most of
`connectors/docs/hubspot.md` and all of `tools/hubspot_scopes.py` exist to recover
from a missing ticket, list or pipeline scope. Here **oto asks for the scopes** in the
authorization URL — the same reason Zoho gained its server-based mode.

**The two shapes live on the SAME vault row** (connector `hubspot`, `secret_kind
api_key`, one secret):

- pasted: the secret is the Bearer token itself, `meta` carries nothing of ours;
- OAuth (this module): the secret is the **refresh token**, `meta.auth = "oauth"`
  with the portal (`hub_id`, `hub_domain`), who consented (`user`) and the granted
  `scopes`. Pasting a key over it rewrites `meta` (the upsert replaces it), so the row
  goes back to the pasted shape without anything to clean.

`bearer()` is the ONE way the HubSpot tools get their token: the pasted key as is, or
an access token minted from the refresh token (30 min, cached until a minute before
it expires). `HubSpotClient` (oto-core) still receives a plain Bearer — it does not
know which shape it is.

**Where the app comes from.** One HubSpot app published by the instance (Developer
Platform project, `platformVersion 2026.09`): its `client_id` / `client_secret` are
PLATFORM `connector_settings`, set by the operator — the `meta_ads` pattern, not
the tenant (ADR 0052). Not set ⟹ `app_ready` is false and the card keeps the form
alone, as before.

**Endpoints are DATED** (`/oauth/2026-09/…`): HubSpot refuses new Marketplace
listings on the `/oauth/v1` ones. Refresh-token rotation is not documented: whatever
`refresh_token` a refresh returns is written back, conditionally (`_store_rotation`).
Never refreshed inside the callback — the Salesforce lesson (31/07): a probe that
consumes the token it verifies, on a path that cannot write the new one back, kills it.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import requests

from .. import credentials_store
from ..connectors import flow as connector_flow
from . import flow as oauth_flow

logger = logging.getLogger(__name__)

CONNECTOR = "hubspot"
AUTH_OAUTH = "oauth"   # `meta.auth` of a row this module wrote

_AUD = "hubspot"
CALLBACK_PATH = "/api/hubspot/oauth/callback"

_API = "https://api.hubapi.com"
OAUTH_VERSION = "2026-09"
AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = f"{_API}/oauth/{OAUTH_VERSION}/token"
INTROSPECT_URL = f"{_API}/oauth/{OAUTH_VERSION}/token/introspect"

_TIMEOUT = 20

# --- scopes -------------------------------------------------------------------
#
# Derived from `tools/hubspot_scopes.SCOPES`, the reference the probe and the refusals
# already read: the consent asks for what the tools need, by the same names.
# REQUIRED = a portal that cannot grant one cannot install. OPTIONAL (`optional_scope`)
# = HubSpot drops it silently where the portal lacks the tool: tickets (Service Hub
# objects, and a legacy `tickets` scope HubSpot is migrating away from) and the events
# API (`business-intelligence`, Professional/Enterprise). ⚠️ This list must be the one
# ticked in the app's `app-hsmeta.json` (`docs/hubspot-app/app-hsmeta.json`, held equal
# by `test_hubspot_oauth.py`): a required scope the app does not declare breaks the
# authorization page, and changing a REQUIRED scope after the first install makes every
# portal reconnect.


def _from(families: tuple[str, ...], *, write: bool) -> tuple[str, ...]:
    from ..tools.hubspot_scopes import SCOPES
    out: list[str] = []
    for fam in families:
        read, wr = SCOPES[fam]
        for s in (read[0],) + ((wr[0],) if write and wr else ()):
            if s not in out:
                out.append(s)
    return tuple(out)


REQUIRED_SCOPES: tuple[str, ...] = (
    *_from(("contacts", "companies", "deals", "lists"), write=True),
    *_from(("owners",), write=False),
    "crm.schemas.contacts.read", "crm.schemas.companies.read", "crm.schemas.deals.read",
)
OPTIONAL_SCOPES: tuple[str, ...] = (
    "crm.objects.tickets.read", "crm.objects.tickets.write", "crm.schemas.tickets.read",
    "business-intelligence",
)


class HubSpotOAuthError(RuntimeError):
    """Acquisition or renewal failure — message WITHOUT secrets (incident #284)."""


class HubSpotReauthRequired(HubSpotOAuthError):
    """HubSpot no longer accepts this connection's refresh token: reconnect."""


# --- the app (client_id / client_secret) -------------------------------------

_SETTINGS = ("client_id", "client_secret")
_COMMAND = ('oto_admin_connector_setting(op="set", connector="hubspot", '
            'key="<client_id|client_secret>", value="<value>")')


def _settings() -> dict:
    """COLD read (`meta_ads._reglages`): start of a flow, return of a consent, and a
    token renewal — once per half hour and per row, never per tool call."""
    from ..db import connector_settings as store

    return {r["key"]: (r["value"] or "").strip()
            for r in store.list_connector_settings()
            if r["scope_type"] == "platform" and r["connector"] == CONNECTOR
            and r["key"] in _SETTINGS}


def missing_settings() -> list[str]:
    poses = _settings()
    return [k for k in _SETTINGS if not poses.get(k)]


def app() -> tuple[str, str]:
    """`(client_id, client_secret)` of the instance's HubSpot app, or a refusal that
    NAMES what is missing and who sets it."""
    poses = _settings()
    missing = [k for k in _SETTINGS if not poses.get(k)]
    if missing:
        raise HubSpotOAuthError(
            f"The HubSpot app is not configured on this instance: missing "
            f"{', '.join(missing)}. Nothing to do on your account — the instance "
            f"operator sets them with {_COMMAND}. Meanwhile, a service key pasted on "
            "the connector card works.")
    return poses["client_id"], poses["client_secret"]


def app_ready(sub: str) -> bool:
    del sub
    try:
        return not missing_settings()
    except Exception as e:  # noqa: BLE001 — a settings outage must not break /api/me
        logger.warning("hubspot: app settings unreadable (%s)", type(e).__name__)
        return False


# --- the signed state --------------------------------------------------------

def make_state(sub: str, org_id: int, scope: str, group_id: Optional[int] = None,
               return_app: str = "") -> str:
    """Bound to the `hubspot` audience and single-use (`jti`, `consume_state`): the
    callback WRITES a credential, a replayed state must not write it twice."""
    payload = {"sub": sub, "org": org_id, "scope": scope, "app": return_app,
               "jti": oauth_flow.new_jti()}
    if group_id is not None:
        payload["group"] = group_id
    return oauth_flow.sign_state(_AUD, payload)


def read_state(state: Optional[str]) -> Optional[dict]:
    """The payload if valid, unexpired and issued FOR this flow — else None. A
    `group` scope without its team is refused (we do not guess where to write)."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    if (not isinstance(data.get("sub"), str) or not isinstance(data.get("org"), int)
            or data.get("scope") not in ("member", "org", "group")):
        return None
    if data["scope"] == "group" and not isinstance(data.get("group"), int):
        return None
    if not isinstance(data.get("app"), str):
        data["app"] = ""
    return data


def can_write(sub: str, org_id: int, scope: str, group_id: Optional[int]) -> bool:
    """The right to write at `scope` — checked at start AND again at the callback
    (the state lives 10 min, a role can be lost in between: ADR 0038)."""
    from .. import roles
    if scope == "org":
        return roles.is_org_admin(sub, org_id)
    if scope == "group":
        return group_id is not None and roles.can_admin_group(sub, group_id)
    return True


# --- flow start --------------------------------------------------------------

def build_auth_url(sub: str, scope: str = "member", return_app: str = "") -> str:
    """HubSpot's consent URL. Raises `ValueError` (bad scope), `PermissionError`
    (not admin of the requested level) or `HubSpotOAuthError` (app not configured)."""
    from .. import access

    if scope not in ("member", "org", "group"):
        raise ValueError(f"invalid scope: {scope!r} (expected 'member', 'org' or 'group')")
    org_id = access.current_org(sub)
    if org_id is None:
        raise HubSpotOAuthError(
            "No org in context — cannot scope the HubSpot connection. Sign in again "
            "and retry.")
    group_id = access.current_group(sub) if scope == "group" else None
    if scope == "group" and group_id is None:
        raise HubSpotOAuthError(
            "No active team — select one to connect HubSpot for your team.")
    if not can_write(sub, org_id, scope, group_id):
        who = "an org admin" if scope == "org" else "a team lead"
        raise PermissionError(
            f"Only {who} can connect HubSpot on behalf of the whole "
            f"{'org' if scope == 'org' else 'team'}.")
    client_id, _ = app()
    params = {
        "client_id": client_id,
        "redirect_uri": oauth_flow.redirect_uri(CALLBACK_PATH),
        "scope": " ".join(REQUIRED_SCOPES),
        "optional_scope": " ".join(OPTIONAL_SCOPES),
        "state": make_state(sub, org_id, scope, group_id,
                            oauth_flow.resolve_return_app(return_app)),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _start_flow(ctx, values: dict) -> "connector_flow.FlowStart":
    from ..capabilities._types import AuthzDenied

    values = values or {}
    scope = values.get("scope") or "member"
    try:
        url = build_auth_url(ctx.sub, scope, values.get("app") or "")
    except ValueError as e:
        raise AuthzDenied(400, "invalid_scope_param", str(e))
    except PermissionError as e:
        raise AuthzDenied(403, "org_admin_required", str(e))
    except HubSpotOAuthError as e:
        raise AuthzDenied(503, "oauth_misconfigured", str(e))
    return connector_flow.FlowStart(auth_url=url, details={"scope": scope})


# The scope (member | org | group) is DEDUCED from the surface and passed by the caller
# (`values["scope"]`), like Salesforce: no "for whom?" parameter.
connector_flow.declare(
    CONNECTOR,
    start=_start_flow,
    label="Connect HubSpot",
    callback_path=CALLBACK_PATH,
    app_ready=app_ready,
)


# --- the return: exchange, identify, store ----------------------------------

def _post(url: str, data: dict) -> requests.Response:
    """Form-encoded body, never a query string: secrets stay out of URLs (#284)."""
    return requests.post(url, data=data, timeout=_TIMEOUT)


def exchange_code(code: str) -> dict:
    client_id, client_secret = app()
    try:
        return oauth_flow.exchange_code(
            TOKEN_URL, code=code, client_id=client_id, client_secret=client_secret,
            redirect=oauth_flow.redirect_uri(CALLBACK_PATH))
    except oauth_flow.OAuthFlowError as e:
        raise HubSpotOAuthError(str(e)) from e


def introspect(token: str, hint: str = "refresh_token") -> dict:
    """What HubSpot says of a token: `hub_id`, `hub_domain`, `user`, `scopes`…
    Introspecting the REFRESH token proves the grant is alive and names the portal
    WITHOUT minting an access token — nothing rotates."""
    client_id, client_secret = app()
    r = _post(INTROSPECT_URL, {"client_id": client_id, "client_secret": client_secret,
                               "token": token, "token_type_hint": hint})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or not isinstance(body, dict):
        msg = body.get("message") if isinstance(body, dict) else None
        raise HubSpotOAuthError(
            f"HubSpot refused the token introspection (HTTP {r.status_code})"
            + (f": {str(msg)[:200]}" if msg else "."))
    if body.get("active") is False:
        raise HubSpotReauthRequired(
            "HubSpot says this connection is no longer active (uninstalled or "
            "revoked): reconnect HubSpot from the connector card.")
    return body


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connection_meta(info: dict, sub: str) -> dict:
    scopes = info.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return {"auth": AUTH_OAUTH,
            "hub_id": info.get("hub_id"),
            "hub_domain": info.get("hub_domain"),
            "user": info.get("user"),
            "scopes": " ".join(sorted(str(s) for s in scopes)),
            "installed_by": sub,
            "connected_at": _iso_now()}


def persist(sub: str, org_id: int, scope: str, group_id: Optional[int],
            tokens: dict) -> dict:
    """Synchronous (HTTP + DB): the callback runs it off the loop. Identifies the
    portal (introspection), then writes the row at the requested level. Returns what
    the log may say — never a token."""
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise HubSpotOAuthError("HubSpot did not return a refresh token: restart the "
                                "connection.")
    info = introspect(refresh)
    meta = connection_meta(info, sub)
    entity_type, entity_id = credentials_store.entity_for_scope(
        scope, org_id, sub, group_id)
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR, refresh,
                                     set_by=sub, meta=meta)
    # Seed the cache with the access token the exchange already gave: the first tool
    # call does not pay a refresh.
    if tokens.get("access_token") and tokens.get("expires_in"):
        _keep((entity_type, entity_id, ""), refresh, tokens["access_token"],
              int(tokens["expires_in"]))
    return {"hub_id": meta["hub_id"], "scope": scope}


# --- the Bearer the tools use ------------------------------------------------

_TOKENS: dict[str, tuple[str, float]] = {}
_LOCK = threading.Lock()


def _key(row: tuple, refresh_token: str) -> str:
    """The row and its refresh token, hashed — a reconnection changes the key."""
    return hashlib.sha256("|".join((*row, refresh_token)).encode()).hexdigest()


def _keep(row: tuple, refresh_token: str, access_token: str, expires_in: int) -> None:
    with _LOCK:
        _TOKENS[_key(row, refresh_token)] = (access_token, time.time() + expires_in)


def _store_rotation(row: tuple, read: str, new: str) -> None:
    """The refresh token rotated: write the new one on THIS row, its `meta` passed back
    (the upsert would wipe it). Conditional: a concurrent renewal that already wrote a
    newer value keeps it."""
    entity_type, entity_id, account = row
    current = credentials_store.get_credential_with_meta(
        entity_type, entity_id, CONNECTOR, account=account)
    if not current or current.get("secret") != read:
        return
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR, new,
                                     set_by=current.get("set_by"),
                                     meta=current.get("meta") or {}, account=account)


def refresh(refresh_token: str) -> dict:
    """`refresh_token` → `{access_token, expires_in, refresh_token?}`. A dead grant
    (`invalid_grant`) raises `HubSpotReauthRequired`; a configuration fault (wrong
    client secret…) raises `HubSpotOAuthError` and destroys nothing (`grant_is_dead`)."""
    client_id, client_secret = app()
    r = _post(TOKEN_URL, {"grant_type": "refresh_token", "client_id": client_id,
                          "client_secret": client_secret, "refresh_token": refresh_token})
    if r.status_code < 400:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if body.get("access_token"):
            return body
    text = (r.text or "")[:300]
    if oauth_flow.grant_is_dead(r.status_code, text):
        raise HubSpotReauthRequired(
            "HubSpot no longer accepts this connection (the app was uninstalled from "
            "the portal, or the authorization was revoked). Reconnect HubSpot from the "
            "connector card. Nothing was done.")
    raise HubSpotOAuthError(
        f"HubSpot refused to renew the connection's token (HTTP {r.status_code}). "
        "This is usually the instance's HubSpot app configuration, not your account.")


def access_token_for(rc) -> str:
    """A valid access token for the resolved OAuth row, renewed when it expires in
    less than a minute. A dead grant marks THIS row "to reconnect" on its card and
    raises."""
    from ..connectors import health as connector_health

    row = (rc.entity_type or "", rc.entity_id or "", rc.account or "")
    refresh_token = rc.key
    with _LOCK:
        cached = _TOKENS.get(_key(row, refresh_token))
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    try:
        grant = refresh(refresh_token)
    except HubSpotReauthRequired as e:
        connector_health.mark_rejected(rc.entity_type, rc.entity_id, CONNECTOR,
                                       rc.account or "", str(e),
                                       verdict=connector_health.UNAUTHORIZED)
        raise
    new_refresh = grant.get("refresh_token") or refresh_token
    if new_refresh != refresh_token:
        _store_rotation(row, refresh_token, new_refresh)
    _keep(row, new_refresh, grant["access_token"], int(grant.get("expires_in") or 1800))
    return grant["access_token"]


def is_oauth(rc) -> bool:
    return (rc.config or {}).get("auth") == AUTH_OAUTH


#: Which shape served the current call — read by the scope refusal
#: (`hubspot_scopes._scope_to_tick`): a missing scope is a checkbox on a pasted key,
#: but a RECONNECTION on an OAuth connection (the consent asks for today's scopes).
_SERVED_BY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "hubspot_served_by", default="")


def served_by_oauth() -> bool:
    return _SERVED_BY.get() == AUTH_OAUTH


def bearer(account: Optional[str] = None) -> str:
    """The Bearer for the current call — what every HubSpot tool hands `HubSpotClient`.
    Same resolution as `access.resolve_api_key("hubspot")` (member > team > org, byo);
    an OAuth row is turned into an access token."""
    from .. import access

    rc = access.resolve_credential(CONNECTOR, want="auto", account=account)
    if not is_oauth(rc):
        _SERVED_BY.set("key")
        return rc.key
    _SERVED_BY.set(AUTH_OAUTH)
    return access_token_for(rc)

