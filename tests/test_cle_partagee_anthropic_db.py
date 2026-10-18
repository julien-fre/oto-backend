"""La mesure d'une clé partagée EN BASE : l'estampille, chaque tour, le rejeu, la
conclusion et le reste.

Une doublure ne prouve ni le `FOR UPDATE`, ni la mise à jour JSONB du cumul, ni que
la ligne et le cumul s'écrivent ensemble — c'est pourtant ce qui fait la facture.
"""
from __future__ import annotations

import uuid

import pytest

PORTEUR, AUTRE = "worker:banc", "worker:autre"
POSTES = {"entree": 100, "sortie": 50, "ecriture_cache": 0, "lecture_cache": 1000}


@pytest.fixture
def travail(live):
    """Un travail réservé par `PORTEUR`, sur un run réel (clé étrangère)."""
    from oto_mcp.db._conn import _connect
    run_id = f"r-{uuid.uuid4().hex[:8]}"
    with _connect() as c:
        c.execute("INSERT INTO runs (run_id, sub, org_id, label) VALUES (%s, %s, %s, %s)",
                  (run_id, "alexis", 42, "banc"))
        jid = c.execute(
            """INSERT INTO runner_jobs (org_id, kind, payload, status, claimed_by, attempts,
                                        sub, run_id)
               VALUES (42, 'start', '{"model_family": "anthropic"}', 'claimed', %s, 1,
                       'alexis', %s) RETURNING id""", (PORTEUR, run_id)).fetchone()["id"]
    return jid, run_id


def _lignes(jid):
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT tool, quantity, key_mode, org_id, sub, run_id, ok, args FROM tool_calls "
            "WHERE (args->>'runner_job_id')::bigint = %s ORDER BY id", (jid,))]


def _metrer(jid, tour, cout, porteur=PORTEUR):
    from oto_mcp import db
    return db.metrer_un_tour(jid, porteur, tour, cout, POSTES,
                             {"runner_job_id": jid, "turn": tour, "model": "claude-sonnet-5"})


def test_chaque_tour_s_inscrit_une_fois_et_se_cumule(travail):
    from oto_mcp import db
    jid, run_id = travail
    assert db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    assert not db.estampiller_cle_partagee(jid, AUTRE, "anthropic", "platform")
    assert _metrer(jid, 0, 900) == "inscrit"
    assert _metrer(jid, 1, 1100) == "inscrit"
    assert _metrer(jid, 1, 1100) == "deja_inscrit", "un rapport rejoué ne compte pas deux fois"
    assert _metrer(jid, 2, 5, porteur=AUTRE) is None, "le travail n'est pas à lui"
    lignes = _lignes(jid)
    assert [(l["quantity"], l["args"]["turn"]) for l in lignes] == [(900, 0), (1100, 1)]
    assert all((l["tool"], l["key_mode"], l["org_id"], l["sub"], l["run_id"], l["ok"])
               == ("anthropic_tokens", "platform", 42, "alexis", run_id, True)
               for l in lignes)
    assert lignes[0]["args"]["tokens"] == POSTES

    conclu = db.complete_job(jid, PORTEUR, True, result={"model": "claude-sonnet-5"})
    assert conclu["attempts"] == 1 and conclu["org_id"] == 42 and conclu["sub"] == "alexis"
    est = conclu["cle_partagee"]
    assert (est["tentative"], est["dernier_tour"], est["microdollars"]) == (1, 1, 2000)


def test_la_conclusion_inscrit_le_reste_en_base(travail):
    from oto_mcp import db
    from oto_mcp.capabilities import runner_jobs as RJ
    jid, _ = travail
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    _metrer(jid, 0, 2000)
    conclu = db.complete_job(jid, PORTEUR, False, error="coupé")
    # Total déclaré : 400 × 2 + 150 × 10 = 2 300 µ$ ; 2 000 déjà inscrits.
    RJ._mesurer_cle_partagee(jid, conclu, {"model": "claude-sonnet-5", "usage_input": 400,
                                           "usage_output": 150, "usage_cache_write": 0,
                                           "usage_cache_read": 0})
    assert [(l["quantity"], l["args"]["turn"]) for l in _lignes(jid)] == [(2000, 0), (300, "reste")]


def test_une_tentative_non_estampillee_ne_mesure_rien(travail):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    jid, _ = travail
    db.estampiller_cle_partagee(jid, PORTEUR, "anthropic", "platform")
    with _connect() as c:   # repris plus tard sur la clé de l'org : tentative suivante
        c.execute("UPDATE runner_jobs SET attempts = 2 WHERE id = %s", (jid,))
    assert _metrer(jid, 0, 10) == "sans_cle_partagee"
    assert _lignes(jid) == []
