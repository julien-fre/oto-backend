"""Les flottes SUR LA ROUTE SERVIE — la réponse est sérialisée, pas inspectée.

⚠️ **Ce fichier existe à cause de ce que son absence a laissé passer.** Le lot qui a
créé cette capacité ne la faisait jamais passer par HTTP : ses tests lisaient des
champs de modèle et attrapaient des refus levés en appelant le handler à la main.
Deux défauts en sont sortis, de la MÊME famille — une valeur que PostgreSQL rend en
`Decimal`, que rien ne normalise, et qui fait un 500 à la sérialisation :

1. `max_cost_usd NUMERIC` — trouvé en relecture ;
2. `SUM(...)::bigint`, qui rend `numeric` en PostgreSQL — **passé sous le correctif
   du premier**, parce que la garde ajoutée balayait des NOMS (`usd|cost|euro`) et
   que le champ s'appelle `usage_tokens`.

> **Le correctif visait l'axe sur lequel le défaut s'était présenté (un nom), pas
> l'axe sur lequel il vit (un type qui ne se sérialise pas).** Un balayage de noms
> élargi à quatre modèles donnait l'impression d'avoir appris la leçon ; la seule
> occurrence vivante est passée dessous.

**La seule garde qui ne peut pas se tromper d'axe : sérialiser une vraie réponse.**
`TestClient` rend le JSON par la même pile que la production — si un type ne passe
pas, le test rougit, quel que soit le nom du champ.

Les refus sont rejoués ici aussi, parce qu'une déclaration `Capability.errors` sans
rejeu est décorative : elle promet un statut que le serveur ne rend peut-être pas.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

ROUTE = "/api/me/runner/fleets"


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@fleets.invalid", "name": sub}


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

    nom = "oto_fleets_rest_" + uuid.uuid4().hex[:8]
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
    membre = "usr_fleets_membre"
    db.upsert_user(membre, email=f"{membre}@fleets.invalid", name=membre)
    oid = org_store.create_org("Org des flottes", created_by=membre)
    org_store.add_org_member(oid, membre, "org_admin")
    org_store.set_active_org(membre, oid)
    return {"id": oid, "membre": membre}


@pytest.fixture(scope="module")
def flotte(client, org):
    """Une flotte déclarée PAR LA ROUTE — pas insérée en base à la main."""
    r = client.post(ROUTE, headers=_h(org["membre"]), json={
        "op": "create", "label": "passage d'essai", "procedure": "enrichissement",
        "tools": ["oto_kb"], "namespace": "un-tableau", "row_filter": {"lot": "a"},
        "provider": "openai", "model": "un-modele",
        "max_rows": 10, "max_tokens": 1_000_000, "max_tokens_per_row": 50_000})
    assert r.status_code == 200, r.text
    return r.json()["fleet"]


# ── la réponse se SÉRIALISE — la garde qui ne peut pas se tromper d'axe ───────

def test_l_etat_d_une_flotte_vierge_se_serialise(client, org, flotte):
    """Le défaut vivait ICI, et il touchait 100 % des appels.

    `COALESCE(SUM(...), 0)` rend un `Decimal` **même sans un seul job** : la lecture
    phare de la capacité — celle qui devait rendre un opérateur autonome — répondait
    500 sur toute flotte, y compris vierge. Aucun test de modèle ne pouvait le voir.
    """
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "state", "fleet_id": flotte["id"]})
    assert r.status_code == 200, r.text
    etat = r.json()["state"]
    assert etat["no_jobs_attached"] is True
    assert etat["jobs_total"] == 0
    # les compteurs sont des ENTIERS servis, pas des chaînes ni des décimaux
    assert isinstance(etat["usage_tokens"], int)
    assert not isinstance(etat["usage_tokens"], bool)


def test_l_etat_se_serialise_aussi_avec_des_travaux_rattaches(client, org, flotte):
    """Le chemin où `SUM` rend vraiment une somme — l'autre moitié du défaut."""
    from oto_mcp.db._conn import _connect
    with _connect() as c:
        for jetons in (1200, 3400):
            c.execute(
                "INSERT INTO runner_jobs (org_id, kind, fleet_id, status, result) "
                "VALUES (%s, 'start', %s, 'done', %s::jsonb)",
                (org["id"], flotte["id"], '{"usage_tokens": %d}' % jetons))
        c.commit()
    r = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "state", "fleet_id": flotte["id"]})
    assert r.status_code == 200, r.text
    etat = r.json()["state"]
    assert etat["jobs_total"] == 2 and etat["no_jobs_attached"] is False
    assert etat["usage_tokens"] == 4600
    assert isinstance(etat["usage_tokens"], int)


def test_la_flotte_elle_meme_se_serialise_sur_list_et_get(client, org, flotte):
    """`list` ramène la flotte dans le lot des autres : un type qui ne passe pas y
    rend TOUTE la liste illisible, pas seulement la flotte fautive."""
    for corps in ({"op": "list"}, {"op": "get", "fleet_id": flotte["id"]}):
        r = client.post(ROUTE, headers=_h(org["membre"]), json=corps)
        assert r.status_code == 200, f"{corps} → {r.text}"
    lot = client.post(ROUTE, headers=_h(org["membre"]),
                      json={"op": "list"}).json()["fleets"]
    assert any(f["id"] == flotte["id"] for f in lot)


# ── les refus DÉCLARÉS, rejoués sur la route servie ──────────────────────────

