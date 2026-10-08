"""Ubersuggest — a PERSON's connection (OAuth 2.1, public client, PKCE).

Flow hosted by oto, on the common pattern (`connectors/flow` + `auth/flow`), same
shape as `microsoft`:

1. "Connect" on the card → `_start_flow` returns the Ubersuggest sign-in URL, with
   a signed `state` carrying the identity AND the PKCE verifier (integrity, not
   secrecy: the code only ever reaches our HTTPS callback — `auth/pkce.py`);
2. Ubersuggest sends the browser back to `/api/ubersuggest/oauth/callback`
   (`api/ubersuggest.py`); the code there becomes a refresh token;
3. the refresh token goes to the vault, MEMBER tier: the agent works with THAT
   person's Ubersuggest plan and projects.

**No application to register by hand.** The authorization server takes dynamic
client registration: the first flow on an instance registers its callback URL and
keeps the returned `client_id` at the PLATFORM scope of `connector_settings`
(`client_id`). An operator can pin or clear it there like any setting.

**Renewal.** `access_token_for` asks for a fresh access token with the refresh
token when the cached one expires within a minute; the server may rotate the
refresh token, the new one then replaces the old one on the row. The access token
itself never goes to the database: a process cache keyed by the hash of the row
and its refresh token (a reconnection invalidates it by itself). A dead
authorization MARKS the row (`health_ko`), never purges it.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from .. import credentials_store, status_hints
from ..connectors import flow as connector_flow
from ..connectors import health as connector_health
from ..connectors import link as connector_link
from . import flow as oauth_flow
from . import pkce

logger = logging.getLogger("oto_mcp.auth.ubersuggest")

CONNECTOR = "ubersuggest"

_AUD = "ubersuggest"
_CALLBACK_PATH = "/api/ubersuggest/oauth/callback"
_CLIENT_ID_KEY = "client_id"
_PLATFORM = ("platform", "platform")

# {opaque key: (access_token, epoch expiry)} — never in the database.
_JETONS: dict[str, tuple[str, float]] = {}
_VERROU = threading.Lock()


class UbersuggestReauthRequired(RuntimeError):
    """The person's authorization is dead: they must reconnect."""


def _coeur():
    """oto-core's `oto.tools.ubersuggest`, imported AT CALL TIME — the connector
    stays mounted (and refuses, saying so) when the pinned tag does not carry it yet."""
    import importlib

    try:
        return importlib.import_module("oto.tools.ubersuggest")
    except ImportError as e:
        raise RuntimeError(
            "The `ubersuggest` connector is not installed on this instance: its core "
            "lives in oto-core and the pinned tag does not ship it yet. This is an "
            "instance configuration issue, not your account — tell the operator. "
            f"Detail: {e}") from e


# --- the OAuth client -------------------------------------------------------

def client_id() -> str:
    """The instance's `client_id`: the platform setting if set, otherwise a dynamic
    registration of this instance's callback, kept as that setting."""
    from ..db import connector_settings as store

    pose = (store.get_connector_setting(*_PLATFORM, CONNECTOR, _CLIENT_ID_KEY) or "").strip()
    if pose:
        return pose
    cid = _coeur().auth.register_client([oauth_flow.redirect_uri(_CALLBACK_PATH)],
                                        client_name="oto")
    store.set_connector_setting(*_PLATFORM, CONNECTOR, _CLIENT_ID_KEY, cid, set_by="system")
    logger.info("ubersuggest: OAuth client registered for this instance")
    return cid


# --- the signed state --------------------------------------------------------

def _ctx_org(sub: str) -> int:
    from .. import access  # lazy: avoids any import cycle at boot

    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "No org in context — cannot scope the Ubersuggest connection. Sign in "
            "again and retry.")
    return org


def make_state(sub: str, org_id: int, verifier: str, return_app: str = "") -> str:
    return oauth_flow.sign_state(_AUD, {"sub": sub, "org": org_id, "v": verifier,
                                        "app": return_app})


