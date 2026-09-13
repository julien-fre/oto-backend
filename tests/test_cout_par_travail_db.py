"""Le coût EN BASE — les trois regroupements, et les trous qu'ils avouent.

Ce qu'une doublure ne peut pas prouver, et qui est tout l'enjeu :

1. **un run n'est pas un travail** — un `continue` en ajoute un, et le total doit
   les additionner. Lire le dernier travail sous-compterait exactement les
   déroulés longs ;
2. **l'écriture survit à la conclusion, sur les DEUX issues** — un échec a
   dépensé des jetons comme un succès ;
3. **la trace survit à son travail** — c'est la raison d'être de la table : les
   lignes de `runner_jobs` s'effacent en cascade avec leur run ;
4. **un total amputé se DIT amputé** — sans quoi il se propage jusqu'à une facture.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_cout_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    cle_avant = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        for cle, valeur in (("DATABASE_URL", url_avant),
                            ("OTO_MCP_MASTER_KEY", cle_avant)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


ORG = 6100
RESULTAT = {"usage_input": 1000, "usage_output": 200, "usage_cache_read": 50,
            "usage_cache_write": 10, "model": "claude-opus-5", "steps": 3}


def _run(db, org=ORG):
    """Un run réel — la table `runner_job_cost` n'a pas de FK vers lui, mais
    `runner_jobs` en a une, donc un travail lié en exige un."""
    rid = "run_" + uuid.uuid4().hex[:10]
    db.insert_run(rid, sub="alexis", org_id=org, label="banc de coût")
    return rid


def _travail_conclu(db, *, org=ORG, run_id=None, payload=None, fleet_id=None,
                    ok=True, resultat=RESULTAT, depot="anthropic"):
    """Enfile, réserve, conclut — le vrai chemin, pas un INSERT à la main."""
    job = db.enqueue_job(org, "start", payload=payload or {"procedure": "p"},
                         sub="alexis", run_id=run_id, fleet_id=fleet_id)
    db.claim_next_job(org, "worker:banc", lease_seconds=600, depot=depot)
    db.complete_job(job["id"], "worker:banc", ok=ok,
                    error=None if ok else "cassé", run_id=run_id, result=resultat)
    return job["id"]


# ── 1. un RUN n'est pas un travail ────────────────────────────────────────────

def test_un_run_CONTINUE_additionne_ses_travaux(live):
    """⚠️ LE banc du lot. Un `continue` se rattache au même `run_id` : lire un
    seul travail et l'appeler « le coût du run » sous-compte exactement les
    déroulés longs — ceux qu'on veut voir."""
    from oto_mcp import db
    rid = _run(db)
    for _ in range(3):
        _travail_conclu(db, run_id=rid)
    total = db.cout_du_run(rid, ORG)
    assert total["tentatives"] == 3
    assert total["input_tokens"] == 3000 and total["output_tokens"] == 600
    assert total["incomplet"] is False
    un_seul, _ = __import__("oto_mcp.runner_prix", fromlist=["x"]).cout_nano_usd(
        "claude-opus-5", entree=1000, sortie=200, ecriture_cache=10, lecture_cache=50)
    assert total["nano_usd"] == 3 * un_seul


def test_le_run_d_une_AUTRE_org_ne_rend_rien(live):
    from oto_mcp import db
    rid = _run(db)
    _travail_conclu(db, run_id=rid)
    assert db.cout_du_run(rid, ORG + 1)["tentatives"] == 0


# ── 2. les deux issues ────────────────────────────────────────────────────────

def test_un_travail_qui_ECHOUE_compte_quand_meme(live):
    """⚠️ Un échec a dépensé des jetons exactement comme un succès. La branche
    d'échec de `complete_job` jetait le résultat — c'était le trou le plus
    coûteux, parce qu'il ampute précisément les déroulés qui partent en vrille."""
    from oto_mcp import db
    rid = _run(db)
    _travail_conclu(db, run_id=rid, ok=False)
    total = db.cout_du_run(rid, ORG)
    assert total["tentatives"] == 1 and total["input_tokens"] == 1000
    lignes = db.lignes_du_run(rid, ORG)
    assert lignes[0]["outcome"] == "failed"


def test_un_travail_REUSSI_est_marque_done(live):
    from oto_mcp import db
    rid = _run(db)
    _travail_conclu(db, run_id=rid)
    assert db.lignes_du_run(rid, ORG)[0]["outcome"] == "done"


# ── 3. la trace SURVIT à son travail ──────────────────────────────────────────

