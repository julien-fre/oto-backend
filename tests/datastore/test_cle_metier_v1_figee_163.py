"""L'index de clé métier garde son expression V1 pendant que la règle de valeur change (oto#163).

Les index `ds_bkey_<ns_id>` sont des index d'EXPRESSION déjà construits, sur une base
partagée entre préprod et prod, et le lookup ne les sert qu'à chaîne identique. Si la
règle de valeur avait entraîné leur expression, rien ne se serait vu : la déduplication
continuerait, et chaque pose de schéma construirait un index différent de ceux qui
existent.

Contre un vrai PostgreSQL :
- la définition enregistrée, comparée à une chaîne relevée AVANT le changement ;
- le plan du VRAI lookup, capturé sur sa connexion, comparé à celui de la requête V1
  relevée avant le changement, dans les DEUX modes de plan. Le lookup s'exécute en requête
  paramétrée (`ns_id` et la clé en paramètres) sur le pool de `_conn.py`, qui ne règle ni
  `prepare_threshold` ni `plan_cache_mode` : psycopg la prépare côté serveur dès la
  5e exécution sur une connexion, et PostgreSQL choisit alors entre plan personnalisé et
  plan générique. Les deux se rejouent ici par `PREPARE` ;
- un témoin qui prouve que l'épreuve du plan a des dents : la règle de valeur d'oto#163,
  elle, ne sert pas l'index.

⚠️ Ce banc n'affirme `Index Cond` que pour le plan PERSONNALISÉ. En plan générique,
`ns_id` est un paramètre et l'index est PARTIEL (`WHERE ns_id = <n>`) : PostgreSQL ne
peut pas prouver le prédicat, et le plan ne l'emprunte pas — état antérieur à oto#163,
que le lot ne change pas (même plan avant et après).
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql

#: Relevés le 13/09/2026 sur a68e1c68, AVANT le changement.
EXPR_V1 = "COALESCE(data->'siren'->>'valeur', data->>'siren')"
INDEXDEF_V1 = (
    "CREATE UNIQUE INDEX ds_bkey_1 ON public.datastore_rows USING btree "
    "(COALESCE(((data -> 'siren'::text) ->> 'valeur'::text), (data ->> 'siren'::text))) "
    "WHERE ((ns_id = 1) AND (COALESCE(((data -> 'siren'::text) ->> 'valeur'::text), "
    "(data ->> 'siren'::text)) IS NOT NULL))")
LOOKUP_V1 = ("SELECT row_id FROM datastore_rows WHERE ns_id = %s AND " + EXPR_V1
             + " = %s ORDER BY created_at ASC LIMIT 1")

MODES = ("force_custom_plan", "force_generic_plan")


@pytest.fixture()
def pg(pg_module_dsn, monkeypatch):
    """`datastore_rows` minimale, deux tableaux assez peuplés pour que le planner choisisse
    sans qu'on le force, l'index de `siren` posé sur le tableau 1, le module pointé dessus."""
    monkeypatch.setenv("DATABASE_URL", pg_module_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_module_dsn)
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute("CREATE TABLE datastore_rows ("
                  " ns_id INT, row_id TEXT, data JSONB,"
                  " created_at TIMESTAMPTZ DEFAULT now(),"
                  " updated_at TIMESTAMPTZ DEFAULT now())")
        c.execute("INSERT INTO datastore_rows (ns_id, row_id, data) VALUES "
                  "(1, 'a', '{\"siren\": \"552081317\"}'), "
                  "(1, 'b', '{\"siren\": {\"valeur\": \"111111111\", \"comment\": \"registre\"}}')")
        c.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                  "SELECT ns, ns || '-' || g, jsonb_build_object('siren', lpad(g::text, 9, '0')) "
                  "FROM generate_series(1, 2) ns, generate_series(1, 3000) g")
        c.execute("ANALYZE datastore_rows")
        from oto_mcp.db.datastore import datastore_ensure_key_index
        datastore_ensure_key_index(1, "siren")
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _plan(conn, requete: str, params: tuple, mode: str) -> str:
    """Le plan de la requête PRÉPARÉE, dans le mode demandé — le chemin qu'emprunte une
    requête que psycopg a préparée côté serveur."""
    texte = requete
    for i in range(1, len(params) + 1):
        texte = texte.replace("%s", f"${i}", 1)
    conn.execute(f"SET plan_cache_mode = {mode}")
    conn.execute(f"PREPARE lookup_163 AS {texte}")
    try:
        appel = sql.SQL("EXPLAIN EXECUTE lookup_163({})").format(
            sql.SQL(", ").join(sql.Literal(p) for p in params))
        return "\n".join(r[0] for r in conn.execute(appel))
    finally:
        conn.execute("DEALLOCATE lookup_163")
        conn.execute("RESET plan_cache_mode")


def _lookup_capture(monkeypatch, cle: str) -> tuple[str, tuple, object]:
    """Exécute le VRAI lookup et rend `(requête, paramètres, row_id trouvé)`, capturés sur
    sa connexion — rien n'est recomposé ici."""
    from oto_mcp.db import datastore as dsdb
    vu: dict = {}
    reel = dsdb._connect

    @contextmanager
    def espion():
        with reel() as conn:
            class _Connexion:
                def execute(self, requete, params=None):
                    vu["requete"] = requete.as_string(conn)
                    vu["params"] = tuple(params or ())
                    return conn.execute(requete, params)
            yield _Connexion()

    monkeypatch.setattr(dsdb, "_connect", espion)
    trouve = dsdb.datastore_find_row_id_by_key(1, "siren", cle)
    monkeypatch.undo()
    return vu["requete"], vu["params"], trouve


