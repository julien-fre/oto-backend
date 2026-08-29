"""Ce qu'un front tiers a lu dans le contrat, REJOUÉ sur les routes servies (29/08/2026).

Un consommateur pur de l'API REST a dérivé son comportement du contrat — et le contrat
disait faux (`GroupUpdated` : « pas de 409 au renommage »), ne disait rien (la borne
du corps d'un guide), ou taisait un succès ambigu (inviter deux fois). Les
déclarations ajoutées (`Capability.errors`, `NodeOut.doc_id/project_id`,
`ContentBlock.ordered`) ne valent que si le serveur rend ce qu'elles disent : ici
chaque cas part d'une requête HTTP sur la table de routes réelle (`make_routes`), avec
l'adaptateur de capacités et un vrai PostgreSQL — pas un handler appelé à la main sur
un store stubbé, ce que `test_group_refusals.py` et `test_leave_org.py` font déjà.

Le porteur est identifié par un vérifieur factice dont le bearer EST le sub : ce
qu'on teste est en aval de l'authentification, pas elle.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@front-tiers.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE et le vrai `init_db()` (même recette que les lots de nœuds)."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_front_tiers_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield init_db
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


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))


@pytest.fixture(scope="module")
def org(live):
    """Une org avec son admin (créateur), et un simple membre."""
    from oto_mcp import db, org_store
    admin, membre = "usr_ft_admin", "usr_ft_membre"
    for sub in (admin, membre):
        db.upsert_user(sub, email=f"{sub}@front-tiers.invalid", name=sub)
    oid = org_store.create_org("Org du front tiers", created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    org_store.add_org_member(oid, membre, "org_member")
    org_store.set_active_org(admin, oid)
    return {"id": oid, "admin": admin, "membre": membre}


# ── PATCH /api/groups/{id} : 409 group_exists ─────────────────────────────────

def test_renommer_un_groupe_vers_un_nom_pris_rend_409_sur_la_route_servie(client, org):
    oid, admin = org["id"], org["admin"]
    r = client.post(f"/api/orgs/{oid}/groups", json={"name": "Finance"}, headers=_h(admin))
    assert r.status_code == 200, r.text
    r = client.post(f"/api/orgs/{oid}/groups", json={"name": "Sales"}, headers=_h(admin))
    assert r.status_code == 200, r.text
    gid = r.json()["group_id"]

    r = client.patch(f"/api/groups/{gid}", json={"name": "  finance "}, headers=_h(admin))
    assert r.status_code == 409, r.text
    assert r.json()["error"] == "group_exists"
    assert r.json()["detail"]                      # actionnable, pas un code nu
    # Rien n'a été écrit : le groupe s'appelle toujours Sales.
    assert client.get(f"/api/groups/{gid}", headers=_h(admin)).json()["group"]["name"] == "Sales"
    # Et la création refuse pareil — même conflit, même réponse.
    r = client.post(f"/api/orgs/{oid}/groups", json={"name": "FINANCE"}, headers=_h(admin))
    assert (r.status_code, r.json()["error"]) == (409, "group_exists")


# ── POST /api/orgs/{id}/invitations : le doublon est un 200 ───────────────────

def test_inviter_deux_fois_la_meme_adresse_rend_deux_invitations_en_200(client, org):
    """Ce que le serveur FAIT, déclaré tel quel dans `InvitationEmitted` : aucun
    contrôle de doublon. Si ce comportement change un jour, ce test doit changer AVEC
    la déclaration — jamais l'un sans l'autre."""
    oid, admin = org["id"], org["admin"]
    corps = {"email": "Deux.Fois@front-tiers.invalid", "send_email": False}
    r1 = client.post(f"/api/orgs/{oid}/invitations", json=corps, headers=_h(admin))
    r2 = client.post(f"/api/orgs/{oid}/invitations", json=corps, headers=_h(admin))
    assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
    assert r1.json()["ok"] and r2.json()["ok"]
    assert r1.json()["code"] != r2.json()["code"]           # deux secrets porteurs
    assert r1.json()["email"] == "deux.fois@front-tiers.invalid"   # normalisé

    file = client.get(f"/api/orgs/{oid}/invitations", headers=_h(admin)).json()["invitations"]
    assert [i["email"] for i in file].count("deux.fois@front-tiers.invalid") == 2


def test_inviter_un_membre_actuel_rend_aussi_200(client, org):
    oid, admin, membre = org["id"], org["admin"], org["membre"]
    r = client.post(f"/api/orgs/{oid}/invitations",
                    json={"email": f"{membre}@front-tiers.invalid", "send_email": False},
                    headers=_h(admin))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True                # silencieux : rien ne dit « déjà membre »


# ── PUT /api/me/guides/{scope}/{slug} : 400 body_too_large, borne en OCTETS ───

def test_un_corps_de_guide_au_dela_de_65536_octets_rend_400(client, org):
    admin = org["admin"]
    r = client.put("/api/me/guides/user/trop-gros", json={"body_md": "a" * 65537},
                   headers=_h(admin))
    assert (r.status_code, r.json()["error"]) == (400, "body_too_large"), r.text
    # La borne EXACTE passe.
    r = client.put("/api/me/guides/user/juste", json={"body_md": "a" * 65536},
                   headers=_h(admin))
    assert r.status_code == 200, r.text


def test_la_borne_est_en_octets_pas_en_caracteres(client, org):
    """40 000 caractères accentués = 80 000 octets : `maxLength: 65536` (des caractères)
    laisse passer, le serveur refuse — c'est écrit dans la description du champ."""
    r = client.put("/api/me/guides/user/accents", json={"body_md": "é" * 40_000},
                   headers=_h(org["admin"]))
    assert (r.status_code, r.json()["error"]) == (400, "body_too_large"), r.text


