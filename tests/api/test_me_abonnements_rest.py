"""Mes abonnements de modèles, SUR LA ROUTE et sur une vraie base (OTO-130).

Ce que ces bancs tiennent :

1. **Un abonnement est PERSONNEL.** La route ne lit et ne coupe que les miens —
   un admin de mon org n'y a pas accès non plus, et c'est volontaire.
2. **Se déconnecter n'est pas effacer.** Le geste courant laisse le sandbox
   debout ; détruire par défaut ferait payer une reconnexion complète à qui
   voulait mettre en pause.
3. **Rien de ce que la route rend n'approche une session** — ni adresse, ni
   organisation du fournisseur. La plateforme n'en garde rien, et une route qui
   en rendrait serait la preuve du contraire.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

ROUTE = "/api/me/model-subscriptions"
_FAMILLE = "claude_subscription"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@abo.invalid", "name": sub}


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

    nom = "oto_abo_rest_" + uuid.uuid4().hex[:8]
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
def gens(live):
    from oto_mcp import db
    moi, autre = "usr_abo_moi", "usr_abo_autre"
    for sub in (moi, autre):
        db.upsert_user(sub, email=f"{sub}@abo.invalid", name=sub)
    return {"moi": moi, "autre": autre}


@pytest.fixture
def abonne(gens):
    """Une connexion ouverte pour chacun — reposée à chaque banc."""
    from oto_mcp.db import user_subscriptions as US
    for sub in (gens["moi"], gens["autre"]):
        US.upsert_sandbox(sub, _FAMILLE, f"sandbox-{sub}")
        US.marquer_statut(sub, _FAMILLE, US.CONNECTE, plan="max",
                          method="claude.ai", ok=True)
    return gens


def test_je_lis_MES_abonnements_et_seulement_les_miens(client, abonne):
    r = client.get(ROUTE, headers=_h(abonne["moi"]))
    assert r.status_code == 200, r.text
    (mien,) = r.json()["subscriptions"]
    assert mien["family"] == _FAMILLE
    assert mien["statut"] == "connected" and mien["plan"] == "max"
    # L'autre personne a la sienne, et je ne la vois pas.
    assert len(client.get(ROUTE, headers=_h(abonne["autre"])).json()
               ["subscriptions"]) == 1


def test_la_lecture_n_approche_AUCUNE_session(client, abonne):
    """⚠️ La garde qui compte. `claude auth status` rend l'adresse et l'org du
    fournisseur : on n'en garde rien, et aucune route ne doit en rendre."""
    corps = client.get(ROUTE, headers=_h(abonne["moi"])).text.lower()
    for interdit in ("email", "@", "token", "credential", "session", "orgname"):
        assert interdit not in corps, f"la route rend `{interdit}`"


