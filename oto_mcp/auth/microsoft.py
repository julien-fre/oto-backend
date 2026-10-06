"""Microsoft 365 — a PERSON's connection (OAuth 2.0, delegated permissions).

Flow hosted by oto, on the common pattern (`connectors/flow` + `auth/flow`), same
shape as `meta_ads`:

1. "Sign in" on the card → `_start_flow` returns the Microsoft dialog URL,
   with a signed `state` that carries the identity;
2. Microsoft sends the browser back to `/api/microsoft/oauth/callback`
   (`api/microsoft.py`); the code there becomes a refresh token;
3. the refresh token goes to the vault, MEMBER tier: the person acts with THEIR
   Microsoft 365 rights, no more and no less.

**Multiple accounts** (oto-backend#23). A person may link several Microsoft
accounts (their directory, a client's…): one vault row PER ACCOUNT,
`account` = its lowercase address, the Microsoft account (`id` from `/me`) in meta.
Signing in with another account ADDS one; with the same one, replaces its own
(whether or not it was renamed). The first linked account is the default account,
as with Google. The rest is the GENERIC mechanics of multi-account
connectors (`cardinality="multi"`): choice via `_account=`, default via
`oto_identity(op='set')`, refusal that otherwise names the accounts (`access.resolve`),
listing via `oto_identity(op='list')`, removal of an account via
`DELETE /api/settings/api-keys/sharepoint?account=…`.

**Renewal.** An access token lives one hour; `access_token_for` requests it
again with the refresh token, and ⚠️ Entra ROTATES the latter: the new one
replaces the old one in the vault at every renewal, on THIS account's row. The
access token itself never goes to the database: it lives in a process cache, keyed
by the hash of the row and of its refresh token (a reconnection invalidates it
by itself). A dead authorization marks THIS account's row, not the others.

⚠️ The coordinates of oto's Microsoft application (multi-tenant, registered
in the operator's directory) live in the database, PLATFORM scope of
`connector_settings`, set by the operator.
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

logger = logging.getLogger("oto_mcp.auth.microsoft")

CONNECTOR = "sharepoint"

_AUD = "microsoft"
_CALLBACK_PATH = "/api/microsoft/oauth/callback"

#: `client_secret` carries the `_secret` suffix that the admin console masks.
_REGLAGES = ("client_id", "client_secret")

_COMMANDE = ('oto_admin_connector_setting(op="set", connector="sharepoint", '
             'key="<key>", value="<value>")')

# {opaque key: (access_token, epoch expiry)} — never in the database.
_JETONS: dict[str, tuple[str, float]] = {}
_VERROU = threading.Lock()


class MicrosoftReauthRequired(RuntimeError):
    """The person's authorization is dead: they must reconnect."""


def _coeur():
    """oto-core's `oto.tools.microsoft`, imported AT CALL TIME — the connector stays
    mounted (and refuses, saying so) when the pinned tag does not carry it yet."""
    import importlib

    try:
        return importlib.import_module("oto.tools.microsoft")
    except ImportError as e:
        raise RuntimeError(
            "The `sharepoint` connector is not installed on this instance: its core "
            "lives in oto-core and the pinned tag does not ship it yet. This is an "
            "instance configuration issue, not your account — tell the operator. "
            f"Detail: {e}") from e


# --- the application's coordinates -------------------------------------------

def _reglages() -> dict:
    """Database read: at the start of a flow, on return from a consent, and
    at the RENEWAL of an access token (once per hour per person)."""
    from ..db import connector_settings as store

    return {r["key"]: (r["value"] or "").strip()
            for r in store.list_connector_settings()
            if r["scope_type"] == "platform" and r["connector"] == CONNECTOR
            and r["key"] in _REGLAGES}


def coordonnees_manquantes() -> list[str]:
    poses = _reglages()
    return [nom for nom in _REGLAGES if not poses.get(nom)]


def app() -> dict:
    """The instance's `{client_id, client_secret}`, or a refusal that NAMES what is missing."""
    manquantes = coordonnees_manquantes()
    if manquantes:
        raise RuntimeError(
            f"The `sharepoint` connector is not configured on this instance: missing "
            f"{', '.join(manquantes)}. Nothing to do on your account — the instance "
            f"operator sets them with {_COMMANDE}.")
    poses = _reglages()
    return {"client_id": poses["client_id"], "client_secret": poses["client_secret"]}


def app_disponible(sub: str) -> bool:
    del sub
    return not coordonnees_manquantes()


# --- the signed state --------------------------------------------------------

def _ctx_org(sub: str) -> int:
    from .. import access  # lazy: avoids any import cycle at boot

    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "No org in context — cannot scope the Microsoft connection. Sign in "
            "again and retry.")
    return org


