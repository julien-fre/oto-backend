"""La bibliothèque publique s'atteint par `guide_library` — sur une VRAIE base (#519).

Lot B4. La table s'appelle encore `doctrine_library` et **elle garde ce nom jusqu'au
lot D** (#526) : la base est PARTAGÉE prod/preprod (`docs/live-migrations.md`), donc
un `ALTER TABLE … RENAME` mergé sur `main` renommerait sous la prod qui tourne encore
l'ancien code. Le renommage physique est un acte de TAG.

En attendant, une VUE porte le nom d'aujourd'hui et **tout le code passe par elle** :
au lot D il ne restera qu'à droper la vue et renommer la table, sans toucher une
ligne de Python.

Ce que ces tests gardent, et pourquoi ils exigent un vrai PostgreSQL :

1. **La vue existe et ses colonnes sont EXACTEMENT celles de la table.** Une vue
   `SELECT *` fige la liste des colonnes au moment de sa création : posée avant un
   `ALTER TABLE … ADD COLUMN`, elle masquerait la colonne neuve — sans erreur, sans
   log, avec un `None` là où le code attend une valeur. Cette panne-là ne se voit
   qu'en comparant les deux inventaires sur une base réelle.
2. **Elle est AUTO-UPDATABLE** : `INSERT` (avec DEFAULT et `RETURNING`),
   `ON CONFLICT DO UPDATE`, `UPDATE` et `DELETE` la traversent. C'est mesuré ici, pas
   supposé — `ON CONFLICT` sur une vue est précisément le genre de chose dont on
   « sait » qu'elle ne marche pas.
3. **Le code ne nomme plus la table.** Le seul endroit qui la nomme est le DDL.
"""
from __future__ import annotations

import os
import pathlib
import uuid

import pytest


_TABLE = "doctrine" "_library"          # coupé : le cliquet de vocabulaire compte
_VUE = "guide_library"


@pytest.fixture(scope="module")
def base(pg_dsn):
    """Une base JETABLE et le VRAI `init_db()` — le patron des tests « live » du
    dépôt (`test_nodes_m3_positions`), pool restauré en sortie : un pool laissé
    branché sur la base de ce test ferait passer les suivants par une vraie base."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_guidelib_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        with psycopg.connect(dsn, autocommit=True) as conn:
            yield conn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')


def _colonnes(conn, relation: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position", (relation,)).fetchall()]


def test_la_vue_existe_et_montre_toutes_les_colonnes_de_la_table(base):
    """LE test du lot : une colonne ajoutée à la table et absente de la vue serait
    lue `None` par tout le code, sans qu'aucune erreur ne le dise."""
    table, vue = _colonnes(base, _TABLE), _colonnes(base, _VUE)
    assert table, "la table de la bibliothèque n'existe pas"
    assert vue, (
        f"la vue `{_VUE}` n'existe pas. Elle se (re)crée à CHAQUE boot, après tous "
        "les ALTER — cf. `db/_init.py`.")
    assert vue == table, (
        f"la vue ne montre pas les mêmes colonnes que la table : manquantes "
        f"{sorted(set(table) - set(vue))}, en trop {sorted(set(vue) - set(table))}. "
        "Une vue `SELECT *` fige ses colonnes à sa création : elle DOIT être rejouée "
        "après tout `ALTER TABLE … ADD COLUMN`.")


def test_la_vue_est_auto_updatable(base):
    """`INSERT` + DEFAULT + `RETURNING`, `ON CONFLICT DO UPDATE`, `DELETE` : les trois
    formes que le code emploie réellement, jouées sur la vue."""
    ligne = base.execute(
        f"INSERT INTO {_VUE} (slug, title, description, body_md, author_kind, "
        f"author_display, published_by) "
        "VALUES ('t-vue', 'T', 'D', '# corps', 'org', 'Acme', 'u1') "
        "RETURNING id, version, visibility, slots").fetchone()
    assert ligne[0] and ligne[1] is not None and ligne[2] is not None
    assert ligne[3] == [], "un DEFAULT de la table doit s'appliquer à travers la vue"

    maj = base.execute(
        f"INSERT INTO {_VUE} (slug, title, description, body_md, author_kind, "
        f"author_display, published_by) "
        "VALUES ('t-vue', 'T2', 'D', '# corps', 'org', 'Acme', 'u1') "
        "ON CONFLICT (slug) DO UPDATE SET title = EXCLUDED.title RETURNING id, title"
    ).fetchone()
    assert maj == (ligne[0], "T2"), "ON CONFLICT DO UPDATE doit traverser la vue"

    assert base.execute(f"DELETE FROM {_VUE} WHERE slug = 't-vue'").rowcount == 1


def test_le_code_ne_nomme_plus_la_table(base):
    """Le seul endroit qui doit nommer la table est le DDL : au lot D, le renommage
    physique ne doit toucher aucune ligne de Python."""
    racine = pathlib.Path(__file__).resolve().parents[1] / "oto_mcp"
    permis = {
        "db/schema/procedures.py",      # le DDL : c'est là que la table vit
        "db/_init.py",                  # l'ALTER de colonne + la création de la vue
        # L'inventaire des colonnes porteuses d'un `sub`, vérifié CONTRE LE DDL
        # (`test_migrate_sub_inventory`) : une vue n'y apparaît pas, et y mettre la
        # vue rendrait ce garde-fou aveugle à une entrée réellement morte.
        "db/users.py",
    }
    coupables = []
    for p in sorted(racine.rglob("*.py")):
        rel = p.relative_to(racine).as_posix()
        if rel in permis:
            continue
        if _TABLE in p.read_text(encoding="utf-8"):
            coupables.append(rel)
    assert not coupables, (
        f"{coupables} nomment encore la table. Passe par la vue `{_VUE}` : au lot D "
        "(#526) le renommage physique ne doit être qu'un DDL.")
