"""Meta Ads — obtaining authorization (Facebook Login for Business).

Flow hosted by oto, on the common pattern (`connectors/flow` + `auth/flow`), copy
of `instagram_meta`'s:

1. "Connect" on the card → `_start_flow` returns the dialog URL, with a signed
   `state` carrying the identity;
2. Meta brings the browser back to `/api/meta_ads/oauth/callback`
   (`api/meta_ads.py`); the code becomes a token there;
3. the token goes to the vault, MEMBER tier.

**No renewal.** The expected configuration on the Meta side issues a system-user
token (BISU), with no expiry. If it issues a token with a lifetime,
the expiry is stored in `meta.expires_at` and the card goes "to reconnect" at the
first rejection — we do not clone `instagram_meta`'s daily pass for a case
we discourage.

⚠️ The Meta application's coordinates (App ID, secret, configuration) live in the
database, PLATFORM scope of `connector_settings`, set by the operator.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import credentials_store, status_hints
from ..connectors import flow as connector_flow
from ..connectors import link as connector_link
from . import flow as oauth_flow

logger = logging.getLogger("oto_mcp.auth.meta_ads")

CONNECTOR = "meta_ads"

_AUD = "meta_ads"
_CALLBACK_PATH = "/api/meta_ads/oauth/callback"

#: `config_id` = the Facebook Login for Business configuration (permissions + token
#: type). Only `app_secret` is secret — and it carries the `_secret` suffix that the
#: admin console masks.
_REGLAGES = ("app_id", "app_secret", "config_id")

_COMMANDE = ('oto_admin_connector_setting(op="set", connector="meta_ads", '
             'key="<key>", value="<value>")')


def _coeur():
    """oto-core's `oto.tools.meta_ads`, imported AT CALL TIME — the connector stays
    mounted (and refuses, saying so) when the pinned tag does not carry it yet.
    `importlib`: `oto` is a namespace package (cf. `instagram_meta`)."""
    import importlib

    try:
        return importlib.import_module("oto.tools.meta_ads")
    except ImportError as e:
        raise RuntimeError(
            "The `meta_ads` connector is not installed on this instance: its core "
            "lives in oto-core and the pinned tag does not ship it yet. This is an "
            "instance configuration issue, not your account — tell the operator. "
            f"Detail: {e}") from e


# --- the application's coordinates ----------------------------------------

def _reglages() -> dict:
    """COLD read, never on the path of a tool call: only at
    the start of a flow or on return from a consent."""
    from ..db import connector_settings as store

    return {r["key"]: (r["value"] or "").strip()
            for r in store.list_connector_settings()
            if r["scope_type"] == "platform" and r["connector"] == CONNECTOR
            and r["key"] in _REGLAGES}


def coordonnees_manquantes() -> list[str]:
    poses = _reglages()
    return [nom for nom in _REGLAGES if not poses.get(nom)]


def app():
    """The instance's `MetaAdsApp`, or a refusal that NAMES what is missing."""
    manquantes = coordonnees_manquantes()
    if manquantes:
        raise RuntimeError(
            f"The `meta_ads` connector is not configured on this instance: missing "
            f"{', '.join(manquantes)}. Nothing to do on your account — the instance "
            f"operator sets them with {_COMMANDE}.")
    poses = _reglages()
    return _coeur().MetaAdsApp(app_id=poses["app_id"],
                               app_secret=poses["app_secret"],
                               config_id=poses["config_id"])


def app_disponible(sub: str) -> bool:
    del sub
    return not coordonnees_manquantes()


# --- the signed state --------------------------------------------------------

def _ctx_org(sub: str) -> int:
    from .. import access  # lazy: avoids any import cycle at boot

    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "No org in context — cannot scope the Meta Ads connection. Sign in "
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


# --- flow start --------------------------------------------------------------

def build_auth_url(sub: str, return_app: str = "") -> str:
    org_id = _ctx_org(sub)
    return _coeur().authorize_url(
        app(), oauth_flow.redirect_uri(_CALLBACK_PATH),
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
    label="Authorize oto on Facebook",
    callback_path=_CALLBACK_PATH,
    app_ready=app_disponible,
)


# --- the vault ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope(org_id: int, sub: str) -> tuple[str, str]:
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def persist_grant(sub: str, org_id: int, grant) -> dict:
    """The token is the secret (`secret_kind="oauth"`); the rest goes into `meta`.

    `client_business_id` only exists for a BISU token: it says WHICH business
    portfolio authorized. `expires_at` is only stored if Meta gave one."""
    maintenant = datetime.now(timezone.utc)
    meta = {"user_id": grant.user_id, "name": grant.name,
            "client_business_id": grant.client_business_id,
            "connected_at": _iso(maintenant)}
    if grant.expires_in:
        meta["expires_at"] = _iso(maintenant + timedelta(seconds=grant.expires_in))
    entity_type, entity_id = _scope(org_id, sub)
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     grant.access_token, set_by=sub, meta=meta)
    logger.info("meta_ads: account connected (org=%s, business=%s, expiry=%s)",
                org_id, grant.client_business_id or "-",
                meta.get("expires_at") or "none")
    return {"name": grant.name, "expires_at": meta.get("expires_at")}


def _row(org_id: int, sub: str) -> Optional[dict]:
    entity_type, entity_id = _scope(org_id, sub)
    return credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR)


# --- what the card displays --------------------------------------------------

def _link_state(sub: str) -> connector_link.LinkState:
    from .. import access  # lazy

    org = access.current_org(sub)
    if org is None:
        return connector_link.LinkState(linked=False)
    row = _row(org, sub)
    if not row:
        return connector_link.LinkState(linked=False)
    meta = row.get("meta") or {}
    return connector_link.LinkState(
        linked=True, accounts=1, set_at=str(row.get("set_at") or "") or None,
        health_ko=True if meta.get("health_ko") else None,
        health_reason=meta.get("health_reason") or None)


connector_link.register(CONNECTOR, _link_state)


def _etape_manquante(sub: str, org, group, entry: dict) -> Optional[str]:
    """What remains to be done — and by WHOM. Without application coordinates, it is
    not for the user to click "Connect" in a loop."""
    del org, group, entry
    if coordonnees_manquantes():
        return "Meta app to be configured by the operator"
    etat = _link_state(sub)
    if not etat.linked:
        return "Authorize oto on Facebook"
    if etat.health_ko:
        return "Authorization revoked — reconnect"
    return None


status_hints.register(CONNECTOR, _etape_manquante)


def avertir_au_demarrage() -> None:
    """Says AT BOOT what will prevent the connector from serving. Never raises."""
    try:
        manquantes = coordonnees_manquantes()
    except Exception as e:  # noqa: SILENT — at boot the database may not be ready
        logger.info("meta_ads: configuration not verifiable at startup (%s) — "
                    "the first flow will decide.", type(e).__name__)
        return
    if manquantes:
        logger.warning(
            "meta_ads: connector mounted but NOT configured — %s missing. "
            "The \"Connect\" button will refuse, saying so. Set with: %s",
            ", ".join(manquantes), _COMMANDE)
