"""Ce qu'un front tiers a lu dans le contrat, REJOUÉ sur les routes servies (29/08/2026).

Un consommateur pur de l'API REST a dérivé son comportement du contrat — et le contrat
disait faux (`GroupUpdated` : « pas de 409 au renommage »), ne disait rien (la borne
du corps d'un guide), ou taisait un succès ambigu (inviter deux fois — devenu un refus
le 29/08/2026, #622). Les
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

# La recopie des tables historiques vers `nodes` est ARRÊTÉE depuis le 2026-09-01 :
# les deux univers vivent côte à côte, plus rien ne traduit l'un vers l'autre. Un
# test qui ouvre par la nouvelle surface un contenu créé dans l'ancienne ne décrit
# donc plus le système — il décrit le pont qu'on vient de retirer.
#
# **STRICT à dessein.** Le jour où l'un d'eux repasse au vert, c'est qu'une recopie
# est revenue : ce fichier doit le CRIER, pas l'absorber en silence.
#
# ⚠️ Marqué, pas supprimé, et la nuance compte : l'identifiant dérivé et les
# poignées `doc_id`/`project_id` sont SERVIS au front partenaire. Les retirer est un
# changement de contrat qui appartient à un arbitrage en cours, pas à un déblaiement
# de fin de chantier. Ces tests partent — ou sont réécrits sur du contenu natif —
# quand cet arbitrage est rendu.
_SANS_PROJECTION = pytest.mark.xfail(
    strict=True,
    reason="la recopie tables historiques → nodes est arrêtée (2026-09-01)",
)


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


# ── POST /api/orgs/{id}/invitations : déjà membre / déjà invitée = 409 (#622) ──

def _file(client, oid, admin) -> list[dict]:
    return client.get(f"/api/orgs/{oid}/invitations", headers=_h(admin)).json()["invitations"]


def test_inviter_une_adresse_deja_invitee_rend_409_et_l_invitation_existante(client, org):
    """Décision du 29/08/2026 (#622) : une invitation encore valide pour la même adresse
    est un refus, pas un deuxième secret porteur. `details.invitation` porte de quoi la
    renvoyer (id, dates) — jamais son code. La comparaison est faite sur l'adresse
    normalisée (casse, espaces)."""
    oid, admin = org["id"], org["admin"]
    r1 = client.post(f"/api/orgs/{oid}/invitations",
                     json={"email": "Deux.Fois@front-tiers.invalid", "send_email": False},
                     headers=_h(admin))
    assert r1.status_code == 200, r1.text
    assert r1.json()["email"] == "deux.fois@front-tiers.invalid"      # normalisé
    existante = [i for i in _file(client, oid, admin)
                 if i["email"] == "deux.fois@front-tiers.invalid"]
    assert len(existante) == 1

    r2 = client.post(f"/api/orgs/{oid}/invitations",
                     json={"email": "  DEUX.fois@Front-Tiers.INVALID ", "send_email": False},
                     headers=_h(admin))
    assert (r2.status_code, r2.json()["error"]) == (409, "already_invited"), r2.text
    assert "deux.fois@front-tiers.invalid" in r2.json()["detail"]
    inv = r2.json()["details"]["invitation"]
    assert inv["id"] == existante[0]["id"]
    assert inv["created_at"] == existante[0]["created_at"]
    assert inv["expires_at"] == existante[0]["expires_at"]
    assert set(inv) == {"id", "created_at", "expires_at"}     # jamais le code
    assert r1.json()["code"] not in r2.text
    # Rien n'a été écrit : toujours UNE ligne pour cette adresse.
    assert [i["email"] for i in _file(client, oid, admin)].count(
        "deux.fois@front-tiers.invalid") == 1


def test_inviter_un_membre_actuel_rend_409(client, org):
    oid, admin, membre = org["id"], org["admin"], org["membre"]
    adresse = f"{membre}@front-tiers.invalid"
    r = client.post(f"/api/orgs/{oid}/invitations",
                    json={"email": adresse.upper(), "send_email": False}, headers=_h(admin))
    assert (r.status_code, r.json()["error"]) == (409, "already_member"), r.text
    assert adresse in r.json()["detail"] and "membre" in r.json()["detail"]
    assert "details" not in r.json()                      # rien à renvoyer : il est là
    assert adresse not in [i["email"] for i in _file(client, oid, admin)]


def test_la_meme_capacite_refuse_sur_la_face_mcp(org):
    """`oto_org op=invite` aboutit au même handler que la route (pas une copie) : le
    refus y est le même `AuthzDenied`, que l'adaptateur MCP rend en message."""
    from oto_mcp.capabilities import org_console as oc
    from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
    oid, admin, membre = org["id"], org["admin"], org["membre"]
    with pytest.raises(AuthzDenied) as e:
        oc._org(ResolvedCtx(sub=admin, org_id=oid),
                oc.OrgInput(op="invite", org_id=oid, email=f"{membre}@front-tiers.invalid",
                            send_email=False))
    assert (e.value.status, e.value.code) == (409, "already_member")
    with pytest.raises(AuthzDenied) as e:
        oc._org(ResolvedCtx(sub=admin, org_id=oid),
                oc.OrgInput(op="invite", org_id=oid, email="deux.fois@front-tiers.invalid",
                            send_email=False))
    assert (e.value.status, e.value.code) == (409, "already_invited")
    assert set(e.value.details["invitation"]) == {"id", "created_at", "expires_at"}


@pytest.mark.parametrize("sort", ["expiree", "consommee", "revoquee"])
def test_une_invitation_qui_ne_vaut_plus_ne_bloque_pas(client, org, sort):
    """Expirée, consommée ou révoquée : la file ne la porte plus, une nouvelle
    invitation est un 200 normal."""
    from oto_mcp import org_store
    oid, admin = org["id"], org["admin"]
    adresse = f"{sort}@front-tiers.invalid"
    corps = {"email": adresse, "send_email": False}
    r = client.post(f"/api/orgs/{oid}/invitations", json=corps, headers=_h(admin))
    assert r.status_code == 200, r.text
    inv_id = next(i["id"] for i in _file(client, oid, admin) if i["email"] == adresse)
    if sort == "expiree":
        with org_store._connect() as conn:
            conn.execute("UPDATE org_invitations SET expires_at = NOW() - interval '1 day' "
                         "WHERE id = %s", (inv_id,))
    elif sort == "consommee":
        org_store._mark_invitation_accepted(inv_id, "usr_ft_quelqu_un")
    else:
        r = client.delete(f"/api/orgs/{oid}/invitations/{inv_id}", headers=_h(admin))
        assert r.status_code == 200, r.text
    # La même adresse est à nouveau invitable.
    r = client.post(f"/api/orgs/{oid}/invitations", json=corps, headers=_h(admin))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_inviter_sans_adresse_reste_un_code_a_partager(client, org):
    """Le refus ne vaut que pour une adresse : sans email, aucun doublon possible."""
    oid, admin = org["id"], org["admin"]
    r1 = client.post(f"/api/orgs/{oid}/invitations", json={"send_email": False}, headers=_h(admin))
    r2 = client.post(f"/api/orgs/{oid}/invitations", json={"send_email": False}, headers=_h(admin))
    assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
    assert r1.json()["email"] is None and r1.json()["code"] != r2.json()["code"]


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


@_SANS_PROJECTION
def test_une_page_ouverte_rend_ses_poignees_et_l_ordre_de_ses_listes(client, org, noeuds):
    r = client.get(f"/api/me/nodes/{noeuds['nod_doc']}", headers=_h(org["admin"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["doc_id"], out["project_id"]) == (noeuds["did"], noeuds["pid"])
    listes = [b for b in out["body"] if b.get("role") == "list"]
    assert [b["ordered"] for b in listes] == [True, False]
    assert listes[0]["items"] == ["cadrer", "livrer"]
    assert all("ordered" not in b for b in out["body"] if b.get("role") != "list")


@_SANS_PROJECTION
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
