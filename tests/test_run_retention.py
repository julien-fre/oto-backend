"""Rétention d'un run : l'étiquette ne survit plus à ses faits (oto-backend#289).

`prune_tool_calls` effaçait les **faits** d'un run à 30 jours et rien n'effaçait jamais
sa ligne `runs`. Au 31ᵉ jour, la page d'un run — assemblée à la lecture depuis les faits
(ADR 0058-D2) — était donc VIDE pendant que la ligne annonçait toujours « done ».

Ce que ces tests verrouillent :
- **le prédicat**, contre un vrai PostgreSQL : quelles lignes de `runs` partent, quelles
  lignes restent. C'est une purge de données de PRODUCTION, et un prédicat de suppression
  ne s'exerce pas contre un stub — les tables sont créées avec le DDL RÉEL extrait de
  `_schema.py` (mêmes noms de colonnes que la prod ; renommer `started_at` casserait ici) ;
- **l'ordre et l'atomicité**, partout (sans base) : les faits d'abord, l'étiquette
  ensuite — sinon le `NOT EXISTS` lirait l'état d'AVANT la purge et n'effacerait rien —
  et les deux dans la même transaction, pour qu'aucune fenêtre ne montre l'un sans l'autre.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.db import _schema, usage


# ── Le prédicat, contre un vrai PostgreSQL ───────────────────────────────────

def _real_ddl(table: str) -> str:
    """Le `CREATE TABLE` de `table`, extrait du schéma RÉEL (`db/_schema.py`).

    Les deux tables sont autonomes (aucune FK) : les créer seules suffit, et ça évite
    de jouer `init_db` (extension pgvector, ~100 tables) pour exercer un DELETE."""
    m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                  _schema._SCHEMA, re.S | re.M)
    assert m, f"DDL de `{table}` introuvable dans _schema.py"
    return m.group(0)


@pytest.fixture()
def conn(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    with psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS runs")
        c.execute("DROP TABLE IF EXISTS tool_calls")
        c.execute(_real_ddl("tool_calls"))
        c.execute(_real_ddl("runs"))
        yield c


@pytest.fixture()
def prune(conn, monkeypatch):
    """`prune_tool_calls` branché sur la connexion de test (pool réel court-circuité)."""
    @contextmanager
    def _connect_test():
        yield conn

    monkeypatch.setattr(usage, "_connect", _connect_test)
    return usage.prune_tool_calls


def _ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _run(conn, run_id: str, *, started: float, finished: float | None = None,
         outcome: str | None = None, calls: tuple[float, ...] = ()) -> None:
    """Un run : sa ligne + les faits (lignes de journal) qui le portent, par âge en jours."""
    conn.execute(
        "INSERT INTO runs (run_id, sub, label, outcome, started_at, finished_at) "
        "VALUES (%s, 'u1', %s, %s, %s, %s)",
        (run_id, f"label {run_id}", outcome, _ago(started),
         _ago(finished) if finished is not None else None))
    for age in calls:
        conn.execute(
            "INSERT INTO tool_calls (created_at, sub, tool, run_id) "
            "VALUES (%s, 'u1', 'data_write', %s)", (_ago(age), run_id))


def _surviving_runs(conn) -> set:
    return {r["run_id"] for r in conn.execute("SELECT run_id FROM runs").fetchall()}


def _surviving_calls(conn) -> int:
    return conn.execute("SELECT count(*) AS n FROM tool_calls").fetchone()["n"]


def test_le_run_dont_les_faits_sont_partis_part_avec_eux(conn, prune):
    # le cas de #289 : clôturé « done » il y a 40 jours, faits du même jour.
    _run(conn, "vieux-fini", started=41, finished=40, outcome="done", calls=(41, 40))
    prune(30)
    assert _surviving_runs(conn) == set()
    assert _surviving_calls(conn) == 0


def test_le_run_ouvert_et_muet_depuis_longtemps_part_aussi(conn, prune):
    # jamais clôturé (outcome NULL, annoncé « (en cours) » au handshake) : sans faits,
    # il n'y a plus rien à montrer — l'âge se lit alors sur `started_at`.
    _run(conn, "vieux-ouvert", started=45, calls=(45,))
    prune(30)
    assert _surviving_runs(conn) == set()


def test_le_run_ancien_encore_vivant_garde_son_etiquette(conn, prune):
    # ouvert il y a 40 jours, appelé hier : ses faits récents survivent à la purge,
    # donc sa page n'est pas vide → on ne touche pas à sa ligne (garde NOT EXISTS).
    _run(conn, "long-cours", started=40, calls=(40, 1))
    prune(30)
    assert _surviving_runs(conn) == {"long-cours"}
    assert _surviving_calls(conn) == 1          # seul le fait de 40 jours est parti


def test_le_run_recent_sans_aucun_fait_survit(conn, prune):
    # `_persist_open` est best-effort : un run peut naître sans que sa ligne de journal
    # ait été écrite. La garde d'ÂGE est ce qui l'empêche de s'effacer aussitôt.
    _run(conn, "neuf-sans-faits", started=0.1)
    prune(30)
    assert _surviving_runs(conn) == {"neuf-sans-faits"}


def test_un_run_clos_recemment_survit_meme_ouvert_il_y_a_longtemps(conn, prune):
    # l'âge se lit sur `finished_at` quand il existe : clôturé hier, il est récent.
    _run(conn, "clos-hier", started=50, finished=1, outcome="done")
    prune(30)
    assert _surviving_runs(conn) == {"clos-hier"}


def test_le_run_recent_est_intact(conn, prune):
    _run(conn, "recent", started=2, finished=1, outcome="done", calls=(2, 1))
    prune(30)
    assert _surviving_runs(conn) == {"recent"}
    assert _surviving_calls(conn) == 2


def test_les_faits_dun_run_survivant_ne_protegent_pas_un_autre_run(conn, prune):
    # corrélation par run_id, pas par voisinage temporel : deux runs, un seul survit.
    _run(conn, "vivant", started=40, calls=(1,))
    _run(conn, "mort", started=40, calls=(40,))
    prune(30)
    assert _surviving_runs(conn) == {"vivant"}


# ── L'ordre et l'atomicité, sans base ────────────────────────────────────────

class _Cur:
    rowcount = 0


class _Conn:
    """Connexion double : enregistre les SQL joués et compte les transactions."""

    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((sql, params))
        return _Cur()


def _sqls(monkeypatch) -> list:
    log: list = []
    opened = []

    @contextmanager
    def _fake_connect():
        opened.append(1)
        yield _Conn(log)

    monkeypatch.setattr(usage, "_connect", _fake_connect)
    usage.prune_tool_calls(30)
    assert len(opened) == 1, "les deux purges doivent tenir dans UNE transaction"
    return [sql for sql, _ in log]


def test_les_faits_sont_purges_avant_letiquette(monkeypatch):
    sqls = _sqls(monkeypatch)
    assert len(sqls) == 2
    assert "DELETE FROM tool_calls" in sqls[0]
    # l'ordre EST le correctif : le prédicat de la 2e requête lit l'état d'APRÈS la 1re.
    assert "DELETE FROM runs" in sqls[1]


def test_letiquette_nest_effacee_que_si_plus_aucun_fait_ne_la_porte(monkeypatch):
    runs_sql = _sqls(monkeypatch)[1]
    assert "NOT EXISTS" in runs_sql and "tc.run_id = r.run_id" in runs_sql
    assert "COALESCE(r.finished_at, r.started_at)" in runs_sql


def test_la_fenetre_de_retention_est_la_meme_des_deux_cotes(monkeypatch):
    log: list = []

    @contextmanager
    def _fake_connect():
        yield _Conn(log)

    monkeypatch.setattr(usage, "_connect", _fake_connect)
    usage.prune_tool_calls(7)
    assert [params for _, params in log] == [(7,), (7,)]
