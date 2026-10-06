"""Zoho OAuth2 acquisition in **Server-based** mode — the second connection mode.

The two modes coexist, which is what makes the addition non-invasive: they
produce **exactly the same credential** (`client_id` + `client_secret` +
`refresh_token` + `data_center`). Only the way of OBTAINING it changes.

- **Self Client** (existing, unchanged): the user generates the values in the
  Zoho console and pastes them into the form. No dedicated code — it is the
  generic credentials form.
- **Server-based** (this module): the user clicks "Connect", consents at
  Zoho, and comes back connected. Nothing to copy.

Why add it — three incidents of the same family (#190 `zoho_modules`
OAUTH_SCOPE_MISMATCH, #202 Analytics INVALID_OAUTHSCOPE, and Desk limited to
`Desk.articles.READ`, which removed native search): in Self Client, it is
**the user** who picks the scopes by hand, and they get it wrong — with nothing
we can fix on the server side. Here **oto declares them** in the authorization
URL. Secondary benefit: no secret needs to travel by
email any more (lived 28/07, a `client_secret` received in clear text).

**Where does the app come from?** From the **vault**, like any credential — never from
the environment. `client_id`/`client_secret` are set on the connector's card
by the user, the team, the org or the platform, and resolved by the **usual
cascade** (`access.resolve_credential_fields`: member > team > org >
platform). Consequences: each org can bring its own, a **platform** key set by
Otomata gives everyone "one click" without changing anything in the code, and the existing
sharing/governance applies as is.

The connection is therefore in **two steps** (`status_hints` pattern already in place for
unipile/sessions): (1) set the app — `client_id` + `client_secret` + region;
(2) consent, which fills in the `refresh_token`. Hence an OPTIONAL `refresh_token`
on the card: in server-based mode it is not pasted, it is obtained.

The OAuth transport (signed state, code exchange, redirect URI) is delegated
to **`oauth_flow`**, the common factory — this module only keeps what is specific
to Zoho: regions, per-connector scopes, app origin, credential storage.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

from ..mcp_errors import McpError
from .. import access, credentials_store, providers
from . import flow as oauth_flow

logger = logging.getLogger(__name__)

_STATE_TTL = 600  # 10 min — time to read a consent screen

# Zoho regions: the OAuth app and the refresh token are tied to THEIR data center
# (an `.eu` client on `accounts.zoho.com` = opaque `invalid_client`).
_ACCOUNTS = {
    "com": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "in": "https://accounts.zoho.in",
    "au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
}

# Analytics API domain per region — same split as `_ACCOUNTS`, different host.
_ANALYTICS_DOMAINS = {
    "com": "https://analyticsapi.zoho.com",
    "eu": "https://analyticsapi.zoho.eu",
    "in": "https://analyticsapi.zoho.in",
    "au": "https://analyticsapi.zoho.com.au",
    "jp": "https://analyticsapi.zoho.jp",
    "ca": "https://analyticsapi.zohocloud.ca",
}

# Scopes requested PER CONNECTOR — that is the whole point of server-based mode:
# they no longer depend on what the user ticked.
SCOPES = {
    "zoho": ("ZohoCRM.modules.ALL", "ZohoCRM.settings.modules.READ",
             "ZohoCRM.settings.fields.READ", "ZohoCRM.users.READ"),
    "zohodesk": ("Desk.articles.READ", "Desk.basic.READ", "Desk.search.READ",
                 "Desk.tickets.READ", "Desk.contacts.READ"),
    "zohoanalytics": ("ZohoAnalytics.data.read", "ZohoAnalytics.metadata.read"),
}

CONNECTORS = tuple(SCOPES)

# The fields the consent PRODUCES (cf. `persist`) — the rest of a connector's required
# fields must be entered by hand. This is what separates "not yet authorized"
# from "authorization will not be enough": Analytics requires an `org_id` that no OAuth flow
# can guess. Kept here, next to `persist`, so the two do not diverge
# (tripwire `test_editor_app.py`).
PERSISTED_FIELDS = ("client_id", "client_secret", "refresh_token", "data_center")


class ZohoOAuthError(ValueError):
    """Acquisition failure — message WITHOUT secrets (cf. `zoho.auth`, incident #284)."""


# --- app (client_id / client_secret) ----------------------------------------

def editor_app(connector: str, data_center: str) -> dict:
    """The PUBLISHER's (oto) app for this region, or `{}`.

    This is the notch that makes the connection "one click" without anyone having to create
    an app: oto publishes its own, the user only consents. It gives access
    to nothing by itself — cf. the invariant in `credentials_store` §publisher
    app. `{}` if oto publishes none for this region: Self Client then remains
    the way, and `resolve_app` is what says so."""
    if not (data_center or "").strip():
        return {}
    return credentials_store.get_editor_app(connector, data_center) or {}


def app_fields(connector: str, sub: str, data_center: str = "") -> dict:
    """App fields to use for this (sub, connector, region).

    Two origins, in this order — **the brought app always takes precedence over ours**:
    1. the resolved BYO credential (member > team > org): an org that wants to see ITS
       app in its Zoho logs sets it, and nothing changes for it;
    2. failing that, the PUBLISHER app for the region (`data_center`), if oto publishes one.

    `{}` if neither: this is the NOMINAL state of a first connection on a
    connector without a publisher app, never an error."""
    # ⚠️ `resolve_credential(..., sub=…)` and NOT `resolve_credential_fields`, which
    # has no `sub` parameter: we are in a REST route, outside MCP context, where
    # the ambient sub does not exist. `emit_on_failure=False` — this is a PROBE that swallows
    # the failure, it must not pollute the usage signal (ADR 0017).
    #
    # ⚠️ We ONLY catch the cascade's `McpError` — it, and it alone,
    # says "no credential yet". Catching `Exception` made us fall back
    # SILENTLY to oto's publisher app at the slightest vault hiccup
    # (decryption, DB): the org that set ITS app to see it in its Zoho logs
    # no longer saw it, and nothing said so (silent-failure inventory of 2026-08-27,
    # site B7). A vault error is a refusal, not an absence.
    try:
        byo = access.resolve_credential(
            connector, want="byo", sub=sub, emit_on_failure=False).fields or {}
    except McpError:  # NOMINAL state: no credential brought at this level
        byo = {}
    if byo.get("client_id") and byo.get("client_secret"):
        return byo
    return editor_app(connector, data_center)


def resolve_app(fields: Optional[dict]) -> tuple[str, str]:
    """`(client_id, client_secret)` of the app to use, taken from the resolved
    credential. Raises an actionable message if the app has not been set yet."""
    cid = (fields or {}).get("client_id")
    sec = (fields or {}).get("client_secret")
    if cid and sec:
        return cid, sec
    raise ZohoOAuthError(
        "No Zoho OAuth app available for this region. Fill in the \"client id\" "
        "and \"client secret\" of a Zoho self client on the connector's card (or "
        "ask your org to share theirs), then restart the connection.")


def has_app(connector: str, sub: str, data_center: str = "") -> bool:
    """Is an app available? With `data_center`, the answer holds FOR that
    region; without, it means "an app exists somewhere" — the app brought by the
    member/team/org, or a publisher app for at least one region (the front
    shows the button, the region only being chosen on click)."""
    if data_center:
        f = app_fields(connector, sub, data_center)
        return bool(f.get("client_id") and f.get("client_secret"))
    # Without a region, `app_fields` only consults the BYO (the publisher fallback is keyed by
    # region) — hence the second question, asked separately.
    byo = app_fields(connector, sub)
    if byo.get("client_id") and byo.get("client_secret"):
        return True
    return _has_editor_app(connector)


def _has_editor_app(connector: str) -> bool:
    """Does oto publish an app for this connector, in any region?
    Fail-open: a read outage must not hide the button (at worst, `start`
    will return an actionable message)."""
    try:
        return bool(credentials_store.list_editor_apps(connector))
    # noqa: SILENT — fail-open: a read outage must not hide the button
    except Exception:  # noqa: BLE001
        return False


# --- signed state (delegated to the factory) ----------------------------------
#
# `oauth_flow` carries the mechanics — HMAC, base64url, TTL — and BINDS the state to its
# audience, which the separate implementations did not do: they all signed
# with the same secret, in the same format, with no discriminator, so that a
# state from one flow was structurally acceptable to another's callback.

_AUDIENCE = "zoho"


def redirect_uri() -> str:
    """ONE URI for the three Zoho connectors (the connector travels in the
    `state`): a URI is registered byte for byte on the Zoho side — only one to declare
    per app instead of three."""
    return oauth_flow.redirect_uri("/api/zoho/oauth/callback")


def make_state(sub: str, org_id: int, connector: str, data_center: str,
               return_app: str = "") -> str:
    """`return_app` = the FRONT that asked for the consent. It MUST travel in the
    signed state: the callback is called by Zoho, with no session, so it is the only
    memory of the requester that survives the round trip. Already resolved against the closed
    list by the caller (`oauth_flow.resolve_return_app`) — we never sign a raw
    client value."""
    return oauth_flow.sign_state(_AUDIENCE, {"sub": sub, "org": org_id,
                                             "c": connector, "dc": data_center,
                                             "a": return_app})


def verify_state(state: str) -> Optional[dict]:
    """`{sub, org, connector, data_center}` if the state is valid, from THIS flow, and
    not expired. The business invariants (known connector, known region) stay
    here — the factory only knows the transport."""
    d = oauth_flow.read_state(_AUDIENCE, state, ttl=_STATE_TTL)
    if not d:
        return None
    if d.get("c") not in CONNECTORS or d.get("dc") not in _ACCOUNTS:
        return None
    if not isinstance(d.get("sub"), str) or not isinstance(d.get("org"), int):
        return None
    # `a` absent = state signed BEFORE this field and still alive within the
    # TTL window: we degrade to the historical default rather than reject a
    # consent in progress (same tolerance as salesforce).
    return {"sub": d["sub"], "org": d["org"], "connector": d["c"],
            "data_center": d["dc"], "return_app": d.get("a") or ""}


# --- flow --------------------------------------------------------------------

def build_auth_url(sub: str, org_id: int, connector: str, data_center: str,
                   app: Optional[dict] = None, return_app: str = "") -> str:
    """Zoho consent URL. `access_type=offline` + `prompt=consent` are
    REQUIRED to obtain a refresh_token (without them Zoho only returns a one-hour access
    token, and the connection silently dies after an hour)."""
    if connector not in SCOPES:
        raise ZohoOAuthError(f"Unknown Zoho connector: {connector}")
    dc = (data_center or "").strip().lower()
    if dc not in _ACCOUNTS:
        raise ZohoOAuthError(
            f"Unrecognized Zoho data center: {data_center!r} — one of "
            f"{', '.join(_ACCOUNTS)}.")
    client_id, _ = resolve_app(app if app is not None else app_fields(connector, sub, dc))
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "scope": ",".join(SCOPES[connector]),
        "redirect_uri": redirect_uri(),
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(sub, org_id, connector, dc, return_app),
    })
    return f"{_ACCOUNTS[dc]}/oauth/v2/auth?{q}"


