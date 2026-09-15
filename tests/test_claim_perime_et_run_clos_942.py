"""La fusion de la PR #942 (péremption d'un travail webhook) avec le tronc qui
l'a doublée (détachement d'un `start` dont le run est CLOS) dans `claim_next_job` —
LA MÊME réservation doit tenir les deux garanties À LA FOIS.

Les deux bancs existent déjà, séparément :
- `test_declencheur_webhook_db.py::test_un_travail_trop_VIEUX_perime_au_lieu_de_partir`
  pour la péremption (#942) ;
- `test_claim_detache_run_clos.py` pour le détachement (tronc, 13/09/2026).

Mais chacun enfile UN SEUL genre de travail dans une file par ailleurs vide : un
merge qui aurait mécaniquement perdu l'un des deux blocs `UPDATE` de
`claim_next_job` (ils se suivent, au même endroit, dans le même `with _connect()`)
les aurait laissés verts tous les deux — aucun des deux n'aurait vu l'absence de
l'autre. Le seul test qui l'attrape est celui-ci : un `pending` périmé ET un
`start` détachable dans la MÊME file, réservés par le MÊME appel.
"""
from __future__ import annotations

import os
import uuid

import pytest

ORG = 9421
WORKER = "worker-942-fusion"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_942_fusion_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        if avant_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = avant_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture(autouse=True)
def _file_vide(live):
    """Une file vide par cas : la réservation prend le plus ancien travail de
    l'org, et un job laissé par un cas précédent fausserait lequel est choisi."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM runner_jobs")
        conn.execute("DELETE FROM tool_calls")
        conn.execute("DELETE FROM runs")


def test_perime_et_run_clos_dans_le_meme_claim(live):
    """Un seul appel à `claim_next_job`, deux travaux `pending` dans la même org :

    A porte `_perime_apres_s` DÉPASSÉ → périmé, JAMAIS rendu.
    B est un `start` déjà tenté une fois, dont le run lié est CLOS → réservé,
    `run_id` VIDÉ, la trace de détachement posée dans son payload.

    Les deux faits sont vérifiés sur le résultat du MÊME appel, pas sur deux
    tests qui ne se recouvrent jamais."""
    from oto_mcp import db

    # ── B d'abord : un premier passage complet, pour arriver à « tenté, en attente
    # de retry, lié à un run qui vient de se clore » — exactement l'état où le
    # détachement doit mordre à la reprise.
    b = db.enqueue_job(ORG, "start", payload={"procedure": "p-detache",
                                              "input": "fusion #942"})
    t1 = db.claim_next_job(ORG, WORKER, lease_seconds=60)
    assert t1 and t1["id"] == b["id"], t1

    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=WORKER, org_id=ORG, label="fusion #942")
    db.insert_tool_call({"tool": "run_start", "sub": WORKER, "org_id": ORG,
                         "run_id": run_id, "args": {"label": "fusion #942"},
                         "ok": True})
    assert db.bind_job_run(b["id"], WORKER, run_id)

    db.finish_run(run_id, "failed", sub=WORKER)
    db.insert_tool_call({"tool": "run_finish", "sub": WORKER, "org_id": ORG,
                         "run_id": run_id,
                         "args": {"run_id": run_id, "outcome": "failed"},
                         "ok": True})
    assert db.run_closed_at(run_id) is not None

    out = db.complete_job(b["id"], WORKER, ok=False, run_id=run_id,
                          error="échec (banc fusion #942)")
    assert out["status"] == "pending", out
    # Backoff écoulé : sans ça, la ligne resterait due dans le futur et ne
    # serait pas vue par le claim qui suit — comme `_backoff_ecoule` ailleurs.
    with db._connect() as conn:
        conn.execute("UPDATE runner_jobs SET due_at = NOW() WHERE id = %s",
                     (b["id"],))

    # ── A ensuite : un `start` neuf, périmé — jamais tenté, jamais réservé.
    a = db.enqueue_job(ORG, "start", payload={"procedure": "p-perime",
                                              "input": "fusion #942"},
                       perime_apres_s=60)
    with db._connect() as conn:
        conn.execute("UPDATE runner_jobs SET created_at = NOW() - INTERVAL '2 hours' "
                     "WHERE id = %s", (a["id"],))

    # ── LE claim combiné : A et B sont tous deux `pending`, dus, dans la MÊME org.
    pris = db.claim_next_job(ORG, WORKER, lease_seconds=60)

    # Garantie #942 : A ne part jamais, et devient visible `expired` — pas juste
    # silencieusement absent du résultat (qui pourrait aussi vouloir dire « pas
    # encore son tour »).
    a_relu = db.get_job(a["id"], ORG)
    assert a_relu["status"] == "expired", a_relu
    assert "périmée" in (a_relu["last_error"] or ""), a_relu

    # Garantie du tronc : c'est B qui est rendu — si A avait été servi malgré sa
    # péremption, ou si B avait été écarté à tort, cette assertion le dit.
    assert pris and pris["id"] == b["id"], (
        f"seul B est éligible une fois A périmé : rendu {pris!r}")
    assert pris["run_id"] is None, (
        f"le run {run_id} est clos : la reprise devait le détacher — "
        f"servi {pris['run_id']!r}")
    trace = ((pris["payload"] or {}).get("_plateforme") or {}).get(
        "runs_detaches") or []
    assert [(e["run_id"], e["raison"]) for e in trace] == [(run_id, "run_clos")], trace

    # Le second appel ne rend rien : A est `expired`, B vient d'être pris — la
    # péremption n'a pas non plus laissé une ligne fantôme derrière elle.
    assert db.claim_next_job(ORG, WORKER, lease_seconds=60) is None
