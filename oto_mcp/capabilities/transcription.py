"""Transcription (ADR 0074) — déposer un travail, le relire. Le cœur des TROIS faces :

- les outils MCP `transcription_create`/`transcription_status` (`tools/transcription.py`),
  qui désignent le fichier par sa RÉFÉRENCE et prennent le projet dans `_project` ;
- la ressource REST ci-dessous : `POST /api/me/projects/{id}/transcriptions` (une
  référence `file_source`, typiquement un `project_file`) et
  `GET /api/me/transcriptions/{job_id}` (l'état, et les tours de parole en JSON) ;
- le dépôt DIRECT d'un fichier audio, `POST /api/me/projects/{id}/transcriptions/upload`
  (`api/transcription.py`) : un corps multipart, hors couche capacité par nature
  (ADR 0009), qui appelle le même `deposer`.

**Ce qui se fait dans le contexte de l'appel** (avant de rendre), dans cet ordre :
le droit d'ÉCRIRE dans le projet, puis la lecture de l'audio, puis la résolution du
credential — jamais un fichier lu ni un travail facturable créé pour un texte qui
n'aura nulle part où se ranger. Le credential et le fichier dépendent du `sub`/de
l'org ACTIFS : ils n'existent plus une fois le travail rendu, d'où leur place ici et
pas dans le worker (`oto_mcp/transcription_worker.py`).

La clé est CHIFFRÉE (même enveloppe que le coffre, `oto_mcp/crypto.py`) avant d'être
portée sur le travail — aucune colonne plaintext.
"""
from __future__ import annotations

import math
import statistics
from typing import Callable, Literal, Optional

from pydantic import BaseModel

from .. import access, db, file_source, media_store
from ..crypto import encrypt as _encrypt
from . import projects as _projects
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Plafond d'un AUDIO à transcrire, plus large que celui d'un fichier de projet (25 Mo) :
# une réunion de 2 h pèse ~45 Mo en mono 48 kbps. Mistral accepte 500 Mo et 3 h par
# requête ; la borne est la nôtre — le fichier tient en RAM au dépôt comme au worker,
# qui en mène `CONCURRENCE` de front (3 × 100 Mo au pire). 100 Mo = 3 h en mono 64 kbps.
MAX_AUDIO_BYTES = 100 * 1024 * 1024

# `language` vide = cette langue ; `auto` = détection par le fournisseur (aucune langue
# envoyée). Mesuré au banc : aucun écart entre `fr` forcé et `auto` sur du français.
_LANGUE_PAR_DEFAUT = "fr"
_AUTO = "auto"


def _langue(creds: dict) -> str | None:
    """Langue envoyée d'après le champ `language` de l'instance (None = détection)."""
    valeur = (creds.get("language") or "").strip() or _LANGUE_PAR_DEFAUT
    return None if valeur.lower() == _AUTO else valeur


def _vocabulaire(instance: str | None, appel: str | None, remplace: bool) -> str | None:
    """Vocabulaire du travail : celui de l'instance complété par celui de l'appel
    (l'instance d'abord : au-delà de 100 mots c'est l'appel qui est rogné), ou
    l'appel seul s'il remplace."""
    parts = [appel] if remplace else [instance, appel]
    return "\n".join(p.strip() for p in parts if p and p.strip()) or None


def _introuvable(job_id: int) -> AuthzDenied:
    return AuthzDenied(404, "unknown_transcription",
                       f"Travail de transcription #{job_id} introuvable.")


