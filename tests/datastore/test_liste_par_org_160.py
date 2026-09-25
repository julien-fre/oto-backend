"""oto#160, phase 2 : un tableau personnel n'est LISTÉ que dans l'org où il a été créé.

Base réelle. Un même utilisateur, membre de deux orgs A et B :
- son tableau créé dans A est listé dans A, pas dans B ;
- son tableau à `context_org_id` NULL (d'avant la colonne, non reconstitué) est listé
  dans les deux ;
- les tableaux d'org ne changent pas ;
- depuis B, son tableau de A s'ouvre toujours par son numéro (et par son nom) : on
  filtre la liste, pas le droit.

Sur chaque face : le store (`list_datastores`), `GET /api/datastores` (`X-Oto-Org`),
l'outil `data_list_datastores`, et la recherche, tenue en parité avec la liste.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

SUB = "usr_liste_160"


def _nom() -> str:
    return "l160-" + uuid.uuid4().hex[:6]


@pytest.fixture(scope="module")
def monde(live):
    from oto_mcp import db, org_store
    db.upsert_user(SUB, email=f"{SUB}@t.invalid", name=SUB)
    a = org_store.create_org("A liste 160", created_by=SUB)
    b = org_store.create_org("B liste 160", created_by=SUB)
    for o in (a, b):
        org_store.add_org_member(o, SUB)
    assert org_store.set_active_org(SUB, a)
    return {
        "a": a, "b": b,
        "perso_a": db.create_datastore("user", SUB, _nom(), context_org_id=a),
        "perso_b": db.create_datastore("user", SUB, _nom(), context_org_id=b),
        "perso_null": db.create_datastore("user", SUB, _nom(), context_org_id=None),
        "org_a": db.create_datastore("org", str(a), _nom(), context_org_id=None),
    }


@pytest.fixture
def sous_org():
    """Pose l'org de l'appel (`_org=` côté agent) le temps d'un bloc."""
    from contextlib import contextmanager

    from oto_mcp import session_org

    @contextmanager
    def _sous(org_id: int):
        jeton = session_org.set_call_org(org_id)
        try:
            yield
        finally:
            session_org.reset_call_org(jeton)
    return _sous


def _ids(entrees) -> set[int]:
    return {int(e["id"]) for e in entrees}


def _mes_tableaux(monde) -> set[int]:
    return {monde[k] for k in ("perso_a", "perso_b", "perso_null", "org_a")}


def test_store_liste_le_personnel_dans_son_org_et_le_null_partout(monde, sous_org):
    from oto_mcp.datastore.core import make_store
    with sous_org(monde["a"]):
        dans_a = _ids(make_store(SUB).list_datastores()) & _mes_tableaux(monde)
    with sous_org(monde["b"]):
        dans_b = _ids(make_store(SUB).list_datastores()) & _mes_tableaux(monde)
    assert dans_a == {monde["perso_a"], monde["perso_null"], monde["org_a"]}
    assert dans_b == {monde["perso_b"], monde["perso_null"]}


def test_depuis_b_le_tableau_de_a_s_ouvre_par_son_numero_et_son_nom(monde, sous_org):
    from oto_mcp import db, ownership
    from oto_mcp.datastore.core import make_store
    ns_a = monde["perso_a"]
    nom_a = db.get_datastore_by_id(ns_a)["datastore"]
    with sous_org(monde["b"]):
        store = make_store(SUB)
        assert store.resolve_ns_id(str(ns_a)) == ns_a
        assert store.resolve_ns_id(nom_a) == ns_a
        store.upsert_row(str(ns_a), "k1", {"a": 1})
        assert [r["a"] for r in store.list_rows(str(ns_a))] == [1]
        assert ownership.can_access(SUB, ownership.TYPE_RESSOURCE_DATASTORE,
                                    str(ns_a), "write")


def test_face_rest_get_api_datastores_suit_x_oto_org(monde):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from oto_mcp.api import routes as api_routes

    class _Claims:
        claims = {"sub": SUB, "email": f"{SUB}@t.invalid", "name": SUB}

    class _Verifier:
        async def verify_token(self, token):
            return _Claims()

    # `X-Oto-Org` est lu par `ViewAsMiddleware`, comme en production.
    client = TestClient(api_routes.ViewAsMiddleware(
        Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)),
        verifier=_Verifier()))

    def liste(org: int) -> set[int]:
        r = client.get("/api/datastores", headers={"Authorization": f"Bearer {SUB}",
                                                   "X-Oto-Org": str(org)})
        assert r.status_code == 200, r.text
        return _ids(r.json()["datastores"]) & _mes_tableaux(monde)

    assert liste(monde["a"]) == {monde["perso_a"], monde["perso_null"], monde["org_a"]}
    assert liste(monde["b"]) == {monde["perso_b"], monde["perso_null"]}
    r = client.get(f"/api/datastores/{monde['perso_a']}/rows",
                   headers={"Authorization": f"Bearer {SUB}", "X-Oto-Org": str(monde["b"])})
    assert r.status_code == 200, r.text


def test_face_mcp_data_list_datastores(monde, sous_org, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as surface
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: SUB)
    mcp = FastMCP("l160")
    surface.register(mcp)
    outil = asyncio.run(mcp.get_tool("data_list_datastores")).fn
    with sous_org(monde["b"]):
        dans_b = _ids(outil()["datastores"]) & _mes_tableaux(monde)
    assert dans_b == {monde["perso_b"], monde["perso_null"]}


def test_la_recherche_reste_en_parite_avec_la_liste(monde, sous_org):
    from oto_mcp import search
    from oto_mcp.datastore.core import make_store
    for org in (monde["a"], monde["b"]):
        with sous_org(org):
            liste = _ids(make_store(SUB).list_datastores())
        assert _ids(search._accessible_namespaces(SUB, org)) == liste


def test_le_filtre_ne_touche_que_les_personnels_de_l_appelant():
    from oto_mcp import ownership
    lignes = [
        {"id": 1, "owner_type": "user", "owner_id": "moi", "context_org_id": 7},
        {"id": 2, "owner_type": "user", "owner_id": "moi", "context_org_id": 8},
        {"id": 3, "owner_type": "user", "owner_id": "moi", "context_org_id": None},
        {"id": 4, "owner_type": "user", "owner_id": "moi"},          # ligne sans la clé
        {"id": 5, "owner_type": "user", "owner_id": "autre", "context_org_id": 8},
        {"id": 6, "owner_type": "org", "owner_id": "7", "context_org_id": 8},
    ]
    assert [n["id"] for n in ownership.tableaux_du_contexte("moi", 7, lignes)] \
        == [1, 3, 4, 5, 6]
