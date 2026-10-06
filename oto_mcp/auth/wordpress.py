"""Acquiring the WordPress credential through the site's NATIVE authorization screen.

WordPress (≥ 5.6) serves `wp-admin/authorize-application.php`: the user,
logged into their wp-admin, clicks "Approve", and WordPress CREATES an application
password which it sends back to the `success_url` in the query string (`site_url`,
`user_login`, `password`). This is not OAuth — no code to exchange —
but the same shape of gesture: a button, a consent, a return. No
plugin to install, no password to copy.

This module carries the start (`start`) and the end (`finish`); the HTTP callback
lives in `api/wordpress.py`. The credential stored is EXACTLY the form's
(same three fields, same validation, same account guard), under the
account named by the site's host — one account = one site.

Guards specific to this flow:
- **signed state** (`oauth_flow.sign_state`, audience `wordpress`) carrying the
  sub, the org, the tier, the REQUESTED site and the return front. WordPress
  keeps the parameters of `success_url` (`add_query_arg`), so the state comes back
  as is.
- **single-use state** (`jti`, `oauth_flow.consume_state`): the
  `success_url` travels through the site, its plugins and its logs; replayed with
  another identifier of the same host, it would overwrite the credential.
- **rights re-checked on return** (`still_allowed`): the state lives 10 min, an
  admin who loses their role in the meantime sets nothing for the org any more (ADR 0038,
  like Salesforce).
- **HTTPS only**, except an internal destination declared by the operator
  (`check_site`): the password goes out in HTTP Basic.
- **the returned site must be the requested one** (same host): a `site_url`
  substituted on return never sets a credential elsewhere.
- **verified before being stored**: `users/me` with the received password. An application
  password does not rotate (unlike a Salesforce refresh token), so the probe destroys nothing.
- ⚠️ **the password arrives in the query string**: the query of this route is
  stripped from the access log (`journal_secrets`) and from Sentry (`sentry_setup`). It remains in
  the user's browser history — inherent to WordPress's protocol;
  the password is revocable from the WordPress profile.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlsplit

from . import flow as oauth_flow

logger = logging.getLogger(__name__)

AUD = "wordpress"
CALLBACK_PATH = "/api/wordpress/connect/callback"
# FIXED `app_id`: WordPress files all passwords created by this flow under
# the same application identifier — the user sees a single entry to
# revoke, and an audit on the site side can find them.
APP_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "oto-mcp/wordpress-connector"))
_SCOPES = ("member", "org", "group")


class ConnectRefused(ValueError):
    """Actionable refusal at start — message safe to display."""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def check_site(site_url: str) -> bool:
    """The guard of EVERY path that sends the password to the site: egress, then
    HTTPS. The application password goes out in HTTP Basic — in clear text over `http`.
    Only exception: an internal destination DECLARED by the operator
    (`OTO_EGRESS_ALLOW`, a local development WordPress), the same named list
    as the egress guard — never a per-connector setting.

    Returns `allow_http` to pass to the client (which refuses `http` by default): True
    only for this declared exception."""
    from .. import egress

    egress.check_url(site_url, connector="wordpress", field="site_url")
    return http_allowed(site_url)


def http_allowed(site_url: str) -> bool:
    """The "scheme" half of `check_site`, for a caller that sets the egress guard
    itself (the tools, which `tests/test_egress_guard.py` reads): False over
    HTTPS, True for an internal destination declared over HTTP, refusal otherwise."""
    from .. import egress

    parts = urlsplit((site_url or "").strip())
    if parts.scheme == "https":
        return False
    port = parts.port or 80
    declared = egress.declared_exceptions()
    if any((a, port) in declared
           for a in egress.resolved_addresses(parts.hostname or "", port)):
        return True
    raise ConnectRefused(
        f"{site_url} is on HTTP: the application password would go out in "
        "clear text. Give the address as https:// (WordPress also refuses these "
        "passwords outside HTTPS, except in a local environment).")


def still_allowed(parsed: dict) -> bool:
    """The right to write at the state's tier, re-read ON RETURN. Synchronous (SQL)."""
    from .. import roles

    if parsed["scope"] == "org":
        return roles.is_org_admin(parsed["sub"], parsed["org"])
    if parsed["scope"] == "group":
        return roles.can_admin_group(parsed["sub"], parsed["group"])
    return True