def make_state(sub: str, org_id: int, return_app: str = "") -> str:
    return oauth_flow.sign_state(_AUD, {"sub": sub, "org": org_id, "app": return_app})


def verify_state(state: str) -> Optional[tuple[str, int, str]]:
    """`(sub, org_id, return_app)` if the state is valid and issued FOR this flow."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    sub, org, return_app = data.get("sub"), data.get("org"), data.get("app")
    if not isinstance(sub, str) or not isinstance(org, int):
        return None
    return sub, org, return_app if isinstance(return_app, str) else ""


# --- starting the flow -------------------------------------------------------

def build_auth_url(sub: str, return_app: str = "") -> str:
    org_id = _ctx_org(sub)
    return _coeur().auth.authorize_url(
        app()["client_id"], oauth_flow.redirect_uri(_CALLBACK_PATH),
        make_state(sub, org_id, oauth_flow.resolve_return_app(return_app)))


def _start_flow(ctx, values: dict) -> "connector_flow.FlowStart":
    from ..capabilities._types import AuthzDenied

    try:
        return connector_flow.FlowStart(
            auth_url=build_auth_url(ctx.sub, (values or {}).get("app") or ""))
    except RuntimeError as e:
        raise AuthzDenied(503, "oauth_misconfigured", str(e))


connector_flow.declare(
    CONNECTOR,
    start=_start_flow,
    label="Sign in with Microsoft",
    callback_path=_CALLBACK_PATH,
    app_ready=app_disponible,
)


# --- the vault ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope(org_id: int, sub: str) -> tuple[str, str]:
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def _cle(ligne: tuple, refresh_token: str) -> str:
    """Cache key: the row (entity AND account) and its refresh token, hashed
    (never a clear-text secret as a key). A reconnection changes the refresh token,
    hence the key."""
    return hashlib.sha256("|".join((*ligne, refresh_token)).encode()).hexdigest()


def _garder(ligne: tuple, grant) -> None:
    with _VERROU:
        _JETONS[_cle(ligne, grant.refresh_token)] = (
            grant.access_token, time.time() + int(grant.expires_in))


def _comptes(org_id: int, sub: str) -> list[dict]:
    """The Microsoft accounts linked by the person in this org (no secret)."""
    entity_type, entity_id = _scope(org_id, sub)
    return credentials_store.list_accounts(entity_type, entity_id, CONNECTOR)


def persist_grant(sub: str, org_id: int, grant) -> dict:
    """Stores the account that has just connected, ALONGSIDE the others: the refresh
    token is the secret (`secret_kind="oauth"`), the identity read from `/me` goes into
    `meta`.

    The account is recognized by its Microsoft `id`, not by its row name: a
    reconnection of the same account replaces ITS row, even if renamed
    (`oto_identity(op='rename')`); another account creates one, named by its
    lowercase address. The first linked account is the default (Google rule); a
    reconnection keeps the row's default status and clears its health mark."""
    me = _coeur().GraphClient(grant.access_token).get_me() or {}
    microsoft_id = me.get("id")
    adresse = (me.get("mail") or me.get("userPrincipalName") or "").strip()
    if not microsoft_id or not adresse:
        raise RuntimeError(
            "Microsoft did not say which account just connected (`/me` without `id` "
            "or without address): nothing was saved.")
    entity_type, entity_id = _scope(org_id, sub)
    comptes = _comptes(org_id, sub)
    deja = next((c for c in comptes
                 if (c.get("meta") or {}).get("microsoft_id") == microsoft_id), None)
    if deja is not None:
        account = deja["account"]
    else:
        account = adresse.lower()
        if any(c["account"] == account for c in comptes):
            # Another Microsoft account was renamed to this address: overwriting it
            # would lose its connection.
            raise RuntimeError(
                f"Another linked Microsoft account already has the name `{account}`: "
                "rename it (oto_identity op='rename') then reconnect this one. "
                "Nothing was saved.")
    ancien = (deja or {}).get("meta") or {}
    meta = {**{k: v for k, v in ancien.items() if not k.startswith("health_")},
            "email": adresse,
            "name": me.get("displayName"),
            "microsoft_id": microsoft_id,
            "scopes": grant.scope,
            "connected_at": _iso(datetime.now(timezone.utc)),
            "is_default": bool(ancien.get("is_default")) if deja else not comptes}
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     grant.refresh_token, set_by=sub, meta=meta,
                                     account=account)
    _garder((entity_type, entity_id, account), grant)
    logger.info("sharepoint: Microsoft account %s (org=%s)",
                "reconnected" if deja else "added", org_id)
    return {"account": account, "email": adresse, "name": meta["name"]}


