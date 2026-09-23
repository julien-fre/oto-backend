"""Sur une base À JOUR, le démarrage ne prend AUCUN verrou fort (#1015 étendu, 23/09/2026).

Incident : deux déploiements préprod (`08bd5dee`, `cb77c82e`) morts au boot en
`LockNotAvailable` sur `ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS request_id` —
une colonne posée depuis des semaines. `IF NOT EXISTS` évite l'ERREUR, pas le VERROU :
l'ordre demandait quand même son `AccessExclusiveLock`, et une lecture longue (un
`psql` d'enquête) sur `tool_calls` suffisait à faire échouer la bascule. Relevé sur un
rejeu avant correctif : 40 tables en `AccessExclusiveLock`, 48 en `ShareLock`.

Deux preuves, contre un PostgreSQL réel, qui visent l'AXE et non une liste d'ordres :

1. **Aucun verrou fort n'est tenu par la transaction du boot** rejouée sur une base à
   jour. On lit `pg_locks` pour notre propre backend avant l'annulation : un verrou
   pris reste tenu jusqu'à la fin de la transaction, donc rien ne peut échapper à ce
   relevé — ni un ordre écrit à la main, ni un f-string dans une boucle, ni une aide
   `_migrate_*`, ni un module qui génère son DDL. Un ordre ajouté demain sans garde
   rougit ici, avec son texte.
2. **L'incident, rejoué** : une autre connexion tient une LECTURE sur toutes les
   tables (`ACCESS SHARE`, ce que prend un `SELECT`) ; le boot, avec un `lock_timeout`
   court, doit passer. Avant le correctif, il mourait sur le premier `ALTER`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_boot_order_replay import base_bootee  # noqa: E402,F401

# Les modes qui bloquent le trafic applicatif : `ShareLock` (celui de `CREATE INDEX`)
# bloque déjà les écritures ; au-dessus, tout bloque davantage.
_FORTS = ("ShareLock", "ShareRowExclusiveLock", "ExclusiveLock", "AccessExclusiveLock")
_VERROUS_TENUS = """
    SELECT l.mode, l.relation::regclass::text AS rel
      FROM pg_locks l
     WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation' AND l.mode = ANY(%s)