def account_for(site_url: str) -> str:
    """A site's account name: its host (+ path of an install in a
    subfolder). Stable from one connection to the next of the same site."""
    parts = urlsplit(site_url)
    path = parts.path.strip("/")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return f"{(parts.hostname or '').lower()}{port}{'/' + path if path else ''}"


def _app_name(org_id: int) -> str:
    """The name shown on the authorization screen — the brand of the org's tenant
    (the one the user knows), falling back to the instance's."""
    from .. import db, email_brand
    try:
        return email_brand.marque(db.org_tenant_slug(org_id)).nom
    except Exception:  # noqa: BLE001 — a screen name does not block a connection
        logger.warning("wordpress app_name: tenant brand unreadable for org %s, "
                       "instance name shown", org_id, exc_info=True)
        return email_brand.nom_instance()


def _authorization_endpoint(site_url: str, *, allow_http: bool = False) -> str:
    """The authorization URL that the SITE announces in its REST index
    (`authentication.application-passwords.endpoints.authorization`) — never
    guessed: its absence says the site does not offer it (HTTP without a
    local environment, security plugin, application passwords
    disabled)."""
    from oto.tools.wordpress import WordPressClient

    # `public_index` goes out WITHOUT an Authorization header: WordPress checks Basic
    # credentials on ANY REST route, index included — a dummy credential
    # would return a 401 there (`rest_application_password_check_errors`). The two
    # constructor values are never sent.
    c = WordPressClient(site_url, "-", "-", allow_http=allow_http)
    try:
        index = c.public_index()
    except ValueError as e:
        raise ConnectRefused(str(e)) from e
    except Exception as e:  # noqa: BLE001 — any network failure is told to the user
        raise ConnectRefused(
            f"{site_url} unreachable ({type(e).__name__}) — check the URL.") from e
    endpoint = (((index or {}).get("authentication") or {})
                .get("application-passwords") or {}).get("endpoints", {}).get("authorization")
    if not endpoint:
        raise ConnectRefused(
            "this site does not offer application authorization (application "
            "passwords disabled, or site on HTTP). Create an application "
            "password in your WordPress profile and paste it into the form.")
    return endpoint


def _saved_site(sub: str) -> str:
    """Reconnection: the access card's button only posts the flow's defaults
    (no form), hence no `site_url`. We then take the site of the credential
    already set — the one the cascade resolves. Nothing set ⟹ "" (and the named
    refusal that follows); any other refusal (several sites, access) is told as
    is, an outage bubbles up."""
    from .. import access
    from ..mcp_errors import McpError
    try:
        return access.resolve_credential("wordpress", want="byo", sub=sub,
                                         emit_on_failure=False,
                                         check_usage=False).fields.get("site_url") or ""
    except access.CredentialUnavailable:
        return ""
    except McpError as e:
        raise ConnectRefused(e.error.message) from e


