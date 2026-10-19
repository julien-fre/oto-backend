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
from contextlib import contextmanager
from typing import Optional

from ..mcp_errors import McpError
from .. import access, credentials_store, group_store, providers, session_org
from ..access import chain_shadow
from ..connectors import access_levels
from ..db.lecture_d_appel import lecture_d_appel
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
# they no longer depend on what the user ticked. The CRM and Desk declare ACCESS LEVELS
# (`providers/<name>.py`, `connectors/access_levels.py`): their `full` scopes come from
# there — one home — and a consent asks for the scopes of the level in force
# (`consent_scopes`). Analytics declares none: its scopes are written here.
SCOPES = {
    "zoho": access_levels.scopes_for("zoho", access_levels.FULL),
    "zohodesk": access_levels.scopes_for("zohodesk", access_levels.FULL),
    "zohoanalytics": ("ZohoAnalytics.data.read", "ZohoAnalytics.metadata.read"),
}

CONNECTORS = tuple(SCOPES)


def consent_scopes(connector: str, level: str) -> tuple[str, ...]:
    """The scopes a consent asks for at `level`. A connector without levels has only
    `full`, its `SCOPES`."""
    if access_levels.declared(connector) is not None:
        return access_levels.scopes_for(connector, level)
    if level != access_levels.FULL:
        raise ZohoOAuthError(f"{connector} declares no access level `{level}`.")
    return SCOPES[connector]


def consent_level(connector: str, org_id: Optional[int], group_id: Optional[int]) -> str:
    """The access level in force for (org, team) — the one the consent asks for."""
    return access_levels.effective_level(connector, org_id, group_id)


def normalize_region(data_center: Optional[str]) -> str:
    """A known Zoho region, lowercase, or `ZohoOAuthError`."""
    dc = (data_center or "").strip().lower()
    if dc not in _ACCOUNTS:
        raise ZohoOAuthError(
            f"Unrecognized Zoho data center: {data_center!r} — one of "
            f"{', '.join(_ACCOUNTS)}.")
    return dc

# The fields the consent PRODUCES (cf. `persist`) — the rest of a connector's required
# fields must be entered by hand. This is what separates "not yet authorized"
# from "authorization will not be enough": Analytics requires an `org_id` that no OAuth flow
# can guess. Kept here, next to `persist`, so the two do not diverge
# (tripwire `test_editor_app.py`).
PERSISTED_FIELDS = ("client_id", "client_secret", "refresh_token", "data_center")


class ZohoOAuthError(ValueError):
    """Acquisition failure — message WITHOUT secrets (cf. `zoho.auth`, incident #284)."""


class ZohoRegionMissing(ZohoOAuthError):
    """No region for the consent: neither the app nor the request carries one. Named
    (`zoho_region_missing`) because the fix is a gesture — fill in the region —, never
    a default region chosen for the user."""


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


class DedicatedApp(dict):
    """The fields of a DEDICATED app — a dict that says where it comes from: its
    `data_center` is the app's own region, which binds the consent (`consent_region`).
    A brought credential's `data_center` binds it the same way."""


def dedicated_app(connector: str, org_id: Optional[int],
                  group_id: Optional[int]) -> dict:
    """The app DEDICATED to the consent by the team, else by the org
    (`credentials_store` §dedicated app) — `{client_id, client_secret, data_center}` —
    or `{}`. Never a call credential: only the consent reads it."""
    for owner_type, owner_id in (("group", group_id), ("org", org_id)):
        if owner_id:
            app = credentials_store.get_dedicated_app(owner_type, owner_id, connector)
            if app is not None:
                return DedicatedApp(app["fields"])
    return {}


@contextmanager
def _under(org_id: Optional[int], group_id: Optional[int]):
    """Resolves the cascade under THIS org and team, whatever the ambient context. The
    consent start passes its own (identity), the callback — called by Zoho, with no
    session — the ones signed in the state: the same app on the way out and back."""
    t_org = session_org.set_view_org(org_id or 0)
    t_group = session_org.set_view_group(group_id or 0)
    try:
        yield
    finally:
        session_org.reset_view_group(t_group)
        session_org.reset_view_org(t_org)


def app_fields(connector: str, sub: str, data_center: str = "", *,
               org: Optional[int], group: Optional[int]) -> dict:
    """App fields to use for this (sub, connector, region) under (org, team).

    Origins, in this order — **an app brought by the org always takes precedence over
    ours**:
    1. the app DEDICATED by the team, then by the org (`dedicated_app`) — it carries its
       own region;
    2. the resolved BYO credential (member > team > org): an org that wants to see ITS
       app in its Zoho logs sets it, and nothing changes for it;
    3. failing that, the PUBLISHER app for the region (`data_center`), if oto publishes one.

    The start AND the callback call it with the same (org, team) — signed in the state —
    so the code is exchanged with the app it was issued for.

    `{}` if none: this is the NOMINAL state of a first connection on a
    connector without a publisher app, never an error."""
    dedie = dedicated_app(connector, org, group)
    if dedie:
        return dedie
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
        with _under(org, group):
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


