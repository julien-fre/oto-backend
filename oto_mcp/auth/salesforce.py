"""Salesforce OAuth — live "Connect" flow. THE only way to obtain the
`refresh_token` now — the old manual Postman-style acquisition (paste a
Callback URL that led nowhere on our side, copy an authorization code out of
the browser's address bar, exchange it by hand) is gone: the registry entry
(`providers/salesforce.py`) no longer declares a `refresh_token` field at all, only
`client_id`/`client_secret`/`login_url`.

Unlike oto's other live-OAuth flow (google),
Salesforce's OAuth client — a "Connected App" — is **per-customer**: each
customer creates their own inside their own org, with their own
`client_id`/`client_secret`/`login_url`. There is no platform-wide Salesforce
client oto could register once (Google shares ONE
Otomata-owned client). So this flow is a hybrid: the customer still saves
`client_id`/`client_secret`/`login_url` through the existing generic
`/api/settings/api-keys/salesforce` form (unchanged — those 3 fields are now
ALL of what that form collects for Salesforce), and this module's `/start`
reads THAT already-saved partial credential to build a per-customer authorize
URL, instead of a module-level constant like `google_oauth.py`'s
`GOOGLE_WORKSPACE_CLIENT_ID`. That the credential gets completed OUTSIDE the form is stated by `status_hints`
(`register_state` + `pending_action`, in tools/salesforce.py) — the common seam,
the one Zoho already uses — and not by a dedicated auth method: the set of
`auth_method` values is closed and consumed by a dashboard switch.

State design mirrors `google_oauth.py` specifically (hand-rolled HMAC, not the
shared `oauth2_pkce.make_state`/`verify_state`): the credential is scoped
`(org, sub)` (a MEMBER-entity row, exactly Google's situation), so the state
must carry `org_id` in addition to `sub` — `oauth2_pkce.make_state`'s fixed
`(secret, sub, verifier)` signature has no slot for that, which is the exact
reason `google_oauth.py` itself diverged from the shared helper. This module
also needs a `scope` field ("member" | "org" | "group" — see `build_auth_url`)
that neither Google nor the shared helper need. `oauth2_pkce.pkce_pair()` IS
reused as-is for generating the PKCE pair — that one function is genuinely
generic.

PKCE is included even though Salesforce's confidential client doesn't
strictly require it: it's invisible to the customer, costs nothing, and
pre-empts a real failure mode if a security-conscious admin has already
toggled "Require PKCE" in their Connected App's OAuth policies.

Setup ops:
- Env `OTO_MCP_PUBLIC_URL` / `OTO_MCP_OAUTH_STATE_SECRET` — already used by
  every other oauth module here, no new infra needed.
- Customer-facing Callback URL to register on their Connected App:
  `https://mcp.oto.cx/api/salesforce/oauth/callback` (prod). Our own preprod
  testing uses `mcp.oto.ninja` — never hand that one to a customer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import credentials_store, org_store
from . import pkce as oauth2_pkce, flow as oauth_flow

# Audience of the state (`oauth_flow.sign_state`): a state issued for Salesforce is valid
# ONLY for the Salesforce callback. Before the factory, five flows signed in the same
# format with the same secret, with no discriminator — a state from one flow passed at
# another. That is exactly what this name closes.
_AUD = "salesforce"
_CALLBACK_PATH = "/api/salesforce/oauth/callback"

# `api` = REST API access ; `refresh_token` = long-lived refresh token issuance
# (Salesforce also accepts the synonym `offline_access`, but this is the name
# used in Salesforce's own docs/UI for the "Perform requests at any time"
# scope, so it's what we document to the customer).
SCOPES = "api refresh_token"

def _ctx_org(sub: str) -> int:
    from .. import access  # lazy: avoid any import cycle at boot
    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "No context org — cannot scope the Salesforce connection. "
            "Sign in again and retry."
        )
    return org


def _ctx_group(sub: str) -> int:
    from .. import access  # lazy: avoid any import cycle at boot
    group = access.current_group(sub)
    if group is None:
        raise RuntimeError(
            "No active team — cannot connect Salesforce on behalf "
            "of your team. Select an active team and retry."
        )
    return group


def make_state(sub: str, org_id: int, scope: str, verifier: str,
               group_id: Optional[int] = None, return_app: str = "") -> str:
    """Signed state, BOUND to the `salesforce` audience (`oauth_flow.sign_state`).

    What the payload carries that is specific: `org` (the credential is scoped (org, sub)),
    `scope` ("member" | "org" | "group") and, for a team, `group`. The callback
    arrives WITHOUT an auth header: these values must travel with it, not be
    re-derived from a live session. `group` is frozen here — the team active at click time
    is the one we write to, even if another tab changes it in the meantime.

    `return_app` (payload `app`) carries which FRONT requested the connection (e.g. a
    third-party tenant) — not the Salesforce application (`_APP`/`_read_app`, an entirely different
    meaning of the word in this module). Always written, even empty: `oauth_flow.return_url`
    correctly degrades an empty string to the historical oto-dashboard
    default. Already resolved/validated by the caller (`build_auth_url`) via
    `oauth_flow.resolve_return_app` — this module does not revalidate."""
    payload = {"sub": sub, "org": org_id, "scope": scope, "v": verifier, "app": return_app}
    if group_id is not None:
        payload["group"] = group_id
    return oauth_flow.sign_state(_AUD, payload)


def verify_state(state: str) -> Optional[tuple[str, int, str, str, Optional[int], str]]:
    """(sub, org_id, scope, verifier, group_id, return_app) if the state is valid,
    unexpired and issued FOR this flow; None otherwise. `group_id` is only set in
    `group` scope — and a `scope="group"` payload without `group` is refused (we do not
    guess the team to write a secret to).

    `return_app` missing (state signed BEFORE this field, still alive in the 10-minute
    window of a deploy) or of the wrong type ⇒ `""`, not a refusal of the whole
    state — losing the targeted return is no reason to lose the connection."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    sub, org, scope, verifier, group, return_app = (
        data.get("sub"), data.get("org"), data.get("scope"),
        data.get("v"), data.get("group"), data.get("app"))
    if (not isinstance(sub, str) or not isinstance(org, int)
            or scope not in ("member", "org", "group") or not isinstance(verifier, str)):
        return None
    if scope == "group" and not isinstance(group, int):
        return None
    if not isinstance(return_app, str):
        return_app = ""
    return sub, org, scope, verifier, group, return_app