def start(ctx, values: dict):
    """`connector_flow` → the site's authorization URL. `values`: `site_url`
    (typed), `scope` and `app` (set by the front, outside the form)."""
    from .. import access, roles
    from ..connectors import flow as connector_flow
    from oto.tools.wordpress import normalize_site_url

    raw = (values.get("site_url") or "").strip() or _saved_site(ctx.sub)
    if not raw:
        raise ConnectRefused("enter your WordPress site's URL.")
    try:
        # The SHAPE only here (http included): `check_site` then decides on the scheme,
        # with the declared exception that it alone knows.
        site = normalize_site_url(raw, allow_http=True)
    except ValueError as e:
        raise ConnectRefused(str(e)) from e
    allow_http = check_site(site)

    scope = (values.get("scope") or "member").strip() or "member"
    if scope not in _SCOPES:
        raise ConnectRefused(f"unknown tier: {scope!r}")
    org_id = access.current_org(ctx.sub)
    if org_id is None:
        raise ConnectRefused("no context org — sign in again and retry.")
    group_id: Optional[int] = None
    if scope == "org" and not roles.is_org_admin(ctx.sub, org_id):
        raise PermissionError("only an org admin can connect a site for the whole org.")
    if scope == "group":
        group_id = access.current_group(ctx.sub)
        if group_id is None or not roles.can_admin_group(ctx.sub, group_id):
            raise PermissionError("only a team lead can connect a site for the team.")

    endpoint = _authorization_endpoint(site, allow_http=allow_http)
    # The authorization screen is served by the site itself: its host is
    # the site's (a site that announces an authorization ELSEWHERE is refused).
    if _host(endpoint) != _host(site):
        raise ConnectRefused(
            f"the site announces an authorization page on another host "
            f"({_host(endpoint)}) — refused.")

    payload = {"sub": ctx.sub, "org": org_id, "scope": scope, "site": site,
               "app": oauth_flow.resolve_return_app(values.get("app")),
               "jti": oauth_flow.new_jti()}
    if group_id is not None:
        payload["group"] = group_id
    state = oauth_flow.sign_state(AUD, payload)
    callback = oauth_flow.redirect_uri(CALLBACK_PATH)
    success = f"{callback}?{urlencode({'state': state})}"
    reject = f"{callback}?{urlencode({'state': state, 'success': 'false'})}"
    params = {"app_name": _app_name(org_id), "app_id": APP_ID,
              "success_url": success, "reject_url": reject}
    sep = "&" if "?" in endpoint else "?"
    return connector_flow.FlowStart(auth_url=f"{endpoint}{sep}{urlencode(params)}",
                                    details={"site": site, "account": account_for(site)})


def read_state(state: Optional[str]) -> Optional[dict]:
    data = oauth_flow.read_state(AUD, state)
    if not data:
        return None
    if (not isinstance(data.get("sub"), str) or not isinstance(data.get("org"), int)
            or data.get("scope") not in _SCOPES or not isinstance(data.get("site"), str)
            or not isinstance(data.get("jti"), str)):
        return None
    if data["scope"] == "group" and not isinstance(data.get("group"), int):
        return None
    if not isinstance(data.get("app"), str):
        data["app"] = ""
    return data


def finish(parsed: dict, site_url: str, user_login: str, password: str) -> str:
    """Verifies then sets the credential. Synchronous (network + SQL) — the caller
    runs it off the loop. Returns the account name set."""
    from .. import credentials_store
    from oto.tools.wordpress import WordPressClient, normalize_site_url

    asked = parsed["site"]
    returned = normalize_site_url(site_url or asked, allow_http=True)
    if _host(returned) != _host(asked):
        raise ConnectRefused("the returned site does not match the requested site.")
    if not user_login or not password:
        raise ConnectRefused("WordPress returned neither a username nor a password.")
    allow_http = check_site(asked)

    me = WordPressClient(asked, user_login, password, allow_http=allow_http).me()
    if not me.get("id"):
        raise ConnectRefused("the received password does not authenticate (empty users/me).")

    fields = credentials_store.validate_fields("wordpress", {
        "site_url": asked, "username": user_login, "application_password": password})
    secret = credentials_store.pack_secret("wordpress", fields)
    meta = {"verified_at": datetime.now(timezone.utc).isoformat(),
            "connected_via": "authorize_application",
            **credentials_store.meta_fields("wordpress", fields)}
    account = account_for(asked)
    sub, org_id = parsed["sub"], parsed["org"]
    etype, eid = credentials_store.entity_for_scope(parsed["scope"], org_id, sub,
                                                    parsed.get("group"))
    credentials_store.guard_account_write(etype, eid, "wordpress", account, org=org_id)
    credentials_store.set_credential(etype, eid, "wordpress", secret, set_by=sub,
                                     account=account, meta=meta)
    return account
