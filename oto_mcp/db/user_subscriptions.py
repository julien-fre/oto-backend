"""L'ABONNEMENT d'une personne à un fournisseur de modèles (OTO-130).

Une ligne par (personne, famille) dans `user_model_subscriptions`. Ce qu'elle
porte : le bac à sable où le programme officiel du fournisseur est installé, et
l'état de la connexion que la personne y a ouverte ELLE-MÊME.

⚠️ **Rien ici n'est un secret, et rien ne doit le devenir.** La session Claude
vit dans le bac à sable, écrite par le programme au terme de sa propre procédure
de connexion. La collecter, la stocker ou la relayer nous est INTERDIT (politique
Anthropic) et nous est inutile : le travail s'exécute dans le bac à sable, où le
programme lit sa session tout seul. Une fonction qui rendrait une session n'a
donc pas sa place dans ce module — ni ailleurs.

⚠️ **Par PERSONNE, jamais par org.** Un abonnement appartient à qui le paie. La
réservation refuse d'y faire tourner l'agent d'un autre (`capabilities/
_abonnement`), et cette table n'a pas de colonne `org_id` pour qu'aucun chemin ne
puisse en fabriquer un.
"""
from __future__ import annotations

from typing import Any, Optional

from ._conn import _connect

#: Les états d'une connexion, et ce que chacun veut dire pour la réservation.
CONNECTE = "connected"          # sert des travaux
A_RECONNECTER = "needs_login"   # le programme ne trouve plus de session valide
PLAFOND = "paused_limit"        # forfait épuisé jusqu'à `limit_reset_at`
DECONNECTE = "disconnected"     # la personne a coupé, ou n'a jamais connecté

_CHAMPS = ("sub, famille, sandbox_id, statut, plan, method, limit_reset_at, "
           "last_ok_at, created_at, updated_at")


def get_subscription(sub: str, famille: str) -> Optional[dict]:
    """L'abonnement d'une personne pour une famille, ou None."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_CHAMPS} FROM user_model_subscriptions "
            f"WHERE sub = %s AND famille = %s",
            (sub, famille),
        ).fetchone()
    return dict(row) if row else None


def list_subscriptions(sub: str) -> list[dict]:
    """Tous les abonnements d'une personne — ce que l'écran « Fournisseurs de
    modèles » affiche."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_CHAMPS} FROM user_model_subscriptions "
            f"WHERE sub = %s ORDER BY famille",
            (sub,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_sandbox(sub: str, famille: str, sandbox_id: str) -> dict:
    """Pose (ou retrouve) le bac à sable d'une personne, sans toucher à son état.

    ⚠️ Le statut n'est PAS remis à `connected` ici : poser un bac à sable n'est pas
    s'y connecter. C'est la sonde (`claude auth status`, rapportée par
    `marquer_statut`) qui l'établit — elle seule a vu une session valide."""
    with _connect() as conn:
        row = conn.execute(
            f"""INSERT INTO user_model_subscriptions (sub, famille, sandbox_id)
                     VALUES (%s, %s, %s)
                ON CONFLICT (sub, famille) DO UPDATE
                        SET sandbox_id = EXCLUDED.sandbox_id, updated_at = NOW()
                  RETURNING {_CHAMPS}""",
            (sub, famille, sandbox_id),
        ).fetchone()
    return dict(row)


def marquer_statut(sub: str, famille: str, statut: str, *,
                   plan: Optional[str] = None, method: Optional[str] = None,
                   limit_reset_at: Optional[Any] = None,
                   ok: bool = False) -> Optional[dict]:
    """Écrit l'état observé. Ne crée RIEN : sans bac à sable, il n'y a rien à
    décrire, et une ligne née d'un rapport de worker serait une connexion qui
    n'a jamais eu lieu.

    `plan` / `method` : ce que la sonde a lu (`Max`, `claude.ai`). `None` laisse la
    valeur d'avant — un worker qui rapporte un plafond ne sait rien du palier.

    ⚠️ `limit_reset_at` s'écrit TOUJOURS avec le statut `paused_limit`, y compris
    à `None` : une échéance dépassée qu'on garderait ferait sauter la personne
    dans la réservation pour toujours (`claim_next_job` lit `limit_reset_at >
    NOW()`), et une échéance sans statut ne freine rien."""
    with _connect() as conn:
        row = conn.execute(
            f"""UPDATE user_model_subscriptions
                   SET statut = %s,
                       plan = COALESCE(%s, plan),
                       method = COALESCE(%s, method),
                       limit_reset_at = CASE WHEN %s = '{PLAFOND}' THEN %s::timestamptz
                                             ELSE NULL END,
                       last_ok_at = CASE WHEN %s THEN NOW() ELSE last_ok_at END,
                       updated_at = NOW()
                 WHERE sub = %s AND famille = %s
             RETURNING {_CHAMPS}""",
            (statut, plan, method, statut, limit_reset_at, bool(ok), sub, famille),
        ).fetchone()
    return dict(row) if row else None


def oublier(sub: str, famille: str) -> Optional[str]:
    """Efface la ligne et rend le bac à sable à détruire, s'il y en avait un.

    ⚠️ L'ordre importe : la ligne part d'abord, la destruction suit côté
    infrastructure. Une destruction qui échoue laisse un bac à sable orphelin —
    coûteux, mais muet ; l'inverse laisserait une personne « connectée » sur un bac
    à sable qui n'existe plus, donc des travaux réservés qui ne tourneront jamais."""
    with _connect() as conn:
        row = conn.execute(
            "DELETE FROM user_model_subscriptions WHERE sub = %s AND famille = %s "
            "RETURNING sandbox_id",
            (sub, famille),
        ).fetchone()
    return (dict(row).get("sandbox_id") if row else None)
