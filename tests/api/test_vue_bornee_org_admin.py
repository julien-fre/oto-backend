"""« Voir en tant que » d'un org_admin, BORNÉ à son org (oto#270) — contre PostgreSQL.

Un membre vit dans deux orgs, O et P, et a son palier personnel : des projets, des
tableaux, des partages reçus, des clés. L'org_admin de O le consulte : il voit ce que
le membre voit DANS O (dont la part perso qui descend dans O), rien de P, aucun secret.
Et le témoin : hors vue, les mêmes lectures et les fonctions du seam `ownership` rendent
ce qu'elles rendaient — les chemins normaux ne bougent pas.

Les requêtes passent par la vraie table de routes (`make_routes`), l'adaptateur des
capacités et le vrai `ViewAsMiddleware`. Le porteur est identifié par un vérifieur
factice dont le bearer EST le sub : ce qu'on teste est en aval de l'authentification.
"""
from __future__ import annotations

import base64

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

ADMIN, MEMBRE, SIMPLE, DEHORS = "vb-admin", "vb-membre", "vb-simple", "vb-dehors"
SECRET_O, SECRET_P = "hunter-SECRET-O-7f3a91", "hunter-SECRET-P-2c8d44"
MARQUE_P = "zzP"   # tout ce qui vit hors de O porte cette marque
_CLE_MAITRE = base64.b64encode(b"\x27" * 32).decode()


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@vue-bornee.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def monde(live):
    from oto_mcp import credentials_store as cs
    from oto_mcp import db, group_store, org_store
    mp = pytest.MonkeyPatch()
    mp.setenv("OTO_MCP_MASTER_KEY", _CLE_MAITRE)
    for sub in (ADMIN, MEMBRE, SIMPLE, DEHORS):
        db.upsert_user(sub, email=f"{sub}@vue-bornee.invalid", name=sub)
    o = org_store.create_org("Org O", created_by=ADMIN)
    p = org_store.create_org(f"{MARQUE_P} Org P", created_by=DEHORS)
    org_store.add_org_member(o, ADMIN, "org_admin")
    org_store.add_org_member(o, MEMBRE, "org_member")
    org_store.add_org_member(o, SIMPLE, "org_member")
    org_store.add_org_member(p, DEHORS, "org_admin")
    org_store.add_org_member(p, MEMBRE, "org_member")
    # La maison du membre est P : `/api/me` ne doit pas la nommer dans la vue.
    org_store.set_active_org(MEMBRE, p)
    g = group_store.create_group(o, "Équipe O", created_by=ADMIN)
    h = group_store.create_group(p, f"{MARQUE_P} Équipe P", created_by=DEHORS)
    group_store.add_group_member(g, MEMBRE)
    group_store.add_group_member(h, MEMBRE)

    pr = {
        "org_o": db.create_project("org", str(o), "Projet de O", created_by=ADMIN),
        "equipe_o": db.create_project("group", str(g), "Projet d'équipe O", created_by=ADMIN),
        "perso_o": db.create_project("user", MEMBRE, "Perso rangé dans O",
                                     created_by=MEMBRE, context_org_id=o),
        "org_p": db.create_project("org", str(p), f"{MARQUE_P} projet de P",
                                   created_by=DEHORS),
        "perso_p": db.create_project("user", MEMBRE, f"{MARQUE_P} perso rangé dans P",
                                     created_by=MEMBRE, context_org_id=p),
        "partage_p": db.create_project("org", str(p), f"{MARQUE_P} partagé en propre",
                                       created_by=DEHORS),
    }
    db.grant_resource("project", str(pr["partage_p"]), "user", MEMBRE, "read",
                      granted_by=DEHORS)
    ds = {
        "tab_o": db.create_datastore("org", str(o), "tab_o"),
        "tab_perso": db.create_datastore("user", MEMBRE, "tab_perso"),
        "tab_p": db.create_datastore("org", str(p), f"{MARQUE_P}_tab_p"),
        "tab_p_partage": db.create_datastore("org", str(p), f"{MARQUE_P}_tab_partage"),
    }
    db.grant_resource("datastore_namespace", str(ds["tab_p_partage"]), "user", MEMBRE,
                      "read", granted_by=DEHORS)
    cs.set_credential(cs.MEMBER, cs.member_id(o, MEMBRE), "hunter", SECRET_O, set_by=MEMBRE)
    cs.set_credential(cs.MEMBER, cs.member_id(p, MEMBRE), "hunter", SECRET_P, set_by=MEMBRE)
    jeton = db.create_api_token(MEMBRE, label=f"{MARQUE_P} jeton")
    yield {"o": o, "p": p, "g": g, "h": h, "pr": pr, "ds": ds, "jeton": jeton}
    mp.undo()


