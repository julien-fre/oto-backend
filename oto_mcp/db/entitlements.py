"""Droits déclarés par org (ADR 0070 §7) : les poser, les retirer, les relire.

Le cœur ne sait pas qui paie. Un producteur (commerce, admin, partenaire) POSE un
droit sous son étiquette `source` ; le cœur le RELIT à chaque usage. Un droit est
vivant tant qu'une de ses lignes l'est — toutes sources confondues.

NON aplati dans la surface `db.*` (comme `outreach`) : `grant`, `revoke`, `has` sont
trop communs pour elle. Les appelants écrivent `from ..db import entitlements`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ._conn import _connect

# Le prédicat de vivacité, UNE fois : l'horloge est celle de la base (`NOW()`), la même
# pour tous les processus qui la lisent, jamais celle de l'appelant.
_VIVANT = "starts_at <= NOW() AND (expires_at IS NULL OR expires_at > NOW())"


def grant(org_id: int, right_key: str, source: str, *, value: Optional[int] = None,
          starts_at: Optional[datetime] = None, expires_at: Optional[datetime] = None,
          granted_by: Optional[str] = None) -> None:
    """Pose (upsert) le droit `right_key` de l'org sous l'étiquette `source`. Idempotent.

    Rejouer remplace la ligne de CETTE source, bornes comprises : le producteur dit
    l'état entier de son droit à chaque pose. `starts_at` omis = maintenant ;
    `expires_at` omis = sans échéance ; `value` omis = pas d'avis."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO org_entitlements (org_id, right_key, source, value, starts_at, "
            "expires_at, granted_by) VALUES (%s, %s, %s, %s, COALESCE(%s, NOW()), %s, %s) "
            "ON CONFLICT (org_id, right_key, source) DO UPDATE SET "
            "value = EXCLUDED.value, starts_at = EXCLUDED.starts_at, "
            "expires_at = EXCLUDED.expires_at, granted_by = EXCLUDED.granted_by, "
            "granted_at = NOW()",
            (org_id, right_key, source, value, starts_at, expires_at, granted_by),
        )


def revoke(org_id: int, right_key: str, source: str) -> bool:
    """Retire la ligne de CETTE source — les autres sources du même droit restent.
    True si une ligne a été supprimée."""
    with _connect() as conn:
        n = conn.execute(
            "DELETE FROM org_entitlements WHERE org_id = %s AND right_key = %s "
            "AND source = %s",
            (org_id, right_key, source),
        ).rowcount
    return n > 0


def has(org_id: int, right_key: str) -> bool:
    """Une ligne VIVANTE accorde-t-elle ce droit à l'org, quelle que soit sa source ?"""
    with _connect() as conn:
        return conn.execute(
            f"SELECT 1 FROM org_entitlements WHERE org_id = %s AND right_key = %s "
            f"AND {_VIVANT} LIMIT 1",
            (org_id, right_key),
        ).fetchone() is not None


def list_for_org(org_id: int) -> list[dict[str, Any]]:
    """Toutes les lignes de l'org, échues et à venir comprises.

    ⚠️ **Ne filtre PAS les dates**, à la différence de `has` : une console doit voir
    un droit échu, sinon il devient invisible et donc irrécupérable."""
    with _connect() as conn:
        return list(conn.execute(
            "SELECT org_id, right_key, value, source, starts_at, expires_at, "
            "granted_by, granted_at FROM org_entitlements WHERE org_id = %s "
            "ORDER BY right_key, source",
            (org_id,),
        ).fetchall())