def exchange_code(code: str, data_center: str,
                  app: Optional[dict] = None) -> dict:
    """Ephemeral code → tokens, via the factory (form-encoded body, redacted errors).
    Only the Zoho specifics remain here: the regional domain and the diagnosis
    of the missing refresh_token."""
    client_id, client_secret = resolve_app(app)
    try:
        payload = oauth_flow.exchange_code(
            f"{_ACCOUNTS[data_center]}/oauth/v2/token",
            code=code, client_id=client_id, client_secret=client_secret,
            redirect=redirect_uri())
    except oauth_flow.OAuthFlowError as e:
        msg = str(e)
        if "code" in msg.lower():
            msg += (" The authorization code expires within a few minutes — restart "
                    "the connection.")
        raise ZohoOAuthError(msg)
    if not payload.get("refresh_token"):
        raise ZohoOAuthError(
            "Zoho did not return a refresh_token: authorization was already "
            "granted to this app. Revoke it in your Zoho account "
            "(Security → Connected Apps) then restart the connection.")
    return payload


def analytics_orgs(fields: dict) -> list[dict]:
    """Analytics organizations visible to this credential (`org_id`, `name`, `role`).

    Analytics is the only one of the three that requires an organization on EVERY call (CRM
    infers it from the token, Desk can do without in single-org). It therefore has to be filled in
    — but the API can tell it, which avoids sending the user to look for an
    eleven-digit identifier in the Zoho interface."""
    from oto.tools.zohoanalytics.client import ZohoAnalyticsClient
    dc = (fields.get("data_center") or "").strip().lower()
    if dc not in _ACCOUNTS:
        raise ZohoOAuthError(f"Unrecognized Zoho data center: {fields.get('data_center')!r}.")
    return ZohoAnalyticsClient(
        client_id=fields.get("client_id"), client_secret=fields.get("client_secret"),
        refresh_token=fields.get("refresh_token"), org_id=None,
        api_domain=_ANALYTICS_DOMAINS[dc], accounts_url=_ACCOUNTS[dc],
    ).list_orgs()


