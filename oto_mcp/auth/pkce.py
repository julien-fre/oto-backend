"""Helpers OAuth2 + PKCE génériques (state HMAC-signé, paire PKCE S256, expiry).

Sans dépendance à un connecteur — partagés par les flows web OAuth d'oto.

Le `code_verifier` PKCE est porté DANS le `state` HMAC-signé (intégrité, pas
confidentialité) plutôt que stocké côté serveur. Acceptable pour les flows web
d'oto, y compris **client public** : le `redirect_uri` est HTTPS et
contrôlé par oto — le code d'autorisation arrive directement au serveur, pas via
un custom-scheme interceptable comme une app native — donc PKCE reste effectif.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Optional


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def pkce_pair() -> tuple[str, str]:
    """(verifier, challenge S256)."""
    verifier = b64url(secrets.token_bytes(48))
    challenge = b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def make_state(secret: bytes, audience: str, sub: str, verifier: str) -> str:
    """State opaque {sub, code_verifier, ts, aud}, HMAC-signé avec `secret`.

    `audience` (ex. « salesforce », « zoho ») est INCLUS dans le payload signé —
    même mécanique que `flow.sign_state` (oto-backend#572 point 8) : un state
    signé ici ne vaut que pour le flux qui l'a demandé, la signature seule ne le
    garantissait pas (la clé HMAC, `OTO_MCP_OAUTH_STATE_SECRET`, est mutualisée
    entre familles de jetons — #572 point 7)."""
    payload = json.dumps({"sub": sub, "v": verifier, "ts": int(time.time()),
                          "aud": audience}, separators=(",", ":")).encode()
    sig = hmac.new(secret, payload, hashlib.sha256).digest()
    return f"{b64url(payload)}.{b64url(sig)}"


def verify_state(secret: bytes, audience: str, state: str, ttl: int) -> Optional[tuple[str, str]]:
    """(sub, code_verifier) si signature valide, non expiré (< ttl s) ET
    l'audience correspond — sinon None (un callback ne doit jamais distinguer
    les causes d'un refus).

    ⚠️ Rétrocompatibilité (oto-backend#572 point 8) : un state SANS champ `aud`
    (émis avant son introduction) est ACCEPTÉ pour toute audience — son absence
    ne peut valoir que « émis avant ce champ », jamais « émis pour un autre
    fournisseur ». Un state qui PORTE un `aud` différent, lui, est refusé. La
    fenêtre où un vieux state sans `aud` peut encore se présenter est bornée par
    `ttl` (l'ordre de la minute), pas par une date de bascule — aucun état signé
    après ce déploiement n'en est jamais dépourvu."""
    if not state or "." not in state:
        return None
    p_b64, sig_b64 = state.split(".", 1)
    try:
        payload, sig = b64url_decode(p_b64), b64url_decode(sig_b64)
    # noqa: SILENT — fail-closed : un callback ne distingue jamais les causes d'un refus
    except Exception:
        return None
    if not hmac.compare_digest(sig, hmac.new(secret, payload, hashlib.sha256).digest()):
        return None
    try:
        data = json.loads(payload)
    # noqa: SILENT — fail-closed : un callback ne distingue jamais les causes d'un refus
    except Exception:
        return None
    if int(time.time()) - int(data.get("ts", 0)) > ttl:
        return None
    aud = data.get("aud")
    if aud is not None and aud != audience:
        return None            # state d'un AUTRE flux → refus (anti-rejeu)
    sub, v = data.get("sub"), data.get("v")
    return (sub, v) if isinstance(sub, str) and isinstance(v, str) else None


def expires_at(expires_in) -> Optional[str]:
    """ISO 8601 UTC de l'expiration d'un access token, depuis `expires_in` (s)."""
    n = int(expires_in or 0)
    if not n:
        return None
    return datetime.fromtimestamp(time.time() + n, tz=timezone.utc).isoformat()