def _read_fields(entity_type: str, entity_id: str) -> Optional[dict]:
    """The customer's already-saved partial credential (client_id/client_secret/
    login_url, and — after a first Connect — refresh_token too), or None if
    nothing has been saved yet for this entity."""
    row = credentials_store.get_credential_with_meta(entity_type, entity_id, "salesforce")
    if not row or not row.get("secret"):
        return None
    return credentials_store.unpack_secret("salesforce", row["secret"])


def read_saved_fields(sub: str, org_id: int, scope: str,
                      group_id: Optional[int] = None) -> Optional[dict]:
    """The application to use for the code EXCHANGE, on return from Salesforce.

    Same rule as on the way out (`build_auth_url`): the row of this scope if it exists,
    otherwise the nearest application going up. The two MUST agree — the
    code was issued for a specific `client_id`, exchanging it with another fails.
    That is why this entry point no longer queries the exact entity."""
    entity_type, entity_id = credentials_store.entity_for_scope(scope, org_id, sub, group_id)
    champs = _read_fields(entity_type, entity_id)
    if champs and all(champs.get(k) for k in _APP):
        return champs
    return _read_app(org_id, sub, scope, group_id)


_APP = ("client_id", "client_secret", "login_url")


def _entites_montantes(org_id: int, sub: str, scope: str,
                       group_id: Optional[int]) -> list[tuple[str, str]]:
    """The entities where to LOOK for the application, from the requested scope upwards.

    The application (client_id/secret/login_url) is ORG infrastructure: an
    admin sets it once. The refresh token, on the other hand, is an IDENTITY: it belongs to
    whoever consents. Reading them in the same place forced every member to paste back
    their org's application credentials just to authenticate —
    in practice, to know a secret that is none of their business.
    """
    # Built PER SCOPE, with no index arithmetic: a version computed on
    # positions assumed the team was always present and went out of bounds without it
    # (hence no entity, hence "no application" on a perfectly valid case).
    org = ("org", str(org_id))
    equipe = ("group", str(group_id)) if group_id else None
    if scope == "org":
        return [org]
    if scope == "group":
        return [e for e in (equipe, org) if e]
    membre = (credentials_store.MEMBER, credentials_store.member_id(org_id, sub))
    return [e for e in (membre, equipe, org) if e]