@pytest.fixture(scope="module")
def client(monde):
    from oto_mcp.api import routes as api_routes
    app = Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None))
    return TestClient(api_routes.ViewAsMiddleware(app, verifier=_Verifier()))


def _vue(monde, qui=ADMIN, cible=MEMBRE, org=None) -> dict:
    return {"Authorization": f"Bearer {qui}", "X-Oto-View-As": cible,
            "X-Oto-Org": str(org if org is not None else monde["o"])}


def _soi(sub, org) -> dict:
    return {"Authorization": f"Bearer {sub}", "X-Oto-Org": str(org)}


def _noms(r) -> set:
    return {p["name"] for p in r.json()["projects"]}


# ── Ce que la vue montre ─────────────────────────────────────────────────────

def test_la_vue_montre_ce_que_le_membre_voit_dans_o(client, monde):
    h = _vue(monde)
    me = client.get("/api/me", headers=h)
    assert me.status_code == 200, me.text
    assert me.json()["sub"] == MEMBRE
    assert me.json()["view_as_read_only"] is True
    assert me.json()["active_org"] == monde["o"]
    # La maison du membre est P : masquée, jamais nommée.
    assert me.json()["home_org"] is None and me.json()["home_org_name"] is None

    orgs = client.get("/api/me/orgs", headers=h).json()["orgs"]
    assert [x["id"] for x in orgs] == [monde["o"]]

    r = client.post("/api/me/projects", json={"op": "list"}, headers=h)
    assert r.status_code == 200, r.text
    assert _noms(r) == {"Projet de O", "Projet d'équipe O", "Perso rangé dans O"}
    r = client.post("/api/me/projects", json={"op": "list", "scope": "me"}, headers=h)
    assert r.status_code == 200 and _noms(r) == set(), r.text

    r = client.post("/api/me/projects",
                    json={"op": "get", "project_id": monde["pr"]["perso_o"]}, headers=h)
    assert r.status_code == 200, r.text

    tableaux = client.get("/api/datastores", headers=h)
    assert tableaux.status_code == 200, tableaux.text
    assert {t["datastore"] for t in tableaux.json()["datastores"]} == {"tab_o", "tab_perso"}

    cle = client.get("/api/settings/api-keys/hunter", headers=h)
    assert cle.status_code == 200, cle.text
    assert SECRET_O not in cle.text and SECRET_P not in cle.text


@pytest.mark.parametrize("cle", ["org_p", "perso_p", "partage_p"])
def test_un_projet_hors_de_o_est_introuvable_dans_la_vue(client, monde, cle):
    r = client.post("/api/me/projects",
                    json={"op": "get", "project_id": monde["pr"][cle]}, headers=_vue(monde))
    assert r.status_code == 404, r.text
    assert MARQUE_P not in r.text


@pytest.mark.parametrize("cle", ["tab_p", "tab_p_partage"])
def test_un_tableau_hors_de_o_est_introuvable_dans_la_vue(client, monde, cle):
    r = client.get(f"/api/datastores/{monde['ds'][cle]}/rows", headers=_vue(monde))
    assert r.status_code == 404, r.text
    assert MARQUE_P not in r.text


def test_rien_de_p_ni_aucun_secret_dans_les_lectures_ouvertes(client, monde):
    h = _vue(monde)
    corps = []
    for chemin in ("/api/me", "/api/me/orgs", "/api/me/shell", "/api/me/recent-changes",
                   "/api/me/guides", "/api/me/instructions", "/api/me/agent-context",
                   "/api/me/connectors", "/api/datastores", f"/api/orgs/{monde['o']}",
                   f"/api/groups/{monde['g']}", "/api/settings/api-keys/hunter"):
        r = client.get(chemin, headers=h)
        assert r.status_code == 200, (chemin, r.status_code, r.text[:300])
        corps.append(r.text)
    for op in ({"op": "list"}, {"op": "list_templates"}):
        r = client.post("/api/me/projects", json=op, headers=h)
        assert r.status_code == 200, r.text
        corps.append(r.text)
    r = client.post("/api/me/docs", json={"op": "shared_with_me"}, headers=h)
    assert r.status_code == 200, r.text
    corps.append(r.text)
    tout = "\n".join(corps)
    assert MARQUE_P not in tout
    assert SECRET_O not in tout and SECRET_P not in tout and monde["jeton"] not in tout


