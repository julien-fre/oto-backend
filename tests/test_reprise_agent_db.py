"""La reprise d'un agent, EN BASE (`db.reprendre_trigger`, 25/09/2026).

Ce qui ne se juge qu'en SQL, sur une vraie base :

1. le déclencheur change de propriétaire ;
2. ses travaux EN ATTENTE (`pending`, `held`) le suivent — c'est leur `sub` qui fixe
   le jeton du run et l'abonnement qui paie ;
3. un travail déjà PRIS reste à l'identité qui l'a pris, et les travaux des AUTRES
   déclencheurs ne bougent pas ;
4. un déclencheur inconnu dans l'org rend None, sans rien écrire.

Les règles de la capacité (admin seulement, garde d'abonnement) ont leur banc :
`test_reprise_agent.py`.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_reprise_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "6" * 64
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


def _personne(sub):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO users (sub) VALUES (%s) ON CONFLICT DO NOTHING", (sub,))
    return sub


def _org(nom):
    from oto_mcp import org_store
    admin = _personne(f"{nom}-admin")
    oid = org_store.create_org(f"Org {nom}", created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    return oid, admin


def _agent(oid, sub):
    from oto_mcp import db
    return db.create_trigger(oid, sub, procedure="p", tz="Europe/Paris", tools=["t"],
                             cron="0 8 * * *", model="sub:sonnet")


def _travail(oid, sub, trigger_id, statut="pending"):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    job = db.enqueue_job(oid, "start", payload={"trigger_id": trigger_id}, sub=sub)
    if statut != "pending":
        with _connect() as conn:
            conn.execute("UPDATE runner_jobs SET status = %s WHERE id = %s",
                         (statut, job["id"]))
    return job["id"]


def _sub_du_travail(job_id):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT sub FROM runner_jobs WHERE id = %s",
                            (job_id,)).fetchone()["sub"]


def test_le_declencheur_et_ses_travaux_EN_ATTENTE_changent_de_proprietaire(live):
    from oto_mcp import db
    oid, admin = _org("a")
    ancien = _personne("a-ancien")
    t = _agent(oid, ancien)
    en_attente = _travail(oid, ancien, t["id"])
    retenu = _travail(oid, ancien, t["id"], "held")

    nouveau, precedent, deplaces = db.reprendre_trigger(t["id"], oid, admin)

    assert nouveau["sub"] == admin
    assert db.get_trigger(t["id"], oid)["sub"] == admin
    assert precedent == ancien
    assert deplaces == 2
    assert _sub_du_travail(en_attente) == admin
    assert _sub_du_travail(retenu) == admin


def test_un_travail_PRIS_et_ceux_d_un_AUTRE_agent_ne_bougent_pas(live):
    from oto_mcp import db
    oid, admin = _org("b")
    ancien = _personne("b-ancien")
    t = _agent(oid, ancien)
    autre = _agent(oid, ancien)
    pris = _travail(oid, ancien, t["id"], "claimed")
    d_un_autre = _travail(oid, ancien, autre["id"])

    _, _, deplaces = db.reprendre_trigger(t["id"], oid, admin)

    assert deplaces == 0
    assert _sub_du_travail(pris) == ancien
    assert _sub_du_travail(d_un_autre) == ancien
    assert db.get_trigger(autre["id"], oid)["sub"] == ancien


def test_un_declencheur_d_une_AUTRE_org_est_inconnu_et_rien_ne_s_ecrit(live):
    from oto_mcp import db
    oid, _ = _org("c")
    autre_org, admin_ailleurs = _org("d")
    ancien = _personne("c-ancien")
    t = _agent(oid, ancien)

    assert db.reprendre_trigger(t["id"], autre_org, admin_ailleurs) is None
    assert db.get_trigger(t["id"], oid)["sub"] == ancien
