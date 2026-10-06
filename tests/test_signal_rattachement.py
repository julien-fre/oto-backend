"""Un sujet en attente ne se redépose pas : il se RATTACHE.

Mesuré le 06/10/2026 : 338 signaux en attente d'arbitrage, dont une quarantaine de
redites — douze fois la même valeur absente d'une procédure, huit fois la même clé
morte. La pile comptait des répétitions comme des sujets, et l'agent qui redéposait ne
savait pas que le sujet était connu, ni ce qui avait été décidé.

Ce que le lot garantit, et que ces tests figent :
- même org, même type, même cible qu'un signal en attente ⟹ une OCCURRENCE de
  celui-ci, son texte et son auteur gardés, l'agent informé de l'état et de la décision ;
- un sujet CLOS qui revient ⟹ un signal NEUF, qui cite le dernier clos ;
- sans cible, rien ne se rattache ;
- la liste admin compte les occurrences et trie sur la dernière.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

import pytest

from oto_mcp.db import _schema, usage


@pytest.fixture()
def live_signals(pg_module_dsn, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    def _ddl(table: str) -> str:
        m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                      _schema._SCHEMA, re.S | re.M)
        assert m, f"DDL de `{table}` introuvable"
        return m.group(0)

    with psycopg.connect(pg_module_dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS usage_signal_occurrences")
        c.execute("DROP TABLE IF EXISTS usage_signals")
        c.execute("DROP TABLE IF EXISTS users CASCADE")
        c.execute(_ddl("usage_signals"))
        c.execute(_ddl("usage_signal_occurrences"))
        c.execute(_ddl("users"))

        @contextmanager
        def _connect_test():
            yield c

        monkeypatch.setattr(usage, "_connect", _connect_test)
        yield c
        c.execute("DROP TABLE IF EXISTS usage_signal_occurrences")
        c.execute("DROP TABLE IF EXISTS usage_signals")


def _depose(**kw):
    base = dict(sub="agent-1", org_id=318, signal="gap", kind="missing_data",
                target="hs_lead_status UNQUALIFIED", body="la valeur n'existe pas",
                session_id="s1")
    base.update(kw)
    return usage.insert_usage_signal(**base)


def test_une_redite_s_ATTACHE_au_signal_en_attente(live_signals):
    premier = _depose()
    for i in range(3):
        d = _depose(body=f"toujours absente, run {i}", kind="wrong_result")
        assert d.rattache is True and d.id == premier.id
    assert d.occurrences == 4
    n = live_signals.execute("SELECT count(*) AS n FROM usage_signals").fetchone()["n"]
    assert n == 1, "la pile compte UN sujet"
    genres = {r["kind"] for r in live_signals.execute(
        "SELECT kind FROM usage_signal_occurrences").fetchall()}
    assert genres == {"wrong_result"}, "chaque occurrence garde son genre"


def test_la_cible_se_compare_sans_casse_ni_blancs(live_signals):
    premier = _depose()
    d = _depose(target="  HS_LEAD_STATUS unqualified ", body="autre texte")
    assert d.rattache is True and d.id == premier.id


def test_un_signal_ACQUITTE_attend_encore_et_rattache(live_signals):
    premier = _depose()
    usage.set_usage_signal_status(premier.id, status="acknowledged", by="op", note="vu")
    d = _depose(body="encore")
    assert d.rattache is True and d.status == "acknowledged" and d.resolution == "vu"


def test_un_sujet_CLOS_qui_revient_ouvre_un_signal_NEUF(live_signals):
    """Une régression doit se voir : pas enterrée sous un arbitrage déjà rendu."""
    premier = _depose()
    usage.set_usage_signal_status(premier.id, status="resolved", by="op",
                                  note="valeur ajoutée au portail")
    d = _depose(body="elle manque de nouveau")
    assert d.rattache is False and d.id != premier.id
    assert d.precedent == {"id": premier.id, "status": "resolved",
                           "resolution": "valeur ajoutée au portail"}
    # Et la redite suivante se rattache au NOUVEAU, pas à l'ancien clos.
    e = _depose(body="et encore")
    assert e.rattache is True and e.id == d.id and e.precedent is None


def test_sans_cible_rien_ne_se_rattache(live_signals):
    a = _depose(target=None)
    b = _depose(target=None, body="autre")
    assert b.rattache is False and b.id != a.id


def test_le_rejeu_d_une_OCCURRENCE_ne_s_ecrit_pas(live_signals):
    premier = _depose()
    _depose(sub="agent-2", body="vu aussi")
    rejeu = _depose(sub="agent-2", body="vu aussi")
    assert rejeu.deja is True and rejeu.id == premier.id
    n = live_signals.execute(
        "SELECT count(*) AS n FROM usage_signal_occurrences").fetchone()["n"]
    assert n == 1


def test_la_liste_compte_les_occurrences_et_trie_sur_la_DERNIERE(live_signals):
    ancien = _depose(target="sujet ancien")
    recent = _depose(target="sujet récent")
    _depose(target="sujet ancien", body="il revient")   # l'ancien redevient actif
    lignes = usage.list_usage_signals(status="pending")
    assert [r["id"] for r in lignes] == [ancien.id, recent.id]
    par_id = {r["id"]: r for r in lignes}
    assert par_id[ancien.id]["occurrences"] == 2
    assert par_id[recent.id]["occurrences"] == 1
    assert par_id[ancien.id]["last_seen_at"] >= par_id[recent.id]["last_seen_at"]


def test_la_surface_DIT_le_rattachement_et_la_regression(live_signals, monkeypatch):
    """L'agent apprend que le sujet est connu, son état et sa décision : il n'a pas à
    le redéposer. Et un sujet clos qui revient est annoncé comme tel."""
    from oto_mcp.capabilities import usage as cap

    monkeypatch.setattr(cap, "_correlation", lambda: ("agent", "s1"))
    monkeypatch.setattr(cap, "_active_org", lambda _s: 318)

    class _Ctx:
        sub, org_id = "agent-1", 318

    class _In:
        signal, kind, target, text = "gap", "missing_data", "t", "premier"

    class _In2(_In):
        text = "second"

    premier = cap._feedback(_Ctx(), _In())
    second = cap._feedback(_Ctx(), _In2())
    assert second["id"] == premier["id"]
    assert second["already_reported"]["occurrences"] == 2
    assert second["already_reported"]["status"] == "open"
    assert "No need to report it again" in second["already_reported"]["hint"]

    usage.set_usage_signal_status(premier["id"], status="declined", by="op",
                                  note="hors périmètre")

    class _In3(_In):
        text = "troisième"

    troisieme = cap._feedback(_Ctx(), _In3())
    assert troisieme["id"] != premier["id"] and "already_reported" not in troisieme
    assert troisieme["regression_of"]["id"] == premier["id"]
    assert troisieme["regression_of"]["resolution"] == "hors périmètre"
