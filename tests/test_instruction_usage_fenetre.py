"""L'usage d'une procédure : une seule fenêtre, une lecture sur l'index, et une
lecture groupée pour les listes.

Ce que ces tests tiennent (vrai PostgreSQL, DDL réel de `tool_calls` et `users`) :

  · `count` porte sur la MÊME fenêtre que la série : un chargement vieux de 40 jours
    n'entre ni dans l'un ni dans l'autre. Avant, `count` n'avait aucune borne de date ;
  · `callers` est trié du plus actif au moins actif, sans les appelants inconnus,
    lesquels restent comptés dans le total ;
  · seuls les appels réussis et les membres passés en `subs` comptent ;
  · la lecture groupée rend, par slug, chargements et déroulés (clés d'`args`
    distinctes) avec le dernier de chacun, sans additionner les deux ;
  · la lecture prend l'index partiel de son verbe (EXPLAIN sur une table chargée) ;
  · un verbe hors de la liste fermée est refusé — il serait écrit en littéral.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.db import _schema, usage


def _real_ddl(table: str) -> str:
    m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                  _schema._SCHEMA, re.S | re.M)
    assert m, f"DDL de `{table}` introuvable dans _schema.py"
    return m.group(0)


@pytest.fixture()
def conn(pg_module_dsn, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    with psycopg.connect(pg_module_dsn, row_factory=dict_row, autocommit=True) as c:
        for t in ("tool_calls", "users"):
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        for t in ("users", "tool_calls"):
            c.execute(_real_ddl(t))
        for ddl in usage.DDL_INDEX_USAGE.values():
            c.execute(ddl)
        c.execute("INSERT INTO users (sub, email) VALUES ('u1', 'a@x.io'), ('u2', 'b@x.io')")

        @contextmanager
        def _connect_test():
            yield c

        monkeypatch.setattr(usage, "_connect", _connect_test)
        yield c


def _appel(conn, sub, tool, args, *, il_y_a=timedelta(hours=1), ok=True, server="oto"):
    conn.execute(
        "INSERT INTO tool_calls (created_at, server, sub, tool, args, ok) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
        (datetime.now(timezone.utc) - il_y_a, server, sub, tool,
         __import__("json").dumps(args), ok),
    )


def test_count_et_serie_portent_sur_la_meme_fenetre(conn):
    _appel(conn, "u1", "oto_procedure", {"slug": "p"})
    _appel(conn, "u1", "oto_procedure", {"slug": "p"}, il_y_a=timedelta(days=2))
    _appel(conn, "u1", "oto_procedure", {"slug": "p"}, il_y_a=timedelta(days=40))
    u = usage.instruction_usage(["u1"], "oto_procedure", "p", days=30)
    assert u["count"] == 2
    assert u["count"] == sum(u["daily"].values())


def test_callers_tries_et_inconnus_comptes_mais_pas_nommes(conn):
    for _ in range(3):
        _appel(conn, "u2", "oto_procedure", {"slug": "p"})
    _appel(conn, "u1", "oto_procedure", {"slug": "p"})
    _appel(conn, "u9", "oto_procedure", {"slug": "p"})  # pas de compte `users`
    u = usage.instruction_usage(["u1", "u2", "u9"], "oto_procedure", "p")
    assert u["callers"] == ["b@x.io", "a@x.io"]
    assert u["count"] == 5


def test_seuls_les_membres_et_les_appels_reussis_comptent(conn):
    _appel(conn, "u1", "oto_procedure", {"slug": "p"})
    _appel(conn, "u1", "oto_procedure", {"slug": "p"}, ok=False)
    _appel(conn, "u2", "oto_procedure", {"slug": "p"})
    assert usage.instruction_usage(["u1"], "oto_procedure", "p")["count"] == 1


def test_les_deroules_se_lisent_sous_leur_propre_cle(conn):
    _appel(conn, "u1", "run_start", {usage._ARG_PROCEDURE: "p"})
    _appel(conn, "u1", "run_start", {"slug": "p"})  # mauvaise clé : pas un déroulé de p
    r = usage.instruction_usage(["u1"], "run_start", "p", slug_key=usage._ARG_PROCEDURE)
    assert r["count"] == 1


def test_lecture_groupee_par_slug(conn):
    _appel(conn, "u1", "oto_procedure", {"slug": "a"}, il_y_a=timedelta(days=3))
    _appel(conn, "u1", "oto_procedure", {"slug": "a"}, il_y_a=timedelta(hours=2))
    _appel(conn, "u1", "oto_procedure", {"slug": "b"})
    _appel(conn, "u1", "run_start", {usage._ARG_PROCEDURE: "a"}, il_y_a=timedelta(days=1))
    _appel(conn, "u1", "oto_procedure", {"slug": "c"}, il_y_a=timedelta(days=45))
    _appel(conn, "u1", "oto_procedure", {})  # une liste, pas un chargement nommé
    out = usage.instructions_usage_by_slug(["u1"], days=30)
    assert set(out) == {"a", "b"}
    assert out["a"]["count"] == 2 and out["a"]["runs_count"] == 1
    assert out["b"]["count"] == 1 and out["b"]["runs_count"] == 0
    # Le dernier chargement de `a` est le plus récent des deux.
    assert out["a"]["last_at"] > datetime.now(timezone.utc) - timedelta(hours=3)
    assert out["b"]["last_run_at"] is None


def test_lecture_groupee_sans_membres_ne_lit_rien():
    assert usage.instructions_usage_by_slug([]) == {}


def test_un_verbe_hors_liste_est_refuse():
    with pytest.raises(ValueError):
        usage.instruction_usage(["u1"], "oto_search", "p")
    with pytest.raises(ValueError):
        usage.instruction_usage(["u1"], "run_start", "p")  # mauvaise clé pour ce verbe


def test_la_lecture_prend_l_index_partiel_du_verbe(conn):
    """Sur une table chargée d'autres verbes, le plan passe par l'index du verbe — pas
    par un parcours du journal. `enable_seqscan=off` ôte au planificateur l'excuse
    d'une petite table ; si l'index n'était pas UTILISABLE, il resterait sur Seq Scan."""
    conn.execute(
        "INSERT INTO tool_calls (created_at, server, sub, tool, args, ok) "
        "SELECT NOW() - (i || ' minutes')::interval, 'oto', 'u1', 'data_query', "
        "'{}'::jsonb, true FROM generate_series(1, 2000) i")
    _appel(conn, "u1", "oto_procedure", {"slug": "p"})
    _appel(conn, "u1", "run_start", {usage._ARG_PROCEDURE: "p"})
    conn.execute("ANALYZE tool_calls")
    conn.execute("SET enable_seqscan = off")
    for tool, index in (("oto_procedure", usage.INDEX_USAGE_CHARGEMENTS),
                        ("run_start", usage.INDEX_USAGE_DEROULES)):
        key = usage._VERBES_USAGE[tool]
        plan = "\n".join(r["QUERY PLAN"] for r in conn.execute(
            f"EXPLAIN SELECT 1 FROM tool_calls l WHERE l.tool = '{tool}' "
            f"AND l.created_at >= NOW() - make_interval(days => 30) "
            f"AND l.sub = ANY(%s) AND l.args->>'{key}' = %s AND l.ok",
            (["u1"], "p")).fetchall())
        assert index in plan, plan
    conn.execute("RESET enable_seqscan")