def verify_state(state: str) -> Optional[tuple[str, int, str, str]]:
    """`(sub, org_id, verifier, return_app)` if the state is valid and issued FOR
    this flow."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    sub, org, v, return_app = data.get("sub"), data.get("org"), data.get("v"), data.get("app")
    if not isinstance(sub, str) or not isinstance(org, int) or not isinstance(v, str) or not v:
        return None
    return sub, org, v, return_app if isinstance(return_app, str) else ""


# --- starting the flow -------------------------------------------------------

def build_auth_url(sub: str, return_app: str = "") -> str:
    org_id = _ctx_org(sub)
    verifier, challenge = pkce.pkce_pair()
    return _coeur().auth.authorize_url(
        client_id(), oauth_flow.redirect_uri(_CALLBACK_PATH),
        make_state(sub, org_id, verifier, oauth_flow.resolve_return_app(return_app)),
        challenge)


def _start_flow(ctx, values: dict) -> "connector_flow.FlowStart":
    from ..capabilities._types import AuthzDenied

    try:
        return connector_flow.FlowStart(
            auth_url=build_auth_url(ctx.sub, (values or {}).get("app") or ""))
    except Exception as e:  # registration refused, core missing, no org
        raise AuthzDenied(503, "oauth_misconfigured", str(e))


connector_flow.declare(
    CONNECTOR,
    start=_start_flow,
    label="Sign in with Ubersuggest",
    callback_path=_CALLBACK_PATH,
)


# --- the vault ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope(org_id: int, sub: str) -> tuple[str, str]:
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def _cle(ligne: tuple, refresh_token: str) -> str:
    """Cache key: the row and its refresh token, hashed (never a clear-text secret
    as a key)."""
    return hashlib.sha256("|".join((*ligne, refresh_token)).encode()).hexdigest()


def _garder(ligne: tuple, grant) -> None:
    with _VERROU:
        _JETONS[_cle(ligne, grant.refresh_token)] = (
            grant.access_token, time.time() + int(grant.expires_in))


def exchange_and_persist(sub: str, org_id: int, code: str, verifier: str) -> dict:
    """The code brought back by the browser → a grant → the vault. The refresh token
    is the secret (`secret_kind="oauth"`); a reconnection replaces the row and
    clears its health mark."""
    grant = _coeur().auth.exchange_code(
        client_id(), code, oauth_flow.redirect_uri(_CALLBACK_PATH), verifier)
    if not grant.refresh_token:
        raise RuntimeError("Ubersuggest did not issue a refresh token: nothing was saved.")
    entity_type, entity_id = _scope(org_id, sub)
    meta = {"scopes": grant.scope, "connected_at": _iso(datetime.now(timezone.utc))}
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     grant.refresh_token, set_by=sub, meta=meta)
    _garder((entity_type, entity_id, ""), grant)
    logger.info("ubersuggest: account connected (org=%s)", org_id)
    return {"connected": True}


def _ranger_rotation(ligne: tuple, lu: str, nouveau: str) -> None:
    """The refresh token rotated: the new one replaces the old one, `meta` passed back
    as is. Conditional: a concurrent call that already rotated keeps its value."""
    entity_type, entity_id, account = ligne
    row = credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR,
                                                     account=account)
    if not row or row.get("secret") != lu:
        return
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR, nouveau,
                                     set_by=row.get("set_by"), meta=row.get("meta") or {},
                                     account=account)


def access_token_for(sub: str) -> str:
    """A valid access token for the person, renewed if it expires within a minute.
    Raises a `McpError` (not connected), `RuntimeError` (core missing) or
    `UbersuggestReauthRequired` (dead authorization: the row is marked, the card
    says "reconnect")."""
    from .. import access  # lazy: avoids any import cycle at boot

    rc = access.resolve_credential(CONNECTOR, want="byo", sub=sub)
    ligne = (rc.entity_type, rc.entity_id, rc.account or "")
    refresh_token = rc.key
    with _VERROU:
        cached = _JETONS.get(_cle(ligne, refresh_token))
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    coeur = _coeur()
    try:
        grant = coeur.auth.refresh(client_id(), refresh_token)
    except coeur.UbersuggestGrantExpired as e:
        message = ("Ubersuggest no longer accepts this sign-in (expired or revoked). "
                   "Reconnect from your connectors page, connector « Ubersuggest ».")
        connector_health.mark_rejected(rc.entity_type, rc.entity_id, CONNECTOR,
                                       rc.account or "", message)
        raise UbersuggestReauthRequired(message) from e
    if grant.refresh_token != refresh_token:
        _ranger_rotation(ligne, refresh_token, grant.refresh_token)
    connector_health.record_health(CONNECTOR, ligne, True, None)
    _garder(ligne, grant)
    return grant.access_token


# --- what the card displays --------------------------------------------------

def _row(org_id: int, sub: str) -> Optional[dict]:
    entity_type, entity_id = _scope(org_id, sub)
    return credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR)


def _link_state(sub: str) -> connector_link.LinkState:
    from .. import access  # lazy

    org = access.current_org(sub)
    row = _row(org, sub) if org is not None else None
    if not row:
        return connector_link.LinkState(linked=False)
    meta = row.get("meta") or {}
    return connector_link.LinkState(
        linked=True, accounts=1, set_at=str(row.get("set_at") or "") or None,
        health_ko=True if meta.get("health_ko") else None,
        health_reason=meta.get("health_reason") or None)


connector_link.register(CONNECTOR, _link_state)


def _etape_manquante(sub: str, org, group, entry: dict) -> Optional[str]:
    """What remains to be done by the person."""
    del org, group, entry
    etat = _link_state(sub)
    if not etat.linked:
        return "Sign in with Ubersuggest"
    if etat.health_ko:
        return "Sign-in expired — reconnect"
    return None


status_hints.register(CONNECTOR, _etape_manquante)
