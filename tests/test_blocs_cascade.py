"""Le corps d'un nœud part AVEC lui — et c'est la base qui le garantit (#800).

Jusqu'au 2026-09-01, `blocks.node_id` était un BIGINT nu. L'intégrité reposait donc
sur la discipline de chaque appelant, et elle a manqué **deux fois** :

1. la purge des conversions retirait le nœud sans son corps — origine des 1 939 blocs
   orphelins mesurés en production ;
2. `db/guides.py::delete_guide_db` refait le même geste, sur du contenu **natif**
   cette fois (une couche de contexte, seule écriture directe qui reste dans `nodes`),
   et le faisait encore le jour où cette issue a été écrite.

Un bloc orphelin est **invisible** : toute lecture de `blocks` part de `node_id`
(`db/node_view.blocks_of`, `db/blocks.write_node_blocks`). Rien ne le rend, rien ne le
signale ; il ne se voit qu'en comptant ce qui ne se rattache à rien.

Ce fichier éprouve les quatre choses qu'aucune ne se déduit du code :

- la contrainte existe sur une base **VIERGE** (elle naît du `CREATE TABLE`) ;
- elle existe aussi sur une base qui **EXISTE DÉJÀ** (elle naît de `_init.py`) —
  retirer l'une des deux ne rougirait nulle part ailleurs, et prod et install fraîche
  divergeraient en silence, cf. `test_runner_fleets_boot_base_existante.py` ;
- **elle MORD** : le geste qui fuyait ne fuit plus, et un bloc ne peut plus naître
  orphelin ;
- **elle se pose même quand la base porte déjà des orphelins**, et la cascade joue
  quand même. C'est la propriété qui rend le déploiement sûr sur une base partagée
  prod/preprod : une contrainte VALIDE d'emblée refuserait de se poser sur un tel
  état, donc ferait échouer le boot. `NOT VALID` ne saute QUE la vérification des
  lignes déjà là — les triggers référentiels, eux, sont créés dans tous les cas.

Les points ② et ③ de l'issue portent sur le retrait du résidu : ils sont éprouvés là où
ce geste vit, dans `tests/test_residu_projete.py`.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest


@pytest.fixture(scope="module")
def base(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le vrai pool.

    Base à part et non le conteneur partagé : un boot complet y laisse des dizaines
    de tables et leurs FK, et rougirait des tests voisins qui recréent des tables
    autonomes (recette du dépôt, cf. `test_boot_order_replay`).
    """
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_blocs_fk_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        # Relevé sur une base VIERGE, AVANT toute manipulation : c'est la moitié qui
        # ne se voit qu'ici. La contrainte y naît du `CREATE TABLE` ; la retirer de
        # là ne rougirait pas sur une base qu'on ramène en arrière.
        yield _contrainte()
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def _sql(sql: str, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()] if cur.description else []


def _contrainte() -> dict | None:
    """La ligne de `pg_constraint`, ou None. `convalidated` dit si le stock EXISTANT
    a été vérifié — la cascade, elle, joue dans les deux cas."""
    rows = _sql("SELECT conname, convalidated, pg_get_constraintdef(oid) AS definition "
                "FROM pg_constraint WHERE conrelid = 'blocks'::regclass "
                "AND conname = 'blocks_node_fk'")
    return rows[0] if rows else None


def _pose_noeud(titre: str, *, marque: str | None = None) -> int:
    props = {"title": titre, "body_md": "corps de " + titre}
    if marque is not None:
        props["legacy"] = marque
        props["legacy_id"] = "1"
    return _sql("INSERT INTO nodes (public_id, kind, owner_type, owner_id, props) "
                "VALUES (%s, 'page', 'user', 'u1', %s::jsonb) RETURNING id",
                ("pid_" + uuid.uuid4().hex[:10], json.dumps(props)))[0]["id"]


def _pose_bloc(node_id: int) -> str:
    pid = "blk_" + uuid.uuid4().hex[:12]
    _sql("INSERT INTO blocks (public_id, node_id, position, type, props) "
         "VALUES (%s, %s, 0, 'text', %s::jsonb)",
         (pid, node_id, json.dumps({"md": "un corps"})))
    return pid


def _blocs_de(node_id: int) -> int:
    return _sql("SELECT count(*) AS n FROM blocks WHERE node_id = %s",
                (node_id,))[0]["n"]


def _remonte_le_temps() -> None:
    """Ramène la base à l'état d'AVANT ce lot : la contrainte n'existe pas.

    `blocks` existe déjà, donc son `CREATE TABLE` sera sauté au rejeu — c'est le
    chemin de naissance « base existante », le seul que la production emprunte.
    """
    _sql("ALTER TABLE blocks DROP CONSTRAINT IF EXISTS blocks_node_fk")


def _rejoue_le_boot() -> None:
    from oto_mcp.db import init_db
    init_db()


# --- la contrainte, ses deux naissances --------------------------------------