def _read_app(org_id: int, sub: str, scope: str,
              group_id: Optional[int]) -> Optional[dict]:
    """The nearest COMPLETE application, going up from the requested scope."""
    for etype, eid in _entites_montantes(org_id, sub, scope, group_id):
        champs = _read_fields(etype, eid)
        if champs and all(champs.get(k) for k in _APP):
            return champs
    return None


def _clean_login_url(login_url: Optional[str]) -> str:
    return (login_url or "").strip().rstrip("/") or "https://login.salesforce.com"


def build_auth_url(sub: str, scope: str = "member", return_app: Optional[str] = None) -> str:
    """Authorize URL for THIS customer's Connected App — the client_id/login_url
    come from their own already-saved credential, never a module constant
    (unlike every other oauth module here, whose client is Otomata-owned).

    Raises `LookupError` if client_id/client_secret/login_url aren't saved yet
    (the `/start` route translates this into an actionable 400 the dashboard's
    Connect button can gate on) and `PermissionError` if `scope="org"`/`"group"`
    is requested by a non-admin.

    `return_app`: front key declared by the CALLER (e.g. a third-party tenant), never a
    sniffed Origin (capabilities are transport-agnostic, ADR 0009). Validated
    HERE, once, BEFORE `make_state` — `oauth_flow.resolve_return_app`
    reduces any value outside its closed list to `""`: the state never carries
    an unverified client value (no open redirect).
    """
    if scope not in ("member", "org", "group"):
        raise ValueError(f"invalid scope: {scope!r} (expected 'member', 'org' or 'group')")
    org_id = _ctx_org(sub)
    group_id: Optional[int] = None
    if scope == "org":
        from .. import roles
        if not roles.is_org_admin(sub, org_id):
            from .. import detenteurs  # WHO can, named to a member (oto#108)
            raise PermissionError(
                "Only an org_admin can connect Salesforce on behalf of the whole org."
                + detenteurs.phrase("Ses administrateurs, à qui le demander",
                                    detenteurs.admins_de_l_org(sub, org_id))
            )
    elif scope == "group":
        from .. import roles
        group_id = _ctx_group(sub)
        if not roles.can_admin_group(sub, group_id):
            from .. import detenteurs  # WHO can, named to a member (oto#108)
            raise PermissionError(
                "Only a team lead can connect Salesforce on behalf of the whole team."
                + detenteurs.phrase("Chefs de cette équipe",
                                    detenteurs.chefs_de_l_equipe(sub, group_id, org_id))
            )
    # The application is looked up IN CASCADE (see `_entites_montantes`): a member
    # consents with their org's application without ever knowing its credentials.
    # The token, for its part, will be written at the requested scope — that is the whole asymmetry.
    fields = _read_app(org_id, sub, scope, group_id)
    if not fields:
        raise LookupError(
            "No Salesforce application is registered at this level or above. "
            "An administrator must set the Consumer Key, the Consumer Secret and the "
            "Login URL on the connector card (at org level so that the whole "
            "team benefits), then restart the authorization."
        )
    resolved_app = oauth_flow.resolve_return_app(return_app)
    from urllib.parse import urlencode
    verifier, challenge = oauth2_pkce.pkce_pair()
    login_url = _clean_login_url(fields["login_url"])
    params = {
        "response_type": "code",
        "client_id": fields["client_id"],
        "redirect_uri": oauth_flow.redirect_uri(_CALLBACK_PATH),
        "scope": SCOPES,
        "state": make_state(sub, org_id, scope, verifier, group_id, resolved_app),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{login_url}/services/oauth2/authorize?{urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str, login_url: str,
                  verifier: str) -> dict:
    """Exchange the code for tokens at THIS CLIENT's endpoint (its `login_url`,
    not a fixed Otomata URL). The dance itself lives in `oauth_flow.exchange_code`
    (form-encoded body, never a secret in the query string, error without URL); here we only
    keep the PKCE `code_verifier` and the translation of the message into an actionable hint
    (`_sf_error_hint`, shared with the probe)."""
    from ..tools.salesforce import _sf_error_hint
    try:
        return oauth_flow.exchange_code(
            f"{_clean_login_url(login_url)}/services/oauth2/token",
            code=code, client_id=client_id, client_secret=client_secret,
            redirect=oauth_flow.redirect_uri(_CALLBACK_PATH),
            extra={"code_verifier": verifier},
        )
    except oauth_flow.OAuthFlowError as e:
        raise RuntimeError(_sf_error_hint(e)) from e


def persist_token(sub: str, org_id: int, scope: str, token_response: dict,
                  group_id: Optional[int] = None) -> dict:
    """Synchronous (DB): the caller runs it via `run_in_threadpool`, never in the event loop.

    Read-merge-write: `secret_enc` is one encrypted blob per row (no
    column-level partial update for a multi-field secret exists in
    credentials_store) — so we read back the client_id/client_secret/login_url
    saved before `/start`, merge in the refresh_token this exchange just
    produced, and re-pack the whole thing. `instance_url`/`identity_url` go in
    `meta` (unencrypted, freely mergeable later — e.g. on token refresh —
    without touching the encrypted blob), same pattern as Google's
    access_token/expires_at satellites.
    """
    # Checked before any DB read — matches folk_oauth.py's persist_token order
    # (fail fast on the more obviously wrong input, no unnecessary round trip).
    refresh_token = token_response.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "Salesforce did not return a refresh_token. Check that the "
            "`refresh_token` (or `offline_access`) scope is ticked in the OAuth "
            "Scopes of the Connected App (Setup → App Manager → your app → "
            "Edit Policies)."
        )
    entity_type, entity_id = credentials_store.entity_for_scope(scope, org_id, sub, group_id)
    # `existing` = the row of THIS scope if it exists (we preserve what it carries).
    # Otherwise the application comes from the cascade: that is the case of a member who consents
    # with their org's application — they have no row of their own before this moment.
    # The application credentials are then COPIED into their row, which is
    # acceptable here: regenerating the application secret on the Salesforce side invalidates
    # all issued tokens anyway, hence forces everyone to reconnect.
    existing = _read_fields(entity_type, entity_id) or _read_app(
        org_id, sub, scope, group_id)
    if not existing:
        raise RuntimeError(
            "The Salesforce application disappeared between the click on Connect and the "
            "return from Salesforce — start over."
        )
    merged = {**existing, "refresh_token": refresh_token}
    secret = credentials_store.pack_secret("salesforce", merged)
    meta = {
        "instance_url": token_response.get("instance_url"),
        "identity_url": token_response.get("id"),
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    if scope == "org":
        org_store.set_org_secret(org_id, "salesforce", secret, set_by=sub, meta=meta)
    elif scope == "group":
        from .. import group_store
        group_store.set_group_secret(group_id, "salesforce", secret, set_by=sub, meta=meta)
    else:
        credentials_store.set_credential(entity_type, entity_id, "salesforce", secret,
                                         set_by=sub, meta=meta)

    # NO post-write probe. There used to be one — "best-effort", meant to
    # confirm that the freshly obtained token worked. Under rotation (RTR,
    # imposed by Salesforce), it **destroyed** what it verified: the probe
    # consumes the refresh token, Salesforce returns a new one, and this path
    # had no way of writing it. Measured on 31/07, three times in a row —
    # token set at 14:36:58.158, probed successfully at 14:36:58.679, dead afterwards.
    #
    # Wiring persistence into the probe is not enough here: we are in the
    # OAuth CALLBACK, a browser request without authenticated context (the `sub`
    # comes from the signed state, not from a token), so the probe cannot resolve the
    # cascade to know where to write back.
    #
    # And the cost bought nothing: `verified_at`/`verify_error` had NO
    # reader — neither backend nor dashboard. The original comment already admitted
    # that a failure was never used to reject the token. So we paid for
    # the connection with a marker that nobody read.
    #
    # The real state of the connection is found at first use, or via the explicit
    # probe (`oto_instance op=verify`), which runs in an authenticated context
    # and, for its part, persists the renewed token.
    return {"verified": None, "verify_error": None}
