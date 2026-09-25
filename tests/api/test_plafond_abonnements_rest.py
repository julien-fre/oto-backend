"""Le PLAFOND de consommation des abonnements, SUR LES ROUTES et sur une vraie base.

Décision du 25/09/2026 : l'org règle la part maximale de l'usage TOTAL du compte
(défaut 80 %), une personne peut la resserrer pour elle-même, jamais la relâcher.
Ces bancs tiennent les deux surfaces et leurs droits :

1. **l'org** — `GET|PUT /api/orgs/{id}/model-subscriptions/{family}` : lire est un
   geste de membre, régler un geste d'admin d'org ; `null` revient au défaut ;
2. **la personne** — `PATCH /api/me/model-subscriptions/{family}` : sa ligne
   seulement, 404 sans abonnement, rendue par la liste.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

_FAMILLE = "claude_subscription"
_ME = "/api/me/model-subscriptions"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@plafond.invalid", "name": sub}


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

    nom = "oto_plafond_rest_" + uuid.uuid4().hex[:8]
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
    admin, membre, dehors = "usr_plafond_admin", "usr_plafond_membre", "usr_plafond_dehors"
    for sub in (admin, membre, dehors):
        db.upsert_user(sub, email=f"{sub}@plafond.invalid", name=sub)
    oid = org_store.create_org("Org du plafond", created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    org_store.add_org_member(oid, membre, "org_member")
    return {"id": oid, "admin": admin, "membre": membre, "dehors": dehors,
            "route": f"/api/orgs/{oid}/model-subscriptions/{_FAMILLE}"}


@pytest.fixture
def sans_reglage(org):
    from oto_mcp.db import org_subscription_limits as OL
    OL.retirer_limite(org["id"], _FAMILLE)
    return org


# ── l'org ────────────────────────────────────────────────────────────────────
def test_un_membre_lit_le_DEFAUT_de_80(client, sans_reglage):
    r = client.get(sans_reglage["route"], headers=_h(sans_reglage["membre"]))
    assert r.status_code == 200, r.text
    assert r.json()["limit_pct"] == 80 and r.json()["default"] is True


def test_un_admin_regle_et_un_membre_le_lit(client, sans_reglage):
    o = sans_reglage
    r = client.put(o["route"], json={"limit_pct": 60}, headers=_h(o["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["limit_pct"] == 60 and r.json()["default"] is False
    assert r.json()["updated_by"] == o["admin"]
    lu = client.get(o["route"], headers=_h(o["membre"])).json()
    assert (lu["limit_pct"], lu["default"]) == (60, False)


def test_null_revient_au_defaut(client, sans_reglage):
    o = sans_reglage
    client.put(o["route"], json={"limit_pct": 50}, headers=_h(o["admin"]))
    r = client.put(o["route"], json={"limit_pct": None}, headers=_h(o["admin"]))
    assert r.status_code == 200, r.text
    assert (r.json()["limit_pct"], r.json()["default"]) == (80, True)


def test_un_MEMBRE_ne_regle_pas_le_plafond_de_l_org(client, sans_reglage):
    from oto_mcp.db import org_subscription_limits as OL
    o = sans_reglage
    r = client.put(o["route"], json={"limit_pct": 100}, headers=_h(o["membre"]))
    assert r.status_code == 403, r.text
    assert OL.get_limite(o["id"], _FAMILLE) is None


def test_hors_de_l_org_on_ne_lit_rien(client, org):
    assert client.get(org["route"], headers=_h(org["dehors"])).status_code == 403


@pytest.mark.parametrize("pct", [0, 101, -5])
def test_hors_bornes_400(client, sans_reglage, pct):
    from oto_mcp.db import org_subscription_limits as OL
    o = sans_reglage
    r = client.put(o["route"], json={"limit_pct": pct}, headers=_h(o["admin"]))
    assert r.status_code == 400 and r.json()["error"] == "invalid_limit", r.text
    assert OL.get_limite(o["id"], _FAMILLE) is None


def test_org_famille_inconnue_400(client, org):
    r = client.get(f"/api/orgs/{org['id']}/model-subscriptions/anthropic",
                   headers=_h(org["membre"]))
    assert r.status_code == 400 and r.json()["error"] == "unknown_family"


def test_org_inconnue_404(live, org):
    """Une org qui n'existe pas : la garde de palier refuse d'abord tout non-membre
    (403) ; le 404 est celui du handler, que seul un admin de plateforme atteint."""
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    from oto_mcp.capabilities.orgs import model_subscriptions as M
    with pytest.raises(AuthzDenied) as e:
        M._get_plafond(ResolvedCtx(sub=org["admin"], org_id=None),
                       M.GetOrgPlafondInput(org_id=987654321, family=_FAMILLE))
    assert (e.value.status, e.value.code) == (404, "unknown_org")


# ── la personne ──────────────────────────────────────────────────────────────
@pytest.fixture
def abonne(org):
    from oto_mcp.db import user_subscriptions as US
    for sub in (org["membre"], org["admin"]):
        US.upsert_sandbox(sub, _FAMILLE, f"sandbox-{sub}")
        US.marquer_statut(sub, _FAMILLE, US.CONNECTE, ok=True)
        US.poser_limite(sub, _FAMILLE, None)
    return org


def test_je_pose_MON_plafond_et_la_liste_le_rend(client, abonne):
    from oto_mcp.db import user_subscriptions as US
    moi, autre = abonne["membre"], abonne["admin"]
    r = client.patch(f"{_ME}/{_FAMILLE}", json={"limit_pct": 50}, headers=_h(moi))
    assert r.status_code == 200, r.text
    assert r.json()["limit_pct"] == 50 and r.json()["family"] == _FAMILLE
    (mien,) = client.get(_ME, headers=_h(moi)).json()["subscriptions"]
    assert mien["limit_pct"] == 50
    assert US.get_subscription(autre, _FAMILLE)["limite_pct"] is None, (
        "la ligne d'un autre n'a pas bougé")


def test_je_retire_mon_plafond_avec_null(client, abonne):
    moi = abonne["membre"]
    client.patch(f"{_ME}/{_FAMILLE}", json={"limit_pct": 40}, headers=_h(moi))
    r = client.patch(f"{_ME}/{_FAMILLE}", json={"limit_pct": None}, headers=_h(moi))
    assert r.status_code == 200 and r.json()["limit_pct"] is None, r.text


@pytest.mark.parametrize("pct", [0, 101])
def test_mon_plafond_hors_bornes_400(client, abonne, pct):
    r = client.patch(f"{_ME}/{_FAMILLE}", json={"limit_pct": pct},
                     headers=_h(abonne["membre"]))
    assert r.status_code == 400 and r.json()["error"] == "invalid_limit", r.text


def test_sans_abonnement_404_not_connected(client, org):
    r = client.patch(f"{_ME}/{_FAMILLE}", json={"limit_pct": 50},
                     headers=_h(org["dehors"]))
    assert r.status_code == 404 and r.json()["error"] == "not_connected", r.text


def test_mon_plafond_famille_inconnue_400(client, abonne):
    r = client.patch(f"{_ME}/anthropic", json={"limit_pct": 50},
                     headers=_h(abonne["membre"]))
    assert r.status_code == 400 and r.json()["error"] == "unknown_family"


def test_omettre_limit_pct_n_efface_rien(client, abonne):
    """`null` est un geste ; un corps vide est une erreur de forme, pas un retrait."""
    from oto_mcp.db import user_subscriptions as US
    moi = abonne["membre"]
    US.poser_limite(moi, _FAMILLE, 30)
    r = client.patch(f"{_ME}/{_FAMILLE}", json={}, headers=_h(moi))
    assert r.status_code == 400, r.text
    assert US.get_subscription(moi, _FAMILLE)["limite_pct"] == 30
