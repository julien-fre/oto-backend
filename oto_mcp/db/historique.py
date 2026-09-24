"""La LECTURE du journal des révisions de ligne (oto#273, jalon M3).

Le journal est écrit par un déclencheur (`db/journal_revisions.py`) et estampillé par
le serveur (`db/estampille.py`). Ce module est le seul à le lire. Il sert :

- l'historique d'une ligne, `GET …/rows/{row_id}/history` et `data_row_history`
  (`capabilities/datastore/history.py`) ;
- le parcours d'une ligne, `GET …/rows/{row_id}/activity`, qui y prend les écritures
  avec leurs valeurs (`capabilities/datastore/activity.py`).

Un module à part, et pas une fonction de plus dans `journal_revisions.py` : celui-là est
importé par `_conn.py` (l'interrupteur se lit à l'ouverture du pool), il ne peut donc
pas lui-même ouvrir une connexion.

⚠️ **Ce que le journal ne sait pas, il le dit** (`COUVERTURE`) : il ne couvre que les
écritures faites depuis sa mise en service. Une ligne sans révision n'est pas une ligne
jamais modifiée.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect
from .journal_revisions import TABLE, VARIABLE

# La MISE EN SERVICE du journal sur la base partagée (prod et préprod) : le jour où les
# déclencheurs ont été posés. Commit M1 `4119391a` sur le tronc le 24/09/2026 à 07:42Z,
# prod `v1.341.0` à 08:26Z ; la préprod, qui partage la base, a pu les poser entre les
# deux.
#
# Une constante, et pas `min(at)` de la table : la rétention décidée dans oto#273 (90
# jours) déplacera ce minimum sans que le début de la couverture ait changé. Un chiffre
# lu dans une donnée qu'une purge taille finit par décrire la purge. Le JOUR, pas
# l'heure : c'est la précision que l'on sait garantir.
MISE_EN_SERVICE = "2026-09-24"

# Servie telle quelle, à l'agent comme à l'écran. Sans elle, une ligne sans révision se
# lirait « jamais modifiée ».
COUVERTURE = (
    f"Le journal ne couvre que les écritures faites depuis sa mise en service, le "
    f"{MISE_EN_SERVICE} au matin (UTC) : ce qui a été écrit avant n'y figure pas, et une "
    f"ligne sans révision n'est PAS une ligne jamais modifiée. Il peut avoir des trous : "
    f"coupé par `{VARIABLE}=off`, il n'écrit rien. `rev` 0 est une insertion ; sous un "
    f"même `row_id`, elle revient si la ligne a été supprimée puis recréée. La "
    f"suppression d'une ligne n'est pas une révision.")

_COLONNES = "id, rev, at, acteur, run_id, source, geste_id"


def revisions_de_ligne(ns_id: int, row_id: str, *, champ: Optional[str] = None,
                       avant_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    """Les révisions d'une ligne, la plus récente d'abord (`id` décroissant), au plus
    `limit + 1` : la ligne de trop dit à l'appelant qu'il reste une page.

    L'ordre suit l'`id`, pas la `rev` : une ligne supprimée puis recréée sous le même
    `row_id` repart de `rev` 0, seul l'`id` est monotone. `champ` ne garde que les
    révisions qui touchent cette colonne, et ne rend que sa partie du diff."""
    clauses = ["ns_id = %s", "row_id = %s"]
    params: list = [int(ns_id), str(row_id)]
    diff, params_diff = "diff", []
    if champ is not None:
        clauses.append("diff ? %s")
        params.append(champ)
        diff, params_diff = "jsonb_build_object(%s::text, diff -> %s::text)", [champ, champ]
    if avant_id is not None:
        clauses.append("id < %s")
        params.append(int(avant_id))
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT {_COLONNES}, {diff} AS diff FROM {TABLE} "
            f"WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT %s",
            (*params_diff, *params, int(limit) + 1)).fetchall()]


def bilan_de_ligne(ns_id: int, row_id: str) -> dict:
    """Ce que le journal sait d'une ligne, tous champs confondus : combien de
    révisions, la date de la première, et si une insertion y figure. `rev` 0 n'est
    portée que par une insertion : le déclencheur de révision avance `rev` à toute mise
    à jour de `data`, avant que le journal ne la lise."""
    with _connect() as conn:
        return dict(conn.execute(
            f"SELECT count(*) AS revisions, min(at) AS premiere, "
            f"COALESCE(bool_or(rev = 0), false) AS insertion "
            f"FROM {TABLE} WHERE ns_id = %s AND row_id = %s",
            (int(ns_id), str(row_id))).fetchone())
