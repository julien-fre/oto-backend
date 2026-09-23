"""CHAQUE tentative garde son motif — `last_error` ne gardait que la dernière.

Un travail meurt au bout de trois essais et ne montrait qu'un tiers de son
histoire : `complete_job` écrase `last_error` à chaque conclusion. Rien ne disait
donc si les trois avaient échoué pour la MÊME raison — et « trois fois la même
panne » ne se soigne pas comme « trois pannes différentes ».

Mesuré sur un agent événementiel de production, 18-22/09/2026 : trois travaux,
neuf tentatives, trois lignes lisibles.

Ce que ces bancs figent :
- ① un échec relançable inscrit SA tentative, numérotée ;
- ② les tentatives s'AJOUTENT — c'est un journal, pas un état ;
- ③ un travail qui finit par RÉUSSIR garde la trace de ses échecs (le cas où
  l'écrasement effaçait l'incident le plus discrètement) ;
- ④ un succès d'emblée n'invente aucune tentative ;
- ⑤ le motif est borné comme `last_error`, et un motif vide ne perd pas sa ligne ;
- ⑥ la livraison sert le CORPS du travail et ses tentatives — de quoi rejouer.

Contre un PostgreSQL jetable (`OTO_TEST_PG_DSN`), sur le vrai `complete_job`.
"""
from __future__ import annotations

import os
import uuid

import pytest

WORKER = "worker-tentatives"
SUB = "sub-tentatives"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_tentatives_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
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
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


# Une org PAR CAS : la réservation prend le plus ancien travail de l'org, et un cas
# qui laisse un `pending` derrière lui ne doit pas servir le travail du suivant.

def _job_claime(org: int, *, max_attempts: int = 3) -> int:
    from oto_mcp import db
    j = db.enqueue_job(org, "start", payload={"procedure": "p"},
                       max_attempts=max_attempts)
    job = db.claim_next_job(org, WORKER, lease_seconds=60)
    assert job and job["id"] == j["id"], "la file portait ce travail : le claim le rend"
    return int(j["id"])


def _reclaim(org: int, job_id: int, tentative: int) -> None:
    """Le backoff écoulé, un worker reprend le travail — la tentative suivante."""
    from oto_mcp import db
    _sql("UPDATE runner_jobs SET due_at = NOW() WHERE id = %s", job_id)
    repris = db.claim_next_job(org, WORKER, lease_seconds=60)
    assert repris and repris["id"] == job_id and repris["attempts"] == tentative, repris


def _sql(requete: str, *args) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(requete, args)


def _tentatives(job_id: int) -> list[dict]:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        row = conn.execute("SELECT attempt_errors FROM runner_jobs WHERE id = %s",
                           (job_id,)).fetchone()
    return list(row["attempt_errors"])


# ── ① un échec relançable inscrit sa tentative ────────────────────────────────

def test_un_echec_inscrit_sa_tentative_numerotee(live):
    from oto_mcp import db as d
    org, job_id = 9301, None
    job_id = _job_claime(org)

    d.complete_job(job_id, WORKER, False, error="fin_anormale (max_tokens)")

    (t,) = _tentatives(job_id)
    assert t["attempt"] == 1, f"la tentative se NOMME, elle ne se devine pas — {t!r}"
    assert t["error"] == "fin_anormale (max_tokens)"
    assert t["at"], "une tentative porte son instant : trois motifs identiques à " \
                    "une heure d'écart ne disent pas la même chose qu'en rafale"


# ── ② les tentatives s'AJOUTENT ───────────────────────────────────────────────

def test_les_trois_tentatives_sont_toutes_lisibles(live):
    """LE banc. Trois motifs DIFFÉRENTS : si l'un écrasait l'autre, seul le
    dernier survivrait — c'est exactement ce qu'on corrige."""
    from oto_mcp import db as d
    org = 9302
    job_id = _job_claime(org, max_attempts=3)

    d.complete_job(job_id, WORKER, False, error="fin_anormale (max_tokens)")
    _reclaim(org, job_id, 2)
    d.complete_job(job_id, WORKER, False, error="fin_anormale (pause_turn)")
    _reclaim(org, job_id, 3)
    out = d.complete_job(job_id, WORKER, False, error="appel_outil_mal_encode (data_write)")
    assert out["status"] == "failed", out

    faites = _tentatives(job_id)
    assert [t["attempt"] for t in faites] == [1, 2, 3], faites
    assert [t["error"] for t in faites] == [
        "fin_anormale (max_tokens)",
        "fin_anormale (pause_turn)",
        "appel_outil_mal_encode (data_write)",
    ], "les trois motifs, dans l'ordre — aucun n'en écrase un autre"


# ── ③ un travail qui RÉUSSIT garde la trace de ses échecs ──────────────────────

