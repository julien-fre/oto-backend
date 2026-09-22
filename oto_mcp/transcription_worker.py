"""Worker de transcription (ADR 0074) — boucle de fond dédiée.

`transcription_create` ne bloque plus l'agent (#674) : il dépose un travail
`pending` (`db.create_transcription_job`) et rend sa référence tout de suite. Cette boucle le
réclame, appelle le fournisseur — jusqu'à 300 s, mesuré au banc — et écrit la
page, hors de la boucle d'événements et hors de tout contexte d'appel MCP.

**Pourquoi un job peut s'exécuter sans contexte d'appel.** Les deux points qui
ont besoin du `sub` et de l'org ACTIFS de l'appelant (résoudre le credential
byo_user, lire l'octet du fichier source) sont déjà faits à la création du
travail, dans `oto_mcp/tools/transcription.py`, pendant que ce contexte existe
encore. Le worker ne fait plus que : relire un objet déjà déposé (`media_store`,
comme `file_extract_worker`), déchiffrer une clé déjà résolue (`crypto`, même
enveloppe que le coffre), et écrire une page — `capabilities.docs.writes.create`
vérifie le droit d'écrire par OWNERSHIP réel (`sub` + `project_id` explicites),
jamais par un contexte ambiant.

**Une seule tentative.** Contrairement à `file_extract_worker` (gratuit, local),
l'appel est PAYANT : un échec ne se retente pas tout seul (`attempts` sert
l'observation, pas une boucle de reprise) — l'agent redemande une transcription
s'il le veut. L'objet audio temporaire est purgé après le tour, succès ou échec :
aucune copie ne traîne plus longtemps que nécessaire.

**Hors de la boucle d'événements**, comme les autres workers (`docs/event-loop-perf.md`) :
le tour est SYNC, passé par `run_in_threadpool`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from . import db, media_store, transcript
from .crypto import decrypt as _decrypt

logger = logging.getLogger(__name__)

# Un seul travail à la fois : chacun peut tenir un thread du pool jusqu'à 300 s
# (le délai du client Mistral), un lot plus large tiendrait juste la file plus
# longtemps pour rien. Poll court : contrairement à l'extraction de fichiers,
# une transcription est un travail que l'agent ATTEND (il relit le statut).
_POLL_S = 5


def _aad(audio_key: str) -> str:
    return f"transcription_jobs:{audio_key}"


def _traduire(e: Exception) -> str:
    """Un refus du fournisseur → un message actionnable (le statut, pas une pile) —
    ce que lira l'agent via `transcription_status`."""
    status = getattr(e, "status_code", None)
    if status == 401:
        return ("Mistral a refusé la clé de l'instance (401) : invalide ou "
                "révoquée. Vérifie la clé posée sur le connecteur.")
    if status == 429:
        return "Mistral limite le débit de cette clé (429). Réessaie dans un moment."
    if isinstance(status, int) and status >= 500:
        return f"Mistral est momentanément indisponible ({status}). Réessaie dans un moment."
    if isinstance(status, int):
        return f"Mistral a refusé ce fichier ({e}). Vérifie que c'est bien un enregistrement audio lisible."
    return f"Mistral n'a pas pu transcrire ce fichier ({e})."


def _ecrire_page(sub: str, pid: int, titre: str, corps: str) -> dict:
    from .capabilities._types import AuthzDenied
    from .capabilities.docs import writes
    from .capabilities.docs.core import DocInput
    try:
        return writes.create(sub, DocInput(op="create", project_id=pid, title=titre,
                                           body_md=corps, kind="source"))
    except AuthzDenied as e:
        raise RuntimeError(f"écriture refusée sur le projet #{pid} ({e.code})") from None


