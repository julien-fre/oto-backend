"""Les LIENS entre agents hébergés — qui passe la main à qui, qui exige qui.

Deux genres (`runner_trigger_links.kind`), docs/runner-et-automatisations.md §Lier des
agents :

- `starts` (`DEMARRE`) — l'agent `from` lance `to` en fin de run (`run_now` + `handoff`).
  Le lien est la PERMISSION : un run hébergé ne lance que les agents auxquels un tel lien
  le relie (`chain_not_linked`). Déclaré, il se dessine aussi avant le premier run.
- `requires` (`EXIGE`) — l'agent `to` part à SON heure, mais seulement si le dernier run
  de `from` du jour a réussi. Le garde est dans l'horloge (`runner_tick`), jamais dans
  la procédure de `to` : une procédure qu'on édite perd sa vérification sans bruit.

Non aplati dans la surface `db.*` (comme `lecture_bornee`) : `poser`, `retirer`,
`existe` y seraient trop communs. Les appelants écrivent `db.runner_links.<fn>`.
"""
from __future__ import annotations

import datetime
from typing import Optional

from ._conn import _connect

DEMARRE = "starts"
EXIGE = "requires"
GENRES = (DEMARRE, EXIGE)

_COLS = ("id, org_id, from_trigger_id, to_trigger_id, kind, condition, created_by, "
         "created_at")


def poser(org_id: int, de: int, vers: int, genre: str, condition: Optional[str],
          par: Optional[str]) -> dict:
    """Pose le lien, ou met à jour sa phrase s'il existe déjà (même `(de, vers, genre)`).

    Les gardes de PERMISSION (droits sur les deux agents, boucle, profondeur, largeur)
    sont celles de la capacité, AVANT cet appel. Une seule est ici, parce qu'elle tient
    à la table et non à l'appelant : les deux agents sont de l'org `org_id` — sinon
    `ValueError`, et rien n'est écrit. Un lien entre deux orgs ne peut pas exister."""
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO runner_trigger_links (org_id, from_trigger_id, to_trigger_id,
                                              kind, condition, created_by)
                 SELECT %s, %s, %s, %s, %s, %s
                  WHERE (SELECT COUNT(*) FROM runner_triggers
                          WHERE org_id = %s AND id IN (%s, %s)) = 2
            ON CONFLICT (from_trigger_id, to_trigger_id, kind)
              DO UPDATE SET condition = EXCLUDED.condition
              RETURNING {_COLS}
            """,
            (org_id, de, vers, genre, condition, par, org_id, de, vers),
        ).fetchone()
    if row is None:
        raise ValueError(f"agents {de} et {vers} : pas tous deux de l'org {org_id}")
    return row


def lire(org_id: int, lien_id: int) -> Optional[dict]:
    with _connect() as conn:
        return conn.execute(
            f"SELECT {_COLS} FROM runner_trigger_links WHERE org_id = %s AND id = %s",
            (org_id, lien_id),
        ).fetchone()


def retirer(org_id: int, lien_id: int) -> Optional[dict]:
    """Retire le lien ; rend ce qu'il était, None s'il n'existait pas (dans cette org)."""
    with _connect() as conn:
        return conn.execute(
            f"DELETE FROM runner_trigger_links WHERE org_id = %s AND id = %s "
            f"RETURNING {_COLS}",
            (org_id, lien_id),
        ).fetchone()


def de_l_org(org_id: int) -> list[dict]:
    """Tous les liens de l'org, du plus ancien au plus récent."""
    with _connect() as conn:
        return conn.execute(
            f"SELECT {_COLS} FROM runner_trigger_links WHERE org_id = %s ORDER BY id",
            (org_id,),
        ).fetchall()


def existe(org_id: int, de: int, vers: int, genre: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM runner_trigger_links WHERE org_id = %s AND from_trigger_id = %s "
            "AND to_trigger_id = %s AND kind = %s",
            (org_id, de, vers, genre),
        ).fetchone()
    return row is not None


def vers_l_agent(org_id: int, vers: int, genre: str) -> list[dict]:
    """Les liens de ce genre qui ARRIVENT à l'agent `vers`, avec l'état de l'agent d'où
    ils partent (`from_enabled`) — le garde `requires` ignore un agent en pause."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT l.id, l.from_trigger_id, l.to_trigger_id, l.kind, l.condition,
                   f.enabled AS from_enabled
              FROM runner_trigger_links l
              JOIN runner_triggers f ON f.id = l.from_trigger_id
             WHERE l.org_id = %s AND l.to_trigger_id = %s AND l.kind = %s
             ORDER BY l.id
            """,
            (org_id, vers, genre),
        ).fetchall()