def test_une_reussite_tardive_n_efface_pas_les_echecs_d_avant(live):
    """Le cas le plus discret : un travail `done` au troisième essai ne gardait
    AUCUNE trace des deux premiers — `last_error` est remis à NULL au succès.
    Deux échecs sur trois est un incident, pas un travail sain."""
    from oto_mcp import db as d
    org = 9303
    job_id = _job_claime(org, max_attempts=3)

    d.complete_job(job_id, WORKER, False, error="t1")
    _reclaim(org, job_id, 2)
    d.complete_job(job_id, WORKER, False, error="t2")
    _reclaim(org, job_id, 3)
    out = d.complete_job(job_id, WORKER, True, result={"usage_tokens": 12})
    assert out["status"] == "done", out

    faites = _tentatives(job_id)
    assert [t["error"] for t in faites] == ["t1", "t2"], (
        "le succès efface `last_error` — il n'efface pas le JOURNAL des tentatives")


# ── ④ un succès d'emblée n'invente rien ───────────────────────────────────────

def test_un_succes_d_emblee_ne_porte_aucune_tentative(live):
    """`[]` est un vrai vide : rien n'a échoué. Pas un `null` qu'on lirait
    « non mesuré »."""
    from oto_mcp import db as d
    org = 9304
    job_id = _job_claime(org)
    d.complete_job(job_id, WORKER, True)
    assert _tentatives(job_id) == []


# ── ⑤ le motif est borné, et un motif vide garde sa ligne ─────────────────────

def test_le_motif_est_borne_comme_last_error(live):
    from oto_mcp import db as d
    org = 9305
    job_id = _job_claime(org)
    d.complete_job(job_id, WORKER, False, error="x" * 900)
    (t,) = _tentatives(job_id)
    assert len(t["error"]) == 500, (
        "500 caractères, la borne de `last_error` — une trace de diagnostic ne "
        "doit pas pouvoir faire grossir la ligne sans limite")


def test_un_echec_SANS_motif_garde_quand_meme_sa_ligne(live):
    """Une tentative sans motif reste une tentative : c'est le COMPTE qui dit
    qu'un travail s'est débattu trois fois, et il ne doit pas dépendre du soin
    qu'un worker a mis à se décrire."""
    from oto_mcp import db as d
    org = 9306
    job_id = _job_claime(org)
    d.complete_job(job_id, WORKER, False, error=None)
    (t,) = _tentatives(job_id)
    assert t["attempt"] == 1 and t["error"] == "échec non détaillé", t


# ── ⑥ la livraison sert de quoi REJOUER ───────────────────────────────────────

def test_une_livraison_sert_le_corps_du_travail_et_ses_tentatives(live):
    """Rejouer une livraison morte demandait de DEVINER ce qu'elle portait — et un
    corps deviné ne reproduit pas la panne qu'on cherche. Le corps n'est pas
    recopié sur la livraison (il vit sur le travail) : il se LIT en joignant."""
    from oto_mcp import db as d
    from oto_mcp.db._conn import _connect
    org = 9307
    t = d.create_trigger(org, SUB, procedure="pipeline", tz="UTC",
                         tools=["data_write"], kind="webhook")
    j = d.enqueue_job(org, "start",
                      payload={"procedure": "pipeline",
                               "input": "Lis la procédure et applique-la.\n"
                                        "<corps>{\"account_id\": \"A-1\"}</corps>"})
    with _connect() as conn:
        d.enregistrer(conn, t["id"], org, "queued", job_id=j["id"], source="n8n")
    claime = d.claim_next_job(org, WORKER, lease_seconds=60)
    assert claime and claime["id"] == j["id"]
    d.complete_job(j["id"], WORKER, False, error="fin_anormale (max_tokens)")

    (sans,) = d.livraisons(t["id"], org)
    assert "job_input" not in sans, (
        "le corps ne part PAS par défaut : 200 corps de 4 Ko pour « a-t-il "
        "tourné » sont un transfert, pas un relevé")
    (livraison,) = d.livraisons(t["id"], org, avec_corps=True)
    assert "A-1" in (livraison["job_input"] or ""), (
        f"demandé, le corps reçu se relit sur la livraison — {livraison['job_input']!r}")
    assert [x["error"] for x in (livraison["job_attempt_errors"] or [])] == [
        "fin_anormale (max_tokens)"], livraison["job_attempt_errors"]


def test_une_livraison_REFUSEE_ne_porte_ni_corps_ni_tentative(live):
    """Un refus n'a produit aucun travail : `null` des deux côtés, jamais un `[]`
    qui se lirait « un travail qui n'a pas échoué »."""
    from oto_mcp import db as d
    from oto_mcp.db._conn import _connect
    org = 9308
    t = d.create_trigger(org, SUB, procedure="pipeline-2", tz="UTC",
                         tools=["a"], kind="webhook")
    with _connect() as conn:
        d.enregistrer(conn, t["id"], org, "refused_paused")

    (livraison,) = d.livraisons(t["id"], org, avec_corps=True)
    assert livraison["job_input"] is None
    assert livraison["job_attempt_errors"] is None
