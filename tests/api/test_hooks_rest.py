"""Le webhook SUR LA ROUTE SERVIE — une vraie requête, un vrai code de réponse.

⚠️ Ce fichier existe parce que cette route est ÉCRITE À LA MAIN, hors de la couche
capacité : aucune des garanties que l'adaptateur offre (validation d'entrée,
sérialisation, mise en forme des refus) ne s'applique ici. Ce qu'un banc de module
prouve du module ne prouve rien de la route.

Et parce que **chaque code de réponse est un contrat avec un logiciel tiers** qui
ne lit pas la documentation : un 5xx là où on voulait un refus définitif se
transforme en tempête de retentatives, et un 200 sur un corps refusé fait croire à
une source qu'elle est branchée alors que rien ne part.
"""
from __future__ import annotations

import json
import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import runner_hook

ROUTE = "/api/hooks/{}"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@hooks.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_hooks_rest_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    cle_avant = os.environ.get("OTO_MCP_MASTER_KEY")
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
        for cle, valeur in (("DATABASE_URL", url_avant),
                            ("OTO_MCP_MASTER_KEY", cle_avant)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
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
    membre = "usr_hooks"
    db.upsert_user(membre, email=f"{membre}@hooks.invalid", name=membre)
    oid = org_store.create_org("Org des webhooks", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    org_store.set_active_org(membre, oid)
    return {"id": oid, "membre": membre}


@pytest.fixture
def agent(org):
    """Un déclencheur webhook, posé EN BASE, avec son secret en clair."""
    from oto_mcp import db
    t = db.create_trigger(org["id"], org["membre"], procedure="veille",
                          tz="UTC", tools=["oto_doc"], kind="webhook",
                          input="fais la veille")
    secret, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(t["id"], org["id"], hache)
    return {"id": t["id"], "secret": secret, "org": org["id"]}


def _post(client, trigger_id, secret=None, corps=None, **kw):
    entetes = {"Authorization": f"Bearer {secret}"} if secret else {}
    return client.post(ROUTE.format(trigger_id), headers=entetes,
                       content=json.dumps(corps) if corps is not None else b"", **kw)


# ── le chemin qui marche ──────────────────────────────────────────────────────

def test_un_appel_VALIDE_enfile_un_travail_et_rend_202(client, agent):
    from oto_mcp import db
    r = _post(client, agent["id"], agent["secret"], {"data": {"id": 1}})
    assert r.status_code == 202, r.text
    rendu = r.json()
    assert rendu["ok"] is True and rendu["trigger_id"] == agent["id"]
    assert rendu["delayed_seconds"] is None
    # Le travail existe VRAIMENT, et porte l'identité du créateur.
    job = db.get_job(rendu["job_id"], agent["org"])
    assert job and job["kind"] == "start" and job["sub"] == "usr_hooks"
    assert job["payload"]["trigger_id"] == agent["id"]
    assert job["payload"]["hook"] is True
    # Et la livraison est journalisée, avec le travail qu'elle a produit.
    lues = db.livraisons(agent["id"], agent["org"])
    assert lues[0]["outcome"] == db.QUEUED and lues[0]["job_id"] == rendu["job_id"]


def test_un_corps_VIDE_est_accepte(client, agent):
    """Une sonnette n'a pas besoin de porter un message."""
    r = client.post(ROUTE.format(agent["id"]),
                    headers={"Authorization": f"Bearer {agent['secret']}"})
    assert r.status_code == 202, r.text


def test_la_source_est_journalisee(client, agent):
    from oto_mcp import db
    client.post(ROUTE.format(agent["id"]),
                headers={"Authorization": f"Bearer {agent['secret']}",
                         "User-Agent": "HubSpot-Webhooks/2.0"},
                content=b"{}")
    assert db.livraisons(agent["id"], agent["org"])[0]["source"] == "HubSpot-Webhooks/2.0"


# ── les refus, un par un ──────────────────────────────────────────────────────

def test_sans_entete_le_refus_est_404(client, agent):
    r = _post(client, agent["id"], None, {})
    assert (r.status_code, r.json()["error"]) == (404, "hook_not_found")


def test_un_MAUVAIS_secret_rend_404(client, agent):
    r = _post(client, agent["id"], "otoh_" + "x" * 40, {})
    assert (r.status_code, r.json()["error"]) == (404, "hook_not_found")


def test_un_id_INCONNU_rend_le_MEME_404(client, agent):
    """⚠️ Mot pour mot le même corps : sinon la route dit à un tiers quels
    déclencheurs existent."""
    a = _post(client, 999_999_999, agent["secret"], {})
    b = _post(client, agent["id"], "otoh_" + "z" * 40, {})
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


def test_un_id_NON_NUMERIQUE_rend_404_pas_500(client):
    """Une URL abîmée est une erreur de l'appelant, pas une panne : un 500 ferait
    retenter un envoyeur qui n'a aucune chance de réussir."""
    r = client.post("/api/hooks/pas-un-nombre",
                    headers={"Authorization": "Bearer otoh_x"}, content=b"{}")
    assert r.status_code == 404


def test_un_jeton_de_COMPTE_ne_passe_pas(client, agent, org):
    """`oto_…` est un credential d'une autre surface. L'accepter ici serait le
    début d'une confusion de credentials."""
    from oto_mcp import db
    jeton = db.create_api_token(org["membre"], label="cli")
    r = _post(client, agent["id"], jeton, {})
    assert r.status_code == 404


def test_un_corps_qui_n_est_PAS_du_JSON_est_refuse_nommement(client, agent):
    """Une source qui envoie du formulaire verrait sinon ses agents tourner sans
    jamais recevoir sa donnée, et rien ne le dirait."""
    r = client.post(ROUTE.format(agent["id"]),
                    headers={"Authorization": f"Bearer {agent['secret']}"},
                    content=b"id=42&type=lead")
    assert (r.status_code, r.json()["error"]) == (400, "invalid_json")


def test_un_corps_TROP_GROS_est_refuse_avec_413(client, agent):
    from oto_mcp import db
    r = client.post(ROUTE.format(agent["id"]),
                    headers={"Authorization": f"Bearer {agent['secret']}"},
                    content=b'{"x":"' + b"a" * (runner_hook.CORPS_MAX + 100) + b'"}')
    assert (r.status_code, r.json()["error"]) == (413, "payload_too_large")
    assert "RÉFÉRENCE" in r.json()["detail"]
    # Le propriétaire VOIT la source trop bavarde : lui seul peut la réparer.
    assert db.livraisons(agent["id"], agent["org"])[0]["outcome"] == db.REFUSE_TOO_LARGE


def _lignes_brutes(trigger_id: int) -> int:
    """Les livraisons de ce déclencheur DANS LA TABLE, toutes orgs confondues.

    ⚠️ `db.livraisons` scope sur l'org — donc une écriture faite sous une MAUVAISE
    org lui est invisible, tout en existant. Une épreuve de chute l'a montré : la
    garde ci-dessous passait alors qu'un inconnu écrivait bel et bien une ligne.
    """
    from oto_mcp import db
    with db._connect() as conn:
        r = conn.execute("SELECT COUNT(*)::int AS n FROM runner_hook_deliveries "
                         "WHERE trigger_id = %s", (trigger_id,)).fetchone()
    return int(r["n"])


def test_un_corps_trop_gros_SANS_secret_n_ecrit_RIEN_du_tout(client, agent):
    """⚠️ Sinon un inconnu remplit la table en postant du volume sur un id deviné :
    une écriture non authentifiée déclenchée par un tiers, c'est-à-dire de quoi
    faire grossir la base sans jamais présenter de credential."""
    avant = _lignes_brutes(agent["id"])
    r = client.post(ROUTE.format(agent["id"]),
                    content=b'{"x":"' + b"a" * (runner_hook.CORPS_MAX + 100) + b'"}')
    assert r.status_code == 413
    assert _lignes_brutes(agent["id"]) == avant


def test_un_corps_trop_gros_avec_un_MAUVAIS_secret_n_ecrit_rien(client, agent):
    avant = _lignes_brutes(agent["id"])
    r = client.post(ROUTE.format(agent["id"]),
                    headers={"Authorization": "Bearer otoh_" + "q" * 40},
                    content=b'{"x":"' + b"a" * (runner_hook.CORPS_MAX + 100) + b'"}')
    assert r.status_code == 413
    assert _lignes_brutes(agent["id"]) == avant


def test_un_agent_en_PAUSE_rend_409(client, agent):
    from oto_mcp import db
    db.update_trigger(agent["id"], agent["org"], {"enabled": False})
    try:
        r = _post(client, agent["id"], agent["secret"], {})
        assert (r.status_code, r.json()["error"]) == (409, "trigger_paused")
        assert db.livraisons(agent["id"], agent["org"])[0]["outcome"] == db.REFUSE_PAUSED
    finally:
        db.update_trigger(agent["id"], agent["org"], {"enabled": True})


def test_au_dela_du_debit_la_livraison_est_RETARDEE_pas_refusee(client, org):
    """⚠️ Sur la route servie : une rafale ne perd rien. Le débit est posé à 1/h
    pour que le deuxième appel franchisse le seuil."""
    from oto_mcp import db
    t = db.create_trigger(org["id"], org["membre"], procedure="veille-rafale",
                          tz="UTC", tools=["oto_doc"], kind="webhook",
                          input="x", max_per_hour=1, fraicheur_s=0)
    secret, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(t["id"], org["id"], hache)

    assert _post(client, t["id"], secret, {}).json()["delayed_seconds"] is None
    second = _post(client, t["id"], secret, {})
    assert second.status_code == 202, "retardé, jamais refusé"
    assert second.json()["delayed_seconds"] > 0
    assert db.livraisons(t["id"], org["id"])[0]["outcome"] == db.DELAYED
    # Et le travail retardé n'est PAS réservable tout de suite.
    assert db.get_job(second.json()["job_id"], org["id"])["status"] == "pending"


def test_une_file_au_dela_de_sa_FRAICHEUR_rend_429(client, org):
    from oto_mcp import db
    t = db.create_trigger(org["id"], org["membre"], procedure="veille-perimee",
                          tz="UTC", tools=["oto_doc"], kind="webhook",
                          input="x", max_per_hour=1, fraicheur_s=1)
    secret, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(t["id"], org["id"], hache)
    _post(client, t["id"], secret, {})          # remplit la fenêtre
    r = _post(client, t["id"], secret, {})      # partirait après sa péremption
    assert (r.status_code, r.json()["error"]) == (429, "hook_rate_limited")
    assert r.headers.get("Retry-After"), "un 429 sans Retry-After n'aide personne"
    # ⚠️ Le refus est ENREGISTRÉ, et ce banc a sa raison d'être : lever à
    # l'intérieur de la transaction la faisait rouler en arrière, et le journal
    # du propriétaire restait vide sur le refus même qu'il devait expliquer.
    assert db.livraisons(t["id"], org["id"])[0]["outcome"] == db.REFUSE_RATE


# ── ce que le CORPS devient, vu de bout en bout ───────────────────────────────

def test_le_mode_FIELDS_de_bout_en_bout(client, org):
    from oto_mcp import db
    t = db.create_trigger(org["id"], org["membre"], procedure="veille-fields",
                          tz="UTC", tools=["oto_doc"], kind="webhook",
                          input="traite le lead", payload_mode="fields",
                          payload_fields={"lead_id": "$.data.id"})
    secret, hache = runner_hook.nouveau_secret()
    db.poser_secret_de_hook(t["id"], org["id"], hache)
    r = _post(client, t["id"], secret,
              {"data": {"id": 4242, "email": "prive@exemple.test"}})
    charge = db.get_job(r.json()["job_id"], org["id"])["payload"]["input"]
    assert "4242" in charge and "NON FIABLE" in charge
    assert "prive@exemple.test" not in charge, (
        "ce qui n'est pas nommé ne quitte jamais la livraison")


def test_par_defaut_le_corps_ne_touche_PAS_l_instruction(client, agent):
    from oto_mcp import db
    r = _post(client, agent["id"], agent["secret"], {"secret_du_tiers": "abc"})
    charge = db.get_job(r.json()["job_id"], agent["org"])["payload"]["input"]
    assert charge == "fais la veille"
