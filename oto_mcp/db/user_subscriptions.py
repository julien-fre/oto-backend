"""L'ABONNEMENT d'une personne à un fournisseur de modèles (OTO-130).

Une ligne par (personne, famille) dans `user_model_subscriptions`. Ce qu'elle
porte : le sandbox où le programme officiel du fournisseur est installé, et
l'état de la connexion que la personne y a ouverte ELLE-MÊME.

⚠️ **Rien ici n'est un secret, et rien ne doit le devenir.** La session Claude
vit dans le sandbox, écrite par le programme au terme de sa propre procédure
de connexion. La collecter, la stocker ou la relayer nous est INTERDIT (politique
Anthropic) et nous est inutile : le travail s'exécute dans le sandbox, où le
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
           "last_ok_at, created_at, updated_at, limite_pct")

# Le plafond PERSO de consommation (% de l'usage total du compte). Même forme dans le
# `CREATE TABLE` (`db/schema/runs.py`), dans la révision `0019` et au démarrage : une
# base neuve, une base migrée et une base que le démarrage rattrape ont la même colonne.
COLONNE_LIMITE = "limite_pct"
DDL_COLONNE_LIMITE = (f"ALTER TABLE user_model_subscriptions ADD COLUMN IF NOT EXISTS "
                      f"{COLONNE_LIMITE} SMALLINT "
                      f"CHECK ({COLONNE_LIMITE} BETWEEN 1 AND 100)")


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
    """Pose (ou retrouve) le sandbox d'une personne, sans toucher à son état.

    ⚠️ Le statut n'est PAS remis à `connected` ici : poser un sandbox n'est pas
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
                   ok: bool = False,
                   observe: bool = False) -> Optional[dict]:
    """Écrit l'état observé. Ne crée RIEN : sans sandbox, il n'y a rien à
    décrire, et une ligne née d'un rapport de worker serait une connexion qui
    n'a jamais eu lieu.

    ⚠️ `observe=True` = cet état a été OBSERVÉ par un worker, pas VOULU par la
    personne — et une observation ne défait JAMAIS une déconnexion (revue du
    21/09/2026). Le cas est banal : elle se déconnecte pendant qu'un de ses
    travaux tourne, et la conclusion de ce travail la remettait `connected` — ou
    `paused_limit`, servable dès l'échéance passée. Son « non » était annulé par
    un travail parti avant lui. La garde vit ICI, dans l'écriture, pour qu'aucun
    appelant ne puisse l'oublier ; seuls les gestes de la personne (connexion,
    déconnexion) écrivent sans elle. Rend `None` quand elle a mordu.

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
                   AND (NOT %s::boolean OR statut <> '{DECONNECTE}')
             RETURNING {_CHAMPS}""",
            (statut, plan, method, statut, limit_reset_at, bool(ok), sub, famille,
             bool(observe)),
        ).fetchone()
    return dict(row) if row else None


def poser_limite(sub: str, famille: str, limite_pct: Optional[int]) -> Optional[dict]:
    """Pose (ou retire, `None`) le plafond PERSO d'une personne sur SA ligne. Ne crée
    rien : sans abonnement, il n'y a rien à plafonner — rend `None`.

    Ne touche ni au statut ni à l'échéance : le plafond s'applique au PROCHAIN
    rapport de forfait (`_abonnement.noter_rapport`), jamais au run en cours."""
    with _connect() as conn:
        row = conn.execute(
            f"""UPDATE user_model_subscriptions
                   SET limite_pct = %s, updated_at = NOW()
                 WHERE sub = %s AND famille = %s
             RETURNING {_CHAMPS}""",
            (limite_pct, sub, famille),
        ).fetchone()
    return dict(row) if row else None


def oublier(sub: str, famille: str) -> Optional[str]:
    """Efface la ligne et rend le sandbox à détruire, s'il y en avait un.

    ⚠️ L'ordre importe : la ligne part d'abord, la destruction suit côté
    infrastructure. Une destruction qui échoue laisse un sandbox orphelin —
    coûteux, mais muet ; l'inverse laisserait une personne « connectée » sur un sandbox
    à sable qui n'existe plus, donc des travaux réservés qui ne tourneront jamais."""
    from .org_subscription_pool import oublier_prets
    with _connect() as conn:
        row = conn.execute(
            "DELETE FROM user_model_subscriptions WHERE sub = %s AND famille = %s "
            "RETURNING sandbox_id",
            (sub, famille),
        ).fetchone()
        # Ses PRÊTS aux pools d'org partent avec lui, dans la même transaction : un
        # abonnement reconnecté plus tard ne reprête rien sans un nouveau geste.
        oublier_prets(sub, famille, conn=conn)
    return (dict(row).get("sandbox_id") if row else None)
