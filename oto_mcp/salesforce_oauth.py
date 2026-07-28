"""Salesforce OAuth — live "Connect" flow. THE only way to obtain the
`refresh_token` now — the old manual Postman-style acquisition (paste a
Callback URL that led nowhere on our side, copy an authorization code out of
the browser's address bar, exchange it by hand) is gone: `providers.py`'s
Salesforce entry no longer declares a `refresh_token` field at all, only
`client_id`/`client_secret`/`login_url`.

Unlike every other live-OAuth flow oto has (google/atlassian/folkmcp/memento),
Salesforce's OAuth client — a "Connected App" — is **per-customer**: each
customer creates their own inside their own org, with their own
`client_id`/`client_secret`/`login_url`. There is no platform-wide Salesforce
client oto could register once (Google/Atlassian/Folk all share ONE
Otomata-owned client). So this flow is a hybrid: the customer still saves
`client_id`/`client_secret`/`login_url` through the existing generic
`/api/settings/api-keys/salesforce` form (unchanged — those 3 fields are now
ALL of what that form collects for Salesforce), and this module's `/start`
reads THAT already-saved partial credential to build a per-customer authorize
URL, instead of a module-level constant like `google_oauth.py`'s
`GOOGLE_WORKSPACE_CLIENT_ID`. `providers.py`'s `oauth_followup=True` flag on
the registry entry signals this shape to the frontend (`auth_method` derives
to `"secret_then_oauth"`) so it knows to show a Connect button after the form
saves, rather than treating this as a plain `secret_kind="fields"` connector.

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

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from . import credentials_store, oauth2_pkce, org_store

# `api` = REST API access ; `refresh_token` = long-lived refresh token issuance
# (Salesforce also accepts the synonym `offline_access`, but this is the name
# used in Salesforce's own docs/UI for the "Perform requests at any time"
# scope, so it's what we document to the customer).
SCOPES = "api refresh_token"

_STATE_TTL = 600  # 10 min, matches google_oauth.py


def _state_secret() -> bytes:
    v = os.environ.get("OTO_MCP_OAUTH_STATE_SECRET")
    if not v:
        raise RuntimeError("OTO_MCP_OAUTH_STATE_SECRET env var manquante")
    return v.encode()


def _redirect_uri() -> str:
    base = os.environ.get("OTO_MCP_PUBLIC_URL", "https://mcp.oto.ninja").rstrip("/")
    return f"{base}/api/salesforce/oauth/callback"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _ctx_org(sub: str) -> int:
    from . import access  # lazy: avoid any import cycle at boot
    org = access.current_org(sub)
    if org is None:
        raise RuntimeError(
            "Aucune org de contexte — impossible de scoper la connexion Salesforce. "
            "Reconnecte-toi et réessaie."
        )
    return org


def _ctx_group(sub: str) -> int:
    from . import access  # lazy: avoid any import cycle at boot
    group = access.current_group(sub)
    if group is None:
        raise RuntimeError(
            "Aucune équipe active — impossible de connecter Salesforce au nom "
            "de ton équipe. Sélectionne une équipe active et réessaie."
        )
    return group


def make_state(sub: str, org_id: int, scope: str, verifier: str,
               group_id: Optional[int] = None) -> str:
    """HMAC-signed state: `<b64(payload)>.<b64(sig)>`, payload = {sub, org,
    scope, v, ts, group?}. `scope` ("member" | "org" | "group") travels here
    so the callback — which arrives from Salesforce with zero auth headers —
    knows which row to merge the refresh_token into without re-deriving it
    from a live session. `group_id` is baked in the SAME way `org_id` is (not
    re-resolved from `access.current_group(sub)` at callback time): the
    group active when the user clicked Connect must be the one the callback
    writes to, even if their active group changed in another tab meanwhile."""
    payload_dict = {"sub": sub, "org": org_id, "scope": scope, "v": verifier,
                    "ts": int(time.time())}
    if group_id is not None:
        payload_dict["group"] = group_id
    payload = json.dumps(payload_dict, separators=(",", ":")).encode()
    sig = hmac.new(_state_secret(), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def verify_state(state: str) -> Optional[tuple[str, int, str, str, Optional[int]]]:
    """(sub, org_id, scope, verifier, group_id) if the state is valid and
    unexpired, else None. `group_id` is None unless `scope == "group"`."""
    if not state or "." not in state:
        return None
    p_b64, sig_b64 = state.split(".", 1)
    try:
        payload = _b64url_decode(p_b64)
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    expected = hmac.new(_state_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if int(time.time()) - int(data.get("ts", 0)) > _STATE_TTL:
        return None
    sub, org, scope, verifier, group = (data.get("sub"), data.get("org"), data.get("scope"),
                                        data.get("v"), data.get("group"))
    if (not isinstance(sub, str) or not isinstance(org, int)
            or scope not in ("member", "org", "group") or not isinstance(verifier, str)):
        return None
    if scope == "group" and not isinstance(group, int):
        return None
    return sub, org, scope, verifier, group


def _fields_entity(org_id: int, sub: str, scope: str, group_id: Optional[int] = None) -> tuple[str, str]:
    if scope == "org":
        return "org", str(org_id)
    if scope == "group":
        return "group", str(group_id)
    return credentials_store.MEMBER, credentials_store.member_id(org_id, sub)


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
    """Public entry point for the callback route to read the customer's
    already-saved partial credential without reaching into this module's
    private entity-resolution helpers directly."""
    entity_type, entity_id = _fields_entity(org_id, sub, scope, group_id)
    return _read_fields(entity_type, entity_id)


def _clean_login_url(login_url: Optional[str]) -> str:
    return (login_url or "").strip().rstrip("/") or "https://login.salesforce.com"


def build_auth_url(sub: str, scope: str = "member") -> str:
    """Authorize URL for THIS customer's Connected App — the client_id/login_url
    come from their own already-saved credential, never a module constant
    (unlike every other oauth module here, whose client is Otomata-owned).

    Raises `LookupError` if client_id/client_secret/login_url aren't saved yet
    (the `/start` route translates this into an actionable 400 the dashboard's
    Connect button can gate on) and `PermissionError` if `scope="org"`/`"group"`
    is requested by a non-admin.
    """
    if scope not in ("member", "org", "group"):
        raise ValueError(f"scope invalide : {scope!r} (attendu 'member', 'org' ou 'group')")
    org_id = _ctx_org(sub)
    group_id: Optional[int] = None
    if scope == "org":
        from . import roles
        if not roles.is_org_admin(sub, org_id):
            raise PermissionError(
                "Seul un org_admin peut connecter Salesforce au nom de toute l'org."
            )
    elif scope == "group":
        from . import roles
        group_id = _ctx_group(sub)
        if not roles.can_admin_group(sub, group_id):
            raise PermissionError(
                "Seul un chef d'équipe peut connecter Salesforce au nom de toute l'équipe."
            )
    entity_type, entity_id = _fields_entity(org_id, sub, scope, group_id)
    fields = _read_fields(entity_type, entity_id)
    if not fields or not all(fields.get(k) for k in ("client_id", "client_secret", "login_url")):
        raise LookupError(
            "Enregistre d'abord le Consumer Key, le Consumer Secret et la Login URL "
            "de ta Connected App Salesforce (formulaire de connecteur), puis clique "
            "sur Connecter."
        )
    from urllib.parse import urlencode
    verifier, challenge = oauth2_pkce.pkce_pair()
    login_url = _clean_login_url(fields["login_url"])
    params = {
        "response_type": "code",
        "client_id": fields["client_id"],
        "redirect_uri": _redirect_uri(),
        "scope": SCOPES,
        "state": make_state(sub, org_id, scope, verifier, group_id),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{login_url}/services/oauth2/authorize?{urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str, login_url: str,
                  verifier: str) -> dict:
    """POSTs to this customer's own token endpoint (their `login_url`, not a
    fixed Otomata one). Raises with the same friendly hints `tools/salesforce.py`'s
    `connector_verify` probe already uses (`_sf_error_hint`) on failure."""
    import requests

    r = requests.post(
        f"{_clean_login_url(login_url)}/services/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _redirect_uri(),
            "code_verifier": verifier,
        },
        timeout=15,
    )
    if not r.ok:
        try:
            body = r.json()
        except Exception:
            body = {"error": r.text[:300]}
        from .tools.salesforce import _sf_error_hint
        raise RuntimeError(
            _sf_error_hint(RuntimeError(f"{body.get('error')}: {body.get('error_description', '')}"))
        )
    return r.json()


async def persist_token(sub: str, org_id: int, scope: str, token_response: dict,
                        group_id: Optional[int] = None) -> dict:
    """Read-merge-write: `secret_enc` is one encrypted blob per row (no
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
            "Salesforce n'a pas renvoyé de refresh_token. Vérifie que le scope "
            "`refresh_token` (ou `offline_access`) est coché dans les OAuth "
            "Scopes de la Connected App (Setup → App Manager → ton app → "
            "Edit Policies)."
        )
    entity_type, entity_id = _fields_entity(org_id, sub, scope, group_id)
    existing = _read_fields(entity_type, entity_id)
    if not existing:
        raise RuntimeError(
            "Le Consumer Key/Secret/Login URL ont disparu entre le clic sur "
            "Connecter et le retour de Salesforce — recommence."
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
        from . import group_store
        group_store.set_group_secret(group_id, "salesforce", secret, set_by=sub, meta=meta)
    else:
        credentials_store.set_credential(entity_type, entity_id, "salesforce", secret,
                                         set_by=sub, meta=meta)

    # Best-effort post-write confirmation. This deliberately differs from
    # api_key_save's verify-BEFORE-persist (#106): here, the write itself IS
    # the OAuth mechanic (there's no complete credential to test before it
    # exists), so there's nothing to gate the write on. A probe failure here
    # is surfaced in meta, never used to discard a token Salesforce itself
    # just issued via a real, successful browser authorization.
    from . import connector_verify
    verified = False
    verify_error = None
    try:
        await connector_verify.run("salesforce", merged)
        verified = True
    except Exception as e:  # noqa: BLE001 — the auth failure IS the result
        verify_error = str(e)
    credentials_store.update_meta(entity_type, entity_id, "salesforce", "", {
        "verified_at": datetime.now(timezone.utc).isoformat() if verified else None,
        "verify_error": verify_error,
    })
    return {"verified": verified, "verify_error": verify_error}
