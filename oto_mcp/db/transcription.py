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
from typing import Optional

from ._conn import _connect


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
            "SELECT id, project_id, sub, status, filename, page_id, result, error, "
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


def mark_transcription_job_done(job_id: int, *, page_id: int, result: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE transcription_jobs SET status = 'done', page_id = %s, "
            "  result = %s::jsonb, updated_at = NOW() WHERE id = %s",
            (page_id, json.dumps(result), job_id),
        )


def mark_transcription_job_failed(job_id: int, *, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE transcription_jobs SET status = 'failed', error = %s, "
            "  updated_at = NOW() WHERE id = %s",
            (error, job_id),
        )