def test_le_cout_SURVIT_a_la_suppression_du_run_et_du_travail(live):
    """⚠️⚠️ La raison d'être de cette table. `runner_jobs.run_id` est
    `ON DELETE CASCADE` vers `runs`, et `prune_orphan_runs` efface un run un mois
    après son journal : les travaux partent AVEC, et les jetons qu'ils portaient
    dans `result` avec eux. On ne refacture pas un mois dont la preuve a été
    supprimée le trentième jour."""
    from oto_mcp import db
    rid = _run(db)
    job_id = _travail_conclu(db, run_id=rid)
    with db._connect() as conn:
        conn.execute("DELETE FROM runs WHERE run_id = %s", (rid,))
        restant = conn.execute(
            "SELECT COUNT(*)::int AS n FROM runner_jobs WHERE id = %s",
            (job_id,)).fetchone()["n"]
    assert restant == 0, "le travail a bien disparu en cascade"
    total = db.cout_du_run(rid, ORG)
    assert total["tentatives"] == 1 and total["input_tokens"] == 1000, (
        "la ligne de coût doit SURVIVRE au travail qui l'a produite")


# ── 4. un total amputé le DIT ─────────────────────────────────────────────────

def test_une_EPAVE_laisse_une_trace_et_rend_le_total_incomplet(live):
    """Un bail expiré sans conclusion : personne n'a rendu de compte, mais les
    jetons ont bien été dépensés. Le total doit se déclarer PLANCHER."""
    from oto_mcp import db
    rid = _run(db)
    job = db.enqueue_job(ORG, "start", payload={"procedure": "p"}, sub="alexis",
                         run_id=rid, max_attempts=1)
    db.claim_next_job(ORG, "worker:banc", lease_seconds=600, depot="anthropic")
    with db._connect() as conn:
        conn.execute("UPDATE runner_jobs SET lease_until = NOW() - INTERVAL '1 hour' "
                     "WHERE id = %s", (job["id"],))
    # Le balayage des épaves tourne au claim suivant.
    db.claim_next_job(ORG, "worker:banc2", lease_seconds=600, depot="anthropic")
    total = db.cout_du_run(rid, ORG)
    assert total["tentatives"] == 1
    assert total["jetons_manquants"] is True
    assert total["incomplet"] is True, "un total amputé doit s'avouer amputé"
    assert db.lignes_du_run(rid, ORG)[0]["outcome"] == "lost"


def test_un_modele_NON_TARIFE_rend_le_total_incomplet_sans_perdre_les_jetons(live):
    """Les deux incomplétudes sont distinctes : ici les jetons sont MESURÉS, seul
    le montant manque. Perdre les jetons aussi serait doublement faux."""
    from oto_mcp import db
    rid = _run(db)
    _travail_conclu(db, run_id=rid,
                    resultat={**RESULTAT, "model": "un-modele-inedit"})
    total = db.cout_du_run(rid, ORG)
    assert total["input_tokens"] == 1000, "les jetons restent comptés"
    assert total["nano_usd"] == 0 and total["non_tarifes"] is True
    assert total["jetons_manquants"] is False
    assert total["incomplet"] is True


# ── 5. agent, passage, ventilation ────────────────────────────────────────────

def test_le_cout_d_un_AGENT_regroupe_par_declencheur(live):
    from oto_mcp import db
    rid = _run(db)
    for _ in range(2):
        _travail_conclu(db, run_id=rid, payload={"procedure": "p", "trigger_id": 42})
    _travail_conclu(db, run_id=_run(db), payload={"procedure": "p", "trigger_id": 43})
    assert db.cout_de_l_agent(42, ORG)["tentatives"] == 2
    assert db.cout_de_l_agent(43, ORG)["tentatives"] == 1


def test_le_cout_d_un_PASSAGE_regroupe_par_flotte(live):
    from oto_mcp import db
    fid = db.create_fleet(ORG, "alexis", procedure="p", namespace="n",
                          label="passage de banc", tools=["a"])["id"]
    rid = _run(db)
    for _ in range(2):
        _travail_conclu(db, run_id=rid, fleet_id=fid)
    total = db.cout_du_passage(fid, ORG)
    assert total["tentatives"] == 2
    assert db.lignes_du_run(rid, ORG)[0]["source"] == "batch"


def test_la_ventilation_dit_OU_part_l_argent(live):
    from oto_mcp import db
    org = ORG + 7
    rid = _run(db, org)
    _travail_conclu(db, org=org, run_id=rid)
    _travail_conclu(db, org=org, run_id=rid,
                    resultat={**RESULTAT, "model": "claude-haiku-4-5"})
    parts = {p["cle"]: p for p in db.ventilation(org, par="modele")}
    assert set(parts) == {"claude-opus-5", "claude-haiku-4-5"}
    assert parts["claude-opus-5"]["nano_usd"] > parts["claude-haiku-4-5"]["nano_usd"]


def test_le_payeur_par_DEFAUT_est_la_plateforme(live):
    """Sans dépôt servi, c'est notre clé qui paie — et c'est ce que la ligne doit
    dire, pas NULL."""
    from oto_mcp import db
    rid = _run(db)
    _travail_conclu(db, run_id=rid)
    assert db.lignes_du_run(rid, ORG)[0]["key_source"] == "platform"
