"""Les champs que l'appelant n'écrit pas (#586, #606) — l'EFFET EN BASE, sur un
vrai PostgreSQL.

Le banc stubé (`test_champs_reserves_586_606.py`) prouve la règle ; celui-ci prouve
ce que porte la base, jamais ce que le store a bien voulu rendre (même parti que
`test_write_by_id_effect`) : la couche posée par le système est bien dans le blob et
se lit par son adresse (`raison_sociale.origine`), un refus ne laisse aucune trace,
et `data_patch_schema` pose ET lève un cran sans réécrire — la levée ne touchant
aucune ligne.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp.datastore.errors import RowValidationError


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_res_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


SCHEMA = {
    "key": "siren",
    "fields": [
        {"key": "siren", "type": "text"},
        {"key": "raison_sociale", "type": "text", "origine": "system"},
        {"key": "adresse", "type": "text", "readonly": True,
         "report_to": "notes_verification"},
        {"key": "notes_verification", "type": "text"},
    ],
}


def _store():
    from oto_mcp.datastore.core import make_store
    return make_store("sub-test")


@pytest.fixture
def table(live):
    """Un tableau sous les deux crans, et UNE ligne remise par le client."""
    from oto_mcp import db
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore_namespace("user", "sub-test", ns)
    st = _store()
    pose = st.set_schema(ns, SCHEMA)
    assert {"readonly", "origine"} <= set(pose["enforced"])
    row = st.append_row(ns, {"siren": "552032534", "raison_sociale": "TEMOIN",
                             "adresse": "1 rue A"})
    return st, ns, ns_id, row["_id"]


def _donnees(ns_id: int, row_id: str) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute(
            "SELECT data FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id)).fetchone()
    return dict((r or {}).get("data") or {})


def test_l_origine_posee_est_dans_le_blob_et_se_lit_par_son_adresse(table):
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {"raison_sociale": "TEMOIN SA"})
    st.update_row(ns, rid, {"raison_sociale": "TEMOIN GROUP"})
    assert _donnees(ns_id, rid)["raison_sociale"] == {"valeur": "TEMOIN GROUP",
                                                     "origine": "TEMOIN"}
    page = st.page_rows(ns, offset=0, limit=10,
                        filters=[{"field": "raison_sociale.origine", "op": "eq",
                                  "value": "TEMOIN"}])
    assert [r["_id"] for r in page["rows"]] == [rid]
    assert page["rows"][0]["raison_sociale.origine"] == "TEMOIN"


def test_un_refus_ne_laisse_AUCUNE_trace(table):
    st, ns, ns_id, rid = table
    avant = _donnees(ns_id, rid)
    with pytest.raises(RowValidationError, match="`adresse`"):
        st.update_row(ns, rid, {"adresse": "2 rue B", "notes_verification": "x"})
    with pytest.raises(RowValidationError, match="raison_sociale.origine"):
        st.write_rows(ns, [{"siren": "552032534",
                            "raison_sociale": {"origine": "moi"}}])
    assert _donnees(ns_id, rid) == avant


def test_patch_schema_pose_et_leve_sans_toucher_les_lignes(table):
    """Lever le cran par `null` : le schéma ne le porte plus, la couche déjà posée
    reste — et l'appelant retrouve la main sur l'origine."""
    st, ns, ns_id, rid = table
    st.update_row(ns, rid, {"raison_sociale": "TEMOIN SA"})
    leve = st.patch_schema(ns, fields=[{"key": "raison_sociale", "origine": None},
                                       {"key": "adresse", "readonly": None}])
    assert leve["updated"] == ["raison_sociale", "adresse"]
    assert leve.get("declarations_effacees", []) == []      # une levée n'est pas une perte
    assert _donnees(ns_id, rid)["raison_sociale"] == {"valeur": "TEMOIN SA",
                                                     "origine": "TEMOIN"}
    st.update_row(ns, rid, {"adresse": "2 rue B",
                            "raison_sociale": {"origine": "moi"}})
    d = _donnees(ns_id, rid)
    assert d["adresse"] == "2 rue B" and d["raison_sociale"]["origine"] == "moi"
    repose = st.patch_schema(ns, fields=[{"key": "adresse", "readonly": True}])
    assert "readonly" in repose["enforced"]
    with pytest.raises(RowValidationError):
        st.update_row(ns, rid, {"adresse": "3 rue C"})
