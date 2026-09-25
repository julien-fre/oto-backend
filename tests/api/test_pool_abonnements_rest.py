"""Le POOL d'org des abonnements, SUR LES ROUTES et sur une vraie base (25/09/2026).

1. **le mode de l'org** — `PUT /api/orgs/{id}/model-subscriptions/{family}/mode`
   (admin d'org), lu par le `GET` de la ressource avec `pool_size` (membre) ;
2. **le prêt** — `PATCH /api/me/model-subscriptions/{family}` `{"lent_to": [...]}` :
   opt-in, par org, aux seules orgs dont on est membre, rendu par la liste.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

_F = "claude_subscription"
_ME = "/api/me/model-subscriptions"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@pool.invalid", "name": sub}


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

    nom = "oto_pool_rest_" + uuid.uuid4().hex[:8]
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
    admin, membre, dehors = "usr_pool_admin", "usr_pool_membre", "usr_pool_dehors"
    for sub in (admin, membre, dehors):
        db.upsert_user(sub, email=f"{sub}@pool.invalid", name=sub)
    oid = org_store.create_org("Org du pool", created_by=admin)
    autre = org_store.create_org("Autre org", created_by=dehors)
    org_store.add_org_member(oid, admin, "org_admin")
    org_store.add_org_member(oid, membre, "org_member")
    org_store.add_org_member(autre, dehors, "org_admin")
    return {"id": oid, "autre": autre, "admin": admin, "membre": membre,
            "dehors": dehors,
            "route": f"/api/orgs/{oid}/model-subscriptions/{_F}",
            "mode": f"/api/orgs/{oid}/model-subscriptions/{_F}/mode"}


@pytest.fixture
def option(monkeypatch):
    """Prêter exige l'option `claude_subscription`, comme se connecter."""
    from oto_mcp.capabilities import _abonnement
    ouverts = set()
    monkeypatch.setattr(_abonnement.access, "has_option",
                        lambda sub, opt, **k: opt == _abonnement.OPTION and sub in ouverts)
    return ouverts


@pytest.fixture
def abonne(org):
    from oto_mcp.db import org_subscription_pool as P
    from oto_mcp.db import user_subscriptions as US
    moi = org["membre"]
    US.upsert_sandbox(moi, _F, f"sandbox-{moi}")
    US.marquer_statut(moi, _F, US.CONNECTE, ok=True)
    P.poser_prets(moi, _F, [])
    return org


# ── le mode de l'org ─────────────────────────────────────────────────────────
def test_le_mode_par_defaut_est_personnel(client, org):
    from oto_mcp.db import org_subscription_pool as P
    P.poser_mode(org["id"], _F, "personnel", org["admin"])
    lu = client.get(org["route"], headers=_h(org["membre"])).json()
    assert (lu["mode"], lu["pool_size"]) == ("personnel", 0)


