"""L'instance de connecteur comme OBJET — SQL seul (blueprint ADR 0053-D9, lot L6).

La table `connector_instances` est posée par `db/schema/connectors.py` ; ce module
est son unique lecteur/écrivain. Il ne porte AUCUNE politique : ce qui est visible,
ce qui résout, ce qui est partagé continue de se décider ailleurs (`access/`,
`grants_chain`, `capabilities/connectors_instances`). Ici, des requêtes.

⚠️ **Trois voisins à ne pas confondre** : `connector_grants` (comptes opérés, #55),
`grants` (le droit d'UTILISER une ressource, chaîne matérialisée 0053) et
`connector_credentials` (le coffre, où vit le secret). `connector_instances` est
l'OBJET qu'on possède, qu'on désigne et qu'on partage ; le credential en est un
attribut, pas l'identité.

**Le lien vers le coffre est un quadruplet, pas un id** — et ce n'est pas un pis
aller : le coffre n'a pas de clé de substitution, sa PK EST
`(entity_type, entity_id, connector, account)`, et l'AAD du chiffrement en dérive
(`credentials_store._aad`). Les quatre colonnes portées ici sont donc le pointeur
lui-même, à l'octet près ; c'est aussi ce qui fait qu'une instance SANS ligne de
coffre reste représentable (instance `http` qui ne fige qu'une `base_url`,
sous-instance qui ne pose qu'une détermination).

⚠️ **Aucune fonction d'ici n'est sur un chemin de résolution.** `walk_cascade` et
`resolve_credential` ne connaissent pas cette table (lot L7). La seule lecture
servie aujourd'hui est la PROJECTION `GET /api/me/connector-instances`, qui n'en
tire qu'un identifiant d'affichage.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from ._conn import _connect

logger = logging.getLogger(__name__)

# Le vocabulaire de propriétaire ACCEPTÉ par la table (CHECK nommé côté DDL).
# `tenant` y est prévu et INERTE — l'entité `tenant` du coffre est le lot L-clés.
OWNER_KINDS = ("platform", "tenant", "org", "group", "member", "user")

# La même liste, en littéral SQL. Construite explicitement et pas par le `repr` du
# tuple : à un seul élément, `repr` rend `('x',)` — une virgule finale que PostgreSQL
# refuse. Le vocabulaire est une CONSTANTE de module, jamais une entrée d'appelant.
_OWNER_KINDS_SQL = "(" + ", ".join(f"'{k}'" for k in OWNER_KINDS) + ")"

_INSTANCE_COLS = ("id, connector, owner_type, owner_id, account, label, config, "
                  "visibility, parent_id, created_at, revoked_at")


def instance_id_for_vault_row(owner_type: str, owner_id: str, connector: str,
                              account: str = "", conn=None) -> Optional[int]:
    """Id de l'instance VIVANTE nommant cette ligne de coffre, ou None.

    `account` suit la convention du coffre : `''` = mono-compte, jamais NULL."""
    sql = ("SELECT id FROM connector_instances WHERE owner_type = %s AND owner_id = %s "
           "AND connector = %s AND account = %s AND revoked_at IS NULL")
    params = (owner_type, str(owner_id), connector, account or "")
    if conn is not None:
        row = conn.execute(sql, params).fetchone()
    else:
        with _connect() as c:
            row = c.execute(sql, params).fetchone()
    return row["id"] if row else None


def instance_ids_for_vault_rows(
        keys: Sequence[tuple[str, str, str, str]]) -> dict[tuple[str, str, str, str], int]:
    """Résolution EN LOT des quadruplets → id, pour une projection qui en tient des
    dizaines (`GET /api/me/connector-instances`).

    **Une seule requête**, jamais une par instance : la projection tourne sur un
    serveur MONO-LOOP, et N lookups par PK y coûtent N allers-retours vers une base
    managée DISTANTE — le mode de panne que `docs/event-loop-perf.md` documente. Les
    couples sont comparés colonne à colonne (`(a,b,c,d) IN ((…),(…))`), servis par
    `idx_connector_instances_vault`."""
    uniques = sorted({(k[0], str(k[1]), k[2], k[3] or "") for k in keys})
    if not uniques:
        return {}
    clause = ", ".join(["(%s, %s, %s, %s)"] * len(uniques))
    params: list = []
    for k in uniques:
        params += list(k)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, owner_type, owner_id, connector, account FROM connector_instances "
            "WHERE revoked_at IS NULL AND (owner_type, owner_id, connector, account) "
            f"IN ({clause})", params).fetchall()
    return {(r["owner_type"], r["owner_id"], r["connector"], r["account"]): r["id"]
            for r in rows}


def instance_by_id(instance_id: int, conn=None) -> Optional[dict]:
    """L'instance et son quadruplet de coffre — la résolution d'un `inst:{id}`.

    Rend AUSSI les archivées (`revoked_at` non nul) : l'appelant tranche. Un binding
    ou un grant peut désigner une instance retirée, et « elle a été retirée » n'est
    pas le même verdict que « elle n'a jamais existé »."""
    sql = f"SELECT {_INSTANCE_COLS} FROM connector_instances WHERE id = %s"
    if conn is not None:
        row = conn.execute(sql, (instance_id,)).fetchone()
    else:
        with _connect() as c:
            row = c.execute(sql, (instance_id,)).fetchone()
    return dict(row) if row else None


