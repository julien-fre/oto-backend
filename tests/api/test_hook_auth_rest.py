"""L'authentification par SIGNATURE, de bout en bout sur les routes SERVIES.

L'écran pose le mode et le secret (`PUT …/hook-auth`), puis la source livre
(`POST /api/hooks/{id}`). Ce que seule cette traversée prouve : que le champ de chemin
atteint la capacité, que le secret n'est jamais rendu, que le porteur est bien refusé
une fois le mode posé, et qu'une livraison signée — puis sa retentative — font UN
travail, en base réelle.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

SECRET = "whsec_" + base64.b64encode(b"cle de signature du banc rest").decode()


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@signature.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_hook_auth_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    avant = {k: os.environ.get(k) for k in ("DATABASE_URL", "OTO_MCP_MASTER_KEY")}
    pool_avant = dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        for k, v in avant.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(),
                                                              mcp_instance=None)))


@pytest.fixture(scope="module")
def agent(live):
    """Une org, son membre, et un agent webhook AU PORTEUR — l'état d'avant."""
    from oto_mcp import db, org_store, runner_hook
    membre = "usr_signature"
    db.upsert_user(membre, email=f"{membre}@signature.invalid", name=membre)
    oid = org_store.create_org("Org des signatures", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    org_store.set_active_org(membre, oid)
    t = db.create_trigger(oid, membre, procedure="appel-vers-crm", tz="UTC",
                          tools=["a"], kind="webhook", payload_mode="inline")
    porteur, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(t["id"], oid, hache)
    return {"id": t["id"], "org": oid, "membre": membre, "porteur": porteur}


def _signe(msg_id: str, corps: bytes, ts: str | None = None) -> dict:
    ts = ts or str(int(time.time()))
    cle = base64.b64decode(SECRET[len("whsec_"):])
    mac = hmac.new(cle, f"{msg_id}.{ts}.".encode() + corps, hashlib.sha256).digest()
    return {"webhook-id": msg_id, "webhook-timestamp": ts,
            "webhook-signature": "v1," + base64.b64encode(mac).decode(),
            "content-type": "application/json", "user-agent": "Granola-Webhooks"}


def test_1_poser_la_signature_sur_la_route_ne_rend_JAMAIS_le_secret(client, agent):
    r = client.put(f"/api/me/runner/triggers/{agent['id']}/hook-auth",
                   headers={"Authorization": f"Bearer {agent['membre']}"},
                   json={"hook_auth": "standard_webhooks", "signing_secret": SECRET})
    assert r.status_code == 200, r.text
    corps = r.json()
    assert corps["trigger"]["hook_auth"] == "standard_webhooks"
    assert corps["trigger"]["signing_secret_set"] is True
    assert SECRET not in r.text and SECRET[6:] not in r.text


def test_2_l_ancien_PORTEUR_est_refuse(client, agent):
    r = client.post(f"/api/hooks/{agent['id']}", json={"note_id": "not_1"},
                    headers={"Authorization": f"Bearer {agent['porteur']}"})
    assert r.status_code == 404


def test_3_une_livraison_SIGNEE_enfile_et_sa_RETENTATIVE_non(client, agent):
    from oto_mcp import db
    corps = json.dumps({"event_type": "note.generated", "note_id": "not_1"}).encode()
    r1 = client.post(f"/api/hooks/{agent['id']}", content=corps,
                     headers=_signe("evt_rest_1", corps))
    assert r1.status_code == 202, r1.text
    assert r1.json()["job_id"] and not r1.json().get("duplicate")
    r2 = client.post(f"/api/hooks/{agent['id']}", content=corps,
                     headers=_signe("evt_rest_1", corps))
    assert r2.status_code == 202 and r2.json()["duplicate"] is True
    assert r2.json()["job_id"] == r1.json()["job_id"]
    livrees = db.livraisons(agent["id"], agent["org"])
    assert [l["outcome"] for l in livrees] == ["queued"]


def test_4_une_signature_FAUSSE_rend_le_meme_404_qu_un_id_inconnu(client, agent):
    corps = b'{"note_id": "not_2"}'
    entetes = _signe("evt_rest_2", corps)
    entetes["webhook-signature"] = "v1,AAAA"
    faux = client.post(f"/api/hooks/{agent['id']}", content=corps, headers=entetes)
    inconnu = client.post(f"/api/hooks/{agent['id'] + 9999}", content=corps,
                          headers=_signe("evt_rest_2", corps))
    assert faux.status_code == inconnu.status_code == 404
    assert faux.json() == inconnu.json()


def test_5_un_REJEU_hors_fenetre_est_nomme(client, agent):
    corps = b'{"note_id": "not_3"}'
    r = client.post(f"/api/hooks/{agent['id']}", content=corps,
                    headers=_signe("evt_rest_3", corps,
                                   ts=str(int(time.time()) - 3600)))
    assert r.status_code == 400 and r.json()["error"] == "hook_stale_timestamp"


def test_6_revenir_au_porteur_rend_un_porteur_NEUF_qui_ouvre(client, agent):
    r = client.put(f"/api/me/runner/triggers/{agent['id']}/hook-auth",
                   headers={"Authorization": f"Bearer {agent['membre']}"},
                   json={"hook_auth": "bearer"})
    assert r.status_code == 200, r.text
    neuf = r.json()["hook_secret"]
    assert neuf.startswith("otoh_") and neuf != agent["porteur"]
    assert r.json()["trigger"]["signing_secret_set"] is False
    ok = client.post(f"/api/hooks/{agent['id']}", json={"note_id": "not_4"},
                     headers={"Authorization": f"Bearer {neuf}"})
    assert ok.status_code == 202
    ancien = client.post(f"/api/hooks/{agent['id']}", json={"note_id": "not_5"},
                         headers={"Authorization": f"Bearer {agent['porteur']}"})
    assert ancien.status_code == 404, "l'otoh_ d'avant la signature reste MORT"