def test_un_admin_passe_l_org_en_pool_et_un_membre_le_lit(client, org, abonne, option):
    o = org
    r = client.put(o["mode"], json={"mode": "pool"}, headers=_h(o["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "pool" and r.json()["pool_size"] == 0
    option.add(o["membre"])
    client.patch(f"{_ME}/{_F}", json={"lent_to": [o["id"]]}, headers=_h(o["membre"]))
    lu = client.get(o["route"], headers=_h(o["membre"])).json()
    assert (lu["mode"], lu["pool_size"]) == ("pool", 1)
    assert lu["limit_pct"] == 80, "le plafond n'a pas bougé : un geste par réglage"


def test_un_MEMBRE_ne_change_pas_le_mode(client, org):
    from oto_mcp.db import org_subscription_pool as P
    P.poser_mode(org["id"], _F, "personnel", org["admin"])
    r = client.put(org["mode"], json={"mode": "pool"}, headers=_h(org["membre"]))
    assert r.status_code == 403, r.text
    assert P.get_mode(org["id"], _F)["mode"] == "personnel"


def test_un_mode_hors_contrat_400(client, org):
    r = client.put(org["mode"], json={"mode": "tous"}, headers=_h(org["admin"]))
    assert r.status_code == 400, r.text


def test_mode_famille_inconnue_400(client, org):
    r = client.put(f"/api/orgs/{org['id']}/model-subscriptions/anthropic/mode",
                   json={"mode": "pool"}, headers=_h(org["admin"]))
    assert r.status_code == 400 and r.json()["error"] == "unknown_family"


# ── le prêt ──────────────────────────────────────────────────────────────────
def test_je_prete_a_MON_org_et_la_liste_le_rend(client, abonne, option):
    o = abonne
    option.add(o["membre"])
    r = client.patch(f"{_ME}/{_F}", json={"lent_to": [o["id"]]}, headers=_h(o["membre"]))
    assert r.status_code == 200, r.text
    assert r.json()["lent_to"] == [o["id"]]
    (mien,) = client.get(_ME, headers=_h(o["membre"])).json()["subscriptions"]
    assert mien["lent_to"] == [o["id"]]


def test_rien_n_est_prete_par_defaut(client, abonne):
    (mien,) = client.get(_ME, headers=_h(abonne["membre"])).json()["subscriptions"]
    assert mien["lent_to"] == []


def test_je_retire_mon_pret(client, abonne, option):
    o = abonne
    option.add(o["membre"])
    client.patch(f"{_ME}/{_F}", json={"lent_to": [o["id"]]}, headers=_h(o["membre"]))
    r = client.patch(f"{_ME}/{_F}", json={"lent_to": []}, headers=_h(o["membre"]))
    assert r.status_code == 200 and r.json()["lent_to"] == [], r.text


def test_retirer_ne_demande_pas_l_option(client, abonne, option):
    """Prêter est réservé aux personnes nommées ; se retirer, jamais."""
    r = client.patch(f"{_ME}/{_F}", json={"lent_to": []}, headers=_h(abonne["membre"]))
    assert r.status_code == 200, r.text


def test_preter_sans_l_option_403(client, abonne, option):
    r = client.patch(f"{_ME}/{_F}", json={"lent_to": [abonne["id"]]},
                     headers=_h(abonne["membre"]))
    assert r.status_code == 403 and r.json()["error"] == "subscription_not_enabled"


def test_on_ne_prete_pas_a_une_org_dont_on_n_est_pas_membre(client, abonne, option):
    from oto_mcp.db import org_subscription_pool as P
    o = abonne
    option.add(o["membre"])
    r = client.patch(f"{_ME}/{_F}", json={"lent_to": [o["id"], o["autre"]]},
                     headers=_h(o["membre"]))
    assert r.status_code == 403 and r.json()["error"] == "not_org_member", r.text
    assert P.orgs_pretees(o["membre"], _F) == [], "rien d'écrit sur un refus"


def test_le_plafond_seul_ne_touche_pas_aux_prets(client, abonne, option):
    o = abonne
    option.add(o["membre"])
    client.patch(f"{_ME}/{_F}", json={"lent_to": [o["id"]]}, headers=_h(o["membre"]))
    r = client.patch(f"{_ME}/{_F}", json={"limit_pct": 40}, headers=_h(o["membre"]))
    assert r.status_code == 200, r.text
    assert (r.json()["limit_pct"], r.json()["lent_to"]) == (40, [o["id"]])


def test_preter_sans_abonnement_404(client, org, option):
    option.add(org["admin"])
    r = client.patch(f"{_ME}/{_F}", json={"lent_to": [org["id"]]}, headers=_h(org["admin"]))
    assert r.status_code == 404 and r.json()["error"] == "not_connected", r.text


def test_corps_vide_400_nothing_to_change(client, abonne):
    r = client.patch(f"{_ME}/{_F}", json={}, headers=_h(abonne["membre"]))
    assert r.status_code == 400 and r.json()["error"] == "nothing_to_change", r.text


def test_la_console_regle_le_mode_seul(live, org):
    """Face MCP (`oto_org_settings`) : le mode se règle seul, un `limit_pct` à côté
    est refusé — sur cette face, omis et `null` arrivent identiques."""
    from oto_mcp.capabilities import org_console as oc
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    ctx = ResolvedCtx(sub=org["admin"], org_id=org["id"])
    S = oc.OrgSettingsInput
    out = oc._org_settings(ctx, S(op="set", domain="model_subscriptions",
                                  org_id=org["id"], family=_F, mode="pool"))
    assert out["mode"] == "pool"
    for inp, code in ((S(op="set", domain="model_subscriptions", org_id=org["id"],
                         family=_F, mode="pool", limit_pct=50), "one_setting_per_call"),
                      (S(op="set", domain="model_subscriptions", org_id=org["id"],
                         family=_F, mode="tous"), "invalid_mode")):
        with pytest.raises(AuthzDenied) as e:
            oc._org_settings(ctx, inp)
        assert e.value.code == code