# ── Ce que la vue refuse ─────────────────────────────────────────────────────

@pytest.mark.parametrize("chemin", [
    "/api/me/tokens", "/api/me/connector-accounts/grants", "/api/me/datastores/shared",
    "/api/me/model-subscriptions", "/api/me/connector-instances"])
def test_les_listes_compte_entier_sont_refusees(client, monde, chemin):
    r = client.get(chemin, headers=_vue(monde))
    assert (r.status_code, r.json()["error"]) == (403, "view_as_hors_org"), r.text


def test_l_org_et_l_equipe_de_p_sont_refusees(client, monde):
    for chemin in (f"/api/orgs/{monde['p']}", f"/api/groups/{monde['h']}"):
        r = client.get(chemin, headers=_vue(monde))
        assert (r.status_code, r.json()["error"]) == (403, "view_as_hors_org"), chemin


def test_une_org_resolue_hors_de_o_est_refusee(client, monde):
    """La règle d'autz résout l'org depuis un champ d'entrée : l'adaptateur la refuse."""
    r = client.post("/api/me/functions", json={"op": "list", "org": monde["p"]},
                    headers=_vue(monde))
    assert (r.status_code, r.json()["error"]) == (403, "view_as_hors_org"), r.text


def test_les_runs_ouverts_du_compte_sont_refuses(client, monde):
    r = client.post("/api/me/projects", json={"op": "runs"}, headers=_vue(monde))
    assert (r.status_code, r.json()["error"]) == (403, "view_as_hors_org"), r.text


def test_l_org_admin_ne_peut_rien_ecrire(client, monde):
    from oto_mcp import db
    avant = len(db.list_projects_for_owners([("user", MEMBRE)]))
    for entetes in (_vue(monde), {**_vue(monde), "X-Oto-View-As-Write": "1"}):
        r = client.post("/api/me/projects", json={"op": "create", "name": "intrus"},
                        headers=entetes)
        assert r.status_code == 403, r.text
    assert r.json()["error"] == "view_as_write_forbidden"
    assert len(db.list_projects_for_owners([("user", MEMBRE)])) == avant


def test_un_org_member_est_refuse(client, monde):
    r = client.get("/api/me", headers=_vue(monde, qui=SIMPLE))
    assert (r.status_code, r.json()["error"]) == (403, "forbidden")


def test_une_cible_hors_de_o_est_refusee(client, monde):
    r = client.get("/api/me", headers=_vue(monde, cible=DEHORS))
    assert (r.status_code, r.json()["error"]) == (403, "view_as_hors_org")


def test_l_admin_de_o_n_ouvre_pas_de_vue_dans_p(client, monde):
    r = client.get("/api/me", headers=_vue(monde, org=monde["p"]))
    assert (r.status_code, r.json()["error"]) == (403, "forbidden")


# ── Le témoin : hors vue, rien ne change ─────────────────────────────────────

