"""Le run a UNE source, et c'est le journal (chantier du run, lot J3 ; #289 / ADR 0058-D2).

Le run existait DEUX fois : une ligne `runs` (écrite par `insert_run`/`finish_run`, lue
par le bloc C du handshake, la pastille de procédure et l'activité datastore) et une
reconstruction depuis `tool_calls` (lue par les lentilles admin et org). Deux objets, deux
vérités possibles du même déroulé — et rien pour dire laquelle sert.

Verdict du 12/08 : **la table n'est pas le run**. Le run est un assemblage à la lecture ;
la ligne `runs` n'est au mieux qu'un index, et son seul champ crédible est `project_id`
(le seul fait qu'aucune ligne de journal ne porte).

Ce fichier prouve le verdict **par la divergence** : on fabrique un run dont l'index
annonce `done` et dont le journal dit `failed`, puis on interroge TOUTES les surfaces qui
rendent un run. Une seule réponse est admise — celle du journal. Le cas n'est pas
théorique : `finish_run` est un UPDATE qui no-ope quand la ligne n'existe pas (la pose
d'index est best-effort) ou quand le `sub` ne matche pas, pendant que le fait `run_finish`
est écrit dans tous les cas.

Contre un vrai PostgreSQL : la question posée est « quelle ligne gagne », c'est-à-dire un
JOIN et un LATERAL. Ça ne s'exerce pas contre un stub — un test qui n'inspecterait que la
chaîne SQL passerait au vert sur une requête qui ne rend rien. Tables créées avec le DDL
RÉEL extrait de `_schema.py`.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp.db import _schema, usage


# ── Outillage ────────────────────────────────────────────────────────────────

def _real_ddl(table: str) -> str:
    m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                  _schema._SCHEMA, re.S | re.M)
    assert m, f"DDL de `{table}` introuvable dans _schema.py"
    return m.group(0)


@pytest.fixture()
def conn(pg_dsn, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    with psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True) as c:
        for t in ("runs", "tool_calls", "users"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        for t in ("users", "tool_calls", "runs"):
            c.execute(_real_ddl(t))

        @contextmanager
        def _connect_test():
            yield c

        monkeypatch.setattr(usage, "_connect", _connect_test)
        yield c


def _ago(minutes: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _fact(conn, run_id, tool, args, *, sub="u1", org_id=None, ago=10.0, kind="mcp",
          stamp=True):
    """Un fait du journal. `stamp` = la colonne `tool_calls.run_id` est posée (ce que
    fait `run_start`/`run_finish`) ; la corrélation d'une clôture, elle, passe par
    `args->>'run_id'` — c'est le seul lien que porte l'historique."""
    conn.execute(
        "INSERT INTO tool_calls (created_at, kind, sub, tool, args, run_id, org_id) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
        (_ago(ago), kind, sub, tool, json.dumps(args), run_id if stamp else None, org_id))


def _journal_open(conn, run_id, *, label, doctrine=None, version=None, sub="u1",
                  org_id=None, ago=10.0):
    args = {"label": label}
    if doctrine is not None:
        args["doctrine"] = doctrine
    if version is not None:
        args["doctrine_version"] = version
    _fact(conn, run_id, "run_start", args, sub=sub, org_id=org_id, ago=ago)


def _journal_close(conn, run_id, *, outcome, sub="u1", org_id=None, ago=5.0):
    _fact(conn, run_id, "run_finish", {"run_id": run_id, "outcome": outcome},
          sub=sub, org_id=org_id, ago=ago)


def _index(conn, run_id, *, label="étiquette", doctrine=None, outcome=None, sub="u1",
           org_id=None, project_id=None):
    """La ligne `runs` — l'index. Ce que le test lui fait dire est délibérément FAUX."""
    conn.execute(
        "INSERT INTO runs (run_id, sub, org_id, project_id, label, doctrine, outcome) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (run_id, sub, org_id, project_id, label, doctrine, outcome))


@pytest.fixture()
def divergent(conn):
    """LE cas : l'index annonce « prospection Q3 → done », le journal dit
    « prospection été → failed ». Rangé dans le projet 9 (que seul l'index sait)."""
    _index(conn, "r1", label="prospection Q3", doctrine="prospection", outcome="done",
           org_id=35, project_id=9)
    _journal_open(conn, "r1", label="prospection été", doctrine="prospection-ete",
                  version=4, org_id=35)
    _journal_close(conn, "r1", outcome="failed", org_id=35)
    conn.execute("INSERT INTO users (sub, email) VALUES ('u1', 'a@otomata.tech')")
    return conn


# ── Toutes les surfaces servent le journal ──────────────────────────────────

def test_la_lentille_admin_sert_le_journal(divergent):
    (run,) = usage.list_runs(100)
    assert run["outcome"] == "failed"
    assert run["label"] == "prospection été" and run["doctrine"] == "prospection-ete"
    assert run["email"] == "a@otomata.tech"


def test_la_lentille_dorg_sert_le_journal(divergent):
    (run,) = usage.list_runs(100, org_id=35)
    assert run["outcome"] == "failed"
    assert usage.list_runs(100, org_id=36) == []      # scope inchangé


def test_le_bloc_c_du_handshake_sert_le_journal(divergent):
    """Le mensonge le plus cher : cette ligne est RÉINJECTÉE à chaque nouvelle session.
    Annoncer « prospection Q3 → done » quand le déroulé a échoué oriente l'agent."""
    (run,) = usage.recent_runs("u1", 35)
    assert run["outcome"] == "failed" and run["label"] == "prospection été"
    # …et l'index garde son unique champ crédible.
    assert run["project_id"] == 9


def test_la_pastille_de_projet_sert_le_journal(divergent):
    (run,) = usage.project_runs(9)
    assert run["outcome"] == "failed" and run["doctrine"] == "prospection-ete"


def test_le_filtre_de_doctrine_dune_pastille_lit_le_journal(divergent):
    """La pastille d'une procédure filtre par slug : lu de l'index, un run se rangerait
    sous la procédure qu'il n'a pas déroulée."""
    assert [r["run_id"] for r in usage.project_runs(9, doctrine="prospection-ete")] == ["r1"]
    assert usage.project_runs(9, doctrine="prospection") == []


def test_linertie_dune_procedure_se_juge_sur_le_journal(divergent):
    stats = usage.project_run_stats(9)
    assert stats == {"runs": 1, "doctrines": ["prospection-ete"]}


def test_lactivite_dun_tableau_sert_le_journal(divergent):
    _fact(divergent, "r1", "data_write", {"ns_id": 160, "id": "row-1"}, ago=6.0)
    (entry,) = usage.datastore_namespace_activity(160, "mucho-leads")
    assert entry["outcome"] == "failed" and entry["run_label"] == "prospection été"
    assert entry["doctrine"] == "prospection-ete"


def test_lactivite_dune_ligne_sert_le_journal(divergent):
    _fact(divergent, "r1", "data_write", {"ns_id": 160, "id": "row-1"}, ago=6.0)
    (entry,) = usage.datastore_row_activity("row-1")
    assert entry["outcome"] == "failed" and entry["run_label"] == "prospection été"


# ── Ce que « le journal fait foi » implique ─────────────────────────────────

def test_un_index_sans_le_moindre_fait_nexiste_nulle_part(conn):
    """La pose d'index est best-effort : une ligne `runs` peut exister sans qu'aucun
    fait ne la porte. Elle n'a alors rien à montrer — plus d'étiquette sans déroulé."""
    _index(conn, "orphelin", label="fantôme", outcome="done", org_id=35, project_id=9)
    assert usage.list_runs(100) == []
    assert usage.recent_runs("u1", 35) == []
    assert usage.project_runs(9) == []
    assert usage.project_run_stats(9) == {"runs": 0, "doctrines": []}


def test_un_run_sans_index_reste_pleinement_lisible(conn):
    """Symétrique : le journal se suffit. Seul `project_id` manque — il n'existe que
    dans l'index, et c'est la seule chose que l'index sache."""
    _journal_open(conn, "r2", label="audit", org_id=35)
    _journal_close(conn, "r2", outcome="done", org_id=35)
    (run,) = usage.list_runs(100)
    assert run["label"] == "audit" and run["outcome"] == "done"
    assert usage.recent_runs("u1", 35)[0]["project_id"] is None


def test_une_cloture_dautrui_ne_clot_pas_le_run(conn):
    """`finish_run` scope sa clôture au propriétaire (#108). Sans la même règle à la
    lecture, un `run_finish` tapé par un tiers sur un run_id deviné donnerait au journal
    une issue que l'index refuse — la deuxième vérité, rouverte par la porte de service."""
    _journal_open(conn, "r3", label="prospection", org_id=35)
    _journal_close(conn, "r3", outcome="done", sub="intrus", org_id=35)
    assert usage.list_runs(100)[0]["outcome"] is None       # toujours ouvert


def test_une_pretendue_cloture_anterieure_a_louverture_est_ignoree(conn):
    _journal_open(conn, "r4", label="prospection", ago=10.0)
    _journal_close(conn, "r4", outcome="done", ago=20.0)
    assert usage.list_runs(100)[0]["outcome"] is None


def test_la_derniere_cloture_gagne(conn):
    """Un agent peut rejouer `run_finish` (retry, correction) : c'est le dernier fait
    qui dit où le déroulé s'est arrêté."""
    _journal_open(conn, "r5", label="prospection", ago=10.0)
    _journal_close(conn, "r5", outcome="blocked", ago=8.0)
    _journal_close(conn, "r5", outcome="done", ago=2.0)
    assert usage.list_runs(100)[0]["outcome"] == "done"


def test_une_cloture_dont_la_colonne_run_id_est_vide_compte_quand_meme(conn):
    """L'axe `_run_id=` n'est pas advertisé sur les verbes de run : dans la fenêtre de
    rétention, des clôtures ne portent PAS `tool_calls.run_id`. Corréler par la colonne
    les perdrait en silence — le lien est `args->>'run_id'`."""
    _journal_open(conn, "r6", label="prospection")
    _fact(conn, "r6", "run_finish", {"run_id": "r6", "outcome": "done"}, ago=5.0,
          stamp=False)
    assert usage.list_runs(100)[0]["outcome"] == "done"


def test_les_faits_dun_run_ne_closent_pas_son_voisin(conn):
    _journal_open(conn, "a", label="A")
    _journal_open(conn, "b", label="B")
    _journal_close(conn, "b", outcome="failed")
    got = {r["run_id"]: r["outcome"] for r in usage.list_runs(100)}
    assert got == {"a": None, "b": "failed"}


# ── La clôture appartient au déroulé qu'elle clôt ───────────────────────────

@pytest.mark.asyncio
async def test_la_cloture_est_stampee_sous_son_run(monkeypatch):
    """Un run rendait deux récits incompatibles de sa propre fin : `get_run` (la
    timeline) filtre sur la colonne `tool_calls.run_id`, que la ligne `run_finish` ne
    portait PAS — l'axe `_run_id=` n'est pas advertisé sur les verbes de run. La page
    d'un run n'affichait donc jamais sa fin, alors que son issue se lit de cette
    ligne-là. Un objet, une clôture, une timeline qui la contient."""
    from fastmcp import FastMCP

    from oto_mcp import db, session_org
    from oto_mcp.auth import hooks as auth_hooks
    from oto_mcp.tools import guide_run as drt

    class _SessionCtx:
        def __init__(self):
            self._state: dict = {}

        async def get_state(self, key):
            return self._state.get(key)

        async def set_state(self, key, value):
            self._state[key] = value

    mcp = FastMCP("test")
    drt.register(mcp)
    fn = (await mcp.get_tool("run_finish")).fn
    monkeypatch.setattr(auth_hooks, "current_user_sub_from_token", lambda: "u1")
    monkeypatch.setattr(db, "finish_run", lambda *a, **kw: None)

    out = await fn(_SessionCtx(), run_id="r1", outcome="done")
    assert out["ok"] and session_org.current_call_run() == "r1"
