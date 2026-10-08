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
client registration, and a registration is bound to ONE redirect URI. Instances
that share a base (prod and preprod do) each register their own callback: the
`client_id` is kept at the PLATFORM scope of `connector_settings` under a key that
names that callback (`client_id@<redirect_uri>`), never one key for all.
- A flow carries its `client_id` in the signed state, and the code is exchanged
  with THAT one — two first flows racing to register each finish with their own.
- The connection RECORDS the `client_id` and `redirect_uri` it was granted under
  (`meta`), and renewal uses them: changing or clearing a setting never breaks a
  refresh token issued earlier. Renewal never registers anything; a row without
  a recorded client is refused, named, and asks to reconnect.

**Renewal.** `access_token_for` asks for a fresh access token with the refresh
token when the cached one expires within a minute. The server rotates refresh
tokens (OAuth 2.1, public client): renewal therefore runs under a lock on the ROW
(an advisory lock in the shared base, so across processes AND instances), re-reads
the row once the lock is held, and writes the rotation in the same transaction —
a second caller finds the renewed token instead of replaying a spent one. A
refusal is re-checked against the row before it marks anything: a secret that
changed meanwhile is a reconnection, retried once, never a dead grant. The access
token itself never goes to the database: a process cache keyed by the hash of the
row and its refresh token. A dead authorization MARKS the row (`health_ko`),
never purges it.

**The return is single-use and re-judged.** The state carries a `jti`, consumed
on return (a replayed state is refused); the person must still be a member of
the org and the connector still open there — read on return, not at start.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from .. import credentials_store, status_hints
from ..connectors import flow as connector_flow
from ..connectors import health as connector_health
from ..connectors import link as connector_link
from . import flow as oauth_flow
from . import pkce

logger = logging.getLogger("oto_mcp.auth.ubersuggest")

CONNECTOR = "ubersuggest"

_AUD = "ubersuggest"
CALLBACK_PATH = "/api/ubersuggest/oauth/callback"
_CLIENT_ID_KEY = "client_id"
_PLATFORM = ("platform", "platform")

# {opaque key: (access_token, epoch expiry)} — never in the database.
_JETONS: dict[str, tuple[str, float]] = {}
_VERROU = threading.Lock()


class UbersuggestReauthRequired(RuntimeError):
    """The person's authorization is dead: they must reconnect."""


class UbersuggestClientUnknown(RuntimeError):
    """The connection does not say which OAuth client it was granted under."""


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

def _redirect() -> str:
    return oauth_flow.redirect_uri(CALLBACK_PATH)


def cle_client(redirect_uri: str) -> str:
    """The setting that holds the `client_id` registered for `redirect_uri`."""
    return f"{_CLIENT_ID_KEY}@{redirect_uri}"


def client_id(redirect_uri: str) -> str:
    """The `client_id` registered for `redirect_uri` (this instance's callback): the
    platform setting if set, otherwise a dynamic registration, kept as that setting.
    Called at the START of a flow only — never on renewal."""
    from ..db import connector_settings as store

    cle = cle_client(redirect_uri)
    pose = (store.get_connector_setting(*_PLATFORM, CONNECTOR, cle) or "").strip()
    if pose:
        return pose
    cid = _coeur().auth.register_client([redirect_uri], client_name="oto")
    store.set_connector_setting(*_PLATFORM, CONNECTOR, cle, cid, set_by="system")
    logger.info("ubersuggest: OAuth client registered for %s", redirect_uri)
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


def make_state(sub: str, org_id: int, verifier: str, cid: str, return_app: str = "",
               group_id: Optional[int] = None) -> str:
    return oauth_flow.sign_state(_AUD, {"sub": sub, "org": org_id, "group": group_id,
                                        "v": verifier, "cid": cid, "app": return_app,
                                        "jti": oauth_flow.new_jti()})


