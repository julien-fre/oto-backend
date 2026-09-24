"""La RÉTENTION du journal des révisions de ligne (oto#273, décision du 23/09/2026).

Les révisions de plus de `OTO_JOURNAL_REVISIONS_RETENTION_DAYS` jours (90 par défaut,
`journal_revisions.retention_jours`) sont purgées, SAUF celles de `source = 'import'`
d'une ligne qui existe encore : c'est l'origine de la donnée, que M4 projettera sur la
première révision `import`. Gardée « tant que la ligne existe » : une ligne supprimée
(absente du tableau, ou supprimée depuis cette révision puis recréée sous le même
`row_id` — une révision de suppression plus récente le dit) rend son import à la règle
commune.

Jouée par `oto-mcp maintenance revisions` (timer quotidien, prod seulement : la base est
partagée avec la préprod).

**Par lots bornés, chacun sa transaction, en avançant sur la clé primaire.** Un seul
`DELETE` tiendrait ses verrous de ligne et son WAL le temps de toute la purge, sur une
table que chaque écriture de ligne alimente. Et il n'existe pas d'index sur `at` (la
table n'en porte qu'un, `(ns_id, row_id, rev)`, qui sert la lecture) : filtrer sur `at`
seul parcourrait la table entière à chaque lot, et encore une fois pour constater qu'il
n'y a plus rien à faire. On parcourt donc la table dans l'ordre de `id` — monotone, et
dans le même ordre que `at` à la durée d'une transaction près — par lots de `lot`
révisions, et on s'arrête au premier lot qui ne contient plus AUCUNE révision assez
vieille : la purge ne lit que la zone ancienne, plus un lot.

Les révisions d'import gardées restent dans la zone ancienne et sont relues à chaque
passage : leur nombre est celui des lignes importées encore vivantes, et le parcours
par la clé primaire les traverse sans rien trier.
"""
from __future__ import annotations

from ._conn import _connect
from .journal_revisions import COLONNE_SUPPRESSION, TABLE

LOT = 1000
# Au plus un million de révisions par passage : au-delà, le passage suivant reprend
# (`complet: false`). Une purge qui ne finit pas n'est pas une panne ; une purge sans
# fin dans un timer en serait une.
MAX_LOTS = 1000

# Une révision que la rétention emporte, relue sur `r` (une ligne du journal).
_PURGEABLE = f"""
    r.at < %(borne)s AND (
        r.source IS DISTINCT FROM 'import'
        OR NOT EXISTS (SELECT 1 FROM datastore_rows l
                        WHERE l.ns_id = r.ns_id AND l.row_id = r.row_id)
        OR EXISTS (SELECT 1 FROM {TABLE} s
                    WHERE s.ns_id = r.ns_id AND s.row_id = r.row_id
                      AND s.{COLONNE_SUPPRESSION} AND s.id > r.id))"""

# Un lot : les `lot` révisions qui suivent le curseur, celles du lot qui sont
# purgeables, supprimées. Rend le dernier `id` lu (le curseur suivant, NULL = fin de
# table), combien du lot étaient assez vieilles (0 = on a quitté la zone ancienne) et
# combien sont parties.
_LOT_SQL = f"""
WITH lot AS (
    SELECT id, ns_id, row_id, source, at FROM {TABLE}
     WHERE id > %(curseur)s ORDER BY id LIMIT %(lot)s),
cibles AS (SELECT r.id FROM lot r WHERE {_PURGEABLE}),
parties AS (DELETE FROM {TABLE} d USING cibles c WHERE d.id = c.id RETURNING d.id)
SELECT (SELECT max(id) FROM lot) AS dernier,
       (SELECT count(*) FROM lot WHERE at < %(borne)s) AS anciennes,
       (SELECT count(*) FROM parties) AS purgees"""


def _borne(conn, jours: int):
    # Dans SA transaction : une requête nue ouvrirait la transaction implicite de la
    # connexion du pool, et chaque `conn.transaction()` suivant n'y serait plus qu'un
    # point de sauvegarde — toute la purge dans une seule transaction.
    with conn.transaction():
        return conn.execute("SELECT now() - make_interval(days => %s) AS b",
                            (int(jours),)).fetchone()["b"]


def purger_revisions(jours: int, *, lot: int = LOT, max_lots: int = MAX_LOTS) -> dict:
    """Purge ce que la rétention emporte. Rend `{purgees, lots, complet}` : `complet`
    faux = le plafond de lots est atteint avant la fin de la zone ancienne, le passage
    suivant reprendra."""
    purgees, lots = 0, 0
    with _connect() as conn:
        borne = _borne(conn, jours)
        curseur = 0
        while lots < max_lots:
            with conn.transaction():
                r = conn.execute(_LOT_SQL, {"curseur": curseur, "lot": int(lot),
                                            "borne": borne}).fetchone()
            lots += 1
            purgees += int(r["purgees"])
            if r["dernier"] is None or not r["anciennes"]:
                return {"purgees": purgees, "lots": lots, "complet": True}
            curseur = int(r["dernier"])
    return {"purgees": purgees, "lots": lots, "complet": False}


def compter_purgeables(jours: int) -> int:
    """Ce que `purger_revisions` emporterait — la moitié « à blanc » du travail. Un
    parcours de la table : c'est une lecture à la main, pas un chemin chaud."""
    with _connect() as conn:
        borne = _borne(conn, jours)
        return int(conn.execute(
            f"SELECT count(*) AS n FROM {TABLE} r WHERE {_PURGEABLE}",
            {"borne": borne}).fetchone()["n"])
