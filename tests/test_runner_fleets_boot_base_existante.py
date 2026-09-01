"""Le boot de la flotte contre une base qui EXISTE DÉJÀ — le cas que rien ne jouait.

`test_boot_order_replay` fait `CREATE DATABASE` + `init_db()` : il boote une base
**vierge**, où le `CREATE TABLE` pose les colonnes inline. La panne du 20/07 ne se
produit que sur une base construite AVANT : le `CREATE TABLE IF NOT EXISTS` est
alors **sauté**, une colonne née d'un `ALTER` n'arrive donc jamais par ce chemin,
et tout index posé sur elle **dans le DDL** meurt au démarrage.

⚠️ **C'est la forme d'angle mort la plus coûteuse : le cliquet écrit exactement
pour ce piège ne joue pas le cas qui casse.** Ce lot l'a payé — sept fichiers de
garde au vert pendant que le boot réel mourait sur `column "fleet_id" does not
exist`, reproduit sur un vrai PostgreSQL.

Ce fichier joue la base « d'avant » sans dépendre de git : on boote, on RETIRE la
colonne et son index, puis on rejoue le boot. Une base sans `fleet_id` dont la
table `runner_jobs` existe déjà, c'est précisément l'état de la production le jour
où ce lot arrive.

Trois choses vérifiées, et la troisième est celle qu'on a failli manquer :
1. le boot **passe** sur cette base ;
2. la colonne et son index **reviennent** — par l'`ALTER`, pas par le `CREATE TABLE` ;
3. la **clé étrangère voyage avec l'`ALTER`**. Sans elle, une base fraîche aurait la
   contrainte et la production un entier nu — une divergence permanente que rien
   ne rattraperait, et qu'aucun test de base vierge ne peut voir.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def base_dun_passage_anterieur(pg_dsn):
    """Une base JETABLE bootée, puis RAMENÉE à l'état d'avant ce lot.

    Base à part et non le conteneur partagé : un boot complet y laisserait des
    dizaines de tables et leurs FK, et rougirait des tests voisins qui recréent des
    tables autonomes. Même recette que `test_boot_order_replay`.
    """
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_fleet_boot_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        with dbconn._connect() as c:
            # ── on remonte le temps : l'état d'une base d'AVANT ce lot ──────────
            # `runner_jobs` existe (donc son CREATE TABLE sera sauté au rejeu),
            # mais elle ne connaît pas encore la flotte.
            c.execute("DROP INDEX IF EXISTS idx_runner_jobs_fleet")
            c.execute("ALTER TABLE runner_jobs DROP COLUMN IF EXISTS fleet_id")
            c.commit()
            yield c
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


def _colonnes(conn, table: str) -> set[str]:
    return {r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,)).fetchall()}


def test_la_base_de_depart_est_bien_celle_davant(base_dun_passage_anterieur):
    """L'épreuve ne vaut que si son point de départ est le bon : `runner_jobs`
    présente, `fleet_id` absente. Un test qui part d'une base déjà à jour passerait
    au vert sans rien prouver — c'est exactement le défaut qu'on corrige ici."""
    c = base_dun_passage_anterieur
    cols = _colonnes(c, "runner_jobs")
    assert cols, "runner_jobs doit exister — sinon le CREATE TABLE la reposerait"
    assert "fleet_id" not in cols


def test_le_boot_passe_et_la_colonne_revient_par_l_alter(base_dun_passage_anterieur):
    """Le geste qui mourait. Un index posé dans le DDL sur `fleet_id` fait échouer
    `init_db()` ici avec `UndefinedColumn` — vérifié en réintroduisant la faute."""
    from oto_mcp.db import init_db
    init_db()
    c = base_dun_passage_anterieur
    assert "fleet_id" in _colonnes(c, "runner_jobs")
    index = {r["indexname"] for r in c.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'runner_jobs'").fetchall()}
    assert "idx_runner_jobs_fleet" in index, (
        "l'index doit suivre l'ALTER dans `_init`, pas vivre dans le DDL")


def test_la_cle_etrangere_voyage_avec_l_alter(base_dun_passage_anterieur):
    """Sans elle, une base fraîche aurait la contrainte et la production un BIGINT
    nu. La divergence serait PERMANENTE et invisible : les deux bases « marchent »,
    et seule l'une des deux refuse un `fleet_id` qui ne désigne rien."""
    from oto_mcp.db import init_db
    init_db()
    c = base_dun_passage_anterieur
    fks = {r["conname"] for r in c.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'runner_jobs'::regclass AND contype = 'f'").fetchall()}
    assert any("fleet" in f for f in fks), (
        f"aucune FK vers runner_fleets sur runner_jobs — trouvées : {sorted(fks)}")
    # et la contrainte MORD : un job qui désigne une flotte inexistante est refusé
    with pytest.raises(Exception):
        c.execute("INSERT INTO runner_jobs (org_id, kind, fleet_id) "
                  "VALUES (1, 'start', 999999999)")
    c.rollback()
