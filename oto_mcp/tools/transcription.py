"""Transcription — un audio du projet devient une page du projet (ADR 0074).

Deux outils. `transcription_create` : l'agent désigne un fichier par sa
RÉFÉRENCE (`file_source`, typiquement `project_file`), le serveur lit les octets
et dépose un TRAVAIL — il ne bloque PAS l'agent (#674, arbitrage du 22/09/2026:
un connecteur long ne doit jamais l'attendre en ligne). `transcription_status`
relit ce travail : en cours, terminé (avec la page), ou en échec (avec le refus).

**Ce qui se fait encore ICI, dans le contexte de l'appel** (avant de rendre) :
lire l'audio par le seam d'accès (`file_source.resolve`, garde de visibilité du
projet), résoudre le credential de l'instance (byo_org, ou plateforme accordée à l'org)
et vérifier le droit d'ÉCRIRE dans le projet — dans cet ordre, le droit d'écrire
AVANT l'appel payant reste vrai même déplacé : ici, c'est avant même de CRÉER le
travail, pour ne jamais facturer un texte qui n'aura nulle part où se ranger.
Ces deux résolutions dépendent du `sub`/de l'org ACTIFS de l'appel MCP — elles
n'existent plus une fois le travail rendu, d'où leur place ici et pas dans le
worker (`oto_mcp/transcription_worker.py`, qui exécute hors de ce contexte).

**Ce qui se fait en tâche de fond** : l'appel à Mistral (post-traitement inclus,
`oto_mcp/transcript.py`) et l'écriture de la page — celle-ci ne dépend QUE d'un
`sub` et d'un `project_id` explicites (ownership réelle, pas un contexte ambiant),
portés par le travail.

Credential à 3 champs (ADR 0011), résolu par appel via
`access.resolve_credential("transcription", want="auto")` (cascade, palier plateforme compris) : la clé (secret), la langue et le
vocabulaire (non secrets) — une instance = une clé × un vocabulaire, rattachable à un
projet par slot. La clé est CHIFFRÉE (même enveloppe que le coffre, `oto_mcp/crypto.py`)
avant d'être portée sur le travail — aucune colonne plaintext.
"""
from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData

from .. import access, db, file_source, media_store, upload_tokens
from ..connectors import verify as connector_verify
from ..crypto import encrypt as _encrypt
from ..mcp_errors import McpError

# `language` vide = cette langue ; `auto` = détection par le fournisseur (aucune langue
# envoyée). Mesuré au banc : aucun écart entre `fr` forcé et `auto` sur du français.
_LANGUE_PAR_DEFAUT = "fr"
_AUTO = "auto"


def _refus(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » — couvre `auth` SEUL : `GET /v1/models` n'est
    pas facturé et refuse une clé invalide. Elle ne dit rien du solde du compte."""
    from oto.tools.mistral import MistralClient

    MistralClient(api_key=fields["api_key"]).list_models()


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


def register(mcp: FastMCP) -> None:
    connector_verify.register("transcription", _verify)

    @mcp.tool()
    def transcription_create(source: dict, vocabulary: str | None = None,
                             vocabulary_replace: bool = False) -> dict:
        """Start transcribing an audio recording (site visit, meeting, voice note)
        into a new page of the project — ASYNCHRONOUS, returns a job reference
        immediately (a ~30 min recording takes 20 s to 5 min to process). Needs
        `_project` (the page's project). Poll `transcription_status(job_id)` for
        the result — the page, one paragraph per speaker turn, never the raw text.

        Args:
            source: the file, never its bytes. Project file:
                `{"kind":"project_file","project_id":<id>,"file_id":<id>}` (ids from
                oto_project_files op=list); also `drive`, `gmail`, `url`.
            vocabulary: optional extra words to spell right for THIS recording
                (names, materials), separated by commas or newlines. Added to the
                connector's vocabulary; 100 words at most overall, the surplus is
                reported by transcription_status (`vocabulary_dropped`).
            vocabulary_replace: true = use only `vocabulary`, ignore the connector's.
        """
        pid = access.current_project()
        if pid is None:
            raise _refus("transcription_create écrit une page de projet : passe "
                         "`_project=<id>` (le projet qui recevra la page).")
        sub = access.current_user_sub_or_raise()
        from ..capabilities.docs import common as docs_common
        if not docs_common.can(sub, pid, "write"):
            raise _refus(f"Écriture refusée sur le projet #{pid} : rien n'a été "
                         "transcrit.")
        try:
            fichier = file_source.resolve(source, max_bytes=upload_tokens.max_bytes())
        except file_source.FileSourceError as e:
            raise _refus(str(e)) from None

        creds = access.resolve_credential("transcription", want="auto").fields
        langue = _langue(creds)
        api_key = creds.get("api_key")
        if not api_key:
            raise _refus("Aucune clé Mistral posée sur cette instance : rien n'a "
                         "été transcrit.")

        audio_key = media_store.upload_object(
            "transcription-jobs", str(pid), fichier.data, fichier.mime,
            filename=fichier.filename, max_bytes=upload_tokens.max_bytes())
        # AAD = `audio_key` (déjà unique, déjà colonne de LA ligne créée juste en
        # dessous) : lie le chiffré à sa ligne sans un aller-retour pour connaître
        # l'id serial d'abord (même intention que le coffre, forme plus simple).
        job_id = db.create_transcription_job(
            project_id=pid, sub=sub, audio_key=audio_key, filename=fichier.filename,
            mime=fichier.mime, language=langue,
            vocabulary=_vocabulaire(creds.get("vocabulary"), vocabulary,
                                    vocabulary_replace),
            api_key_enc=_encrypt(api_key, f"transcription_jobs:{audio_key}"))

        return {"job_id": job_id, "status": "pending",
                "note": "Relire avec transcription_status(job_id)."}

    @mcp.tool()
    def transcription_status(job_id: int) -> dict:
        """Read a transcription job started by `transcription_create`. Returns
        `{status: pending|running|done|failed, ...}` — on `done`, the page
        `{id, title, url}` plus words/duration/speakers; on `failed`, `error`."""
        job = db.get_transcription_job(int(job_id))
        if job is None:
            raise _refus(f"Travail de transcription #{job_id} introuvable.")
        sub = access.current_user_sub_or_raise()
        from ..capabilities.docs import common as docs_common
        if not docs_common.can(sub, int(job["project_id"]), "read"):
            raise _refus(f"Travail de transcription #{job_id} introuvable.")
        out = {"job_id": job["id"], "status": job["status"]}
        if job["status"] == "done":
            out.update(job["result"] or {})
        elif job["status"] == "failed":
            out["error"] = job["error"]
        return out
