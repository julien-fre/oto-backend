"""L'identité qu'un travail PORTE — le préalable du worker mutualisé.

Aujourd'hui un agent s'authentifie avec le jeton d'une organisation : c'est ce
qui impose mécaniquement **un worker par organisation**, et c'est ce qui a laissé
41 travaux programmés sans personne pour les prendre (#814). Ce n'est pas un
choix d'architecture qu'on pourrait discuter — c'est un empêchement.

**Un travail qui porte son identité dispense le worker d'en avoir une par
organisation.** Ce lot pose la brique : le travail sait au nom de qui il devra
être exécuté. Il ne change encore aucune authentification.

⚠️ Testé SUR LA ROUTE et sur une vraie base : une colonne ajoutée au schéma sans
être servie est un champ inerte de plus, et il s'en est déjà trouvé un dans cette
même table — `fleet_id` a existé sans le moindre écrivain servi.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

ROUTE = "/api/me/runner/jobs"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@ident.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_ident_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(),
                                                              mcp_instance=None)))


@pytest.fixture(scope="module")
def org(live):
    from oto_mcp import db, org_store
    membre = "usr_ident_membre"
    db.upsert_user(membre, email=f"{membre}@ident.invalid", name=membre)
    oid = org_store.create_org("Org des identités", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    org_store.set_active_org(membre, oid)
    return {"id": oid, "membre": membre}


def test_un_travail_enfile_porte_l_identite_de_qui_l_a_demande(client, org):
    """Le fait de base : sans lui, aucune délégation n'est possible — on ne sait
    même pas au nom de qui l'agent devrait agir."""
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "enqueue", "kind": "start",
                          "payload": {"procedure": "veille"}})
    assert r.status_code == 200, r.text
    assert r.json()["sub"] == org["membre"]


def test_l_identite_vient_de_l_etat_serveur_jamais_du_corps(client, org):
    """⚠️ La garde qui compte. Un travail dont l'appelant choisirait le porteur
    serait une usurpation en une ligne de JSON — et elle passerait inaperçue,
    puisque le travail s'exécuterait normalement, sous un autre nom."""
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "enqueue", "kind": "start",
                          "sub": "usr_quelquun_dautre",
                          "payload": {"procedure": "veille"}})
    # Que le champ soit refusé ou ignoré, une seule chose importe : il ne PASSE pas.
    if r.status_code == 200:
        assert r.json()["sub"] == org["membre"], "le corps a choisi le porteur"
    else:
        assert r.status_code in (400, 422), r.text


def test_le_claim_rend_l_identite_au_worker(client, org):
    """Sans elle dans la réponse du claim, le worker ne peut rien en faire : il
    aurait l'identité en base et pas dans les mains."""
    from oto_mcp import db
    pris = db.claim_next_job(org["id"], "un-worker")
    assert pris is not None and pris["sub"] == org["membre"]


def test_un_travail_de_declencheur_porte_le_sub_du_createur(client, org):
    """C'est le cas RÉEL : personne n'enfile à la main, c'est le tick qui le fait.
    Le tick n'a pas d'identité propre — il est une horloge, pas un acteur."""
    import datetime

    from oto_mcp import db, runner_tick

    # ⚠️ Échéance à la SECONDE ronde, comme en production : le compare-and-swap
    # du tick compare l'échéance qu'il a lue à celle qui est en base, et la
    # lecture ne rend pas les microsecondes. Une échéance qui en porte ne serait
    # jamais consommée — le déclencheur resterait dû sans jamais partir. En vrai
    # elles viennent de croniter, qui n'en produit pas.
    t = db.create_trigger(
        org["id"], org["membre"], procedure="veille", cron="0 4 * * *",
        tz="Europe/Paris",
        next_due=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0),
        tools=["data_write"], label="veille de nuit")
    runner_tick._tick()

    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "list", "status": "pending"})
    assert r.status_code == 200, r.text
    jobs = [j for j in r.json()["jobs"]
            if (j.get("payload") or {}).get("trigger_id") == t["id"]]
    assert jobs, "le tick n'a rien enfilé pour ce déclencheur"
    assert all(j["sub"] == org["membre"] for j in jobs)


def test_un_travail_ancien_garde_une_identite_INCONNUE(client, org):
    """⚠️ NULL et pas un défaut. Les travaux d'avant ce lot n'ont pas de créateur
    connu ; leur en inventer un — le premier admin, un compte de service — donnerait
    un nom qui se lirait comme un fait. **Un « je ne sais pas » explicite vaut
    mieux qu'une réponse fausse**, et c'est celui-là qu'on pourra corriger."""
    from oto_mcp import db
    ancien = db.enqueue_job(org["id"], "start", payload={"procedure": "x"})
    assert ancien.get("sub") is None
    assert db.get_job(ancien["id"], org["id"])["sub"] is None