def test_me_deconnecter_GARDE_le_bac_a_sable(client, abonne):
    from oto_mcp.db import user_subscriptions as US
    r = client.delete(f"{ROUTE}/{_FAMILLE}", headers=_h(abonne["moi"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "family": _FAMILLE, "sandbox_destroyed": False}
    ligne = US.get_subscription(abonne["moi"], _FAMILLE)
    assert ligne["statut"] == US.DECONNECTE
    assert ligne["sandbox_id"], "le sandbox reste : se reconnecter est une connexion, pas tout refaire"


@pytest.fixture
def ferme(monkeypatch):
    """La ferme, doublée : ce qu'on lui a demandé, et ce qu'elle répond."""
    from oto_mcp import ferme as F
    from oto_mcp.capabilities import _abonnement
    appels: list = []
    etat = {"loggedIn": True, "subscriptionType": "max", "authMethod": "claude.ai",
            "email": "secret@fournisseur.invalid", "orgName": "Org du fournisseur"}
    monkeypatch.setattr(F, "creer", lambda sandbox: appels.append(("creer", sandbox)))
    monkeypatch.setattr(F, "demarrer_connexion",
                        lambda sandbox, email=None: appels.append(("login", sandbox)) or
                        "https://claude.com/cai/oauth/authorize?x=1")
    monkeypatch.setattr(F, "transmettre_code",
                        lambda sandbox, code: appels.append(("code", sandbox, code)) or etat)
    monkeypatch.setattr(F, "detruire", lambda sandbox: appels.append(("detruire", sandbox)))
    monkeypatch.setattr(_abonnement.access, "has_option",
                        lambda sub, option, **k: sub != "usr_abo_autre")
    return {"appels": appels, "etat": etat}


def test_me_connecter_en_deux_temps_ouvre_MON_bac(client, gens, ferme):
    from oto_mcp import ferme as F
    from oto_mcp.db import user_subscriptions as US
    US.oublier(gens["moi"], _FAMILLE)
    r = client.post(f"{ROUTE}/{_FAMILLE}/login", json={}, headers=_h(gens["moi"]))
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://claude.com/")
    r = client.put(f"{ROUTE}/{_FAMILLE}/login/code", json={"code": "abc#def"},
                   headers=_h(gens["moi"]))
    assert r.status_code == 200, r.text
    assert r.json()["statut"] == "connected" and r.json()["plan"] == "max"
    sandbox = F.sandbox_de(gens["moi"])
    assert ferme["appels"] == [("creer", sandbox), ("login", sandbox), ("code", sandbox, "abc#def")]
    assert US.get_subscription(gens["moi"], _FAMILLE)["sandbox_id"] == sandbox
    # Ce que le programme a lu du compte du fournisseur ne ressort pas.
    for interdit in ("secret@", "org du fournisseur"):
        assert interdit not in r.text.lower()


def test_sans_l_option_on_ne_se_connecte_pas(client, gens, ferme):
    r = client.post(f"{ROUTE}/{_FAMILLE}/login", json={}, headers=_h(gens["autre"]))
    assert r.status_code == 403 and r.json()["error"] == "subscription_not_enabled"
    assert ferme["appels"] == []


def test_un_code_qui_n_ouvre_pas_de_session_ne_connecte_rien(client, gens, ferme):
    from oto_mcp.db import user_subscriptions as US
    US.oublier(gens["moi"], _FAMILLE)
    ferme["etat"]["loggedIn"] = False
    r = client.put(f"{ROUTE}/{_FAMILLE}/login/code", json={"code": "faux"},
                   headers=_h(gens["moi"]))
    assert r.status_code == 422 and r.json()["error"] == "login_failed"
    assert US.get_subscription(gens["moi"], _FAMILLE) is None


def test_une_destruction_qui_echoue_se_DIT_et_n_efface_rien(client, abonne, ferme,
                                                            monkeypatch):
    from oto_mcp import ferme as F
    from oto_mcp.db import user_subscriptions as US

    def en_panne(sandbox):
        raise F.FermeIndisponible("injoignable")
    monkeypatch.setattr(F, "detruire", en_panne)
    r = client.delete(f"{ROUTE}/{_FAMILLE}?destroy=true", headers=_h(abonne["moi"]))
    assert r.status_code == 502 and r.json()["error"] == "farm_unavailable"
    ligne = US.get_subscription(abonne["moi"], _FAMILLE)
    assert ligne is not None and ligne["statut"] == US.DECONNECTE, \
        "la ligne reste, coupée : plus rien n'y part, et le geste se refait"


def test_effacer_mon_sandbox_detruit_la_ligne_ENTIERE(client, abonne, ferme):
    from oto_mcp.db import user_subscriptions as US
    # ⚠️ En QUERY : l'adaptateur n'ouvre pas les corps de DELETE, et un corps
    # ignoré aurait rendu le drapeau inerte — 200 rendu, rien détruit.
    r = client.delete(f"{ROUTE}/{_FAMILLE}?destroy=true", headers=_h(abonne["moi"]))
    assert r.status_code == 200, r.text
    assert r.json()["sandbox_destroyed"] is True
    assert ferme["appels"] == [("detruire", f"sandbox-{abonne['moi']}")], \
        "le sandbox est VRAIMENT détruit par la ferme, pas seulement oublié"
    assert US.get_subscription(abonne["moi"], _FAMILLE) is None
    # Et celle de l'autre personne n'a pas bougé.
    assert US.get_subscription(abonne["autre"], _FAMILLE) is not None


def test_couper_ce_qu_on_n_a_pas_est_un_404_nomme(client, gens):
    from oto_mcp.db import user_subscriptions as US
    US.oublier(gens["moi"], _FAMILLE)
    r = client.delete(f"{ROUTE}/{_FAMILLE}", headers=_h(gens["moi"]))
    assert r.status_code == 404 and r.json()["error"] == "not_connected"


def test_une_famille_inconnue_est_refusee_par_son_nom(client, abonne):
    r = client.delete(f"{ROUTE}/anthropic", headers=_h(abonne["moi"]))
    assert r.status_code == 400 and r.json()["error"] == "unknown_family"


def test_sans_jeton_rien_ne_se_lit(client):
    assert client.get(ROUTE).status_code in (401, 403)