def test_hors_vue_le_membre_lit_comme_avant(client, monde):
    h = _soi(MEMBRE, monde["o"])
    me = client.get("/api/me", headers=h).json()
    assert me["view_as_read_only"] is False and me["home_org"] == monde["p"]
    # O, P et son espace personnel : toutes ses orgs.
    assert {monde["o"], monde["p"]} < {
        x["id"] for x in client.get("/api/me/orgs", headers=h).json()["orgs"]}
    r = client.post("/api/me/projects", json={"op": "list"}, headers=h)
    assert _noms(r) == {"Projet de O", "Projet d'équipe O", "Perso rangé dans O"}
    r = client.post("/api/me/projects", json={"op": "list", "scope": "me"}, headers=h)
    assert _noms(r) == {f"{MARQUE_P} partagé en propre"}
    # Règle d'avant : ma ressource perso et mon partage personnel me suivent partout.
    for cle in ("perso_p", "partage_p"):
        r = client.post("/api/me/projects",
                        json={"op": "get", "project_id": monde["pr"][cle]}, headers=h)
        assert r.status_code == 200, (cle, r.text)
    r = client.post("/api/me/projects",
                    json={"op": "get", "project_id": monde["pr"]["org_p"]}, headers=h)
    assert (r.status_code, r.json()["error"]) == (403, "wrong_org_context")
    tableaux = {t["datastore"] for t in
                client.get("/api/datastores", headers=h).json()["datastores"]}
    # Règle d'avant (2026-07-01) : le partage reçu en propre ne se liste dans aucune org.
    assert tableaux == {"tab_o", "tab_perso"}
    assert client.get("/api/me/tokens", headers=h).status_code == 200


def _dans_la_vue(monde, fn):
    from oto_mcp import session_org
    jeton = session_org.set_view_as_bound_org(monde["o"])
    try:
        return fn()
    finally:
        session_org.reset_view_as_bound_org(jeton)


def test_le_seam_est_identique_hors_vue_et_borne_en_vue(monde):
    from oto_mcp import org_store
    from oto_mcp import ownership as ow
    o, p, pr, ds = monde["o"], monde["p"], monde["pr"], monde["ds"]
    cas = {
        # appel : (hors vue — la règle d'avant, en vue bornée à O)
        "visible perso rangé dans P": (
            lambda: ow.visible_in_org(MEMBRE, o, "project", str(pr["perso_p"])), True, False),
        "visible partage personnel de P": (
            lambda: ow.visible_in_org(MEMBRE, o, "project", str(pr["partage_p"])), True, False),
        "visible perso rangé dans O": (
            lambda: ow.visible_in_org(MEMBRE, o, "project", str(pr["perso_o"])), True, True),
        "visible tableau perso": (
            lambda: ow.visible_in_org(MEMBRE, o, ow.TYPE_RESSOURCE_DATASTORE,
                                      str(ds["tab_perso"])), True, True),
        "visible dans P": (
            lambda: ow.visible_in_org(MEMBRE, p, "project", str(pr["org_p"])), True, False),
        "contenu projet de P": (
            lambda: ow.can_access(MEMBRE, "project", str(pr["org_p"])), True, False),
        "contenu projet de O": (
            lambda: ow.can_access(MEMBRE, "project", str(pr["org_o"])), True, True),
        "contenu tableau partagé de P": (
            lambda: ow.can_access(MEMBRE, ow.TYPE_RESSOURCE_DATASTORE,
                                  str(ds["tab_p_partage"])), True, False),
        "portée de l'org P": (
            lambda: ow.owner_in_scope(MEMBRE, p, ("org", str(p))), True, False),
        # Hors vue : toutes ses orgs (O, P et son espace personnel) ; en vue : O seule.
        "orgs de l'acteur": (
            lambda: ow.accessor_scope(MEMBRE).org_ids,
            [int(x["org_id"]) for x in org_store.list_orgs_for_user(MEMBRE)], [o]),
        "équipes de l'acteur": (
            lambda: sorted(ow.accessor_scope(MEMBRE).group_ids),
            sorted([monde["g"], monde["h"]]), [monde["g"]]),
        # Hors vue, la recherche garde le partage personnel (cherchable ⇔ lisible).
        "projets cherchables dans O": (
            lambda: sorted(ow.accessible_project_ids(MEMBRE, o)),
            sorted([pr["org_o"], pr["equipe_o"], pr["perso_o"], pr["partage_p"]]),
            sorted([pr["org_o"], pr["equipe_o"], pr["perso_o"]])),
    }
    for nom, (appel, hors_vue, en_vue) in cas.items():
        assert appel() == hors_vue, f"hors vue — {nom}"
        assert _dans_la_vue(monde, appel) == en_vue, f"en vue — {nom}"
    # Les principals du contexte ne changent pas : le bornage vit dans les règles.
    assert _dans_la_vue(monde, lambda: ow.active_org_principals(MEMBRE, o)) == \
        ow.active_org_principals(MEMBRE, o)
    assert {o, p} < set(ow.accessor_scope(MEMBRE).org_ids)
