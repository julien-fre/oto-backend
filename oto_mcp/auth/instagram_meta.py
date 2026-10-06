"""Instagram (statistics) — obtaining authorization, and KEEPING it alive.

Flow hosted by oto, on the common pattern (`connectors/flow` + `auth/flow`):

1. the person clicks "Connect" on the card → `_start_flow` returns the URL of the
   Instagram dialog, with a signed `state` that carries their identity;
2. Instagram sends them back to `/api/instagram_meta/oauth/callback` (hand-written
   route, `api/instagram_meta.py`); the code there becomes a 60-day token;
3. the token goes to the vault, MEMBER tier, with its expiry in `meta`.

This module carries the ACQUISITION and what the card displays. The service — resolving
a call's token, renewing it, refusing while naming the cause — lives in
`tools/instagram_meta_session.py`, which imports from here. The line between the two
is the trigger: a human click on one side, a tool call or the
daily pass on the other.

⚠️ **THIS TOKEN CAN ONLY BE RENEWED WHILE IT IS ALIVE.** Meta does not issue a
`refresh_token` on this product: the current token is exchanged for a new one, and
a dead token can no longer be exchanged. A connection forgotten for sixty days is not
degraded — it is LOST, and only the user can redo it.

This fact, and this fact alone, explains the two renewal mechanisms:

- **on use**, well in advance (`RENEW_WHEN_REMAINING_DAYS` = 53 days
  remaining out of 60, not 10): even very sparse use is then enough to keep it going;
- **and a DAILY pass** (`renouveler_les_jetons`, maintenance job
  `instagram-tokens`), because lazy renewal dies of non-use.
  A person who does not look at their statistics for two months would lose their
  connection without having done anything wrong — a failure mode we cannot
  ask the user to prevent.

⚠️ **The Meta application's coordinates (App ID, secret) are not in the
code**: they live in the database, at the PLATFORM scope of `connector_settings`, and
the instance operator sets them. This repository is public, and a Meta
application belongs to whoever created it — who answers for what it requests and
for whom it invites as a tester.

Ops setting: `OTO_MCP_PUBLIC_URL` and `OTO_MCP_OAUTH_STATE_SECRET`, already set
for the other flows. The return URL to declare at Meta is READ
(`connector_flow.callback_url("instagram_meta")`) — never hard-coded: preprod
and prod do not have the same one, and a URL written in prose lies as soon as it is
read from the other.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from .. import credentials_store, status_hints
from ..connectors import flow as connector_flow
from ..connectors import health as connector_health
from ..connectors import link as connector_link
from . import flow as oauth_flow

logger = logging.getLogger("oto_mcp.auth.instagram_meta")

CONNECTOR = "instagram_meta"

# Audience of the state: a state issued for this flow is valid ONLY for its callback.
_AUD = "instagram_meta"
_CALLBACK_PATH = "/api/instagram_meta/oauth/callback"

#: The Meta application's coordinates, under this connector in
#: `connector_settings`, platform scope.
#:
#: ⚠️ Unlike the three of `planity`, **`app_secret` is a real one**: it
#: signs the code exchange and the switch to a long-lived token. It is not in the vault for
#: all that, and for the opposite reason to the one that keeps a user's secret:
#: the vault stores what belongs to a PERSON or an ORG, and its cascade
#: would fetch it on someone's behalf. This one belongs to nobody in the
#: product — it is an instance setting, like a service key. It is
#: never returned by an API: `oto_admin_connector_setting` is reserved for the platform
#: admin, and nothing in this module writes it into a message or a log.
_REGLAGES = ("app_id", "app_secret")

#: The command that sets them. It lives IN the refusal message: a diagnostic that
#: does not state the gesture sends people searching, and that is how a valid
#: configuration gets relaunched six times.
_COMMANDE = ('oto_admin_connector_setting(op="set", connector="instagram_meta", '
             'key="<key>", value="<value>")')


def _coeur():
    """The `oto.tools.instagram_meta` package of oto-core, imported AT CALL TIME.

    Imported here and not at module load, for a PRODUCT reason: the
    connector stays **registered** even when oto-core is too old to
    carry it. It is then visible, selectable, and every call refuses while NAMING
    what is missing — instead of disappearing from the catalogue, which goes unnoticed
    and cannot be explained.

    `importlib` and not `import oto.tools.instagram_meta as …`: `oto` is a
    namespace package (PEP 420) shared between oto-core and oto-cli, and the
    `import a.b.c as x` form resolves by attribute on the parent there — which fails
    when the subpackage does not exist yet on that path."""
    import importlib

    try:
        return importlib.import_module("oto.tools.instagram_meta")
    except ImportError as e:
        raise RuntimeError(
            f"The `instagram_meta` connector is not installed on this instance: "
            f"the core lives in oto-core and the pinned tag does not carry it yet. "
            f"This is an instance configuration, not a problem with your account — "
            f"notify the operator. Detail: {e}") from e


# --- the application's coordinates, set by the operator ------------------------

def _reglages() -> dict:
    """The coordinates set in the database, platform scope. `{}` if the database is silent.

    COLD read and off-loop: it only happens at the start of a connection
    flow (a human click) or on return from a consent, never on the path
    of a tool call — that is the property `db/connector_settings` asks us to
    hold. What it buys: one write, and every deployed color
    sees it at the next flow, without a restart or per-process reload."""
    from ..db import connector_settings as store

    return {r["key"]: (r["value"] or "").strip()
            for r in store.list_connector_settings()
            if r["scope_type"] == "platform" and r["connector"] == CONNECTOR
            and r["key"] in _REGLAGES}


def coordonnees_manquantes() -> list[str]:
    """Those of the two keys that are missing from the database. Empty = all present."""
    poses = _reglages()
    return [nom for nom in _REGLAGES if not poses.get(nom)]


def app():
    """This instance's `InstagramApp`, or a refusal that NAMES what is missing.

    The refusal is the point: without it, the dialog would go out with an empty
    `client_id` and Instagram would show its generic error screen — which the user
    reads as "oto is not allowed", when nothing depends on them."""
    manquantes = coordonnees_manquantes()
    if manquantes:
        raise RuntimeError(
            f"The `instagram_meta` connector is not configured on this "
            f"instance: {', '.join(manquantes)} missing. It is not your "
            f"account — there is nothing for you to do: notify the instance "
            f"operator, who sets them with {_COMMANDE}.")
    poses = _reglages()
    return _coeur().InstagramApp(app_id=poses["app_id"],
                                 app_secret=poses["app_secret"])


def app_disponible(sub: str) -> bool:
    """`app_ready` of the flow descriptor: is there enough to start a dialog?

    Does not depend on `sub` — the application is the instance's, the same for
    everyone — but the seam's signature passes it, and a connector whose
    app were per-user would answer differently."""
    del sub
    return not coordonnees_manquantes()


# --- the signed state ----------------------------------------------------------

def _ctx_org(sub: str) -> int:
    from .. import access  # lazy: avoids any import cycle at boot

    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "No context org — cannot scope the Instagram account. "
            "Sign in again and retry.")
    return org


def make_state(sub: str, org_id: int, return_app: str = "") -> str:
    """Signed state, BOUND to the `instagram_meta` audience.

    Carries `org` because the credential is scoped (org, sub): the callback arrives
    without an auth header, so these values must travel with it rather than be
    re-derived from a live session. `app` carries the FRONT that requested the
    connection, already reduced by `oauth_flow.resolve_return_app` to a closed
    list — the state never carries an unverified client value."""
    return oauth_flow.sign_state(_AUD, {"sub": sub, "org": org_id, "app": return_app})


def verify_state(state: str) -> Optional[tuple[str, int, str]]:
    """`(sub, org_id, return_app)` if the state is valid, not expired and issued FOR
    this flow; `None` otherwise — a callback never distinguishes the causes of a refusal.

    `app` missing or of the wrong type ⇒ `""`, not a refusal of the whole state: losing
    the targeted return is no reason to lose the connection."""
    data = oauth_flow.read_state(_AUD, state)
    if not data:
        return None
    sub, org, return_app = data.get("sub"), data.get("org"), data.get("app")
    if not isinstance(sub, str) or not isinstance(org, int):
        return None
    return sub, org, return_app if isinstance(return_app, str) else ""


# --- starting the flow ---------------------------------------------------------

def build_auth_url(sub: str, return_app: str = "") -> str:
    """The URL of the Instagram consent dialog for THIS person."""
    org_id = _ctx_org(sub)
    return _coeur().authorize_url(
        app(), oauth_flow.redirect_uri(_CALLBACK_PATH),
        make_state(sub, org_id, oauth_flow.resolve_return_app(return_app)))


def _start_flow(ctx, values: dict) -> "connector_flow.FlowStart":
    """The "connect" gesture, declared like that of any other connector.

    An unconfigured instance (or an oto-core that is too old) is a refusal
    at the ENTRY, not an outage: translated into a named error, the caller knows that
    retrying will change nothing. `app` is a HIDDEN key, passed outside the form
    by the front end that knows who it is — never a visible field."""
    from ..capabilities._types import AuthzDenied

    try:
        return connector_flow.FlowStart(
            auth_url=build_auth_url(ctx.sub, (values or {}).get("app") or ""))
    except RuntimeError as e:
        raise AuthzDenied(503, "oauth_misconfigured", str(e))


connector_flow.declare(
    CONNECTOR,
    start=_start_flow,
    label="Authorize oto on Instagram",
    callback_path=_CALLBACK_PATH,
    app_ready=app_disponible,
)


# --- the vault ------------------------------------------------------------------

def _scope(org_id: int, sub: str) -> tuple[str, str]:
    """This person's vault row in this org (MEMBER tier)."""
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


