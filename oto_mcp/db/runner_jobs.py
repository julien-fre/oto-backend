"""La file d'exécutions du runner — claim par bail, échec visible (chantier R2).

Quatre invariants, gravés ici parce qu'une réécriture distraite les casserait :

1. **Le claim est atomique et org-scopé** : `FOR UPDATE SKIP LOCKED` sur les jobs
   de l'org du worker uniquement — deux workers ne prennent jamais le même job,
   et un worker ne voit jamais les jobs d'une autre org (V1 : un worker = un
   jeton d'org ; le pool multi-org attend l'arbitrage compte-de-service).
2. **Un bail expiré se re-claime, il ne se vole pas** : le claim reprend aussi
   les jobs `claimed` dont le bail est mort — c'est LA reprise (un worker tué
   ne bloque un job que le temps du bail), et `attempts` compte chaque prise.
3. **À bout de tentatives : `failed`, VISIBLE, jamais une boucle.** Le claim
   marque d'abord les épaves (bail mort + tentatives épuisées) avant de servir —
   refuser-et-marquer, pas tourner.
4. **Seul le claimant conclut** : `complete`/`extend`/`bind_run` sont scopés
   `claimed_by = worker` (le patron de `finish_run`) — un pair ne peut ni fermer
   ni prolonger le job d'un autre.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ._conn import _connect

# Backoff linéaire simple : un échec renvoie le job dans la file à +30 s × tentatives.
# Pas d'exponentiel en V1 — les échecs attendus (amont LLM en vrac) se lissent, et un
# job vraiment cassé atteint son plafond en minutes, pas en heures.
_BACKOFF_S = 30
_LEASE_DEFAULT_S = 600  # ~3× la ligne la plus lente mesurée (180 s) — le tour d'un run

# Plafond d'une page de file. Il existait déjà — enfoui dans le LIMIT, appliqué sans
# être dit : `limit=1000` rendait 200 lignes et rien ne l'annonçait (#469). Il est
# nommé ici parce que c'est là que le SQL le fait respecter, et déclaré au contrat
# par la capacité, qui rend le total et le curseur sans lesquels une page pleine est
# indiscernable d'une file épuisée.
JOBS_PAGE_MAX = 200


def enqueue_job(org_id: int, kind: str, payload: Optional[dict] = None,
                run_id: Optional[str] = None, max_attempts: int = 3) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            INSERT INTO runner_jobs (org_id, kind, payload, run_id, max_attempts)
            VALUES (%s, %s, %s::jsonb, %s, %s)
            RETURNING id, status, due_at
            """,
            (org_id, kind,
             json.dumps(payload, ensure_ascii=False) if payload is not None else None,
             run_id, max(1, int(max_attempts))),
        ).fetchone()
    return dict(row)


def claim_next_job(org_id: int, worker_sub: str,
                   lease_seconds: int = _LEASE_DEFAULT_S) -> Optional[dict]:
    """Le prochain job de l'org, bail posé — ou None (file vide).

    Marque d'abord `failed` les épaves (bail mort + tentatives épuisées) : elles
    deviennent VISIBLES au lieu d'être re-servies pour rien."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE runner_jobs
               SET status = 'failed', finished_at = NOW(),
                   last_error = COALESCE(last_error, '') ||
                                ' [bail expiré, tentatives épuisées]'
             WHERE org_id = %s AND status = 'claimed'
               AND lease_until < NOW() AND attempts >= max_attempts
            """,
            (org_id,),
        )
        row = conn.execute(
            """
            UPDATE runner_jobs j
               SET status = 'claimed', claimed_by = %s, attempts = j.attempts + 1,
                   lease_until = NOW() + make_interval(secs => %s)
             WHERE j.id = (
                   SELECT id FROM runner_jobs
                    WHERE org_id = %s AND due_at <= NOW()
                      AND (status = 'pending'
                           OR (status = 'claimed' AND lease_until < NOW()))
                      AND attempts < max_attempts
                    ORDER BY due_at
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1)
            RETURNING id, kind, run_id, payload, attempts, max_attempts, lease_until
            """,
            (worker_sub, int(lease_seconds), org_id),
        ).fetchone()
    return dict(row) if row else None


def bind_job_run(job_id: int, worker_sub: str, run_id: str) -> bool:
    """Lie un job `start` au run que le worker vient d'ouvrir — claimant seul."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE runner_jobs SET run_id = %s
             WHERE id = %s AND claimed_by = %s AND status = 'claimed'
            """,
            (run_id, job_id, worker_sub),
        )
        return bool(cur.rowcount)


def extend_job_lease(job_id: int, worker_sub: str,
                     lease_seconds: int = _LEASE_DEFAULT_S) -> bool:
    """Prolonge le bail — le heartbeat du worker. Claimant seul."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE runner_jobs
               SET lease_until = NOW() + make_interval(secs => %s)
             WHERE id = %s AND claimed_by = %s AND status = 'claimed'
            """,
            (int(lease_seconds), job_id, worker_sub),
        )
        return bool(cur.rowcount)


