"""Part équitable au claim (27/10/2026) : un worker de plateforme sert d'abord l'org qui a
le MOINS de travaux en vol, puis le plus ancien dû. Une org qui enfile une rafale ne retient
plus les autres derrière elle ; seule à attendre, elle garde toutes les places. En SQL,
donc sur vraie base.

⚠️ Un claim de PLATEFORME prend le travail de n'importe quelle org : chaque banc utilise
des orgs qui lui sont propres et vide leur file avant de conclure.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_claim_equitable_" + uuid.uuid4().hex[:8]
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


def _prendre(*orgs):
    from oto_mcp import db
    return db.claim_next_job(None, "w-parc", lease_seconds=600, org_ids=list(orgs))


def _vider(*orgs):
    while _prendre(*orgs):
        pass


def _bail_mort(job_id):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE runner_jobs SET lease_until = NOW() - interval '1 minute' "
                     "WHERE id = %s", (job_id,))


def test_une_rafale_ne_retient_pas_l_autre_org(live):
    rafale = [_enfiler(9301) for _ in range(3)]   # les plus anciens
    seul = _enfiler(9302)
    premier = _prendre(9301, 9302)
    assert premier["id"] == rafale[0]              # à égalité : le plus ancien dû
    # 9301 a un travail en vol, 9302 aucun : 9302 passe devant le reste de la rafale.
    assert _prendre(9301, 9302)["id"] == seul
    assert [_prendre(9301, 9302)["id"] for _ in range(2)] == rafale[1:]
    assert _prendre(9301, 9302) is None


def test_seule_a_attendre_une_org_prend_toutes_les_places(live):
    ids = [_enfiler(9303) for _ in range(4)]
    assert [_prendre(9303)["id"] for _ in range(4)] == ids
    assert _prendre(9303) is None


def test_les_orgs_prennent_leur_tour(live):
    a = [_enfiler(9304) for _ in range(3)]
    b = [_enfiler(9305) for _ in range(3)]
    pris = [_prendre(9304, 9305)["id"] for _ in range(6)]
    assert pris == [a[0], b[0], a[1], b[1], a[2], b[2]]


def test_un_bail_mort_ne_compte_pas_comme_en_vol(live):
    tenu = _enfiler(9306)
    assert _prendre(9306)["id"] == tenu
    _bail_mort(tenu)                 # son worker est mort : rien ne tourne pour 9306
    plus_ancien = _enfiler(9306)
    _enfiler(9307)
    # 9306 n'a rien de VIVANT en vol : à égalité, le plus ancien dû (le bail mort,
    # repris, puis son travail suivant) passe d'abord, comme avant.
    assert _prendre(9306, 9307)["id"] == tenu
    assert _prendre(9306, 9307)["org_id"] == 9307
    assert _prendre(9306, 9307)["id"] == plus_ancien
    _vider(9306, 9307)
