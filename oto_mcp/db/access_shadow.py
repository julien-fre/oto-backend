"""Le compteur de la double lecture L7 — SQL seul (blueprint ADR 0053, lot L7).

La table `access_shadow_l7` est posée par `db/schema/grants.py` ; ce module en est
l'unique lecteur/écrivain. Il ne porte AUCUNE politique : ce qui est comparé, et
comment une divergence se classe, vit dans `access/chain_shadow.py`. Ici, deux
requêtes.

**Une seule forme d'écriture, et c'est un UPSERT additif** : la ligne du jour est
incrémentée, jamais réécrite. `first_at` et `sample` ne bougent plus après la
première occurrence — le premier écart d'un jour est celui qu'on veut retrouver,
pas le dernier. `last_at` suit, pour qu'une classe éteinte se voie.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ._conn import _connect

logger = logging.getLogger(__name__)


def bump_shadow(connector: str, org_id: Optional[int], classe: str, n: int = 1,
                sample: Optional[dict] = None) -> None:
    """Ajoute `n` occurrences à la classe du jour. Idempotent au sens de l'addition.

    `sample` n'est posé que si la ligne naît : `COALESCE` sur l'existant serait faux
    (une ligne naît avec `'{}'`, qui n'est pas NULL), d'où le test explicite sur
    l'objet vide dans le `DO UPDATE`."""
    if n <= 0:
        return
    payload = json.dumps(sample or {}, ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO access_shadow_l7 (day, connector, org_id, classe, n, sample)
            VALUES (CURRENT_DATE, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (day, connector, org_id, classe) DO UPDATE
               SET n = access_shadow_l7.n + EXCLUDED.n,
                   last_at = NOW(),
                   sample = CASE WHEN access_shadow_l7.sample = '{}'::jsonb
                                 THEN EXCLUDED.sample ELSE access_shadow_l7.sample END
            """,
            (connector, int(org_id or 0), classe, int(n), payload),
        )


def read_shadow(days: int = 7, connector: Optional[str] = None,
                classe: Optional[str] = None) -> list[dict]:
    """Les lignes des `days` derniers jours, les plus récentes d'abord.

    Pas de LIMIT : la population est bornée par (jours × connecteurs servis × orgs
    actives × classes), et une fenêtre d'observation qu'on tronque ne prouve rien.
    L'appelant borne `days`."""
    sql = ["SELECT day, connector, org_id, classe, n, first_at, last_at, sample",
           "  FROM access_shadow_l7",
           " WHERE day > CURRENT_DATE - %s::int"]
    args: list = [int(days)]
    if connector:
        sql.append(" AND connector = %s")
        args.append(connector)
    if classe:
        sql.append(" AND classe = %s")
        args.append(classe)
    sql.append(" ORDER BY day DESC, connector, org_id, classe")
    with _connect() as conn:
        return [dict(r) for r in conn.execute("\n".join(sql), tuple(args)).fetchall()]
