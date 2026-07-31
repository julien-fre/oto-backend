"""Fabrique d'acquisition OAuth2 — la danse `authorization_code`, écrite UNE fois.

Le registre de connecteurs sait déclarer un credential (`providers.py`), le coffre
le chiffrer, la cascade le résoudre. Mais rien ne savait l'**ACQUÉRIR** : chaque
connecteur OAuth réécrivait le flux de bout en bout. Mesuré le 2026-07-28 :
`verify_state` ×6, `exchange_code` ×5, `build_auth_url` ×5, `_state_secret` ×5 —
cinq modules, ~1200 lignes, la même mécanique. Chaque copie a re-découvert les
mêmes pièges (secrets en query string, TTL, erreurs opaques).

Ce module porte le noyau réellement commun et stable :

- **state signé** — HMAC-SHA256, base64url, TTL ;
- **échange du code** — POST form-encodé, erreurs rédigées ;
- **URI de redirection** — dérivée de l'URL publique du serveur.

Ce qui reste au connecteur : l'URL d'autorisation (ses paramètres varient), ses
scopes, et le rangement du credential obtenu. C'est le bon partage : le mécanisme
ici, la politique là-bas.

**AUDIENCE — la correction de fond.** Les implémentations séparées signaient toutes
avec le MÊME secret d'env, au même format, sans discriminant : un `state` émis pour
un flux était structurellement valide sur le callback d'un autre (`folk` et
`atlassian` partageaient jusqu'à la forme exacte du payload). Ici le state est
**lié à son audience** et `read_state` rejette celui d'un autre flux — un rejeu
inter-connecteurs devient impossible par construction, pas par convention.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

import requests

DEFAULT_STATE_TTL = 600   # 10 min — le temps de lire un écran de consentement


class OAuthFlowError(ValueError):
    """Échec d'acquisition. Le message est SÛR à afficher : jamais de secret, jamais
    l'URL de la requête (cf. l'incident #284 — des credentials en query string se
    retrouvaient dans les messages d'erreur)."""


# --- state signé --------------------------------------------------------------

def _secret() -> bytes:
    v = os.environ.get("OTO_MCP_OAUTH_STATE_SECRET")
    if not v:
        raise OAuthFlowError("OTO_MCP_OAUTH_STATE_SECRET env var manquante")
    return v.encode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_state(audience: str, payload: dict) -> str:
    """`<b64(payload)>.<b64(hmac)>`. `audience` (ex. « zoho », « google ») est
    INCLUS dans le payload signé : un state d'un flux ne vaut que pour ce flux."""
    body = json.dumps({**payload, "aud": audience, "ts": int(time.time())},
                      separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(_secret(), body, hashlib.sha256).digest()
    return f"{_b64url(body)}.{_b64url(sig)}"


def read_state(audience: str, state: Optional[str], *,
               ttl: int = DEFAULT_STATE_TTL) -> Optional[dict]:
    """Payload si la signature est valide, l'audience correspond et le TTL tient.
    `None` sinon — un callback ne doit jamais distinguer les causes d'un refus."""
    if not state or "." not in state:
        return None
    b64_body, b64_sig = state.split(".", 1)
    try:
        body, sig = _b64url_decode(b64_body), _b64url_decode(b64_sig)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(sig, hmac.new(_secret(), body, hashlib.sha256).digest()):
        return None
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or data.get("aud") != audience:
        return None            # state d'un AUTRE flux → refus (anti-rejeu)
    if int(time.time()) - int(data.get("ts", 0)) > ttl:
        return None
    return data


# --- URI de redirection --------------------------------------------------------

def redirect_uri(path: str) -> str:
    """URL publique + `path`. À enregistrer AU BYTE PRÈS chez le fournisseur."""
    base = os.environ.get("OTO_MCP_PUBLIC_URL", "https://mcp.oto.ninja").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


# --- échange du code -----------------------------------------------------------

def exchange_code(token_url: str, *, code: str, client_id: str, client_secret: str,
                  redirect: str, extra: Optional[dict] = None,
                  timeout: int = 30) -> dict:
    """Code éphémère → tokens.

    ⚠️ Les credentials partent en **`data=`** (corps form-encodé, RFC 6749 §2.3.1),
    JAMAIS en `params=` : en query string ils entrent dans l'URL, donc dans le
    message de toute exception `requests`, dans les logs et chez le fournisseur.
    Et pas de `raise_for_status()` — son message embarque l'URL. On construit
    l'erreur nous-mêmes.
    """
    payload = {"grant_type": "authorization_code", "code": code,
               "client_id": client_id, "client_secret": client_secret,
               "redirect_uri": redirect, **(extra or {})}
    r = requests.post(token_url, data=payload, timeout=timeout)
    host = (token_url or "").split("//")[-1].split("/")[0]
    try:
        body = r.json()
    except ValueError:
        raise OAuthFlowError(
            f"Réponse illisible du serveur d'autorisation ({host}, HTTP {r.status_code}).")
    # Beaucoup de fournisseurs (Zoho…) répondent HTTP 200 avec l'erreur dans le corps.
    if r.status_code >= 400 or "error" in body:
        # `error` seul ne suffit PAS à diagnostiquer : `invalid_grant` est le code
        # fourre-tout d'OAuth 2 (code expiré, déjà consommé, verifier PKCE qui ne
        # correspond pas, appel depuis une IP non autorisée, jeton révoqué…). Le
        # fournisseur dit lequel dans `error_description` ; le jeter obligeait à
        # deviner à la main, une hypothèse à la fois. Vécu le 31/07 sur Salesforce.
        detail = " : ".join(str(v).strip() for v in (
            body.get("error") or body.get("message") or f"HTTP {r.status_code}",
            body.get("error_description"),
        ) if v)
        raise OAuthFlowError(f"Échec de l'échange OAuth ({host}) : {detail}.")
    return body


# --- le grant est-il MORT, ou est-ce ma config qui est fausse ? ----------------

def grant_is_dead(status_code: int, body_text: str) -> bool:
    """`True` seulement si le serveur d'autorisation dit que le GRANT est mort
    (`invalid_grant`, RFC 6749 §5.2) — donc qu'une ré-autorisation est requise.

    ⚠️ Cette distinction garde une opération DESTRUCTRICE. Les appelants purgent le
    credential sur « réauth requise » ; or un AS répond 400 aussi pour `invalid_client`,
    `invalid_request`, `unauthorized_client` — c'est-à-dire pour une CONFIG fausse. Tant
    que tout 400/401 valait « grant mort », un client_secret mal saisi effaçait
    irréversiblement un refresh_token parfaitement valide, et l'utilisateur devait tout
    reconnecter pour une faute de frappe. Un incident de configuration doit remonter,
    pas détruire.

    Le corps prime sur le code : c'est lui qui porte le verdict de l'AS."""
    low = (body_text or "").lower()
    if "invalid_grant" in low:
        return True
    # Un 401 NU (sans corps exploitable) reste un rejet d'identifiants côté grant :
    # les AS qui ne renvoient pas de corps sur refresh révoqué existent. Un 400 nu,
    # lui, est trop ambigu pour justifier une purge.
    return status_code == 401 and not low.strip()