def test_la_definition_enregistree_par_postgres_est_celle_de_V1(pg):
    indexdef = pg.execute("SELECT pg_get_indexdef('ds_bkey_1'::regclass)").fetchone()[0]
    assert indexdef == INDEXDEF_V1


def test_le_lookup_trouve_une_cle_nue_comme_une_cle_enveloppee(pg, monkeypatch):
    assert _lookup_capture(monkeypatch, "552081317")[2] == "a"
    assert _lookup_capture(monkeypatch, "111111111")[2] == "b"


@pytest.mark.parametrize("mode", MODES)
def test_le_plan_du_vrai_lookup_est_celui_de_V1(pg, monkeypatch, mode):
    """Aucune dégradation, dans les deux modes : le plan du lookup d'aujourd'hui est
    exactement celui de la requête relevée avant oto#163."""
    requete, params, _ = _lookup_capture(monkeypatch, "552081317")
    assert requete == LOOKUP_V1, "le lookup n'envoie plus la requête V1"
    assert _plan(pg, requete, params, mode) == _plan(pg, LOOKUP_V1, params, mode)


def test_en_plan_personnalise_le_lookup_est_servi_par_l_index(pg, monkeypatch):
    requete, params, _ = _lookup_capture(monkeypatch, "552081317")
    plan = _plan(pg, requete, params, "force_custom_plan")
    assert "Index Scan using ds_bkey_1" in plan, plan
    assert "Index Cond" in plan, plan


def test_temoin_la_regle_de_valeur_ne_sert_pas_l_index(pg):
    """Sans ce témoin, l'épreuve précédente pourrait être verte par construction. La même
    requête, écrite avec la règle de valeur d'oto#163, ne trouve PAS l'index : c'est ce
    que la délégation d'avant aurait produit."""
    from oto_mcp.db.paths import field_value_sql
    expr = field_value_sql("siren").as_string(pg)
    requete = LOOKUP_V1.replace(EXPR_V1, expr)
    assert requete != LOOKUP_V1
    plan = _plan(pg, requete, (1, "552081317"), "force_custom_plan")
    assert "ds_bkey_1" not in plan, plan
