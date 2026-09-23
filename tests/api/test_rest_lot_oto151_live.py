"""Le lot sur la face REST : `POST /api/datastores/{datastore}/rows/batch` (oto#151).

**Le fait.** Un client REST pur a rechargé ~8 900 lignes. Il a posté `{"rows": […],
"key": …}` sur la route d'UNE ligne — le corps que l'écriture agent lui suggérait —,
reçu `business_key_required` pour une clé qu'il venait d'envoyer, et fini en 8 907
`PATCH` ligne à ligne. Aucune route de lot n'existait : sa conclusion était juste.

Ce banc tourne sur la table de routes RÉELLE (`make_routes` + adaptateur) et sur le
tool MCP monté, contre un vrai PostgreSQL, et lit `datastore_rows`. Ce qu'il fige :

- le lot upsert sur la clé métier déclarée, ou sur `key` passé à l'appel ;
- `donnees_d_origine=true` pose l'origine, et un ré-import ne la touche pas ;
- un mot refusé (`@keep` après sa date) refuse le lot ENTIER : rien d'écrit ;
- une ligne refusée par le schéma est NOMMÉE (rang, reste écrit avant l'arrêt) ;
- les deux faces rendent la même enveloppe et les mêmes notices ;
- un champ inconnu du corps est refusé, jamais ignoré.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@lot.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


SUB = "usr_lot151"
SCHEMA = {"key": "siren", "fields": [{"key": "siren", "type": "text"},
                                     {"key": "nom", "type": "text"},
                                     {"key": "effectif", "type": "number"}]}


def _h() -> dict:
    return {"Authorization": f"Bearer {SUB}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_lot151_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        from oto_mcp import db
        db.upsert_user(SUB, email=f"{SUB}@lot.invalid", name=SUB)
        yield
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


def _table(schema=None):
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", SUB, ns)
    if schema is not None:
        make_store(SUB).set_schema(ns, schema)
    return ns, ns_id


def _base(ns_id: int) -> dict:
    """Ce que porte LA BASE, par siren — jamais ce que l'appel a bien voulu rendre."""
    from oto_mcp.datastore import schema as dsv2
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        rows = [dict(r["data"]) for r in conn.execute(
            "SELECT data FROM datastore_rows WHERE ns_id = %s", (ns_id,)).fetchall()]
    return {dsv2.unwrap(r.get("siren")): r for r in rows}


def _lot(client, ns, corps, query: str = ""):
    return client.post(f"/api/datastores/{ns}/rows/batch{query}", headers=_h(), json=corps)


@pytest.fixture
def data_write(live, monkeypatch):
    """Le tool MCP `data_write` monté, tel qu'un agent l'appelle."""
    import asyncio

    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.tools import datastore as tools_ds
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: SUB)
    mcp = FastMCP("test")
    tools_ds.register(mcp)
    return asyncio.run(mcp.get_tool("data_write")).fn


# ── le lot, par la clé métier ─────────────────────────────────────────────────

def test_le_lot_upsert_sur_la_cle_metier_declaree(client):
    ns, ns_id = _table(SCHEMA)
    r1 = _lot(client, ns, {"rows": [{"siren": "1", "nom": "A"}, {"siren": "2", "nom": "B"}]})
    assert r1.status_code == 200, r1.text
    rep = r1.json()
    assert (rep["inserted"], rep["updated"], rep["count"]) == (2, 0, 2)
    assert rep["key"] == "siren" and len(rep["ids"]) == 2
    assert rep["ns_id"] == ns_id and rep["datastore"] == ns
    # Ré-envoi : la ligne retrouvée par sa clé est FUSIONNÉE, la neuve est créée.
    r2 = _lot(client, ns, {"rows": [{"siren": "2", "nom": "B bis"}, {"siren": "3", "nom": "C"}]})
    assert r2.status_code == 200, r2.text
    assert (r2.json()["inserted"], r2.json()["updated"]) == (1, 1)
    base = _base(ns_id)
    assert sorted(base) == ["1", "2", "3"]
    assert base["2"]["nom"] == "B bis"


def test_le_lot_dedoublonne_sur_la_cle_passee_a_l_appel(client):
    """Sans clé déclarée, `key` du corps dédoublonne — le cas exact du rapport."""
    ns, ns_id = _table()
    corps = {"rows": [{"siren": "9", "nom": "X"}], "key": "siren"}
    assert _lot(client, ns, corps).json()["inserted"] == 1
    r = _lot(client, ns, {"rows": [{"siren": "9", "nom": "Y"}], "key": "siren"})
    assert (r.json()["inserted"], r.json()["updated"]) == (0, 1)
    base = _base(ns_id)
    assert list(base) == ["9"] and base["9"]["nom"] == "Y"
    # `rows` et `key` n'ont PAS fini en colonnes.
    assert "rows" not in base["9"] and "key" not in base["9"]


