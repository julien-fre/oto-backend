"""File des travaux de transcription (ADR 0074) — voir `db/schema/transcription.py`
pour la forme de la table et pourquoi la ligne naît à la demande.

⚠️ Noms PRÉFIXÉS `transcription_job` (pas `create_job`/`get_job`/`claim_next_job` nus) :
`db/__init__.py` aplatit TOUS les modules dans le même namespace `db.*`, et
`runner_jobs.py` porte déjà `get_job`/`claim_next_job` avec une forme différente
(scopés `org_id`) — un nom nu écraserait silencieusement l'un des deux à l'import
(#674, trouvé par `tests/test_runner_jobs.py` rougissant sur un `TypeError` sans
rapport apparent avec ce fichier).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from ._conn import _connect

# Posée par la révision `0026_transcription_tours` et par le démarrage (même DDL).
COLONNE_TRANSCRIPT = "transcript"
DDL_COLONNE_TRANSCRIPT = (f"ALTER TABLE transcription_jobs ADD COLUMN IF NOT EXISTS "
                          f"{COLONNE_TRANSCRIPT} JSONB")


def create_transcription_job(*, project_id: int, sub: str, audio_key: str,
                             filename: str, mime: Optional[str], language: Optional[str],
                             vocabulary: Optional[str], api_key_enc: str) -> int:
    """Crée le travail `pending` et rend son id. Posée dans le contexte de
    l'appel MCP (seul endroit où le fichier source et le credential résolvent) ;
    le worker n'a plus besoin de ce contexte ensuite."""
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO transcription_jobs "
            "  (project_id, sub, audio_key, filename, mime, language, vocabulary, api_key_enc) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, sub, audio_key, filename, mime, language, vocabulary, api_key_enc),
        ).fetchone()
        return int(row["id"])


def get_transcription_job(job_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, project_id, sub, status, filename, page_id, result, transcript, error, "
            "       created_at, updated_at "
            "FROM transcription_jobs WHERE id = %s", (job_id,),
        ).fetchone()
        return dict(row) if row else None


def claim_next_transcription_job() -> Optional[dict]:
    """Réclame ATOMIQUEMENT le plus ancien travail `pending` (`FOR UPDATE SKIP
    LOCKED`, même patron que `runner_jobs`) et le passe `running`. `None` si la
    file est vide — jamais deux tours qui réclament la même ligne."""
    with _connect() as conn:
        row = conn.execute(
            """
            WITH pris AS (
                SELECT id FROM transcription_jobs
                 WHERE status = 'pending'
                 ORDER BY created_at
                   FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE transcription_jobs j
               SET status = 'running', attempts = attempts + 1, updated_at = NOW()
              FROM pris WHERE j.id = pris.id
            RETURNING j.id, j.project_id, j.sub, j.audio_key, j.filename, j.mime,
                      j.language, j.vocabulary, j.api_key_enc
            """,
        ).fetchone()
        return dict(row) if row else None


def mark_transcription_job_done(job_id: int, *, page_id: int, result: dict,
                                transcript: list[dict]) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE transcription_jobs SET status = 'done', page_id = %s, "
            "  result = %s::jsonb, transcript = %s::jsonb, updated_at = NOW() "
            "WHERE id = %s",
            (page_id, json.dumps(result), json.dumps(transcript), job_id),
        )


def mark_transcription_job_failed(job_id: int, *, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE transcription_jobs SET status = 'failed', error = %s, "
            "  updated_at = NOW() WHERE id = %s",
            (error, job_id),
        )


#: Un travail `running` depuis plus longtemps que le délai du client Mistral (300 s)
#: plus la marge de l'écriture de la page n'a plus de tour qui le porte : son
#: processus a été arrêté en cours d'appel (bascule bleu/vert, redémarrage).
RUNNING_ORPHELIN_S = 420


def fail_orphaned_transcription_jobs() -> int:
    """Clôt `failed` les travaux restés `running` sans tour vivant (processus arrêté
    en plein appel). Sans ça, l'agent qui relit le statut attend un travail que plus
    personne ne porte, sans fin. Pas de reprise automatique — l'appel est payant
    (#674) : le refus dit de redemander. Rend le nombre de travaux clos."""
    with _connect() as conn:
        rows = conn.execute(
            "UPDATE transcription_jobs SET status = 'failed', "
            "  error = 'Interrompu : le serveur a redémarré pendant l''appel à Mistral. "
            "Rien n''a été écrit — relance transcription_create.', updated_at = NOW() "
            "WHERE status = 'running' "
            "  AND updated_at < NOW() - make_interval(secs => %s) RETURNING id",
            (RUNNING_ORPHELIN_S,),
        ).fetchall()
        return len(rows)


#: Combien de fins récentes servent à mesurer le débit de la file.
_FINS_MESUREES = 20


def transcription_queue(job_id: int) -> dict:
    """Où en est un travail `pending` dans la file : `ahead` = travaux `pending` plus
    anciens (la file est servie dans l'ordre de dépôt, toutes orgs confondues), et
    `finishes` = les dernières fins (`done`/`failed`) de la dernière heure, plus
    récente d'abord — le débit RÉEL de la file, concurrence du worker comprise.

    `ahead` lit l'index partiel des `pending` ; les fins se lisent à rebours de la
    clé primaire (borné, jamais un parcours de la table)."""
    with _connect() as conn:
        ahead = conn.execute(
            "SELECT count(*) AS n FROM transcription_jobs t "
            " WHERE t.status = 'pending' "
            "   AND t.created_at < (SELECT created_at FROM transcription_jobs WHERE id = %s)",
            (job_id,),
        ).fetchone()["n"]
        fins = conn.execute(
            "SELECT updated_at FROM ("
            "  SELECT status, updated_at FROM transcription_jobs ORDER BY id DESC LIMIT 200"
            ") r WHERE r.status IN ('done', 'failed') "
            "   AND r.updated_at > NOW() - interval '1 hour' "
            " ORDER BY r.updated_at DESC LIMIT %s",
            (_FINS_MESUREES,),
        ).fetchall()
    return {"ahead": int(ahead), "finishes": [r["updated_at"] for r in fins]}
