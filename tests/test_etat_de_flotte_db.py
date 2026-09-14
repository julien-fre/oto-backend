"""Ce que l'état d'un passage dit de l'issue de ses travaux, en SQL RÉEL (oto#243, oto#244).

`done` recouvrait « une ligne traitée » et « rien trouvé », et `usage_tokens` sommait
en ignorant les inconnus. Deux sources, et chacune a son propriétaire :

- ce que la FILE a rendu à un run, compté par la plateforme dans la transaction de la
  réservation (`runs.lignes_reservees`) — le worker ne sait pas ce qu'est une ligne ;
- ce que le worker déclare tel quel : comptes d'appels, motif d'arrêt, usage.

C'est la requête elle-même qui est en cause : base éphémère, vraie réservation, vrai
`complete_job`.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

WORKER = "worker-etat"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_etat_flotte_" + uuid.uuid4().hex[:8]
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


@pytest.fixture
def flotte(live):
    """Une org PAR CAS (la réservation de travail prend le plus ancien de l'org), et
    un tableau à deux lignes libres."""
    from oto_mcp import db, org_store
    from oto_mcp.db._conn import _connect
    sub = "etat_" + uuid.uuid4().hex[:8]
    db.upsert_user(sub)
    org = org_store.create_org("org_" + uuid.uuid4().hex[:8], created_by=sub)
    f = db.create_fleet(org, sub, label="passage", procedure="p",
                        tools=["data_claim_next"])
    ns_id = db.create_datastore("user", sub, "file-" + uuid.uuid4().hex[:6])
    with _connect() as conn:
        for i in range(2):
            conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) VALUES (%s, %s, %s)",
                         (ns_id, f"ligne-{i}", json.dumps({"statut": "a_enrichir"})))
    return {"org": org, "sub": sub, "id": int(f["id"]), "ns_id": ns_id}


def _run(flotte, *, avant_la_mesure=False) -> str:
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=flotte["sub"], org_id=flotte["org"], label="travail")
    if avant_la_mesure:
        with _connect() as conn:
            conn.execute("UPDATE runs SET lignes_reservees = NULL WHERE run_id = %s", (run_id,))
    return run_id


def _lignes_du_run(run_id: str):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT lignes_reservees FROM runs WHERE run_id = %s",
                            (run_id,)).fetchone()["lignes_reservees"]


def _reserver(flotte, run_id):
    from oto_mcp import db
    return db.datastore_claim_next(flotte["ns_id"], worker="w", run_id=run_id)


def _conclu(flotte, result, *, run_id=None, ok=True):
    from oto_mcp import db
    j = db.enqueue_job(flotte["org"], "start", payload={"procedure": "p"},
                       max_attempts=1, fleet_id=flotte["id"])
    pris = db.claim_next_job(flotte["org"], WORKER, lease_seconds=60)
    assert pris and pris["id"] == j["id"], "la file portait ce travail : le claim le rend"
    assert db.complete_job(int(j["id"]), WORKER, ok, run_id=run_id,
                           error=None if ok else "borne", result=result)


def _etat(flotte) -> dict:
    from oto_mcp import db
    return db.fleet_state(flotte["id"], flotte["org"])["state"]


APPEL = {"tool_counts": {"oto_procedure": 1, "data_claim_next": 1},
         "stopped": "end_turn", "usage_tokens": 900}


# ══ la file compte ce qu'elle rend ═════════════════════════════════════════════

def test_la_file_compte_au_run_chaque_ligne_rendue_et_rien_d_autre(flotte):
    run = _run(flotte)
    assert _lignes_du_run(run) == 0, "un run né après la mesure est mesuré dès sa naissance"

    assert _reserver(flotte, run) is not None
    assert _reserver(flotte, run) is not None
    assert _reserver(flotte, run) is None, "le tableau n'a que deux lignes"

    assert _lignes_du_run(run) == 2


def test_un_run_d_avant_la_mesure_reste_non_mesure_meme_s_il_reserve(flotte):
    """`NULL + 1` reste NULL : jamais un compte partiel qui se lirait complet."""
    ancien = _run(flotte, avant_la_mesure=True)

    assert _reserver(flotte, ancien) is not None

    assert _lignes_du_run(ancien) is None


def test_une_reservation_sans_run_ne_compte_rien_et_ne_casse_rien(flotte):
    assert _reserver(flotte, None) is not None


# ══ l'état du passage ══════════════════════════════════════════════════════════

def test_travail_a_vide_travail_reel_et_non_mesure_se_distinguent(flotte):
    reel, vide, ancien = _run(flotte), _run(flotte), _run(flotte, avant_la_mesure=True)
    assert _reserver(flotte, reel) is not None
    _conclu(flotte, APPEL, run_id=reel)
    _conclu(flotte, APPEL, run_id=vide)
    _conclu(flotte, APPEL, run_id=ancien)
    _conclu(flotte, APPEL)                          # travail sans run

    e = _etat(flotte)

    assert (e["done"], e["empty_jobs"], e["reservation_unmeasured"]) == (4, 1, 2), (
        "un run d'avant la mesure, ou un travail sans run, n'est pas un travail à vide")


def test_un_travail_qui_n_appelle_pas_la_file_n_est_pas_a_vide(flotte):
    """Un passage sans tableau réserve zéro ligne sans être à vide pour autant."""
    _conclu(flotte, {"tool_counts": {"oto_procedure": 1}, "stopped": "end_turn"},
            run_id=_run(flotte))

    assert _etat(flotte)["empty_jobs"] == 0


def test_une_borne_atteinte_apres_une_ecriture_se_compte_a_part(flotte):
    ecrit = {"tool_counts": {"data_claim_next": 1, "data_write": 1},
             "stopped": "max_tokens", "usage_tokens": 180_000}
    _conclu(flotte, ecrit, ok=False)
    _conclu(flotte, {**ecrit, "tool_counts": {"data_claim_next": 1}}, ok=False)
    _conclu(flotte, {**ecrit, "stopped": "end_turn"})

    e = _etat(flotte)

    assert (e["failed"], e["stopped_after_write"]) == (2, 1)


def test_un_usage_inconnu_se_compte_et_le_total_ne_somme_que_le_connu(flotte):
    _conclu(flotte, {"usage_tokens": 1000})
    _conclu(flotte, {"usage_tokens": None})
    _conclu(flotte, {"stopped": "end_turn"})

    e = _etat(flotte)

    assert (e["usage_tokens"], e["usage_unknown"], e["heaviest_row_tokens"]) == (1000, 2, 1000)


def test_un_travail_en_vol_n_entre_dans_aucune_issue(flotte):
    from oto_mcp import db
    db.enqueue_job(flotte["org"], "start", payload={"procedure": "p"},
                   fleet_id=flotte["id"])

    e = _etat(flotte)

    assert (e["pending"], e["empty_jobs"], e["stopped_after_write"],
            e["reservation_unmeasured"], e["usage_unknown"]) == (1, 0, 0, 0, 0)


def test_un_resultat_mal_forme_ne_fait_pas_tomber_l_etat(flotte):
    _conclu(flotte, {"tool_counts": {"data_claim_next": "beaucoup", "data_write": "oui"},
                     "stopped": "max_tokens", "usage_tokens": "inconnu"},
            run_id=_run(flotte))

    e = _etat(flotte)

    assert (e["done"], e["empty_jobs"], e["stopped_after_write"],
            e["reservation_unmeasured"], e["usage_unknown"], e["usage_tokens"]) == (
        1, 0, 0, 0, 1, 0)
