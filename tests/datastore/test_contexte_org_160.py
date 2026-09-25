"""oto#160, point 1, phase 1 : un tableau personnel retient l'org où il a été créé.

`GET /api/datastores` devra ne rendre, parmi les tableaux personnels de l'appelant, que
ceux créés dans l'org active (arbitrage amendé du 17/09). Cette phase pose la DONNÉE
sans changer la liste : `user_datastores.context_org_id`, remplie à la création par
toutes les faces avec l'org active de l'appel — même sens que `projects.context_org_id`.
NULL pour un tableau d'org ou d'équipe, dont le contexte se dérive du propriétaire.

Un banc par face : l'outil MCP `data_create_datastore`, la route `POST /api/datastores`,
la création implicite de l'écriture à clé (`upsert_row`), le vivier provisionné par la
copie d'un projet. Plus la révision `0017` (pose, retrait, rattrapage par le boot) et une
garde sur le source : toute voie de création du code nomme `context_org_id`.
"""
from __future__ import annotations

import ast
import asyncio
import uuid
from pathlib import Path

import pytest

SUB = "usr_contexte_160"
RACINE = Path(__file__).resolve().parent.parent.parent


def _contexte(ns_id: int):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT context_org_id FROM user_datastores WHERE id = %s",
                            (ns_id,)).fetchone()["context_org_id"]


@pytest.fixture(scope="module")
def orgs(live):
    """Deux orgs dont l'appelant est membre ; la MAISON (org persistée) est la première."""
    from oto_mcp import db, org_store
    db.upsert_user(SUB, email=f"{SUB}@t.invalid", name=SUB)
    maison = org_store.create_org("Maison 160", created_by=SUB)
    travail = org_store.create_org("Travail 160", created_by=SUB)
    org_store.add_org_member(maison, SUB)
    org_store.add_org_member(travail, SUB)
    assert org_store.set_active_org(SUB, maison)
    return maison, travail


@pytest.fixture
def org_de_l_appel(orgs):
    """L'org nommée par l'appel (`_org=` côté agent), distincte de la maison."""
    from oto_mcp import session_org
    jeton = session_org.set_call_org(orgs[1])
    yield orgs[1]
    session_org.reset_call_org(jeton)


def _nom() -> str:
    return "t160-" + uuid.uuid4().hex[:6]


def test_face_mcp_data_create_datastore(orgs, org_de_l_appel, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as surface
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    mcp = FastMCP("t160")
    surface.register(mcp)
    out = asyncio.run(mcp.get_tool("data_create_datastore")).fn(datastore=_nom())
    assert out["is_personal"] is True
    assert _contexte(out["id"]) == org_de_l_appel, "l'org de l'appel, pas la maison"


def test_face_rest_post_api_datastores(orgs):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from oto_mcp.api import routes as api_routes

    class _Claims:
        claims = {"sub": SUB, "email": f"{SUB}@t.invalid", "name": SUB}

    class _Verifier:
        async def verify_token(self, token):
            return _Claims()

    client = TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))
    r = client.post("/api/datastores", json={"datastore": _nom()},
                    headers={"Authorization": f"Bearer {SUB}"})
    assert r.status_code == 201, r.text
    assert _contexte(r.json()["id"]) == orgs[0], "sans org nommée, l'org active est la maison"


def test_face_ecriture_a_cle_qui_cree_le_tableau(orgs, org_de_l_appel):
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    nom = _nom()
    make_store(SUB).upsert_row(nom, "k1", {"a": 1})
    ns = db.get_datastore("user", SUB, nom)
    assert _contexte(ns["id"]) == org_de_l_appel


def test_face_vivier_provisionne_par_la_copie_d_un_projet(orgs):
    from oto_mcp import db
    source = db.create_datastore("user", SUB, _nom(), context_org_id=orgs[0])
    projet = db.create_project("user", SUB, "Modèle 160", created_by=SUB,
                               context_org_id=orgs[0])
    db.add_project_link(projet, "tableau", str(source), label="vivier",
                        config={"provision": "empty"})
    copie, _ = db.duplicate_project(projet, "Copie 160", "user", SUB, copied_by=SUB,
                                    context_org_id=orgs[1])
    [lien] = [l for l in db.list_project_links(copie) if l["target_type"] == "tableau"]
    assert int(lien["target_ref"]) != source, "un vivier frais, pas le pointeur"
    assert _contexte(int(lien["target_ref"])) == orgs[1], "l'org où la copie est rangée"


def test_un_tableau_d_org_ne_retient_pas_de_contexte(orgs, org_de_l_appel):
    """Son contexte, c'est son propriétaire : une seconde source finirait par le contredire."""
    from oto_mcp.datastore.core import make_store
    out = make_store(SUB).create_datastore(_nom(), owner_type="org", owner_id=str(orgs[0]))
    assert _contexte(out["id"]) is None


def test_une_org_supprimee_laisse_le_tableau_sans_contexte(orgs):
    from oto_mcp import db, org_store
    from oto_mcp.db._conn import _connect
    ephemere = org_store.create_org("Éphémère 160", created_by=SUB)
    ns_id = db.create_datastore("user", SUB, _nom(), context_org_id=ephemere)
    with _connect() as conn:
        conn.execute("DELETE FROM orgs WHERE id = %s", (ephemere,))
    assert db.get_datastore_by_id(ns_id) is not None
    assert _contexte(ns_id) is None


def _alembic():
    from alembic.config import Config
    cfg = Config(str(RACINE / "alembic.ini"))
    cfg.set_main_option("script_location", str(RACINE / "oto_mcp" / "db" / "migrations"))
    return cfg


def _colonne(dsn: str) -> bool:
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT 1 FROM information_schema.columns WHERE table_name = "
                         "'user_datastores' AND column_name = 'context_org_id'"
                         ).fetchone() is not None


def test_la_revision_0017_pose_la_colonne_se_defait_et_le_boot_la_repose(
        orgs, pg_module_dsn):
    from alembic import command

    from oto_mcp.db import init_db
    cfg = _alembic()
    command.stamp(cfg, "head")
    command.downgrade(cfg, "0016_journal_suppression")
    assert not _colonne(pg_module_dsn)
    command.upgrade(cfg, "0017_tableaux_contexte_org")
    assert _colonne(pg_module_dsn)
    command.downgrade(cfg, "0016_journal_suppression")
    init_db()                              # le boot rattrape une base sans la révision
    assert _colonne(pg_module_dsn)
    command.stamp(cfg, "head")
    from oto_mcp import db
    assert _contexte(db.create_datastore("user", SUB, _nom(),
                                         context_org_id=orgs[0])) == orgs[0]


def test_toute_voie_de_creation_du_code_nomme_le_contexte():
    """Le défaut `None` de `db.create_datastore` sert les bancs ; une voie de création
    du code qui l'omettrait ferait naître des personnels sans org, en silence. La voie
    de base se reconnaît à ses trois arguments positionnels (propriétaire, identifiant,
    nom) — celle du store n'en prend qu'un."""
    vues, oublis = [], []
    for fichier in (RACINE / "oto_mcp").rglob("*.py"):
        for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            if not isinstance(noeud, ast.Call) or len(noeud.args) < 3:
                continue
            f = noeud.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) \
                    != "create_datastore":
                continue
            ou = f"{fichier.relative_to(RACINE)}:{noeud.lineno}"
            vues.append(ou)
            if not any(k.arg == "context_org_id" for k in noeud.keywords):
                oublis.append(ou)
    assert len(vues) >= 3, vues          # store, écriture à clé, copie de projet
    assert oublis == [], oublis