def persist_grant(sub: str, org_id: int, grant) -> dict:
    """Stores the token and its satellites, and returns what can be displayed.

    The SECRET is the long-lived token, alone (`secret_kind="oauth"` ⟹ no field
    schema, so the blob is the raw value). Everything else goes in `meta`, in
    clear and mergeable: `user_id` without which no data path can be
    built, `username` so the card says WHICH account is connected, and
    above all `expires_at` — without a stored expiry, we can only suffer
    the expiration, and that is precisely what this connector cannot
    afford."""
    coeur = _coeur()
    maintenant = coeur.utcnow()
    expires_at = coeur.iso(maintenant + timedelta(seconds=grant.expires_in))
    entity_type, entity_id = _scope(org_id, sub)
    credentials_store.set_credential(
        entity_type, entity_id, CONNECTOR, grant.access_token, set_by=sub,
        meta={"user_id": grant.user_id, "username": grant.username,
              "connected_at": coeur.iso(maintenant), "expires_at": expires_at})
    logger.info("instagram_meta: account connected (org=%s, expires %s)",
                org_id, expires_at)
    return {"username": grant.username, "expires_at": expires_at}


def _row(org_id: int, sub: str) -> Optional[dict]:
    entity_type, entity_id = _scope(org_id, sub)
    return credentials_store.get_credential_with_meta(entity_type, entity_id, CONNECTOR)


