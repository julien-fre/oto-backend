"""Acquisition OAuth2 Zoho en **Server-based** — le second mode de connexion.

Les deux modes coexistent, et c'est ce qui rend l'ajout peu invasif : ils
produisent **exactement le même credential** (`client_id` + `client_secret` +
`refresh_token` + `data_center`). Seule la façon de l'OBTENIR change.

- **Self Client** (existant, inchangé) : l'utilisateur génère les valeurs dans la
  console Zoho et les colle dans le formulaire. Aucun code dédié — c'est le
  formulaire générique des credentials.
- **Server-based** (ce module) : l'utilisateur clique « Connecter », consent chez
  Zoho, et revient connecté. Rien à copier.

Pourquoi l'ajouter — trois incidents de la même famille (#190 `zoho_modules`
OAUTH_SCOPE_MISMATCH, #202 Analytics INVALID_OAUTHSCOPE, et le Desk limité à
`Desk.articles.READ` qui a supprimé la recherche native) : en Self Client, c'est
**l'utilisateur** qui choisit les scopes à la main, et il se trompe — sans qu'on
puisse rien corriger côté serveur. Ici **c'est oto qui les déclare** dans l'URL
d'autorisation. Bénéfice second : plus aucun secret n'a besoin de circuler par
mail (vécu 28/07, un `client_secret` reçu en clair).

**D'où vient l'app ?** Deux sources, dans cet ordre :
1. **app de plateforme** (env `ZOHO_OAUTH_CLIENT_ID_<DC>` / `..._SECRET_<DC>`) —
   le vrai « un clic » : l'utilisateur ne manipule rien. L'app Zoho étant liée à
   sa région, elle est déclarée PAR data center.
2. **app de l'org** : l'org a créé sa propre app et posé `client_id`/`client_secret`
   sur la carte. On pilote quand même les scopes et le token ne transite plus par
   mail. C'est le repli quand aucune app de plateforme n'existe pour la région.

Le `state` est signé HMAC (même schéma que `google_oauth`) : le callback arrive
du NAVIGATEUR de l'utilisateur, sans en-tête d'auth — c'est lui qui porte « ce
retour appartient à tel sub, telle org, tel connecteur ».
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Optional

import requests

from . import credentials_store, providers

_STATE_TTL = 600  # 10 min — le temps de lire un écran de consentement

# Régions Zoho : l'app OAuth et le refresh token sont liés à LEUR data center
# (un client `.eu` sur `accounts.zoho.com` = `invalid_client` opaque).
_ACCOUNTS = {
    "com": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "in": "https://accounts.zoho.in",
    "au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
}

# Scopes demandés PAR CONNECTEUR — c'est tout l'intérêt du mode server-based :
# ils ne dépendent plus de ce que l'utilisateur a coché.
SCOPES = {
    "zoho": ("ZohoCRM.modules.ALL", "ZohoCRM.settings.modules.READ",
             "ZohoCRM.settings.fields.READ", "ZohoCRM.users.READ"),
    "zohodesk": ("Desk.articles.READ", "Desk.basic.READ", "Desk.search.READ",
                 "Desk.tickets.READ", "Desk.contacts.READ"),
    "zohoanalytics": ("ZohoAnalytics.data.read", "ZohoAnalytics.metadata.read"),
}

CONNECTORS = tuple(SCOPES)


class ZohoOAuthError(ValueError):
    """Échec d'acquisition — message SANS secret (cf. `zoho.auth`, incident #284)."""


# --- app (client_id / client_secret) ----------------------------------------

def platform_app(data_center: str) -> Optional[tuple[str, str]]:
    """App de plateforme pour cette région, si déclarée en env. `None` sinon."""
    dc = (data_center or "").strip().lower()
    cid = os.environ.get(f"ZOHO_OAUTH_CLIENT_ID_{dc.upper()}")
    sec = os.environ.get(f"ZOHO_OAUTH_CLIENT_SECRET_{dc.upper()}")
    return (cid, sec) if cid and sec else None


def resolve_app(data_center: str, org_app: Optional[dict] = None) -> tuple[str, str]:
    """`(client_id, client_secret)` : app de plateforme d'abord, sinon celle de
    l'org. Lève un message actionnable si aucune n'est disponible."""
    app = platform_app(data_center)
    if app:
        return app
    cid = (org_app or {}).get("client_id")
    sec = (org_app or {}).get("client_secret")
    if cid and sec:
        return cid, sec
    raise ZohoOAuthError(
        f"Aucune app OAuth Zoho pour la région « {data_center} ». Soit la "
        f"plateforme en déclare une (ZOHO_OAUTH_CLIENT_ID_{data_center.upper()}), "
        f"soit ton org renseigne client_id + client_secret sur la carte du "
        f"connecteur avant de lancer la connexion.")


# --- state signé -------------------------------------------------------------