def consent_region(app: dict, data_center: Optional[str]) -> str:
    """The region of the consent. An app that carries its region binds it — the app
    dedicated by the team or the org, AND the app set as a key on the connector's card
    (its `data_center` field): a request naming ANOTHER region is refused (an app
    registered in one data center is rejected by the others), an empty request takes
    it. That is what lets a member click "connect" without knowing the region of their
    team's app. An app without a region (oto's publisher app, keyed by the requested
    region): the requested region.

    Neither: `ZohoRegionMissing`, which says who sets it — never a default region."""
    demande = (data_center or "").strip().lower()
    propre = _region_of(app)
    if propre and demande and propre != demande:
        raise ZohoOAuthError(
            f"The Zoho app used for this connection is registered in the `{propre}` "
            f"data center: restart the connection with data_center={propre}.")
    if propre or demande:
        return normalize_region(propre or demande)
    if app.get("client_id"):
        raise ZohoRegionMissing(
            "The Zoho app set on the connector's card (yours, your team's or your "
            "org's) has no region: whoever set it fills in its \"Data center\" field "
            f"({', '.join(_ACCOUNTS)}), then restart the connection.")
    raise ZohoRegionMissing(
        f"Choose the region of your Zoho account (data_center: {', '.join(_ACCOUNTS)}) "
        "to start the connection.")


def has_app(connector: str, sub: str, data_center: str = "", *,
            org: Optional[int], group: Optional[int]) -> bool:
    """Is an app available? With `data_center`, the answer holds FOR that
    region; without, it means "an app exists somewhere" — the app dedicated or brought
    by the member/team/org, or a publisher app for at least one region (the front
    shows the button, the region only being chosen on click)."""
    if data_center:
        f = app_fields(connector, sub, data_center, org=org, group=group)
        propre = _region_of(f)
        if propre and propre != data_center.strip().lower():
            return False
        return bool(f.get("client_id") and f.get("client_secret"))
    # Without a region, `app_fields` only consults the dedicated app and the BYO (the
    # publisher fallback is keyed by region) — hence the second question, asked separately.
    byo = app_fields(connector, sub, org=org, group=group)
    if byo.get("client_id") and byo.get("client_secret"):
        return True
    return _has_editor_app(connector)


def app_ready(connector: str, sub: str) -> bool:
    """The promise of the connection screen (`connect.app_ready`, served row by row by
    `GET /api/me/connectors`): `has_app` without a region. Cf. `_applicable_app`."""
    return _applicable_app(connector, sub)[0]


def app_region(connector: str, sub: str) -> str:
    """The region the applicable app BINDS (`connect.fixed`), `""` if none does — oto's
    publisher app, keyed by the region chosen on click. The front then shows the region
    instead of a select whose other choices `consent_region` refuses."""
    return _applicable_app(connector, sub)[1]


@lecture_d_appel
def _applicable_app(connector: str, sub: str) -> tuple[bool, str]:
    """`(app_ready, region)` in ONE pass, read once per call (`lecture_d_appel`): the
    listing asks both for each of the three Zoho connectors (oto-backend#1148). Never a
    secret in the answer — only whether an app exists and the region it carries.

    `has_app` asks the RESOLUTION whether an app was brought: on a member who brought
    none — the common case — the failed cascade builds its diagnostic (reachable
    instances, pinning projects, revoked instances) and observes the L7 shadow: ~30
    queries per Zoho connector, three connectors, ~90 queries the listing threw away.
    Here the PRESENCE answers first, with the walker and the preloaded probe of
    `status_for` (`want="byo"`, no platform tier), from the inventory the same call has
    already read: no credential within reach ⟹ nothing brought, and the resolution is
    not asked. A credential within reach ⟹ its fields decide, as in `has_app`.
    """
    org = access.current_org(sub)
    group = access.current_group(sub)
    # An app dedicated by the team or the org: its presence without decrypting, then
    # its region (the one query that decrypts, only when such an app exists).
    if credentials_store.dedicated_app_owners(connector, org, group):
        return True, _region_of(dedicated_app(connector, org, group))
    groups = group_store.list_groups_for_user(sub, org) if org is not None else []
    sonde = access.preloaded_presence_probe(sub, org=org, groups=groups)
    a_portee = next(chain_shadow.resolution_rungs(
        sub, connector, org=org, group=group, probe=sonde,
        want="byo"), None)
    if a_portee is not None:
        byo = app_fields(connector, sub, org=org, group=group)
        if byo.get("client_id") and byo.get("client_secret"):
            return True, _region_of(byo)
    return _has_editor_app(connector), ""


