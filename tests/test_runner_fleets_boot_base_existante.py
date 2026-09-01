"""Ce que le cliquet GÉNÉRIQUE du boot ne voit pas — les CONTRAINTES de la flotte.

`test_boot_order_replay` joue désormais le cas « base qui existe déjà » (#785,
01/09/2026) et il le fait mieux que ce fichier ne le faisait : il DÉRIVE du SQL
exécuté la liste des colonnes posées par un `ALTER`, puis les retire une à une.
`runner_jobs.fleet_id` y est donc couverte sans être citée, et le boot lui-même
n'a plus besoin d'être vérifié ici.

⚠️ **Ce qui reste, et que ce cliquet ne peut pas voir : il compare une empreinte à
elle-même entre deux rejeux, sans affirmer aucune contrainte.** Une FK absente des
DEUX côtés reste donc invisible — l'empreinte est identique, et seul le hash gelé
bouge, celui qu'on met à jour sans y penser.

Or cette FK a **deux chemins de naissance**, et il faut les deux :

```
base VIERGE      la colonne naît du CREATE TABLE, donc l'ALTER … IF NOT EXISTS
                 ne s'applique pas et n'a AUCUNE occasion de poser sa contrainte
                 ⟹ la FK doit être INLINE dans le CREATE TABLE
base EXISTANTE   le CREATE TABLE est sauté ⟹ la FK doit voyager AVEC l'ALTER
```

**Retirer l'une des deux ne rougit nulle part ailleurs**, et la divergence serait
permanente et silencieuse : les deux bases « marchent », seule l'une refuse un
`fleet_id` qui ne désigne rien. C'est le miroir exact du défaut qui a motivé #781.

Trois assertions, donc — la contrainte des deux côtés, et **qu'elle MORD** : sa
présence dans `pg_constraint` ne prouve pas qu'elle refuse quoi que ce soit.
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
            # ⚠️ L'état d'une base VIERGE se relève AVANT de remonter le temps, et
            # c'est la moitié qui manquait : sur une base vierge, `fleet_id` naît du
            # CREATE TABLE, donc l'`ALTER … ADD COLUMN IF NOT EXISTS` ne fait RIEN et
            # n'a aucune occasion de poser sa FK. Retirer la FK inline du CREATE
            # TABLE ne rougirait donc nulle part si on ne regardait que la base
            # ramenée — le miroir exact du défaut d'origine.
            vierge = {r["conname"] for r in c.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'runner_jobs'::regclass AND contype = 'f'"
            ).fetchall()}
            # ── on remonte le temps : l'état d'une base d'AVANT ce lot ──────────
            # `runner_jobs` existe (donc son CREATE TABLE sera sauté au rejeu),
            # mais elle ne connaît pas encore la flotte.
            c.execute("DROP INDEX IF EXISTS idx_runner_jobs_fleet")
            c.execute("ALTER TABLE runner_jobs DROP COLUMN IF EXISTS fleet_id")
            c.commit()
            yield c, vierge
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
    c, _ = base_dun_passage_anterieur
    cols = _colonnes(c, "runner_jobs")
    assert cols, "runner_jobs doit exister — sinon le CREATE TABLE la reposerait"
    assert "fleet_id" not in cols


def test_l_index_suit_l_alter_et_non_le_ddl(base_dun_passage_anterieur):
    """Le boot lui-même est gardé par le cliquet générique ; ce qui reste ici, c'est
    la PLACE de l'index. Posé dans le DDL, il s'exécuterait avant que la colonne
    existe sur une base déjà construite — `UndefinedColumn`, vérifié en
    réintroduisant la faute."""
    from oto_mcp.db import init_db
    init_db()
    c, _ = base_dun_passage_anterieur
    assert "fleet_id" in _colonnes(c, "runner_jobs")
    index = {r["indexname"] for r in c.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'runner_jobs'").fetchall()}
    assert "idx_runner_jobs_fleet" in index, (
        "l'index doit suivre l'ALTER dans `_init`, pas vivre dans le DDL")


def test_la_cle_etrangere_existe_aussi_sur_une_base_VIERGE(base_dun_passage_anterieur):
    """L'autre moitié, et elle ne se voit que relevée avant le retour en arrière.

    Sur une base vierge la colonne naît du `CREATE TABLE` : l'`ALTER … IF NOT
    EXISTS` ne s'applique pas, donc il ne pose pas sa FK. La contrainte doit donc
    être portée AUX DEUX endroits — inline pour la base neuve, par l'`ALTER` pour
    celle qui existe — et retirer l'une des deux ne rougit qu'ici."""
    _, vierge = base_dun_passage_anterieur
    assert any("fleet" in f for f in vierge), (
        "aucune FK vers runner_fleets sur une base VIERGE — la contrainte inline du "
        f"CREATE TABLE manque. Trouvées : {sorted(vierge)}")


def test_la_cle_etrangere_voyage_avec_l_alter(base_dun_passage_anterieur):
    """Sans elle, une base fraîche aurait la contrainte et la production un BIGINT
    nu. La divergence serait PERMANENTE et invisible : les deux bases « marchent »,
    et seule l'une des deux refuse un `fleet_id` qui ne désigne rien."""
    from oto_mcp.db import init_db
    init_db()
    c, _ = base_dun_passage_anterieur
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
