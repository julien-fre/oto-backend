"""Remplissage des vecteurs de classement (#318) — boucle de fond, jamais au boot.

Le classement par pertinence recalculait `to_tsvector` par candidat : 674 ms sur un
mot fréquent, contre 0,2 ms pour le même filtre sans classement. Une colonne
matérialisée par source le ramène à 10 ms — encore faut-il la remplir, et c'est ici.

## Pourquoi en fond, et pas au démarrage

Parce que la variante qui remplissait toute seule (`GENERATED ALWAYS AS … STORED`)
réécrit la table sous **verrou exclusif** : mesuré à **7,55 s sur `datastore_rows`**
au volume de production. Sur une base partagée entre production et préproduction, ce
n'est pas un démarrage plus lent — c'est une interruption de service sur la table la
plus chargée, au moment précis d'un déploiement.

Le remplissage est donc **borné par tranche** (verrou par ligne, jamais sur la table)
et hors du chemin de démarrage, qui reste sous le healthcheck du deploy.

## Pourquoi la boucle NE S'ARRÊTE PAS quand tout est rempli

Elle devient la **réconciliation**. Le vecteur s'écrit dans le chemin d'écriture, mais
une écriture qui l'aurait raté laisse une ligne à `NULL` — le tour suivant la reprend.
C'est ce qui permet de se passer d'un déclencheur en base : l'intégrité vit dans le
code, et le rattrapage est le filet.

Rien ne peut mentir entre-temps : le classement lit `COALESCE(colonne, expression)`,
donc une ligne non remplie se classe exactement comme avant. **Aucun silence n'est
possible**, ce qui est la propriété qui a fait préférer cette forme à une bascule.
"""
from __future__ import annotations

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from .db import search as _search
from .db._conn import _connect

logger = logging.getLogger(__name__)

# Rythme lent : le remplissage n'est pas interactif, et le `COALESCE` fait que
# l'absence de vecteur ne coûte que… ce que ça coûtait avant. Rien ne presse.
_POLL_S = 15
# Tranche : assez pour avancer, assez peu pour que le verrou reste au grain de la
# ligne. 43 782 lignes se rattrapent en une quinzaine de minutes à ce rythme, sans
# que personne ne s'en aperçoive.
_BATCH = 500


def _backfill_round() -> dict:
    """Un tour SYNC (threadpool) : une tranche par source qui en a besoin.

    Une source par tour plutôt que tout d'un coup : deux transactions courtes valent
    mieux qu'une longue, et une source en erreur n'empêche pas les autres d'avancer.
    """
    faits: dict = {}
    for table in _search.RANKED_SOURCES:
        try:
            with _connect() as conn:
                cur = conn.execute(_search.rank_backfill_sql(table, _BATCH))
                n = cur.rowcount or 0
        except Exception as e:  # noqa: BLE001 — table absente, droit manquant : on continue
            logger.warning("rank_backfill: %s ignorée (%s)", table, e)
            continue
        if n:
            faits[table] = n
    return faits


async def run_rank_backfill_loop(interval: int = _POLL_S) -> None:
    """La boucle, composée au lifespan (`server._bg_loops`).

    Séparée du worker d'extraction et de celui des embeddings, pour la raison qui vaut
    pour les trois : des domaines de panne disjoints. Celle-ci ne dépend d'aucun
    service tiers — elle ne fait que du SQL — donc rien ne doit pouvoir la taire."""
    logger.info("rank_backfill: démarré (poll %ss, tranche %s).", interval, _BATCH)
    while True:
        try:
            faits = await run_in_threadpool(_backfill_round)
            if faits:
                logger.info("rank_backfill: %s",
                            ", ".join(f"{t}+{n}" for t, n in faits.items()))
        except Exception as e:  # noqa: BLE001 — un tour raté ne tue pas la boucle
            logger.warning("rank_backfill: tour en échec : %s", e)
        await asyncio.sleep(interval)