def _region_of(app: dict) -> str:
    return (app.get("data_center") or "").strip().lower()


def _has_editor_app(connector: str) -> bool:
    """Does oto publish an app for this connector, in any region?
    Fail-open: a read outage must not hide the button (at worst, `start`
    will return an actionable message).

    The WHOLE list, filtered here: the three Zoho connectors of one listing share one
    read of it (`lecture_d_appel`, oto-backend#1148), not one per connector."""
    try:
        return any(a["connector"] == connector
                   for a in credentials_store.list_editor_apps())
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
               return_app: str = "", *, group_id: Optional[int], level: str) -> str:
    """`return_app` = the FRONT that asked for the consent. It MUST travel in the
    signed state: the callback is called by Zoho, with no session, so it is the only
    memory of the requester that survives the round trip. Already resolved against the closed
    list by the caller (`oauth_flow.resolve_return_app`) — we never sign a raw
    client value.

    `group_id` and `level` travel for the same reason: the callback resolves the app
    under the SAME org and team as the start, and the credential records the level its
    scopes were asked for — signed, it cannot be widened between the two."""
    return oauth_flow.sign_state(_AUDIENCE, {"sub": sub, "org": org_id, "g": group_id,
                                             "c": connector, "dc": data_center,
                                             "l": level, "a": return_app})


def _level_ok(connector: str, level: object) -> bool:
    decl = access_levels.declared(connector)
    if decl is None:
        return level == access_levels.FULL
    return isinstance(level, str) and level in decl.levels


def verify_state(state: str) -> Optional[dict]:
    """`{sub, org, group, connector, data_center, level, return_app}` if the state is
    valid, from THIS flow, and not expired. The business invariants (known connector,
    known region, declared level) stay here — the factory only knows the transport. A
    state without team or level (signed before them) is refused: the callback could not
    replay the start's resolution."""
    d = oauth_flow.read_state(_AUDIENCE, state, ttl=_STATE_TTL)
    if not d:
        return None
    if d.get("c") not in CONNECTORS or d.get("dc") not in _ACCOUNTS:
        return None
    if not isinstance(d.get("sub"), str) or not isinstance(d.get("org"), int):
        return None
    if "g" not in d or not (d["g"] is None or isinstance(d["g"], int)):
        return None
    if not _level_ok(d["c"], d.get("l")):
        return None
    # `a` absent = state signed BEFORE this field and still alive within the
    # TTL window: we degrade to the historical default rather than reject a
    # consent in progress (same tolerance as salesforce).
    return {"sub": d["sub"], "org": d["org"], "group": d["g"], "connector": d["c"],
            "data_center": d["dc"], "level": d["l"], "return_app": d.get("a") or ""}


# --- flow --------------------------------------------------------------------

def build_auth_url(sub: str, org_id: int, connector: str, data_center: str,
                   app: Optional[dict] = None, return_app: str = "", *,
                   group_id: Optional[int], level: str) -> str:
    """Zoho consent URL, asking for the scopes of `level` (`consent_scopes`).
    `access_type=offline` + `prompt=consent` are REQUIRED to obtain a refresh_token
    (without them Zoho only returns a one-hour access token, and the connection silently
    dies after an hour)."""
    if connector not in SCOPES:
        raise ZohoOAuthError(f"Unknown Zoho connector: {connector}")
    dc = normalize_region(data_center)
    client_id, _ = resolve_app(app if app is not None else app_fields(
        connector, sub, dc, org=org_id, group=group_id))
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "scope": ",".join(consent_scopes(connector, level)),
        "redirect_uri": redirect_uri(),
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(sub, org_id, connector, dc, return_app,
                            group_id=group_id, level=level),
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
            tokens: dict, app: Optional[dict] = None, *, level: str,
            entity_type: str = "member") -> None:
    """Stores the credential IN THE SAME SHAPE as Self Client mode — this is what
    lets the two modes coexist without touching the client or the
    resolution. `entity_type='member'` = (sub, org)-scoped key (ADR 0033).

    The meta keeps the access `level` the consent asked for and the `scope` Zoho says it
    granted (`None` when the response carries none): what a later widening of the level
    reads to know a new consent is needed."""
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
        meta={"acquired_via": "oauth", "access_level": level,
              "scope": tokens.get("scope")})


def supports(connector: str) -> bool:
    """Does the connector offer server-based mode? (the front gates the button)"""
    return connector in SCOPES and connector in providers.REGISTRY
