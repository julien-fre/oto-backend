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

**D'où vient l'app ?** Du **coffre**, comme n'importe quel credential — jamais de
l'environnement. `client_id`/`client_secret` sont posés sur la carte du connecteur
par l'user, l'équipe, l'org ou la plateforme, et résolus par la **cascade
habituelle** (`access.resolve_credential_fields` : membre > équipe > org >
plateforme). Conséquences : chaque org peut apporter la sienne, une clé
**plateforme** posée par Otomata donne le « un clic » à tout le monde sans rien
changer au code, et le partage/la gouvernance existants s'appliquent tels quels.

La connexion est donc en **deux temps** (patron `status_hints` déjà en place pour
unipile/sessions) : (1) poser l'app — `client_id` + `client_secret` + région ;
(2) consentir, ce qui remplit le `refresh_token`. D'où un `refresh_token`
FACULTATIF sur la carte : en server-based il n'est pas collé, il est obtenu.

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

from . import access, credentials_store, providers

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

def app_fields(connector: str, sub: str) -> dict:
    """Champs du credential résolu pour ce (sub, connecteur) — cascade habituelle
    (membre > équipe > org > plateforme). `{}` si rien n'est encore posé : c'est
    l'état NOMINAL d'une première connexion, jamais une erreur."""
    try:
        return access.resolve_credential_fields(connector, sub=sub) or {}
    except Exception:  # noqa: BLE001 — pas encore de credential
        return {}


def resolve_app(fields: Optional[dict]) -> tuple[str, str]:
    """`(client_id, client_secret)` de l'app à utiliser, pris dans le credential
    résolu. Lève un message actionnable si l'app n'a pas encore été posée."""
    cid = (fields or {}).get("client_id")
    sec = (fields or {}).get("client_secret")
    if cid and sec:
        return cid, sec
    raise ZohoOAuthError(
        "Aucune app OAuth Zoho disponible. Renseigne d'abord « client id » et "
        "« client secret » de ton app Zoho sur la carte du connecteur (ou "
        "demande à ton org / à la plateforme de partager la sienne), puis relance "
        "la connexion.")


def has_app(connector: str, sub: str) -> bool:
    """Une app est-elle déjà à disposition (à n'importe quel palier) ?"""
    f = app_fields(connector, sub)
    return bool(f.get("client_id") and f.get("client_secret"))


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
                   app: Optional[dict] = None) -> str:
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
    client_id, _ = resolve_app(app if app is not None else app_fields(connector, sub))
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
                  app: Optional[dict] = None) -> dict:
    """Code éphémère → tokens. ⚠️ `data=` et jamais `params=` : en query string les
    secrets partiraient dans l'URL, donc dans tout message d'erreur (incident #284)."""
    client_id, client_secret = resolve_app(app)
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
            tokens: dict, app: Optional[dict] = None, *,
            entity_type: str = "member") -> None:
    """Range le credential SOUS LA MÊME FORME que le mode Self Client — c'est ce
    qui permet aux deux modes de coexister sans toucher au client ni à la
    résolution. `entity_type='member'` = clé scopée (sub, org) (ADR 0033)."""
    client_id, client_secret = resolve_app(app)
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