def complete_job(job_id: int, worker_sub: str, ok: bool,
                 error: Optional[str] = None,
                 run_id: Optional[str] = None,
                 result: Optional[dict] = None) -> Optional[dict]:
    """Conclut la prise : `done`, ou re-file avec backoff, ou `failed` au plafond.

    `result` (R5, flotte) = le résultat DÉCLARÉ par le worker (usage_tokens,
    stopped, steps…) : c'est ce qu'un ordonnanceur de flotte lit pour sa garde
    budget — jamais un secret, jamais du contenu de fil.

    Rend `{status, run_id}` conclu — `run_id` = le run que le job connaît après
    la conclusion (celui de l'appel, sinon celui posé par `bind_run`/`enqueue`,
    sinon None) : c'est la clé de la libération des baux du datastore (#633),
    lue par la capacité sans second aller-retour — ou None si le job n'est pas
    au claimant (déjà re-claimé après bail mort, ou jamais à lui) : l'appelant
    ne conclut pas ce qui ne lui appartient plus."""
    with _connect() as conn:
        if ok:
            row = conn.execute(
                """
                UPDATE runner_jobs
                   SET status = 'done', finished_at = NOW(),
                       run_id = COALESCE(%s, run_id), last_error = NULL,
                       result = COALESCE(%s::jsonb, result)
                 WHERE id = %s AND claimed_by = %s AND status = 'claimed'
                RETURNING status, run_id
                """,
                (run_id, json.dumps(result) if result is not None else None,
                 job_id, worker_sub),
            ).fetchone()
        else:
            # Échec : au plafond → failed VISIBLE ; sinon retour en file, backoff
            # linéaire, la trace d'erreur conservée pour l'audit.
            row = conn.execute(
                """
                UPDATE runner_jobs
                   SET status   = CASE WHEN attempts >= max_attempts
                                       THEN 'failed' ELSE 'pending' END,
                       finished_at = CASE WHEN attempts >= max_attempts
                                          THEN NOW() ELSE NULL END,
                       due_at   = NOW() + make_interval(secs => %s * attempts),
                       lease_until = NULL, claimed_by = NULL,
                       last_error = %s
                 WHERE id = %s AND claimed_by = %s AND status = 'claimed'
                RETURNING status, run_id
                """,
                (_BACKOFF_S, (error or 'échec non détaillé')[:500], job_id, worker_sub),
            ).fetchone()
    return dict(row) if row else None


def _filtre_de_file(org_id: int, status: Optional[str]) -> tuple[str, list]:
    """Le WHERE commun à la page et à son compte — une seule définition de « la
    file », sinon le total finit par décrire une autre population que les lignes
    qu'il accompagne."""
    q = " WHERE org_id = %s"
    params: list = [org_id]
    if status:
        q += " AND status = %s"
        params.append(status)
    return q, params


def list_jobs(org_id: int, status: Optional[str] = None,
              limit: int = 50, before_id: Optional[int] = None) -> list[dict]:
    """La file vue d'en haut (surveillance dashboard) : les jobs de l'org, du
    plus récent au plus ancien, filtrables par statut. Le payload est rendu
    (références seulement, par contrat d'enqueue) mais jamais tronqué en
    silence — c'est une LISTE : elle rend de quoi écarter, le détail par get.

    `before_id` = pagination keyset sur l'ordre servi (`id DESC`) : la page suivante
    est « les jobs plus anciens que celui-ci ». Un keyset plutôt qu'un OFFSET parce
    qu'une file bouge sous la marche — un job enfilé entre deux pages décalerait
    tout un OFFSET et ferait sauter une ligne.

    ⚠️ `JOBS_PAGE_MAX` reste appliqué ICI en dernier ressort, mais la borne qui
    ENGAGE est celle du contrat (`capabilities/runner_jobs.py`) : c'est elle qui la
    déclare et qui rend le total + le curseur qui la disent. Une borne connue du
    seul SQL est exactement ce que #469 reprochait."""
    ou, params = _filtre_de_file(org_id, status)
    q = ("SELECT id, kind, run_id, payload, status, attempts, max_attempts, "
         "       claimed_by, last_error, result, due_at, created_at, finished_at "
         "FROM runner_jobs") + ou
    if before_id is not None:
        q += " AND id < %s"
        params.append(int(before_id))
    q += " ORDER BY id DESC LIMIT %s"
    params.append(max(1, min(int(limit), JOBS_PAGE_MAX)))
    with _connect() as conn:
        rows = conn.execute(q, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def count_jobs(org_id: int, status: Optional[str] = None) -> int:
    """Le nombre de jobs de la file, MÊMES filtres que `list_jobs` et sans son
    plafond : c'est le chiffre qu'un bilan de vague vient chercher, et celui qui
    dit qu'une page est tronquée. Le curseur, lui, dit comment lire la suite."""
    ou, params = _filtre_de_file(org_id, status)
    with _connect() as conn:
        row = conn.execute("SELECT count(*) AS n FROM runner_jobs" + ou,
                           tuple(params)).fetchone()
    return int(dict(row)["n"])


def get_job(job_id: int, org_id: int) -> Optional[dict]:
    """Lecture d'un job, org-scopée — même 404 qu'un job inexistant côté capacité."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, kind, run_id, payload, status, attempts, max_attempts, result, "
            "       claimed_by, last_error, due_at, created_at, finished_at "
            "FROM runner_jobs WHERE id = %s AND org_id = %s",
            (job_id, org_id),
        ).fetchone()
    return dict(row) if row else None