# --- what the card displays ----------------------------------------------------

def _link_state(sub: str) -> connector_link.LinkState:
    """Link state for `/api/me`. Single account: one vault row per member.

    The recorded rejection is READ HERE rather than inferred elsewhere — this is what lets
    the card say "authorization expired, needs reconnecting" without waiting for a
    call to fail."""
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
    """`status_hints` hook: what remains to be done, as a label the front end renders
    as is — and that says WHO must do it.

    Three possible steps, and the first does not belong to the user: without
    application coordinates, the "Connect" button cannot succeed, and showing
    them "Authorize oto" would send them clicking in a loop on a dialog that Meta
    will refuse. A verdict that points at the wrong person costs more than no
    verdict."""
    del org, group, entry
    if coordonnees_manquantes():
        return "Instagram application to be configured by the operator"
    etat = _link_state(sub)
    if not etat.linked:
        return "Authorize oto on Instagram"
    if etat.health_ko:
        return "Authorization expired — reconnect your account"
    return None


status_hints.register(CONNECTOR, _etape_manquante)


def avertir_au_demarrage() -> None:
    """Says AT BOOT what will prevent the connector from serving. Never raises.

    The connector stays registered: without this line, an operator who has not
    set the coordinates would only learn it at a user's first click —
    that is, at the worst moment, and from her."""
    try:
        manquantes = coordonnees_manquantes()
    except Exception as e:  # noqa: SILENT — at boot the database may not be ready
        logger.info("instagram_meta: configuration cannot be verified at startup "
                    "(%s) — the first flow will settle it.", type(e).__name__)
        return
    if manquantes:
        logger.warning(
            "instagram_meta: connector mounted but NOT configured — %s "
            "missing. The \"Connect\" button will refuse and say so. "
            "Set with: %s", ", ".join(manquantes), _COMMANDE)
