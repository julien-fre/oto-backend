"""Les compteurs de N colonnes en UN appel (`count_by`) — `GET /api/datastores/{ds}/counts`
et `data_aggregate(count_by=…)`, sur une base réelle.

Le besoin mesuré (16/10/2026) : un écran qui affiche au-dessus de sa vue filtrée les
compteurs de 20 à 30 colonnes les demandait un `/aggregate` à la fois — chaque appel
relisait tout le jeu filtré. Ici une seule passe SQL, et le banc tient ce que la réponse
DOIT dire : les mêmes nombres que `group_by` colonne par colonne, les vides sous toutes
leurs formes stockées, une liste coupée qui le dit, et les refus nommés.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

SUB = "usr_counts_multi"
SCHEMA = {"fields": [
    {"key": "statut", "type": "enum", "options": ["a", "b", "c"]},
    {"key": "ville", "type": "text"},
    {"key": "secteur", "type": "text"},
]}
LIGNES = [
    {"statut": "a", "ville": "Lyon", "secteur": "x"},
    {"statut": "a", "ville": "Lyon"},
    {"statut": "b", "ville": {"valeur": "Paris", "comment": "vérifié"}},
    {"statut": "b", "ville": ""},
    {"statut": "a", "ville": None},
    {"statut": "c", "ville": {"valeur": "", "comment": "inconnue"}},
    {"statut": "a", "ville": "Nantes", "secteur": "y"},
]


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@counts.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


@pytest.fixture(scope="module")
def ns(live):
    import psycopg
    import json
    import os

    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@counts.invalid", name=SUB)
    ns_id = db.create_datastore("user", SUB, "counts")
    db.set_datastore_schema(ns_id, SCHEMA)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        for i, d in enumerate(LIGNES):
            c.execute("INSERT INTO datastore_rows (ns_id,row_id,data) VALUES (%s,%s,%s)",
                      (ns_id, f"r{i}", json.dumps(d)))
    return ns_id


@pytest.fixture(scope="module")
def store(ns):
    from oto_mcp.datastore.core import make_store
    return make_store(SUB)


@pytest.fixture(scope="module")
def client(ns):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))


def test_les_memes_nombres_que_group_by_colonne_par_colonne(store, ns):
    c = store.count_by(str(ns), ["statut", "ville"])
    assert c["statut"] == {"values": [{"value": "a", "count": 4},
                                      {"value": "b", "count": 2},
                                      {"value": "c", "count": 1}],
                           "empty": 0, "distinct": 3, "total": 7, "truncated": False}
    groupes = {g["statut"]: g["count"] for g in store.aggregate(str(ns), group_by="statut")}
    assert {v["value"]: v["count"] for v in c["statut"]["values"]} == groupes


def test_un_vide_est_un_vide_quelle_que_soit_sa_forme_stockee(store, ns):
    """Absente, `null`, `""`, ou une couche dont la valeur est vide : quatre formes
    stockées, un seul « vide » servi — et la couche à valeur posée compte sa valeur."""
    ville = store.count_by(str(ns), ["ville"])["ville"]
    assert ville["empty"] == 3
    assert ville["values"] == [{"value": "Lyon", "count": 2},
                               {"value": "Nantes", "count": 1},
                               {"value": "Paris", "count": 1}]
    assert ville["total"] == 7 == ville["empty"] + sum(v["count"] for v in ville["values"])
    assert store.count_by(str(ns), ["secteur"])["secteur"]["empty"] == 5


def test_une_liste_coupee_le_dit(store, ns):
    s = store.count_by(str(ns), ["statut"], values_limit=1)["statut"]
    assert s["values"] == [{"value": "a", "count": 4}]
    assert s["distinct"] == 3 and s["truncated"] is True


def test_sous_les_memes_filtres_que_les_lignes(store, ns):
    """Filtre amincissable (colonne nue), filtre qui ne l'est pas (couche), et `q` :
    le total vaut le nombre de lignes du MÊME jeu filtré."""
    f = [{"field": "statut", "op": "eq", "value": "a"}]
    c = store.count_by(str(ns), ["ville"], filters=f)["ville"]
    assert c["total"] == store.page_rows(str(ns), filters=f)["total"] == 4
    fc = [{"field": "ville.comment", "op": "not_empty"}]
    c = store.count_by(str(ns), ["statut"], filters=fc)["statut"]
    assert c["total"] == 2 and {v["value"] for v in c["values"]} == {"b", "c"}
    assert store.count_by(str(ns), ["statut"], filter={"statut": "b"})["statut"]["total"] == 2


@pytest.mark.parametrize("champs,motif", [
    (["inconnue"], "not declared"),
    (["ville.comment"], "pas un nom de colonne nu"),
    (["ville", "ville"], "deux fois"),
    ([], "LISTE NON VIDE"),
])
def test_refus_nommes(store, ns, champs, motif):
    with pytest.raises(ValueError, match=motif):
        store.count_by(str(ns), champs)


@pytest.mark.parametrize("borne", [0, 501])
def test_values_limit_borne(store, ns, borne):
    with pytest.raises(ValueError, match="values_limit"):
        store.count_by(str(ns), ["statut"], values_limit=borne)


def test_route_rest(client, ns):
    h = {"Authorization": f"Bearer {SUB}"}
    r = client.get(f"/api/datastores/{ns}/counts",
                   params={"fields": "statut,ville", "values_limit": 2}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()["counts"]
    assert body["statut"]["truncated"] is True and len(body["statut"]["values"]) == 2
    assert body["ville"]["empty"] == 3
    r = client.get(f"/api/datastores/{ns}/counts", params={"fields": "inconnue"},
                   headers=h)
    assert r.status_code == 400 and "invalid_counts" in r.text


def test_outil_mcp_count_by_exclusif(ns, monkeypatch):
    from fastmcp import FastMCP
    from oto_mcp.mcp_errors import McpError

    from oto_mcp import access
    from oto_mcp.tools import datastore as tools_ds
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: SUB)
    mcp = FastMCP("counts")
    tools_ds.register(mcp)
    agg = asyncio.run(mcp.get_tool("data_aggregate")).fn
    out = agg(datastore=str(ns), count_by=["statut"])
    assert out["counts"]["statut"]["total"] == 7
    with pytest.raises(McpError, match="does not combine"):
        agg(datastore=str(ns), count_by=["statut"], group_by="ville")
    with pytest.raises(McpError, match="only applies with"):
        agg(datastore=str(ns), values_limit=3)
