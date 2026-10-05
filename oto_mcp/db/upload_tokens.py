"""Consommation à usage unique des jetons signés : jetons d'upload (issue
oto-backend#105) et `state` des flux « Connecter » qui posent un credential depuis un
retour de navigateur (`auth.flow.consume_state`).

Le jeton lui-même est STATELESS (HMAC signé, cf. `oto_mcp.upload_tokens`) : on ne
persiste que le `jti` déjà consommé, pour interdire le rejeu. TTL court côté jeton →
purge opportuniste des lignes anciennes à chaque consommation. Un `jti` de state est
préfixé (`state:<audience>:`) : il ne rencontre jamais celui d'un upload.
"""
from __future__ import annotations

from ._conn import _connect

# Bien au-delà du TTL du jeton (15 min) — simple filet pour que la table ne croisse pas.
_PRUNE_AFTER = "1 day"


def consume_upload_token(jti: str) -> bool:
    """Marque `jti` consommé. Renvoie True si c'était la 1re fois (upload autorisé),
    False si déjà utilisé (rejeu → refus). Purge au passage les jtis expirés."""
    return _consume(jti)


def consume_state_jti(audience: str, jti: str) -> bool:
    """Même règle pour le `state` d'un flux : True la 1re fois, False au rejeu."""
    if not audience or not jti:
        return False
    return _consume(f"state:{audience}:{jti}")


def _consume(jti: str) -> bool:
    if not jti:
        return False
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO upload_tokens_used (jti) VALUES (%s) "
            "ON CONFLICT (jti) DO NOTHING RETURNING jti",
            (jti,),
        ).fetchone()
        conn.execute(
            f"DELETE FROM upload_tokens_used WHERE used_at < NOW() - INTERVAL '{_PRUNE_AFTER}'"
        )
        return row is not None
