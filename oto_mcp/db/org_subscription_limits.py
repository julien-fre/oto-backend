"""Le PLAFOND de consommation qu'une org pose sur les abonnements de ses membres.

Une ligne par (org, famille) dans `org_model_subscription_limits` : la part maximale,
en %, de l'usage TOTAL du compte du fournisseur (fenêtres cinq heures et sept jours)
au-delà de laquelle les travaux de l'org attendent la réinitialisation. Sans ligne,
le défaut du code s'applique (`capabilities/_abonnement.DEFAUT_LIMITE_PCT`) : ce
module ne le connaît pas, il ne dit que ce que l'org a RÉGLÉ.

⚠️ Aucune donnée de la personne ici : l'abonnement reste par personne
(`user_subscriptions`) ; l'org ne règle qu'un seuil, qu'une personne peut encore
resserrer pour elle-même.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

_CHAMPS = "org_id, famille, limite_pct, updated_at, updated_by"


def get_limite(org_id: int, famille: str) -> Optional[dict]:
    """Le plafond réglé par l'org pour cette famille, ou None (défaut du code)."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_CHAMPS} FROM org_model_subscription_limits "
            "WHERE org_id = %s AND famille = %s",
            (org_id, famille),
        ).fetchone()
    return dict(row) if row else None


def poser_limite(org_id: int, famille: str, limite_pct: int, par: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"""INSERT INTO org_model_subscription_limits
                       (org_id, famille, limite_pct, updated_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (org_id, famille) DO UPDATE
                        SET limite_pct = EXCLUDED.limite_pct,
                            updated_by = EXCLUDED.updated_by, updated_at = NOW()
             RETURNING {_CHAMPS}""",
            (org_id, famille, limite_pct, par),
        ).fetchone()
    return dict(row)


def retirer_limite(org_id: int, famille: str) -> None:
    """Revenir au défaut : la ligne part."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM org_model_subscription_limits WHERE org_id = %s AND famille = %s",
            (org_id, famille))