def deposer(sub: str, pid: int, lire: Callable[[], file_source.ResolvedFile], *,
            vocabulary: Optional[str] = None, vocabulary_replace: bool = False) -> dict:
    """Dépose un travail `pending` et rend sa référence, SANS appeler Mistral.

    `lire` rend l'audio ; il n'est appelé qu'APRÈS la garde d'écriture (une référence
    `file_source` à résoudre, ou les octets déjà reçus du dépôt direct). Lève
    `AuthzDenied`, que chaque face traduit."""
    from .docs import common as docs_common
    if not docs_common.can(sub, pid, "write"):
        raise AuthzDenied(403, "forbidden",
                          f"Écriture refusée sur le projet #{pid} : rien n'a été transcrit.")
    try:
        fichier = lire()
    except file_source.FileSourceError as e:
        raise AuthzDenied(400, "invalid_source", str(e)) from None
    if not fichier.data:
        raise AuthzDenied(400, "empty_file", "Le fichier audio est vide.")

    from ..mcp_errors import McpError
    try:
        rc = access.resolve_credential("transcription", want="auto", sub=sub)
    except McpError as e:
        # `from e` : la face MCP rend l'erreur d'origine, déjà actionnable.
        raise AuthzDenied(400, "credential_unavailable", e.error.message) from e
    # Un secret plateforme est la clé SEULE (la langue et le vocabulaire sont ceux de
    # l'appel) ; celui d'une instance d'org est le pack JSON des trois champs.
    creds = {"api_key": rc.secret} if rc.is_platform else rc.fields
    api_key = creds.get("api_key")
    if not api_key:
        raise AuthzDenied(400, "credential_unavailable",
                          "Aucune clé Mistral posée sur cette instance : rien n'a été transcrit.")

    try:
        audio_key = media_store.upload_object(
            "transcription-jobs", str(pid), fichier.data, fichier.mime,
            filename=fichier.filename, max_bytes=MAX_AUDIO_BYTES)
    except media_store.MediaError as e:
        raise AuthzDenied(e.status, e.code, str(e)) from None
    # AAD = `audio_key` (déjà unique, déjà colonne de LA ligne créée juste en
    # dessous) : lie le chiffré à sa ligne sans un aller-retour pour connaître
    # l'id serial d'abord (même intention que le coffre, forme plus simple).
    job_id = db.create_transcription_job(
        project_id=pid, sub=sub, audio_key=audio_key, filename=fichier.filename,
        mime=fichier.mime, language=_langue(creds),
        vocabulary=_vocabulaire(creds.get("vocabulary"), vocabulary, vocabulary_replace),
        api_key_enc=_encrypt(api_key, f"transcription_jobs:{audio_key}"))
    return {"job_id": job_id, "status": "pending"}


def relire(sub: str, job_id: int, *, transcript: bool) -> dict:
    """L'état d'un travail. `transcript=False` (face MCP) retire les tours verbatim :
    un agent lit la PAGE, jamais le texte brut dans son contexte. Un travail d'un projet
    illisible répond exactement comme un inconnu (ne révèle pas son existence)."""
    from .docs import common as docs_common
    job = db.get_transcription_job(int(job_id))
    if job is None or not docs_common.can(sub, int(job["project_id"]), "read"):
        raise _introuvable(job_id)
    out = {"job_id": job["id"], "project_id": job["project_id"],
           "status": job["status"], "filename": job["filename"]}
    if job["status"] == "done":
        out.update(job["result"] or {})
        if transcript:
            out["transcript"] = job.get("transcript")
    elif job["status"] == "failed":
        out["error"] = job["error"]
    elif job["status"] == "pending":
        out.update(position_en_file(db.transcription_queue(int(job["id"]))))
    elif job["status"] == "running":
        out["retry_after_s"] = _RELIRE_S
        out["note"] = ("Being transcribed now (up to 5 min for a long recording). "
                       f"Re-read in {_RELIRE_S} s; do not resubmit.")
    return out


# Relire un travail en cours ou en file : assez souvent pour suivre, pas assez pour
# saturer (un appel Mistral dure de 30 s à 5 min).
_RELIRE_S = 30


def position_en_file(file: dict) -> dict:
    """La place d'un travail `pending` et une ESTIMATION d'attente, tirée du débit réel
    de la file : l'écart médian entre les dernières fins (`finishes`, plus récente
    d'abord, en secondes Unix). Ce débit compte déjà la concurrence du worker. Sans assez
    de fins récentes pour le mesurer, pas d'estimation (`null`) plutôt qu'un chiffre
    inventé."""
    ahead = int(file["ahead"])
    fins: list[float] = [float(f) for f in file["finishes"]]
    ecarts = [a - b for a, b in zip(fins, fins[1:])]
    estimation = None
    if ecarts:
        par_travail = max(statistics.median(ecarts), 1.0)
        estimation = int(math.ceil((ahead + 1) * par_travail))
    out = {"queue_position": ahead, "estimated_wait_s": estimation,
           "retry_after_s": _RELIRE_S}
    attente = (f"about {estimation} s (estimate from the queue's recent pace)"
               if estimation is not None else "unknown (no recent pace to measure)")
    out["note"] = (f"Queued: {ahead} job(s) ahead, wait {attente}. The job stays "
                   f"queued until it runs — re-read every {_RELIRE_S} s, do not resubmit "
                   "(a second job only lengthens the queue).")
    return out


# ── Face REST ────────────────────────────────────────────────────────────────


class TranscriptionCreateInput(BaseModel):
    project_id: int
    # La RÉFÉRENCE du fichier, jamais ses octets (`file_source`) : `project_file`
    # (`{"kind":"project_file","file_id":N}`, le projet de l'URL par défaut), `url`,
    # `drive`, `gmail`. Un fichier à envoyer tel quel passe par `…/transcriptions/upload`.
    source: dict
    vocabulary: Optional[str] = None
    vocabulary_replace: bool = False


class TranscriptionReadInput(BaseModel):
    job_id: int