def test_la_contrainte_existe_sur_une_base_VIERGE(base):
    """Sur une base neuve, `blocks` naît du `CREATE TABLE` : l'`ALTER` de `_init.py`
    n'a alors AUCUNE occasion de poser quoi que ce soit. La contrainte doit donc être
    inline dans le DDL — et ça ne se voit que relevé avant tout retour en arrière."""
    assert base is not None, (
        "aucune `blocks_node_fk` sur une base VIERGE : la contrainte inline du "
        "CREATE TABLE manque")
    assert "ON DELETE CASCADE" in base["definition"], base["definition"]
    assert base["convalidated"], (
        "sur une base vierge il n'y a rien à valider : elle doit naître VALIDE")


def test_la_contrainte_renait_au_boot_dune_base_EXISTANTE(base):
    """L'autre moitié. Sans elle, une install fraîche aurait la contrainte et la
    production un BIGINT nu : les deux bases « marchent », seule l'une emporte le
    corps. La divergence serait permanente et silencieuse."""
    _remonte_le_temps()
    assert _contrainte() is None, "le point de départ de l'épreuve est faux"
    _rejoue_le_boot()
    pose = _contrainte()
    assert pose is not None, (
        "la contrainte ne voyage pas avec `_init.py` : une base qui existe déjà — "
        "c'est-à-dire la production — ne l'aura jamais")
    assert "ON DELETE CASCADE" in pose["definition"], pose["definition"]


def test_un_bloc_ne_peut_plus_naitre_orphelin(base):
    """La présence dans `pg_constraint` ne prouve pas qu'elle refuse quoi que ce soit."""
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _sql("INSERT INTO blocks (public_id, node_id, position, type, props) "
             "VALUES (%s, 999999999, 0, 'text', '{}'::jsonb)",
             ("blk_" + uuid.uuid4().hex[:12],))


# --- le geste qui fuyait ------------------------------------------------------

def test_supprimer_une_couche_de_contexte_emporte_son_corps(base):
    """Le défaut ① de #800, sur son chemin réel.

    `delete_guide_db` supprime un nœud NATIF — une couche de contexte — par son
    identifiant public dérivé, et ne touchait pas à ses blocs. Le timer
    `maintenance blocks` leur en donne (le nœud porte un `body_md`), donc chaque
    suppression laissait un corps derrière elle. Sur du contenu dont rien n'est copie.
    """
    from oto_mcp.db import blocks as db_blocks
    from oto_mcp.db import guides as db_guides

    db_guides.seed_guide_db("user", "u-fuite", "une-couche", "# Titre\n\nDu corps.\n")
    noeud = _sql("SELECT id FROM nodes WHERE props->>'slug' = 'une-couche'")[0]["id"]
    db_blocks.backfill_node_blocks()
    assert _blocs_de(noeud) > 0, "le point de départ est faux : aucun bloc à emporter"

    assert db_guides.delete_guide_db("user", "u-fuite", "une-couche") is True
    assert _blocs_de(noeud) == 0, (
        "le nœud est parti, son corps est resté : un orphelin que plus rien ne rend "
        "— toute lecture de `blocks` part de `node_id`")


def test_la_cascade_joue_meme_quand_la_contrainte_nest_pas_VALIDÉE(base):
    """La propriété qui rend le déploiement sûr sur une base PARTAGÉE prod/preprod.

    Si la base porte déjà des orphelins — l'état exact de la production entre le 11/08
    et le retrait du résidu —, une contrainte valide d'emblée refuserait de se poser et
    ferait ÉCHOUER le boot. `_init.py` la pose donc `NOT VALID`. Ce que ce test
    affirme, et qui ne se déduit d'aucune ligne de notre code : **`NOT VALID` ne
    désarme pas la cascade**. PostgreSQL crée les triggers référentiels dans tous les
    cas ; `NOT VALID` ne saute que le parcours des lignes déjà là.
    """
    _remonte_le_temps()
    fantome = _pose_noeud("noeud voué à disparaître")
    _pose_bloc(fantome)
    _sql("DELETE FROM nodes WHERE id = %s", (fantome,))
    assert _blocs_de(fantome) == 1, "le point de départ est faux : pas d'orphelin"

    _rejoue_le_boot()

    pose = _contrainte()
    assert pose is not None, "la contrainte a refusé de se poser sur un état imparfait"
    assert not pose["convalidated"], (
        "elle s'annonce validée alors qu'un orphelin traîne : la validation aurait dû "
        "échouer, donc elle n'a pas eu lieu — ou le compte d'orphelins est faux")
    assert _blocs_de(fantome) == 1, "la pose a détruit des lignes ; elle doit être ADDITIVE"

    vivant = _pose_noeud("noeud vivant")
    _pose_bloc(vivant)
    _sql("DELETE FROM nodes WHERE id = %s", (vivant,))
    assert _blocs_de(vivant) == 0, (
        "la cascade ne joue pas tant que la contrainte n'est pas validée — alors la "
        "fuite reste ouverte sur toute base qui porte un orphelin")

    _sql("DELETE FROM blocks WHERE node_id = %s", (fantome,))
    _rejoue_le_boot()
    assert _contrainte()["convalidated"], (
        "le stock d'orphelins est vide : le boot suivant doit VALIDER la contrainte")
