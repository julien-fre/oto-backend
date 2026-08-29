"""Un nom est une DONNÉE : `&`, `<`, `>`, `"` entrent et ressortent tels quels.

Signalement d'un front tiers (29/08/2026) : « un nom contenant `&` arrive `&amp;` dans
`/api/me/shell` et `/api/me/nodes/{id}` — double échappement à l'écriture côté backend ».
**Reconstitution fausse, et ce fichier tient la preuve du contraire.** Reproduit sur le
chemin servi (table de routes réelle, adaptateur, vrai PostgreSQL) : un groupe, un
projet et une page nommés avec les quatre caractères sont relus au caractère près sur
chaque route de lecture, et le corps JSON brut ne porte aucune entité HTML.

Le relevé en production dit d'où venait `&amp;` : **zéro** nom de groupe et **zéro** nom
d'org n'en portent ; **1** projet et **5** pages en portent, et le journal des appels
montre pour trois d'entre elles — et pour trois projets créés depuis par un compte de
tenant, renommés à la main depuis — que `&amp;` était DANS LES ARGUMENTS REÇUS
(`oto_doc` / `oto_project op=create`), tandis que le `brief_md` du même appel portait
un `&` nu. C'est le client qui échappe son champ `name` à l'écriture ; le serveur
stocke ce qu'il reçoit, ce qui est son rôle. 11 projets et 138 pages avec un `&` nu
sont servis sans encombre.

Ce que ce test garde, donc : que personne ne « corrige » ce signalement en échappant
ou en déséchappant à l'écriture ou à la lecture. L'échappement appartient au RENDU
(`share_ui`, `public_doc_page`, l'email), jamais au stockage ni à une réponse JSON.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

NOM = 'Finance & Administratif <R&D> "Q1"'
ENTITES = ("&amp;", "&lt;", "&gt;", "&quot;", "&#")


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@verbatim.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


def _sans_entite(texte: str) -> bool:
    return not any(e in texte for e in ENTITES)


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE et le vrai `init_db()` (recette des lots de nœuds)."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_verbatim_" + uuid.uuid4().hex[:8]
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
def monde(live):
    from oto_mcp import db, org_store
    from oto_mcp.api import routes as api_routes
    admin = "usr_verbatim_admin"
    db.upsert_user(admin, email=f"{admin}@verbatim.invalid", name=admin)
    oid = org_store.create_org("Org verbatim", created_by=admin)
    org_store.add_org_member(oid, admin, "org_admin")
    org_store.set_active_org(admin, oid)
    client = TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))
    return {"client": client, "admin": admin, "oid": oid}


# ── Groupe : création, renommage, les trois lectures et le rail ───────────────

def test_un_nom_de_groupe_ressort_tel_quel_partout(monde):
    c, admin, oid = monde["client"], monde["admin"], monde["oid"]
    r = c.post(f"/api/orgs/{oid}/groups", json={"name": NOM}, headers=_h(admin))
    assert r.status_code == 200, r.text
    gid = r.json()["group_id"]
    assert r.json()["name"] == NOM

    r = c.patch(f"/api/groups/{gid}", json={"name": NOM + " v2"}, headers=_h(admin))
    assert r.status_code == 200, r.text

    assert c.get(f"/api/groups/{gid}", headers=_h(admin)).json()["group"]["name"] == NOM + " v2"
    liste = c.get(f"/api/orgs/{oid}/groups", headers=_h(admin))
    assert [g["name"] for g in liste.json()["groups"]] == [NOM + " v2"]
    assert _sans_entite(liste.text)

    from oto_mcp import group_store
    group_store.set_active_group(admin, gid)
    shell = c.get("/api/me/shell", headers=_h(admin))
    assert shell.status_code == 200, shell.text
    equipes = [s for s in shell.json()["sections"] if s["kind"] == "team"]
    assert [s["name"] for s in equipes] == [NOM + " v2"]
    assert equipes[0]["context"]["name"] == "Contexte — " + NOM + " v2"
    # Le CORPS BRUT : c'est là qu'un double échappement se verrait, pas dans un
    # `.json()` qui aurait déjà décodé.
    assert _sans_entite(shell.text)


def test_la_face_mcp_ecrit_le_meme_nom_par_le_meme_handler(monde):
    """`oto_group op=create/update` appelle `groups._create_group` / `_update_group`
    (`org_console.py`) : un seul écrivain, celui que le REST vient d'exercer."""
    from oto_mcp import group_store
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.groups import core as groups
    admin, oid = monde["admin"], monde["oid"]
    out = groups._create_group(ResolvedCtx(sub=admin, org_id=oid),
                               groups.CreateGroupInput(org_id=oid, name=NOM + " mcp"))
    assert group_store.get_group(out["group_id"])["name"] == NOM + " mcp"


# ── Projet et page : la surface consolidée, puis le nœud et le rail ───────────

def test_un_nom_de_projet_et_un_titre_de_page_ressortent_tels_quels(monde, live):
    c, admin, oid = monde["client"], monde["admin"], monde["oid"]
    r = c.post("/api/me/projects", headers=_h(admin),
               json={"op": "create", "name": NOM, "brief_md": "Brief & co."})
    assert r.status_code == 200, r.text
    pid = r.json()["id"] if "id" in r.json() else r.json()["project"]["id"]
    r = c.post("/api/me/docs", headers=_h(admin),
               json={"op": "create", "project_id": pid, "title": "Plan " + NOM,
                     "body_md": "Corps & <suite>\n"})
    assert r.status_code == 200, r.text
    did = r.json()["id"] if "id" in r.json() else r.json()["doc"]["id"]

    live()                                        # la projection projets/pages → nœuds
    from oto_mcp.db import shell as db_shell
    for public_id, attendu in ((db_shell._public_id_derive("prj", str(pid)), NOM),
                               (db_shell._public_id_derive("doc", str(did)), "Plan " + NOM)):
        r = c.get(f"/api/me/nodes/{public_id}", headers=_h(admin))
        assert r.status_code == 200, r.text
        assert r.json()["name"] == attendu
        assert _sans_entite(r.text)

    shell = c.get("/api/me/shell", headers=_h(admin))
    assert shell.status_code == 200, shell.text
    assert NOM in _noms(shell.json()["sections"])
    assert _sans_entite(shell.text)          # le corps BRUT, où `"` s'écrit `\\"`


def _noms(noeuds: list) -> set:
    """Tous les `name` d'un arbre du rail (sections, nœuds, enfants)."""
    out: set = set()
    for n in noeuds:
        out.add(n.get("name"))
        out |= _noms(n.get("nodes") or n.get("children") or [])
    return out