def _transcribe_one(job: dict) -> str:
    """Un travail : relire l'audio, appeler Mistral, écrire la page. Rend le
    statut obtenu (`done`/`failed`). Ne lève jamais — le résultat, succès comme
    échec, est PERSISTÉ sur le travail (sinon l'agent qui poll ne saurait
    jamais qu'il est allé au bout)."""
    jid = int(job["id"])
    try:
        data = media_store.fetch_object(job["audio_key"])
    except Exception as e:  # noqa: SILENT — stockage : l'échec est PERSISTÉ sur le travail
        db.mark_transcription_job_failed(jid, error=f"lecture de l'audio impossible ({type(e).__name__}).")
        return "failed"

    import requests
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.mistral import MistralClient
    try:
        api_key = _decrypt(job["api_key_enc"], _aad(job["audio_key"]))
        brut = MistralClient(api_key=api_key).transcribe(
            data, job["filename"], mime=job.get("mime"), language=job["language"],
            timestamps=True, diarize=True, context_bias=job["vocabulary"] or None,
            timeout=(10, 300))
    except UpstreamHTTPError as e:
        db.mark_transcription_job_failed(jid, error=_traduire(e))
        return "failed"
    except requests.Timeout:
        db.mark_transcription_job_failed(jid, error="Mistral n'a pas répondu dans le délai (300 s). "
                           "Rien n'a été écrit.")
        return "failed"
    except requests.RequestException as e:
        db.mark_transcription_job_failed(jid, error=f"Mistral est injoignable ({type(e).__name__}). "
                           "Rien n'a été écrit.")
        return "failed"
    finally:
        try:
            media_store.delete_by_key(job["audio_key"])
        except Exception:  # noqa: BLE001 — best-effort, ne masque jamais le résultat
            logger.warning("transcription_worker: purge de l'audio #%s échouée.", jid)

    tours = transcript.process(brut["segments"])
    if not tours and brut["text"].strip():
        tours = [{"speaker": None, "start": None, "text": brut["text"].strip()}]
    if not tours:
        db.mark_transcription_job_failed(jid, error=f"Aucune parole reconnue dans « {job['filename']} ».")
        return "failed"

    jour = datetime.now(timezone.utc).date().isoformat()
    titre = transcript.title(job["filename"], jour)
    try:
        page = _ecrire_page(job["sub"], int(job["project_id"]), titre, transcript.render(
            tours, filename=job["filename"], duration_s=brut["duration_s"]))
    except RuntimeError as e:
        db.mark_transcription_job_failed(jid, error=str(e))
        return "failed"

    locuteurs = sorted({t["speaker"] for t in tours if t["speaker"]})
    result = {
        "page": {k: page.get(k) for k in ("id", "project_id", "title", "url")},
        "words": transcript.word_count(tours),
        "duration_s": brut["duration_s"],
        "speakers": locuteurs,
        "turns": len(tours),
        "language": job["language"] or "auto",
        "vocabulary_terms": len(brut.get("context_bias") or []),
    }
    if brut.get("context_bias_dropped"):
        result["vocabulary_dropped"] = brut["context_bias_dropped"]
    db.mark_transcription_job_done(jid, page_id=int(page["id"]), result=result)
    return "done"


def _transcribe_batch() -> int:
    """Un tour SYNC (threadpool) : réclame et traite AU PLUS UN travail. Rend
    1 si un travail a été traité, 0 si la file était vide."""
    job = db.claim_next_transcription_job()
    if job is None:
        return 0
    try:
        _transcribe_one(job)
    except Exception as e:  # noqa: BLE001 — ceinture : la boucle survit quoi qu'il arrive
        logger.warning("transcription_worker: travail #%s explosé : %s", job.get("id"), e)
        try:
            db.mark_transcription_job_failed(int(job["id"]), error=f"échec interne ({type(e).__name__}).")
        except Exception:  # noqa: SILENT — la base est injoignable, déjà journalisé au-dessus
            pass
    return 1


async def run_transcription_loop(interval: int = _POLL_S) -> None:
    logger.info("transcription_worker: démarré (poll %ss).", interval)
    while True:
        try:
            traites = await run_in_threadpool(_transcribe_batch)
            if traites:
                logger.info("transcription_worker: %d travail(aux) traité(s).", traites)
        except Exception as e:  # noqa: BLE001 — un tour raté ne tue pas la boucle
            logger.warning("transcription_worker: tour en échec : %s", e)
        await asyncio.sleep(interval)
