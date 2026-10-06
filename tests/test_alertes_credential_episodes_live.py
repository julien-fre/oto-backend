"""L'ÉPISODE d'une clé rouge, contre un VRAI PostgreSQL (signaux oto #1168, #1189).

Une clé refusée ou à sec est annoncée UNE fois par épisode : au premier passage qui la
voit rouge, puis plus jamais tant qu'elle reste rouge. Redevenue verte, sa prochaine
chute est un épisode neuf. Ce qui se prouve ici, et qu'un banc à doublures ne dirait
pas : les trois requêtes de `db.alertes_credential` sur le vrai `meta` JSONB, le
rattachement d'une clé de membre à son org, et l'oubli d'une clé `tenant`/`platform`.

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_alerte_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _exec(sql, params=()):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(sql, params)


def _coffre(lignes):
    _exec("DELETE FROM connector_credentials")
    for et, eid, connector, meta in lignes:
        _exec("INSERT INTO connector_credentials (entity_type, entity_id, connector, "
              "account, secret_enc, meta) VALUES (%s, %s, %s, '', 'x', %s::jsonb)",
              (et, eid, connector, json.dumps(meta)))


ROUGE = {"health_ko": True, "health_reason": "403", "health_verdict": None}


def test_un_episode_est_annonce_UNE_fois_puis_rouvert_apres_guerison(live):
    from oto_mcp.db import alertes_credential as A
    _coffre([("org", "7", "snitcher", ROUGE)])
    vues = A.cles_ko_a_annoncer()
    assert [(l["connector"], l["org_id"]) for l in vues] == [("snitcher", 7)]
    assert A.marquer_cles_annoncees(vues) == 1
    assert A.cles_ko_a_annoncer() == [], "toujours rouge, déjà annoncée : silence"

    # La clé guérit (sonde rejouée, crédits rechargés) : l'épisode se clôt...
    _exec("UPDATE connector_credentials SET meta = meta || "
          "'{\"health_ko\": false, \"health_reason\": null}'::jsonb")
    assert A.clore_episodes_gueris() == 1
    assert A.cles_ko_a_annoncer() == []
    # ... et sa prochaine chute est un épisode NEUF, donc annoncé de nouveau.
    _exec("UPDATE connector_credentials SET meta = meta || %s::jsonb",
          (json.dumps(ROUGE),))
    assert [l["connector"] for l in A.cles_ko_a_annoncer()] == ["snitcher"]


def test_une_cle_de_MEMBRE_remonte_a_son_org(live):
    from oto_mcp.db import alertes_credential as A
    _coffre([("member", "12:usr_x", "theirstack",
              {**ROUGE, "health_verdict": "no_quota"})])
    (l,) = A.cles_ko_a_annoncer()
    assert l["org_id"] == 12 and l["verdict"] == "no_quota"


def test_tenant_plateforme_et_cles_vertes_ne_sont_jamais_lues(live):
    """Une clé partagée au-delà d'une org n'est jamais peinte en rouge pour tout le
    monde (`connectors/health.FLAGGABLE_SCOPES`) ; elle n'a donc pas d'épisode ici."""
    from oto_mcp.db import alertes_credential as A
    _coffre([("tenant", "t", "jev", ROUGE), ("platform", "env", "serper", ROUGE),
             ("org", "7", "hunter", {"health_ko": False})])
    assert A.cles_ko_a_annoncer() == []


def test_marquer_ne_clot_pas_une_cle_reposee_entre_lecture_et_envoi(live):
    from oto_mcp.db import alertes_credential as A
    _coffre([("org", "7", "snitcher", ROUGE)])
    vues = A.cles_ko_a_annoncer()
    _exec("UPDATE connector_credentials SET meta = '{}'::jsonb")  # reposée
    assert A.marquer_cles_annoncees(vues) == 0