# ── DELETE /api/me/orgs/{id}/membership : les quatre refus, dans l'ordre ──────

def test_quitter_une_org_dont_on_est_le_dernier_admin_rend_409(client, org):
    oid, admin = org["id"], org["admin"]
    r = client.delete(f"/api/me/orgs/{oid}/membership", headers=_h(admin))
    assert (r.status_code, r.json()["error"]) == (409, "last_org_admin"), r.text
    # Toujours membre, toujours admin : le refus n'a rien écrit.
    from oto_mcp import org_store
    assert org_store.get_org_role(oid, admin) == "org_admin"


def test_quitter_son_espace_personnel_rend_409(client, org):
    from oto_mcp import org_store
    admin = org["admin"]
    perso = org_store.ensure_personal_org(admin, email=f"{admin}@front-tiers.invalid")
    r = client.delete(f"/api/me/orgs/{perso}/membership", headers=_h(admin))
    assert (r.status_code, r.json()["error"]) == (409, "personal_org"), r.text


def test_quitter_une_org_dont_on_n_est_pas_membre_rend_404(client, org):
    from oto_mcp import db
    etranger = "usr_ft_etranger"
    db.upsert_user(etranger, email=f"{etranger}@front-tiers.invalid")
    r = client.delete(f"/api/me/orgs/{org['id']}/membership", headers=_h(etranger))
    assert (r.status_code, r.json()["error"]) == (404, "not_a_member"), r.text


def test_quitter_une_org_inconnue_rend_404(client, org):
    r = client.delete("/api/me/orgs/999999/membership", headers=_h(org["admin"]))
    assert (r.status_code, r.json()["error"]) == (404, "unknown_org"), r.text


def test_un_simple_membre_quitte_l_org(client, org):
    """Le chemin heureux, pour que les refus ne soient pas testés dans le vide."""
    oid, membre = org["id"], org["membre"]
    r = client.delete(f"/api/me/orgs/{oid}/membership", headers=_h(membre))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "org_id": oid, "left": True}


# ── GET /api/me/nodes/{id} : doc_id, project_id, ordered ──────────────────────

@pytest.fixture(scope="module")
def noeuds(live, org):
    """Un projet et une page CONVERTIS en nœuds (le vrai chemin : `init_db` projette),
    puis les blocs projetés par la passe de maintenance."""
    from oto_mcp.db import blocks, create_doc, create_project, shell as db_shell
    oid, admin = org["id"], org["admin"]
    pid = create_project("org", str(oid), "Refonte", brief_md="Le brief.", created_by=admin)
    did = create_doc(pid, "Plan", body_md="# Plan\n\n1. cadrer\n2. livrer\n\n- puce\n- autre\n",
                     created_by=admin)
    live()                                        # la projection projets/pages → nœuds
    blocks.backfill_node_blocks()
    return {"pid": pid, "did": did,
            "nod_prj": db_shell._public_id_derive("prj", str(pid)),
            "nod_doc": db_shell._public_id_derive("doc", str(did))}


def test_une_page_ouverte_rend_ses_poignees_et_l_ordre_de_ses_listes(client, org, noeuds):
    r = client.get(f"/api/me/nodes/{noeuds['nod_doc']}", headers=_h(org["admin"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["doc_id"], out["project_id"]) == (noeuds["did"], noeuds["pid"])
    listes = [b for b in out["body"] if b.get("role") == "list"]
    assert [b["ordered"] for b in listes] == [True, False]
    assert listes[0]["items"] == ["cadrer", "livrer"]
    assert all("ordered" not in b for b in out["body"] if b.get("role") != "list")


def test_un_projet_ouvert_rend_son_project_id_sans_doc_id(client, org, noeuds):
    r = client.get(f"/api/me/nodes/{noeuds['nod_prj']}", headers=_h(org["admin"]))
    assert r.status_code == 200, r.text
    assert (r.json()["doc_id"], r.json()["project_id"]) == (None, noeuds["pid"])


def test_les_poignees_ouvrent_bien_les_autres_surfaces(client, org, noeuds):
    """La raison d'être des deux champs : ils sont acceptés tels quels par le partage
    (`/api/resources`) et par les pages (`/api/me/docs`). Sinon on aurait publié des
    entiers que personne ne prend."""
    admin = org["admin"]
    r = client.post("/api/resources", headers=_h(admin),
                    json={"op": "get", "resource_type": "project",
                          "resource_id": str(noeuds["pid"])})
    assert r.status_code == 200, r.text
    r = client.post("/api/me/docs", headers=_h(admin),
                    json={"op": "backlinks", "doc_id": noeuds["did"]})
    assert r.status_code == 200, r.text
    assert r.json()["doc_id"] == noeuds["did"]
