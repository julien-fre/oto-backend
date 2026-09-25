"""`org_ids` au claim : un worker ne réserve que les travaux de ces orgs — pour essayer un
moteur sur une organisation avant de le donner au parc. En SQL, donc sur vraie base.

⚠️ Un claim de PLATEFORME (`org_id=None`) prend le travail de n'importe quelle org : chaque
banc vide la file de ses orgs avant de conclure, et utilise des orgs qui lui sont propres.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities import runner_jobs as RJ


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_claim_org_" + uuid.uuid4().hex[:8]
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


def _enfiler(org):
    from oto_mcp import db
    return db.enqueue_job(org, "start", payload={"input": "go", "tools": []})["id"]


def _vider(*orgs):
    from oto_mcp import db
    while db.claim_next_job(None, "w-vidange", lease_seconds=60, org_ids=list(orgs)):
        pass


def test_le_worker_ne_prend_que_les_orgs_qu_il_nomme(live):
    from oto_mcp import db
    ancien = _enfiler(9201)            # le plus ancien : un claim sans filtre le prendrait
    nomme = _enfiler(9202)
    pris = db.claim_next_job(None, "w-essai", lease_seconds=60, org_ids=[9202])
    assert pris and pris["id"] == nomme and pris["org_id"] == 9202
    assert db.claim_next_job(None, "w-essai", lease_seconds=60, org_ids=[9202]) is None
    # L'autre org attend son worker : un claim sans filtre la prend, comme avant.
    assert db.claim_next_job(None, "w-parc", lease_seconds=60, org_ids=[9201])["id"] == ancien
    _vider(9201, 9202)


def test_sans_filtre_le_claim_est_celui_d_avant(live):
    from oto_mcp import db
    _vider(9203, 9204)
    premier = _enfiler(9203)
    _enfiler(9204)
    pris = db.claim_next_job(None, "w-parc", lease_seconds=60)
    assert pris["id"] == premier
    _vider(9203, 9204)


def test_un_appelant_scope_a_son_org_ne_voit_que_l_intersection(live):
    from oto_mcp import db
    _enfiler(9205)
    assert db.claim_next_job(9205, "w-org", lease_seconds=60, org_ids=[9206]) is None
    assert db.claim_next_job(9205, "w-org", lease_seconds=60, org_ids=[9205, 9206])
    _vider(9205, 9206)


def test_la_capacite_refuse_une_liste_vide():
    with pytest.raises(Exception):
        RJ.JobsInput(op="claim", org_ids=[])
    assert RJ.JobsInput(op="claim", org_ids=[9207]).org_ids == [9207]
    assert RJ.JobsInput(op="claim").org_ids is None