def _state_secret() -> bytes:
    v = os.environ.get("OTO_MCP_OAUTH_STATE_SECRET")
    if not v:
        raise ZohoOAuthError("OTO_MCP_OAUTH_STATE_SECRET env var manquante")
    return v.encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def redirect_uri() -> str:
    """UNE seule URI pour les trois connecteurs (le connecteur voyage dans le
    `state`) : une URI de redirection doit être enregistrée au byte près côté
    Zoho — en avoir une seule évite d'en déclarer trois par app."""
    base = os.environ.get("OTO_MCP_PUBLIC_URL", "https://mcp.oto.ninja").rstrip("/")
    return f"{base}/api/zoho/oauth/callback"


def make_state(sub: str, org_id: int, connector: str, data_center: str) -> str:
    payload = json.dumps({"sub": sub, "org": org_id, "c": connector,
                          "dc": data_center, "ts": int(time.time())},
                         separators=(",", ":")).encode()
    sig = hmac.new(_state_secret(), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(sig)}"


def verify_state(state: str) -> Optional[dict]:
    """`{sub, org, connector, data_center}` si signature valide et non expirée."""
    if not state or "." not in state:
        return None
    p_b64, sig_b64 = state.split(".", 1)
    try:
        payload, sig = _b64url_decode(p_b64), _b64url_decode(sig_b64)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(
            sig, hmac.new(_state_secret(), payload, hashlib.sha256).digest()):
        return None
    try:
        d = json.loads(payload)
    except Exception:  # noqa: BLE001
        return None
    if int(time.time()) - int(d.get("ts", 0)) > _STATE_TTL:
        return None
    if d.get("c") not in CONNECTORS or d.get("dc") not in _ACCOUNTS:
        return None
    if not isinstance(d.get("sub"), str) or not isinstance(d.get("org"), int):
        return None
    return {"sub": d["sub"], "org": d["org"], "connector": d["c"],
            "data_center": d["dc"]}


# --- flux --------------------------------------------------------------------

def build_auth_url(sub: str, org_id: int, connector: str, data_center: str,
                   org_app: Optional[dict] = None) -> str:
    """URL de consentement Zoho. `access_type=offline` + `prompt=consent` sont
    REQUIS pour obtenir un refresh_token (sans eux Zoho ne renvoie qu'un access
    token d'une heure, et la connexion meurt silencieusement au bout d'une heure)."""
    if connector not in SCOPES:
        raise ZohoOAuthError(f"Connecteur Zoho inconnu : {connector}")
    dc = (data_center or "").strip().lower()
    if dc not in _ACCOUNTS:
        raise ZohoOAuthError(
            f"Data center Zoho non reconnu : {data_center!r} — l'un de "
            f"{', '.join(_ACCOUNTS)}.")
    client_id, _ = resolve_app(dc, org_app)
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "scope": ",".join(SCOPES[connector]),
        "redirect_uri": redirect_uri(),
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(sub, org_id, connector, dc),
    })
    return f"{_ACCOUNTS[dc]}/oauth/v2/auth?{q}"


def exchange_code(code: str, data_center: str,
                  org_app: Optional[dict] = None) -> dict:
    """Code éphémère → tokens. ⚠️ `data=` et jamais `params=` : en query string les
    secrets partiraient dans l'URL, donc dans tout message d'erreur (incident #284)."""
    client_id, client_secret = resolve_app(data_center, org_app)
    r = requests.post(
        f"{_ACCOUNTS[data_center]}/oauth/v2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri(),
        },
        timeout=30,
    )
    try:
        payload = r.json()
    except ValueError:
        raise ZohoOAuthError(
            f"Réponse illisible du serveur d'autorisation (HTTP {r.status_code}).")
    # Zoho répond HTTP 200 + {"error": …} sur un code périmé/déjà consommé.
    if r.status_code >= 400 or "error" in payload:
        err = payload.get("error", f"HTTP {r.status_code}")
        raise ZohoOAuthError(
            f"Échec de l'échange OAuth Zoho : {err}. "
            + ("Le code d'autorisation expire en quelques minutes — relance la "
               "connexion." if "code" in str(err) else ""))
    if not payload.get("refresh_token"):
        raise ZohoOAuthError(
            "Zoho n'a pas renvoyé de refresh_token : l'autorisation a déjà été "
            "accordée à cette app. Révoque-la dans ton compte Zoho "
            "(Sécurité → Applications connectées) puis relance la connexion.")
    return payload


def persist(sub: str, org_id: int, connector: str, data_center: str,
            tokens: dict, org_app: Optional[dict] = None, *,
            entity_type: str = "member") -> None:
    """Range le credential SOUS LA MÊME FORME que le mode Self Client — c'est ce
    qui permet aux deux modes de coexister sans toucher au client ni à la
    résolution. `entity_type='member'` = clé scopée (sub, org) (ADR 0033)."""
    client_id, client_secret = resolve_app(data_center, org_app)
    fields = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "data_center": data_center,
    }
    secret = credentials_store.secret_from_input(connector, None, fields)
    entity_id = f"{sub}:{org_id}" if entity_type == "member" else str(org_id)
    credentials_store.set_credential(
        entity_type, entity_id, connector, secret, set_by=sub,
        meta={"acquired_via": "oauth"})


def supports(connector: str) -> bool:
    """Le connecteur propose-t-il le mode server-based ? (le front gate le bouton)"""
    return connector in SCOPES and connector in providers.REGISTRY