# ── l'import ──────────────────────────────────────────────────────────────────

def test_donnees_d_origine_pose_l_origine_et_le_reimport_ne_la_touche_pas(client):
    from oto_mcp.datastore import schema as dsv2
    ns, ns_id = _table(SCHEMA)
    r1 = _lot(client, ns, {"rows": [{"siren": "1", "nom": {"valeur": "DUPONT",
                                                          "comment": "fichier du 05/08"}}],
                           "donnees_d_origine": True})
    assert r1.status_code == 200, r1.text
    assert any("origine POSÉE" in n and "`nom`" in n for n in r1.json()["notices"])
    cell = _base(ns_id)["1"]["nom"]
    origine = dsv2.layer_value(cell, dsv2.ORIGIN_LAYER)
    assert dsv2.unwrap(origine) == "DUPONT"
    assert dsv2.layer_value(origine, "comment") == "fichier du 05/08"
    # Ré-import, valeur changée : le courant bouge, l'origine reste figée — et c'est dit.
    r2 = _lot(client, ns, {"rows": [{"siren": "1", "nom": "Dupont SAS"}]},
              query="?donnees_d_origine=true")
    assert r2.status_code == 200, r2.text
    assert r2.json()["updated"] == 1
    assert any("NON posée" in n and "déjà posée" in n for n in r2.json()["notices"])
    cell = _base(ns_id)["1"]["nom"]
    assert dsv2.unwrap(cell) == "Dupont SAS"
    assert dsv2.unwrap(dsv2.layer_value(cell, dsv2.ORIGIN_LAYER)) == "DUPONT"


# ── rien d'écrit à moitié, et les refus nommés ───────────────────────────────

def test_un_mot_refuse_refuse_le_lot_entier_rien_n_est_ecrit(client, monkeypatch):
    """J3 franchi : `@keep` sur la DEUXIÈME ligne refuse le lot avant la première."""
    from oto_mcp.datastore import mots_deprecies as mdp
    monkeypatch.setenv(mdp.ENV_MOTS_DEPRECIES_REFUSES_LE, "2026-01-01")
    ns, ns_id = _table(SCHEMA)
    r = _lot(client, ns, {"rows": [{"siren": "1", "nom": "A"},
                                   {"siren": "2", "nom": "@keep"}]})
    assert r.status_code == 400, r.text
    assert "@keep" in r.json()["detail"]
    assert _base(ns_id) == {}


def test_une_ligne_refusee_est_nommee_et_le_refus_dit_ou_reprendre(client):
    ns, ns_id = _table({**SCHEMA, "strict": True})
    r = _lot(client, ns, {"rows": [{"siren": "1", "effectif": 3},
                                   {"siren": "2", "effectif": "beaucoup"}]})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "row_invalid"
    assert "ligne 2/2" in r.json()["detail"] and "reprends le lot à la ligne 2" in r.json()["detail"]
    assert sorted(_base(ns_id)) == ["1"]


def test_un_champ_inconnu_du_corps_est_refuse(client):
    ns, ns_id = _table(SCHEMA)
    r = _lot(client, ns, {"rows": [{"siren": "1"}], "cle": "siren"})
    assert (r.status_code, r.json()["error"]) == (400, "unknown_fields")
    assert _base(ns_id) == {}


# ── les deux faces, un seul geste ────────────────────────────────────────────

def test_les_deux_faces_rendent_la_meme_enveloppe_et_les_memes_notices(client, data_write):
    """Avant sa date, `@keep` passe avec un avertissement daté : une notice, qui doit
    être la même phrase sur les deux faces."""
    lot = [{"siren": "1", "nom": "A"}, {"siren": "2", "nom": {"valeur": "B",
                                                           "comment": "@keep"}}]
    ns_rest, _ = _table(SCHEMA)
    ns_mcp, _ = _table(SCHEMA)
    rest = _lot(client, ns_rest, {"rows": lot})
    assert rest.status_code == 200, rest.text
    mcp = data_write(datastore=ns_mcp, rows=lot)
    rest = rest.json()
    assert rest["notices"] and rest["notices"] == mcp["notices"]
    for cle in ("inserted", "updated", "count", "key"):
        assert rest[cle] == mcp[cle], cle
