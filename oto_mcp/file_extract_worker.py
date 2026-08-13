"""Worker d'extraction du texte des fichiers déposés (#298) — boucle de fond dédiée.

Draine la file du barreau 2 : les fichiers sans texte extrait, plus les échecs
imprévus pas encore épuisés. Chaque tour lit un petit lot, télécharge, extrait,
enregistre le résultat — succès **comme refus**.

## Pourquoi une boucle à part plutôt qu'un pas de plus dans `embed_worker`

`embed_worker.run_embed_loop` commence par `if not embeddings.enabled(): return` — ce
qui est juste pour ce qu'elle fait : sans clé Mistral, il n'y a pas d'embedding à
calculer. Mais **l'extraction de texte ne dépend d'aucun service tiers**. L'y greffer
la rendrait muette sur tout déploiement sans `MISTRAL_API_KEY`, et le symptôme serait
« la recherche de fichiers ne trouve rien », sans erreur nulle part. Un silence
structurel, exactement la classe de panne que ce dépôt traque.

S'y ajoutent deux raisons de fond : les domaines de panne restent **disjoints** (un
échec d'embedding n'arrête pas l'extraction, ni l'inverse), et les deux travaux n'ont
ni le même rythme ni le même coût — 130 ms de CPU local par fichier ici, un
aller-retour réseau facturé là-bas.

## Deux contraintes du terrain, tenues ici

**Hors de la boucle d'événements.** Le serveur est mono-loop : 130 ms de CPU par
fichier dans la boucle est un gel (`docs/event-loop-perf.md`). Le tour de travail est
donc SYNC et passe par `run_in_threadpool`, comme `embed_worker`.

**Idempotent par le statut.** Un redémarrage en plein lot ne double rien : le travail
se réclame par l'ABSENCE de ligne, et chaque fichier traité en pose une — refus
compris. Au pire un fichier est ré-extrait une fois (le résultat écrase le précédent),
jamais deux fois compté.
"""
from __future__ import annotations

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from . import db, file_extract

logger = logging.getLogger(__name__)

# Rythme volontairement lent : l'indexation d'un fichier n'est pas interactive, et un
# dépôt attend quelques secondes sans que personne ne le remarque. Un poll court ne
# gagnerait rien et réveillerait la base pour rien.
_POLL_S = 30
# Petit lot : chaque fichier est un téléchargement + du CPU. Un gros lot tiendrait un
# thread du pool longtemps, au détriment des requêtes qui en ont besoin.
_BATCH = 5


def _extract_one(f: dict) -> str:
    """Un fichier : télécharger, extraire, enregistrer. Rend le statut obtenu.

    Ne lève jamais — un fichier ne doit pas pouvoir bloquer la file. Une erreur de
    TÉLÉCHARGEMENT (stockage indisponible, clé absente) est un `failed` reprenable :
    contrairement à un format non supporté, elle peut disparaître d'elle-même.
    """
    from . import media_store

    fid = int(f["id"])
    try:
        data = media_store.fetch_object(f["s3_key"])
    except Exception as e:  # noqa: BLE001 — stockage : reprenable, borné par `attempts`
        detail = getattr(e, "code", None) or type(e).__name__
        db.save_extracted_text(fid, status=file_extract.FAILED, detail=str(detail))
        return file_extract.FAILED

    out = file_extract.extract(data, f.get("filename") or "", f.get("mime") or "")
    db.save_extracted_text(fid, status=out.status, text=out.text,
                           pages=out.pages, detail=out.detail)
    return out.status


def _extract_batch() -> tuple:
    """Un tour SYNC (exécuté en threadpool). Rend `(traités, extraits)`.

    `traités` compte tout ce qui a reçu une réponse — refus inclus, puisque c'est ce
    qui les fait sortir de la file. `extraits` ne compte que les succès : c'est le
    chiffre qui dit si la recherche s'enrichit, et les distinguer évite de lire « 40
    fichiers traités » comme « 40 fichiers devenus cherchables »."""
    lot = db.files_pending_extraction(limit=_BATCH)
    if not lot:
        return 0, 0
    extraits = 0
    for f in lot:
        try:
            if _extract_one(f) == file_extract.OK:
                extraits += 1
        except Exception as e:  # noqa: BLE001 — ceinture : la file avance quoi qu'il arrive
            logger.warning("file_extract_worker: fichier #%s ignoré : %s", f.get("id"), e)
    return len(lot), extraits


async def run_extract_loop(interval: int = _POLL_S) -> None:
    """La boucle, composée au lifespan (`server._bg_loops`).

    Pas de gate d'activation : contrairement à l'embedding, l'extraction ne dépend
    d'aucune clé. Si le stockage objet n'est pas configuré, chaque fichier échoue
    proprement et la file se borne d'elle-même (`attempts`), ce qui est plus honnête
    qu'un worker qui se tait."""
    logger.info("file_extract_worker: démarré (poll %ss, lot %s).", interval, _BATCH)
    while True:
        try:
            traites, extraits = await run_in_threadpool(_extract_batch)
            if traites:
                logger.info("file_extract_worker: %d fichier(s) traité(s), "
                            "%d extrait(s).", traites, extraits)
        except Exception as e:  # noqa: BLE001 — un tour raté ne tue pas la boucle
            logger.warning("file_extract_worker: tour en échec : %s", e)
        await asyncio.sleep(interval)