class TranscriptionJobRef(BaseModel):
    job_id: int
    status: Literal["pending"]


class TranscriptionPage(BaseModel):
    id: int
    project_id: int
    title: str
    url: Optional[str] = None


class TranscriptionTurn(BaseModel):
    """Un tour de parole : segments consécutifs d'un même locuteur (`transcript.turns`).
    `speaker` = « Locuteur N » dans l'ordre d'apparition, `null` sans diarisation ;
    `start`/`end` en secondes, `null` si l'amont n'a pas horodaté."""
    speaker: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    text: str


class TranscriptionJob(BaseModel):
    """Les champs du résultat n'existent que sur `done`, `error` que sur `failed`.
    `transcript` = `null` pour un travail terminé avant que les tours soient gardés
    (28/09/2026) : sa page les porte, pas la base."""
    job_id: int
    project_id: int
    status: Literal["pending", "running", "done", "failed"]
    filename: str
    error: Optional[str] = None
    page: Optional[TranscriptionPage] = None
    words: Optional[int] = None
    duration_s: Optional[float] = None
    speakers: Optional[list[str]] = None
    turns: Optional[int] = None
    language: Optional[str] = None
    vocabulary_terms: Optional[int] = None
    vocabulary_dropped: Optional[list[str]] = None
    transcript: Optional[list[TranscriptionTurn]] = None
    # `pending` : la place en file (travaux plus anciens devant) et une ESTIMATION
    # d'attente en secondes (`null` sans débit récent mesurable). `pending`/`running` :
    # quand relire, et une note pour l'agent.
    queue_position: Optional[int] = None
    estimated_wait_s: Optional[int] = None
    retry_after_s: Optional[int] = None
    note: Optional[str] = None


def _projet_ecrivable(ctx: ResolvedCtx, pid: int) -> None:
    row = db.get_project_by_id(pid)
    if row is None:
        raise AuthzDenied(404, "unknown_project", f"Projet #{pid} inconnu.")
    _projects._require_active_org_visible(ctx, row)


def _create(ctx: ResolvedCtx, inp: TranscriptionCreateInput) -> dict:
    _projet_ecrivable(ctx, inp.project_id)
    source = dict(inp.source)
    if source.get("kind") == "project_file":
        source.setdefault("project_id", inp.project_id)
    return deposer(ctx.sub, inp.project_id,
                   lambda: file_source.resolve(source, max_bytes=MAX_AUDIO_BYTES),
                   vocabulary=inp.vocabulary, vocabulary_replace=inp.vocabulary_replace)


def _read(ctx: ResolvedCtx, inp: TranscriptionReadInput) -> dict:
    return relire(ctx.sub, inp.job_id, transcript=True)


_REFUS_DEPOT = (
    DeclaredError(404, "unknown_project", "le projet n'existe pas ou n'est pas visible dans l'org active"),
    DeclaredError(403, "forbidden", "l'appelant ne peut pas écrire dans le projet"),
    DeclaredError(400, "invalid_source", "la référence de fichier est invalide, illisible ou trop grosse"),
    DeclaredError(400, "credential_unavailable", "aucune clé Mistral ne résout pour ce compte"),
)

CAPABILITIES += [
    Capability(
        key="me.transcription.create", handler=_create, Input=TranscriptionCreateInput,
        authz=SUB_ONLY, Output=TranscriptionJobRef,
        description=(
            "Start transcribing an audio file already reachable by oto (a project file, "
            "an URL, Drive, Gmail) into a new page of the project — asynchronous, returns "
            "`{job_id, status: \"pending\"}`. Read the result with "
            "`GET /api/me/transcriptions/{job_id}`. To send the audio itself, POST it as "
            "multipart to `/api/me/projects/{project_id}/transcriptions/upload`."
        ),
        rest=RestBinding("POST", "/api/me/projects/{project_id:int}/transcriptions",
                         status=202),
        errors=_REFUS_DEPOT,
    ),
    Capability(
        key="me.transcription.read", handler=_read, Input=TranscriptionReadInput,
        authz=SUB_ONLY, Output=TranscriptionJob,
        description=(
            "A transcription job: `status` pending|running|done|failed. On `pending`, "
            "`queue_position` (jobs ahead) and `estimated_wait_s` (an estimate, `null` "
            "when unknown); on `pending`/`running`, `retry_after_s`. On `done`, the "
            "page `{id, project_id, title, url}`, words, duration_s, speakers, and "
            "`transcript` — the speaker turns `[{speaker, start, end, text}]` (seconds). "
            "On `failed`, `error`."
        ),
        rest=RestBinding("GET", "/api/me/transcriptions/{job_id:int}"),
        errors=(DeclaredError(404, "unknown_transcription",
                              "le travail n'existe pas ou son projet n'est pas lisible"),),
    ),
]