def _refus(client, org, corps: dict) -> tuple[int, str]:
    r = client.post(ROUTE, headers=_h(org["membre"]), json=corps)
    return r.status_code, (r.json() or {}).get("error", "")


def test_la_cible_figee_est_refusee_sur_la_route(client, org, flotte):
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "namespace": "ailleurs"}) == (400, "target_is_frozen")
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "row_filter": {"lot": "b"}}) == (400, "target_is_frozen")


def test_le_contexte_fige_est_refuse_sur_la_route(client, org, flotte):
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "model": "autre"}) == (400, "context_is_frozen")


def test_l_etat_ne_se_pose_pas_par_update_sur_la_route(client, org, flotte):
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                "status": "stopped"}) == (400, "status_not_settable")


def test_un_champ_non_modifiable_ne_rend_pas_200_sans_effet(client, org, flotte):
    """`procedure` est le champ le plus lourd de la configuration — ce que la flotte
    EXÉCUTE. Il n'était ni refusé ni appliqué : 200, et rien ne changeait.

    ⚠️ La garde appartient au SEAM, pas au champ : tout champ d'entrée ni structurel
    ni modifiable doit aboutir ou être refusé. Écrite champ par champ, elle oublie
    exactement ceux auxquels on n'a pas pensé — et ceux qu'on ajoutera."""
    for champ, valeur in (("procedure", "UNE-AUTRE"), ("project_id", 4242)):
        statut, code = _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                            champ: valeur})
        assert (statut, code) == (400, "field_not_settable"), f"{champ} → {statut}"
    # et la procédure n'a pas bougé
    f = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "get", "fleet_id": flotte["id"]}).json()["fleet"]
    assert f["procedure"] == "enrichissement"


def test_un_perimetre_sans_tableau_est_refuse_sur_la_route(client, org):
    assert _refus(client, org, {"op": "create", "label": "x", "procedure": "p",
                                "tools": ["oto_kb"], "row_filter": {"lot": "a"}}
                  ) == (400, "target_incomplete")


def test_create_sans_les_champs_requis_est_refuse_sur_la_route(client, org):
    assert _refus(client, org, {"op": "create", "label": "x"}
                  ) == (400, "missing_fields")


def test_une_flotte_inconnue_rend_404_sur_la_route(client, org):
    assert _refus(client, org, {"op": "get", "fleet_id": 999_999_999}
                  ) == (404, "fleet_not_found")


def test_une_flotte_d_une_autre_org_est_invisible(client, org, flotte):
    """L'isolation par org se vérifie sur la route, pas sur le handler : c'est là
    que `org_id` est résolu depuis le porteur."""
    from oto_mcp import db, org_store
    autre = "usr_fleets_etranger"
    db.upsert_user(autre, email=f"{autre}@fleets.invalid", name=autre)
    oid = org_store.create_org("Une autre org", created_by=autre)
    org_store.add_org_member(oid, autre, "org_admin")
    org_store.set_active_org(autre, oid)
    r = client.post(ROUTE, headers=_h(autre),
                    json={"op": "get", "fleet_id": flotte["id"]})
    assert (r.status_code, r.json().get("error")) == (404, "fleet_not_found")


# ── les résidus de la 3ᵉ passe : le seam vaut pour TOUTE opération ────────────

def test_create_refuse_ce_qu_il_n_applique_pas(client, org):
    """`create status="running"` rendait 200 avec une flotte `draft` et le champ
    avalé — mot pour mot le geste que le refus d'`update` prédit.

    ⚠️ La garde était écrite dans la branche `update` SEULE. **Une garde écrite
    dans une branche ne garde que cette branche** : c'est le même défaut que celui
    qu'elle corrigeait, déplacé d'un verbe."""
    base = {"op": "create", "label": "x", "procedure": "p", "tools": ["oto_kb"]}
    assert _refus(client, org, {**base, "status": "running"}
                  ) == (400, "status_not_settable")
    assert _refus(client, org, {**base, "fleet_id": 1}
                  ) == (400, "field_not_settable")


def test_une_borne_absurde_est_refusee_des_DEUX_cotes(client, org, flotte):
    """Une borne se compte, donc elle vaut au moins 1. `max_rows=-5`, `workers=0`
    passaient à la création ET à la retouche — une borne absurde acceptée est une
    panne différée, découverte au lancement plutôt qu'à la déclaration."""
    base = {"op": "create", "label": "x", "procedure": "p", "tools": ["oto_kb"]}
    for champ, valeur in (("max_rows", -5), ("workers", 0), ("max_tokens", -1),
                          ("max_tokens_per_row", 0), ("max_steps", -2),
                          ("max_consecutive_failures", 0)):
        assert _refus(client, org, {**base, champ: valeur}) == (400, "invalid_bound"), champ
        assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"],
                                    champ: valeur}) == (400, "invalid_bound"), champ


def test_update_ne_peut_pas_annuler_ce_que_create_exige(client, org, flotte):
    """`create tools=[]` était refusé et `update tools=[]` vidait l'allowlist.
    Une garde qui ne tient qu'à l'entrée laisse la sortie ouverte."""
    assert _refus(client, org, {"op": "update", "fleet_id": flotte["id"], "tools": []}
                  ) == (400, "missing_fields")
    f = client.post(ROUTE, headers=_h(org["membre"]),
                    json={"op": "get", "fleet_id": flotte["id"]}).json()["fleet"]
    assert f["tools"] == ["oto_kb"]