"""


class _Attribution:
    """Connexion qui relève, après chaque ordre, les verrous forts NOUVEAUX qu'il a
    pris — pour qu'un rouge nomme l'ordre fautif, pas seulement la table."""

    def __init__(self, conn):
        self._conn = conn
        self._vus: set = set()
        self.fautifs: list[tuple[str, list]] = []

    def execute(self, query, params=None, **kw):
        cur = (self._conn.execute(query, **kw) if params is None
               else self._conn.execute(query, params, **kw))
        texte = query if isinstance(query, str) else str(query)
        if not re.match(r"\s*SELECT\b", texte, re.I):
            tenus = {(r["mode"], r["rel"])
                     for r in self._conn.execute(_VERROUS_TENUS, (list(_FORTS),)).fetchall()}
            if tenus - self._vus:
                self.fautifs.append((" ".join(texte.split())[:200], sorted(tenus - self._vus)))
            self._vus |= tenus
        return cur

    def __getattr__(self, nom):
        return getattr(self._conn, nom)


def test_rejeu_sur_base_a_jour_ne_prend_aucun_verrou_fort(base_bootee):
    from oto_mcp.db._init import apply_boot_schema

    espion = _Attribution(base_bootee)
    with base_bootee.transaction(force_rollback=True):
        apply_boot_schema(espion)
        tenus = base_bootee.execute(_VERROUS_TENUS, (list(_FORTS),)).fetchall()

    assert not tenus, (
        f"{len(tenus)} verrou(s) fort(s) pris par un boot sur une base DÉJÀ à jour — "
        "chacun bloque le trafic applicatif de sa table pendant la bascule, et une "
        "simple lecture longue suffit à faire échouer le démarrage (préprod du "
        "23/09/2026). Un ordre sans rien à faire ne doit pas partir : faites-le "
        "reconnaître par `oto_mcp/db/_ddl_garde.py`, ou conditionnez-le comme "
        "`_poser_domaine`/`_reposer_index`.\n" + "\n".join(
            f"  {verrous} ← {ordre}" for ordre, verrous in espion.fautifs))


def test_le_boot_passe_quand_une_lecture_longue_tient_toutes_les_tables(
        base_bootee, pg_dsn, monkeypatch):
    """L'incident du 23/09, sur toutes les tables à la fois : une lecture tenue ailleurs
    ne doit plus suffire à faire échouer le démarrage."""
    import psycopg

    from oto_mcp.db._init import apply_boot_schema

    monkeypatch.setenv("OTO_MCP_INIT_DB_LOCK_TIMEOUT_MS", "300")
    lecteur = psycopg.connect(pg_dsn.rsplit("/", 1)[0] + "/" + base_bootee.info.dbname)
    try:
        with lecteur.transaction():
            tables = [r[0] for r in lecteur.execute(
                "SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname = 'public'")]
            assert "tool_calls" in tables, "précondition : la base est bootée"
            lecteur.execute(f"LOCK TABLE {', '.join(tables)} IN ACCESS SHARE MODE")
            with base_bootee.transaction(force_rollback=True):
                apply_boot_schema(base_bootee)
    finally:
        lecteur.close()


# ── Le verdict, forme par forme (sans base : le découpage) ─────────────────────

def test_le_decoupage_respecte_chaines_dollars_et_commentaires():
    from oto_mcp.db._ddl_garde import decouper_sql

    sql = """
    CREATE TABLE IF NOT EXISTS a (x TEXT DEFAULT 'un;deux', -- note ; ici
                                  y TEXT);
    /* bloc ; /* imbriqué ; */ toujours */
    DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;
    CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $corps$ SELECT 1; $corps$;
    SELECT E'échappé \\' ; encore';
    ;
    """
    ordres = decouper_sql(sql)
    assert len(ordres) == 4, ordres
    assert ordres[0].startswith("CREATE TABLE") and "'un;deux'" in ordres[0]
    assert ordres[1].startswith("/* bloc") and ordres[1].endswith("END $$")
    assert ordres[2].endswith("$corps$")
    assert ordres[3].startswith("SELECT E'")


def test_le_schema_assemble_se_decoupe_sans_rien_perdre():
    """Le `_SCHEMA` servi, découpé : le texte recollé est l'original, aux
    séparateurs près — aucun morceau perdu, aucun ordre coupé en deux."""
    from oto_mcp.db._ddl_garde import decouper_sql
    from oto_mcp.db._schema import _SCHEMA

    ordres = decouper_sql(_SCHEMA)
    assert len(ordres) > 100
    sans_blanc = lambda t: re.sub(r"[\s;]", "", t)  # noqa: E731
    assert sans_blanc("".join(ordres)) == sans_blanc(_SCHEMA)


@pytest.mark.parametrize("ordre,a_faire", [
    ("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS request_id TEXT", False),
    ("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS colonne_neuve_xyz TEXT", True),
    ("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS request_id TEXT, "
     "ADD COLUMN IF NOT EXISTS colonne_neuve_xyz TEXT", True),
    ("ALTER TABLE tool_calls DROP COLUMN IF EXISTS colonne_absente_xyz", False),
    ("ALTER TABLE tool_calls DROP COLUMN IF EXISTS request_id", True),
    ("CREATE INDEX IF NOT EXISTS idx_runner_jobs_fleet ON runner_jobs(fleet_id)", False),
    ("CREATE UNIQUE INDEX IF NOT EXISTS idx_neuf_xyz ON tool_calls(id)", True),
    ("CREATE TABLE IF NOT EXISTS tool_calls (id BIGINT)", False),
    ("CREATE TABLE IF NOT EXISTS table_neuve_xyz (id BIGINT)", True),
    ("DROP TABLE IF EXISTS table_absente_xyz", False),
    ("DROP INDEX IF EXISTS idx_absent_xyz", False),
    ("ALTER TABLE runner_triggers ALTER COLUMN cron DROP NOT NULL", False),
    ("ALTER TABLE runner_triggers ALTER COLUMN cron SET NOT NULL", True),
    ("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'member'", False),
    ("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'admin'", True),
    ("ALTER TABLE connector_credentials "
     "ADD PRIMARY KEY (entity_type, entity_id, connector, account)", False),
    ("ALTER TABLE connector_credentials ADD PRIMARY KEY (entity_type, entity_id)", True),
    ("ALTER TABLE IF EXISTS table_absente_xyz ADD COLUMN IF NOT EXISTS x TEXT", False),
    # Le doute part : une forme inconnue n'est jamais sautée.
    ("ALTER TABLE tool_calls ADD CONSTRAINT c_xyz CHECK (true)", True),
    ("ALTER TABLE tool_calls RENAME COLUMN request_id TO rid", True),
    ("VACUUM tool_calls", True),
])
def test_verdict_par_forme(base_bootee, ordre, a_faire):
    from oto_mcp.db._ddl_garde import ddl_a_faire

    assert ddl_a_faire(base_bootee, ordre) is a_faire


def test_la_vue_etoile_n_est_rejouee_que_si_la_table_a_bouge(base_bootee):
    from oto_mcp.db._ddl_garde import ddl_a_faire

    vue = "CREATE OR REPLACE VIEW guide_library AS SELECT * FROM doctrine_library"
    assert ddl_a_faire(base_bootee, vue) is False
    with base_bootee.transaction(force_rollback=True):
        base_bootee.execute("ALTER TABLE doctrine_library ADD COLUMN colonne_neuve_xyz TEXT")
        assert ddl_a_faire(base_bootee, vue) is True


def test_index_partiel_repose_seulement_si_ses_valeurs_changent(base_bootee):
    from oto_mcp.db._init import _reposer_index

    actuel = ("CREATE INDEX IF NOT EXISTS idx_billing_payments_open "
              "ON billing_payments(created_at) "
              "WHERE status NOT IN ('paid', 'failed', 'canceled', 'expired')")
    with base_bootee.transaction(force_rollback=True):
        assert _reposer_index(base_bootee, "idx_billing_payments_open", actuel) is False
        assert _reposer_index(base_bootee, "idx_billing_payments_open",
                              actuel.replace(", 'expired'", "")) is True
        assert "expired" not in base_bootee.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_billing_payments_open'"
        ).fetchone()["indexdef"]