def _derived_fields(connector: str, fields: dict) -> dict:
    """What the consent allows us to DERIVE, when there is only one possible answer.

    Analytics: a single organization ⟹ we set it, the user never sees the
    question. SEVERAL ⟹ we do not guess. Zoho's response designates no
    default organization, and on the first real account tested there were two —
    "the first" would have been the wrong one. The field then stays empty: the credential's
    state flags it, and the user chooses from names.

    Best-effort: a failure here must NEVER make us lose a successful consent —
    the user would fall back to manual entry, not to an error."""
    if connector != "zohoanalytics":
        return {}
    try:
        orgs = analytics_orgs(fields)
    except Exception:  # noqa: BLE001
        logger.debug("analytics org discovery failed", exc_info=True)
        return {}
    return {"org_id": orgs[0]["org_id"]} if len(orgs) == 1 else {}


def persist(sub: str, org_id: int, connector: str, data_center: str,
            tokens: dict, app: Optional[dict] = None, *,
            entity_type: str = "member") -> None:
    """Stores the credential IN THE SAME SHAPE as Self Client mode — this is what
    lets the two modes coexist without touching the client or the
    resolution. `entity_type='member'` = (sub, org)-scoped key (ADR 0033)."""
    client_id, client_secret = resolve_app(app)
    fields = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "data_center": data_center,
    }
    # ⚠️ After the flow's fields, never before: discovery needs the token
    # just obtained. And outside `PERSISTED_FIELDS` on purpose — an `org_id` we
    # could NOT derive must stay flagged as missing.
    fields.update(_derived_fields(connector, fields))
    secret = credentials_store.secret_from_input(connector, None, fields)
    # ⚠️ ALWAYS via `member_id(org, sub)` — the order is (org, sub), and the **encryption
    # AAD derives from it**: an id rebuilt by hand in the wrong order does not
    # produce a "misfiled" credential but an UNDECRYPTABLE one, which the cascade
    # will never see (lived at the first real consent, 28/07).
    entity_id = (credentials_store.member_id(org_id, sub)
                 if entity_type == "member" else str(org_id))
    credentials_store.set_credential(
        entity_type, entity_id, connector, secret, set_by=sub,
        meta={"acquired_via": "oauth"})


def supports(connector: str) -> bool:
    """Does the connector offer server-based mode? (the front gates the button)"""
    return connector in SCOPES and connector in providers.REGISTRY