def connector_instance_counts(conn=None) -> dict[str, int]:
    """Compte des instances VIVANTES par `owner_type` — le relevé que le boot
    journalise, et la mesure que la revue lit."""
    sql = ("SELECT owner_type, COUNT(*) AS n FROM connector_instances "
           "WHERE revoked_at IS NULL GROUP BY owner_type ORDER BY owner_type")
    if conn is not None:
        rows = conn.execute(sql).fetchall()
    else:
        with _connect() as c:
            rows = c.execute(sql).fetchall()
    return {r["owner_type"]: r["n"] for r in rows}


# ── Le backfill de boot ────────────────────────────────────────────────────────

# Taille de lot du backfill. Le volume réel est de l'ordre de la centaine de lignes
# de coffre ; le lot existe pour que la transaction de schéma d'un boot ne devienne
# jamais une écriture de masse sur une base PARTAGÉE prod/preprod, quel que soit ce
# que l'adoption y aura mis d'ici là.
BACKFILL_BATCH = 500

# Additive et idempotente à deux crans, comme `db.grants.edge_exists` au lot L5 :
# · `NOT EXISTS` **sans filtre sur `revoked_at`** — rejouer ne duplique pas, et
#   surtout ne RESSUSCITE pas une instance retirée à la main entre deux boots ;
# · `ON CONFLICT DO NOTHING` — la ceinture mécanique, côté base, si deux migrateurs
#   passaient malgré l'advisory lock d'`init_db`.
# `entity_type` est filtré sur le vocabulaire ACCEPTÉ : une valeur inconnue au coffre
# ferait échouer le CHECK, donc la transaction de schéma, donc le boot — sur une base
# partagée avec la production. Ce qui sort du vocabulaire est compté et journalisé,
# jamais inventé (même règle qu'au lot L5 pour les scopes hors `user:`/`org:`).
_BACKFILL_SQL = f"""
INSERT INTO connector_instances (connector, owner_type, owner_id, account, created_at)
SELECT c.connector, c.entity_type, c.entity_id, c.account, c.set_at
  FROM connector_credentials c
 WHERE c.entity_type IN {_OWNER_KINDS_SQL}
   AND NOT EXISTS (
       SELECT 1 FROM connector_instances i
        WHERE i.owner_type = c.entity_type
          AND i.owner_id   = c.entity_id
          AND i.connector  = c.connector
          AND i.account    = c.account)
 ORDER BY c.entity_type, c.entity_id, c.connector, c.account
 LIMIT %s
ON CONFLICT DO NOTHING
"""


def name_vault_rows_as_instances(conn, batch: int = BACKFILL_BATCH) -> int:
    """Une instance par ligne de coffre existante — le backfill de boot du lot L6.

    **Ce que le backfill NOMME, et ce qu'il ne touche pas.** Il pose l'identité
    (`connector`, le propriétaire, le compte) et la date de naissance de la ligne du
    coffre. Il laisse VIDES `label` et `config` : le nom affiché reste dérivé de
    `meta.label` et la config publique vit dans `meta` — les recopier ici ferait un
    second domicile pour une donnée que rien ne lit encore. Aucune ligne du coffre
    n'est lue en écriture, aucun secret n'est déchiffré, l'AAD ne bouge pas.

    Tourne à CHAQUE boot (pas de marqueur « déjà fait ») et c'est délibéré : une clé
    posée entre deux boots est nommée au suivant, sans qu'aucun chemin d'écriture du
    coffre n'ait à connaître cette table. Rend le nombre d'instances créées."""
    total = 0
    while True:
        cur = conn.execute(_BACKFILL_SQL, (batch,))
        posees = cur.rowcount or 0
        total += posees
        if posees < batch:
            break
    hors = conn.execute(
        "SELECT entity_type, COUNT(*) AS n FROM connector_credentials "
        f"WHERE entity_type NOT IN {_OWNER_KINDS_SQL} GROUP BY entity_type").fetchall()
    for r in hors:
        logger.warning(
            "L6 instances: %d ligne(s) de coffre en entity_type=%r NON nommées "
            "(hors du vocabulaire de propriétaire %s) — les inventer serait décider "
            "à la place du modèle", r["n"], r["entity_type"], list(OWNER_KINDS))
    return total