def depuis_l_agent(org_id: int, de: int, genre: str) -> list[dict]:
    """Les liens de ce genre qui PARTENT de l'agent `de`, avec ce que l'instruction de
    `de` dit de chaque cible : son numéro, son nom, sa procédure."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT l.id, l.to_trigger_id, l.condition,
                   t.label AS to_label, t.procedure AS to_procedure
              FROM runner_trigger_links l
              JOIN runner_triggers t ON t.id = l.to_trigger_id
             WHERE l.org_id = %s AND l.from_trigger_id = %s AND l.kind = %s
             ORDER BY l.id
            """,
            (org_id, de, genre),
        ).fetchall()


def dernier_run_depuis(org_id: int, trigger_id: int,
                       depuis: datetime.datetime) -> Optional[dict]:
    """Le dernier travail de l'agent dû depuis `depuis` (son statut, sa fin), None s'il
    n'y en a aucun — ce que le garde `requires` lit.

    ⚠️ Parcourt les travaux de l'org depuis `depuis` (aucun index ne sert
    `payload->>'trigger_id'`) : lu une fois par échéance d'un agent qui EXIGE, pas à
    chaque tour d'horloge."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, status, due_at, finished_at
              FROM runner_jobs
             WHERE org_id = %s AND payload->>'trigger_id' = %s AND due_at >= %s
             ORDER BY due_at DESC, id DESC
             LIMIT 1
            """,
            (org_id, str(trigger_id), depuis),
        ).fetchone()


def chaines_recentes(org_id: int, depuis: datetime.datetime,
                     limite: int = 500) -> list[dict]:
    """Les travaux des CHAÎNES récentes de l'org : chaque maillon enchaîné (`_chaine`)
    dû depuis `depuis`, plus le travail racine qu'il désigne. Plats, du plus récent au
    plus ancien ; la capacité les regroupe par `root_job`."""
    with _connect() as conn:
        return conn.execute(
            """
            WITH maillons AS (
                SELECT id, status, due_at, finished_at,
                       payload->>'trigger_id' AS trigger_id,
                       (payload->'_chaine'->>'parent_job')::bigint AS parent_job,
                       (payload->'_chaine'->>'root_job')::bigint AS root_job,
                       (payload->'_chaine'->>'depth')::int AS depth
                  FROM runner_jobs
                 WHERE org_id = %s AND due_at >= %s AND payload ? '_chaine'
                 ORDER BY due_at DESC
                 LIMIT %s
            )
            SELECT * FROM maillons
            UNION ALL
            SELECT j.id, j.status, j.due_at, j.finished_at,
                   j.payload->>'trigger_id', NULL, j.id, 0
              FROM runner_jobs j
             WHERE j.org_id = %s
               AND j.id IN (SELECT DISTINCT root_job FROM maillons)
            ORDER BY due_at DESC
            """,
            (org_id, depuis, limite, org_id),
        ).fetchall()


def travaux_des_agents(org_id: int, trigger_ids: list[int],
                       depuis: datetime.datetime) -> list[dict]:
    """Les travaux de ces agents dus depuis `depuis`, sous la forme de
    `chaines_recentes` — ce qu'un agent qui a des liens `starts` a fait de la semaine,
    même quand il n'a rien lancé (l'écran doit pouvoir le dire)."""
    if not trigger_ids:
        return []
    with _connect() as conn:
        return conn.execute(
            """
            SELECT id, status, due_at, finished_at,
                   payload->>'trigger_id' AS trigger_id,
                   (payload->'_chaine'->>'parent_job')::bigint AS parent_job,
                   (payload->'_chaine'->>'root_job')::bigint AS root_job,
                   COALESCE((payload->'_chaine'->>'depth')::int, 0) AS depth
              FROM runner_jobs
             WHERE org_id = %s AND due_at >= %s AND kind = 'start'
               AND payload->>'trigger_id' = ANY(%s)
             ORDER BY due_at DESC
             LIMIT 500
            """,
            (org_id, depuis, [str(i) for i in trigger_ids]),
        ).fetchall()


def sauts_par_exigence(org_id: int, depuis: datetime.datetime) -> list[dict]:
    """Les échéances sautées faute d'exigence tenue (`refused_requires`) depuis
    `depuis`, la plus récente d'abord — `{trigger_id, at}`. Ce que l'écran dit d'un agent
    planifié, dont il ne lit pas les livraisons."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT trigger_id, received_at AS at
              FROM runner_hook_deliveries
             WHERE org_id = %s AND outcome = 'refused_requires' AND received_at >= %s
             ORDER BY received_at DESC
             LIMIT 200
            """,
            (org_id, depuis),
        ).fetchall()