def _ranger_rotation(ligne: tuple, lu: str, nouveau: str) -> None:
    """The refresh token has rotated: the new one replaces the old one on THIS
    account's row, its `meta` passed back as is (the upsert would overwrite it otherwise). Conditional
    write: a concurrent call that has already rotated holds the most
    recent value, we do not replace it with ours."""
    entity_type, entity_id, account = ligne
    row = credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR,
                                                     account=account)
    if not row or row.get("secret") != lu:
        return
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR, nouveau,
                                     set_by=row.get("set_by"), meta=row.get("meta") or {},
                                     account=account)


def access_token_for(sub: str) -> str:
    """A valid access token for the person, on the account the call designates,
    renewed if it expires in less than a minute.

    The account is chosen by the COMMON resolution of multi-account connectors
    (`access.resolve_credential`): the call's `_account=`, otherwise the account pinned
    by the project, otherwise the only linked account, otherwise the default account, otherwise a
    refusal that names the accounts. Raises a `McpError` (no account, unknown account,
    ambiguity), `RuntimeError` (application not configured) or
    `MicrosoftReauthRequired` (dead authorization: THIS account's row is
    marked, the card says "needs reconnecting")."""
    from .. import access  # lazy: avoids any import cycle at boot

    rc = access.resolve_credential(CONNECTOR, want="byo", sub=sub)
    ligne = (rc.entity_type, rc.entity_id, rc.account)
    refresh_token = rc.key
    with _VERROU:
        cached = _JETONS.get(_cle(ligne, refresh_token))
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    coeur = _coeur()
    coordonnees = app()
    try:
        grant = coeur.auth.refresh(coordonnees["client_id"], coordonnees["client_secret"],
                                   refresh_token)
    except coeur.MicrosoftGrantExpired as e:
        message = (f"Microsoft no longer accepts the sign-in of `{rc.account}` (expired, "
                   "revoked, or the password changed). Reconnect this account from your "
                   "connectors page, connector « SharePoint & OneDrive ».")
        connector_health.mark_rejected(rc.entity_type, rc.entity_id, CONNECTOR,
                                       rc.account, message)
        raise MicrosoftReauthRequired(message) from e
    if grant.refresh_token != refresh_token:
        _ranger_rotation(ligne, refresh_token, grant.refresh_token)
    # A successful renewal clears THIS account's "needs reconnecting" mark.
    connector_health.record_health(CONNECTOR, ligne, True, None)
    _garder(ligne, grant)
    return grant.access_token


# --- what the card displays --------------------------------------------------

def _comptes_du_contexte(sub: str) -> tuple[list[dict], list[dict]]:
    """`(accounts, dead)` of the person in their context org — `dead` = those
    whose authorization has lapsed (`health_ko`), to be reconnected one by one."""
    from .. import access  # lazy

    org = access.current_org(sub)
    comptes = _comptes(org, sub) if org is not None else []
    return comptes, [c for c in comptes if (c.get("meta") or {}).get("health_ko")]


def _link_state(sub: str) -> connector_link.LinkState:
    """Linked as soon as there is one account; "needs reconnecting" if one of them does, naming it."""
    comptes, morts = _comptes_du_contexte(sub)
    if not comptes:
        return connector_link.LinkState(linked=False)
    return connector_link.LinkState(
        linked=True, accounts=len(comptes),
        set_at=max((str(c.get("set_at") or "") for c in comptes), default="") or None,
        health_ko=True if morts else None,
        health_reason="; ".join(
            f"{c['account']}: {(c.get('meta') or {}).get('health_reason') or 'rejected'}"
            for c in morts) or None)


connector_link.register(CONNECTOR, _link_state)


def _etape_manquante(sub: str, org, group, entry: dict) -> Optional[str]:
    """What remains to be done — and by WHOM. Without application coordinates, it is
    not for the person to click "Sign in" in a loop. A dead account among
    several is named: the others keep serving."""
    del org, group, entry
    if coordonnees_manquantes():
        return "Microsoft app to be configured by the operator"
    comptes, morts = _comptes_du_contexte(sub)
    if not comptes:
        return "Sign in with Microsoft"
    if morts:
        return f"Sign-in expired for {', '.join(c['account'] for c in morts)} — reconnect"
    return None


status_hints.register(CONNECTOR, _etape_manquante)


def avertir_au_demarrage() -> None:
    """Says AT BOOT what will prevent the connector from serving. Never raises."""
    try:
        manquantes = coordonnees_manquantes()
    except Exception as e:  # noqa: SILENT — at boot the database may not be ready
        logger.info("sharepoint: configuration cannot be verified at startup (%s) — "
                    "the first flow will settle it.", type(e).__name__)
        return
    if manquantes:
        logger.warning(
            "sharepoint: connector mounted but NOT configured — %s missing. "
            "The \"Sign in\" button will refuse and say so. Set with: %s",
            ", ".join(manquantes), _COMMANDE)
