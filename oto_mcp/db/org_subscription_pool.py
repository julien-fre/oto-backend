"""Le POOL d'org des abonnements : le MODE de l'org, et les PRÊTS de ses membres.

Deux tables (`db/schema/runs.py::MODEL_SUBSCRIPTION_POOL`) :

- `org_model_subscription_modes` — une ligne par (org, famille) qui a réglé son mode.
  Sans ligne : `personnel`, le travail tourne sur l'abonnement de SON demandeur ;
- `user_model_subscription_loans` — une ligne par (personne, famille, org) : ce
  membre prête son abonnement au pool de CETTE org. Opt-in, par org.

La réservation (`runner_jobs.claim_next_job`) lit les deux tables en SQL ; ce module
porte les lectures et écritures des surfaces (réglage d'org, écran de la personne) et
celle de la pose (`taille_du_pool`).

⚠️ Aucune session, aucun secret ici, comme dans `user_subscriptions` : un prêt dit
QUEL sandbox peut servir, jamais comment s'y connecter.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ._conn import _connect

PERSONNEL = "personnel"
POOL = "pool"
MODES = (PERSONNEL, POOL)

# Un prêteur SERVABLE : abonnement avec sandbox, connecté — ou au plafond dont
# l'échéance est passée ou inconnue, exactement comme la clause « la personne qui ne
# peut pas servir attend » de la réservation (sans échéance connue, rien ne freine).
# `ab` = `user_model_subscriptions`. Une seule écriture, lue par la pose ET la file.
PRETEUR_SERVABLE = (
    "ab.sandbox_id IS NOT NULL "
    "AND ab.statut IN ('connected', 'paused_limit') "
    "AND (ab.statut = 'connected' OR ab.limit_reset_at IS NULL "
    "OR ab.limit_reset_at <= NOW())")

# Le prêt `l` vaut : son abonnement existe, et son auteur est TOUJOURS membre de
# l'org. Quitter l'org rend le prêt inerte sans qu'aucun chemin de départ n'ait à
# penser à lui.
PRET_VIVANT = (
    "JOIN user_model_subscriptions ab ON ab.sub = l.sub AND ab.famille = l.famille "
    "JOIN org_members om ON om.org_id = l.org_id AND om.sub = l.sub")


def get_mode(org_id: Optional[int], famille: str) -> Optional[dict]:
    """Le mode RÉGLÉ par l'org pour cette famille, ou None (rien réglé : personnel)."""
    if not org_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT org_id, famille, mode, updated_at, updated_by "
            "FROM org_model_subscription_modes WHERE org_id = %s AND famille = %s",
            (org_id, famille)).fetchone()
    return dict(row) if row else None


def en_pool(org_id: Optional[int], famille: str) -> bool:
    return ((get_mode(org_id, famille) or {}).get("mode")) == POOL


def poser_mode(org_id: int, famille: str, mode: str, par: str) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode hors contrat : {mode!r}")
    with _connect() as conn:
        row = conn.execute(
            """INSERT INTO org_model_subscription_modes (org_id, famille, mode, updated_by)
                    VALUES (%s, %s, %s, %s)
               ON CONFLICT (org_id, famille) DO UPDATE
                       SET mode = EXCLUDED.mode, updated_by = EXCLUDED.updated_by,
                           updated_at = NOW()
            RETURNING org_id, famille, mode, updated_at, updated_by""",
            (org_id, famille, mode, par)).fetchone()
    return dict(row)


def taille_du_pool(org_id: int, famille: str) -> int:
    """Combien de membres de l'org lui prêtent un abonnement SERVABLE (connecté, ou
    au plafond) — ce que la pose exige non nul, et ce que le réglage d'org montre."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM user_model_subscription_loans l {PRET_VIVANT} "
            f"WHERE l.org_id = %s AND l.famille = %s AND {PRETEUR_SERVABLE}",
            (org_id, famille)).fetchone()
    return int(row["n"]) if row else 0


def orgs_pretees(sub: str, famille: str) -> list[int]:
    """Les orgs au pool desquelles cette personne prête cet abonnement."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT org_id FROM user_model_subscription_loans "
            "WHERE sub = %s AND famille = %s ORDER BY org_id",
            (sub, famille)).fetchall()
    return [r["org_id"] for r in rows]


def poser_prets(sub: str, famille: str, org_ids: Iterable[int]) -> list[int]:
    """Remplace l'ensemble des orgs auxquelles l'abonnement est prêté. Un prêt
    conservé garde son `servi_at` (sa place dans le tourniquet) ; un prêt retiré
    part tout de suite — le travail SUIVANT ne le voit plus."""
    voulus = sorted({int(o) for o in org_ids})
    with _connect() as conn:
        conn.execute(
            "DELETE FROM user_model_subscription_loans "
            "WHERE sub = %s AND famille = %s AND NOT (org_id = ANY(%s))",
            (sub, famille, voulus))
        for org_id in voulus:
            conn.execute(
                "INSERT INTO user_model_subscription_loans (sub, famille, org_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (sub, famille, org_id))
    return voulus


def oublier_prets(sub: str, famille: str, conn=None) -> None:
    """Retire tous les prêts de cet abonnement (le sandbox est effacé)."""
    sql = "DELETE FROM user_model_subscription_loans WHERE sub = %s AND famille = %s"
    if conn is not None:
        conn.execute(sql, (sub, famille))
        return
    with _connect() as c:
        c.execute(sql, (sub, famille))