def verify_state(state: str) -> Optional[dict]:
    """The state's payload if it is valid, issued FOR this flow, and complete
    (`sub`, `org`, verifier, `client_id`, `jti`); `None` otherwise."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    ok = (isinstance(data.get("sub"), str) and isinstance(data.get("org"), int)
          and isinstance(data.get("v"), str) and data["v"]
          and isinstance(data.get("cid"), str) and data["cid"]
          and isinstance(data.get("jti"), str) and data["jti"]
          and (data.get("group") is None or isinstance(data.get("group"), int)))
    if not ok:
        return None
    if not isinstance(data.get("app"), str):
        data["app"] = ""
    return data


def still_allowed(parsed: dict) -> bool:
    """Re-read ON RETURN, not at start: the person is still a member of the org, and
    the connector is still open there. Synchronous (SQL)."""
    from .. import roles
    from ..connectors import activation

    if not roles.is_org_member(parsed["sub"], parsed["org"]):
        return False
    return activation.cran_qui_coupe(CONNECTOR, parsed["org"], parsed.get("group")) is None


# --- starting the flow -------------------------------------------------------

def build_auth_url(sub: str, return_app: str = "") -> str:
    from .. import access  # lazy: avoids any import cycle at boot

    org_id = _ctx_org(sub)
    verifier, challenge = pkce.pkce_pair()
    redirect = _redirect()
    cid = client_id(redirect)
    return _coeur().auth.authorize_url(
        cid, redirect,
        make_state(sub, org_id, verifier, cid, oauth_flow.resolve_return_app(return_app),
                   access.current_group(sub)),
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
    callback_path=CALLBACK_PATH,
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


def _en_cache(ligne: tuple, refresh_token: str) -> Optional[str]:
    with _VERROU:
        cached = _JETONS.get(_cle(ligne, refresh_token))
    return cached[0] if cached and cached[1] > time.time() + 60 else None


def oublier_jeton(access_token: str) -> None:
    """Drops a cached access token the server refused (401): the next call renews
    instead of replaying it until its announced expiry."""
    with _VERROU:
        for cle in [c for c, (jeton, _) in _JETONS.items() if jeton == access_token]:
            del _JETONS[cle]


@contextmanager
def _verrou_ligne(ligne: tuple) -> Iterator[object]:
    """A transaction holding the renewal lock of ONE vault row — an advisory lock in
    the shared base, so it holds across processes and across instances. Yields the
    connection, for the rotation to be written under the lock."""
    from ..db import _connect

    with _connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                     ("ubersuggest-refresh|" + "|".join(ligne),))
        yield conn


def exchange_and_persist(parsed: dict, code: str) -> dict:
    """The code brought back by the browser → a grant → the vault, with the client and
    redirect it was granted under recorded on the row (`meta`), for renewal. The
    refresh token is the secret (`secret_kind="oauth"`); a reconnection replaces the
    row — under the row's renewal lock — and clears its health mark."""
    sub, org_id, cid = parsed["sub"], parsed["org"], parsed["cid"]
    redirect = _redirect()
    grant = _coeur().auth.exchange_code(cid, code, redirect, parsed["v"])
    if not grant.refresh_token:
        raise RuntimeError("Ubersuggest did not issue a refresh token: nothing was saved.")
    entity_type, entity_id = _scope(org_id, sub)
    meta = {"scopes": grant.scope, "connected_at": _iso(datetime.now(timezone.utc)),
            "client_id": cid, "redirect_uri": redirect}
    ligne = (entity_type, entity_id, "")
    with _verrou_ligne(ligne) as conn:
        credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                         grant.refresh_token, set_by=sub, meta=meta,
                                         conn=conn)
    _garder(ligne, grant)
    logger.info("ubersuggest: account connected (org=%s)", org_id)
    return {"connected": True}


def _lire(ligne: tuple) -> dict:
    entity_type, entity_id, account = ligne
    row = credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR,
                                                     account=account)
    if not row:
        raise UbersuggestReauthRequired(
            "No Ubersuggest connection any more for this account: connect it from your "
            "connectors page, connector « Ubersuggest ».")
    return row


def _renouveler(ligne: tuple, refresh_token: str, *, reprise: bool = False) -> str:
    """Under the row's lock: re-read, reuse a token a concurrent caller just got,
    otherwise renew and write the rotation in the same transaction."""
    coeur = _coeur()
    with _verrou_ligne(ligne) as conn:
        row = _lire(ligne)
        refresh_token = row["secret"]
        if (deja := _en_cache(ligne, refresh_token)):
            return deja  # renewed while we waited for the lock
        cid = (row.get("meta") or {}).get("client_id")
        if not cid:
            raise UbersuggestClientUnknown(
                "This Ubersuggest connection does not record the OAuth client it was "
                "granted under, so it cannot be renewed: reconnect from your connectors "
                "page, connector « Ubersuggest ».")
        try:
            grant = coeur.auth.refresh(cid, refresh_token)
        except coeur.UbersuggestGrantExpired as e:
            refus = e
        else:
            if grant.refresh_token != refresh_token:
                credentials_store.set_credential(
                    ligne[0], ligne[1], CONNECTOR, grant.refresh_token,
                    set_by=row.get("set_by"), meta=row.get("meta") or {},
                    account=ligne[2], conn=conn)
            _garder(ligne, grant)
            refus = None
    if refus is None:
        connector_health.record_health(CONNECTOR, ligne, True, None)
        return grant.access_token
    # A refusal is checked against the row before it marks anything: a secret that
    # changed meanwhile is a reconnection, not a dead grant.
    if not reprise and _lire(ligne)["secret"] != refresh_token:
        return _renouveler(ligne, refresh_token, reprise=True)
    message = ("Ubersuggest no longer accepts this sign-in (expired or revoked). "
               "Reconnect from your connectors page, connector « Ubersuggest ».")
    connector_health.mark_rejected(ligne[0], ligne[1], CONNECTOR, ligne[2], message)
    raise UbersuggestReauthRequired(message) from refus


def access_token_for(sub: str) -> str:
    """A valid access token for the person, renewed if it expires within a minute.
    Raises a `McpError` (not connected), `RuntimeError` (core missing, client not
    recorded) or `UbersuggestReauthRequired` (dead authorization: the row is marked,
    the card says "reconnect")."""
    from .. import access  # lazy: avoids any import cycle at boot

    rc = access.resolve_credential(CONNECTOR, want="byo", sub=sub)
    ligne = (rc.entity_type, rc.entity_id, rc.account or "")
    return _en_cache(ligne, rc.key) or _renouveler(ligne, rc.key)


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
