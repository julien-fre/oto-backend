"""La péremption SUR LA ROUTE SERVIE — sérialisée, pas inspectée.

⚠️ **Ce fichier existe pour une raison précise et déjà vécue.** `expired_since` et
`expired_last` viennent de PostgreSQL en `timestamptz`, donc en `datetime` — et le
modèle servi les déclare en chaîne. Un type qui ne traverse pas la sérialisation
fait un **500 sur 100 % des appels**, et aucun test qui lit un dictionnaire de
handler ne le voit : c'est exactement ce qui est arrivé le 02/09 à `usage_tokens`,
rendu en `Decimal` par un `SUM(…)`.

**La seule garde qui ne peut pas se tromper d'axe : sérialiser une vraie réponse,
issue d'une vraie base.** Si un type ne passe pas, le test rougit — quel que soit
le nom du champ, et sans qu'on ait eu à prévoir lequel.

Et un second piège, propre à ce lot : le tick pose `status = 'expired'`, une
valeur que la contrainte CHECK d'une base **existante** refuse tant que `_init`
ne l'a pas remplacée. Un test sur base neuve ne le verrait pas.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

ROUTE = "/api/me/runner/triggers"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@perime.invalid", "name": sub}


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

    nom = "oto_perime_rest_" + uuid.uuid4().hex[:8]
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
    membre = "usr_perime_membre"
    db.upsert_user(membre, email=f"{membre}@perime.invalid", name=membre)
    oid = org_store.create_org("Org des péremptions", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    org_store.set_active_org(membre, oid)
    return {"id": oid, "membre": membre}


@pytest.fixture(scope="module")
def declencheur(live, org):
    """Un déclencheur posé EN BASE — la route le refuserait, faute de worker armé,
    et c'est précisément la situation qu'on veut reproduire."""
    from oto_mcp import db
    import datetime
    return db.create_trigger(
        org["id"], org["membre"], procedure="veille", cron="0 18 * * *",
        tz="Europe/Paris",
        next_due=datetime.datetime.now(datetime.timezone.utc),
        tools=["data_write"], label="veille du soir")


def test_zero_perdu_traverse_la_route(client, org, declencheur):
    """Avant toute péremption : le champ est SERVI à zéro. Absent, il se lirait
    « je n'ai pas regardé », et un lecteur prudent irait compter à la main."""
    r = client.post(ROUTE, headers=_h(org["membre"]), json={"op": "list"})
    assert r.status_code == 200, r.text
    t = next(t for t in r.json()["triggers"] if t["id"] == declencheur["id"])
    assert t["expired_count"] == 0
    assert t["expired_since"] is None and t["expired_last"] is None


def test_les_dates_de_perte_traversent_la_serialisation(client, org, declencheur):
    """⚠️ LE test de ce fichier. Deux occurrences réellement périmées par le tick,
    dans une vraie base, rendues par la vraie pile HTTP. Un `timestamptz` qui ne
    se sérialise pas ferait ici un 500 — sur 100 % des appels."""
    from oto_mcp import db, runner_tick

    for _ in range(2):
        db.enqueue_job(org["id"], "start",
                       payload={"procedure": "veille",
                                "trigger_id": declencheur["id"]})
    # Le vrai geste du tick, sur la vraie contrainte CHECK de la vraie base.
    assert db.perimer_travaux_du_declencheur(declencheur["id"], org["id"]) == 2

    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "get", "trigger_id": declencheur["id"]})
    assert r.status_code == 200, r.text
    t = r.json()["trigger"]
    assert t["expired_count"] == 2
    # Des CHAÎNES, traversées de bout en bout — c'est ce que le modèle promet.
    assert isinstance(t["expired_since"], str) and t["expired_since"]
    assert isinstance(t["expired_last"], str) and t["expired_last"]


def test_ce_qui_est_perime_ne_se_reclame_plus(client, org, declencheur):
    """La conséquence qui compte : une occurrence périmée ne repart pas. Sinon le
    jour où des agents arrivent, treize jours partent d'un coup — avec le contexte
    de leur époque."""
    from oto_mcp import db
    assert db.claim_next_job(org["id"], "un-worker") is None


def test_un_travail_neuf_reste_reclamable(client, org, declencheur):
    """⚠️ Le contrôle symétrique, sans lequel le précédent ne prouve rien : une
    file vide donnerait le même `None`. Un travail frais, lui, DOIT partir."""
    from oto_mcp import db
    db.enqueue_job(org["id"], "start",
                   payload={"procedure": "veille", "trigger_id": declencheur["id"]})
    pris = db.claim_next_job(org["id"], "un-worker")
    # ⚠️ `claim_next_job` rend ce dont le worker a besoin pour TRAVAILLER (id,
    # kind, payload, bail) — pas le statut, qu'il vient lui-même de poser. Une
    # assertion sur `status` échouerait sur la FORME et masquerait le fait mesuré.
    assert pris is not None, "un travail frais doit partir — sinon le test précédent ne prouve rien"
    assert pris["payload"]["trigger_id"] == declencheur["id"]
    assert pris["lease_until"] is not None
