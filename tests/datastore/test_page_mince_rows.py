"""La page de `/rows` au tri par défaut ne classe plus tout le tableau.

`page_rows` sans recherche, triée sur une colonne méta (`_created_at` par défaut,
`_updated_at`, `_id`) et sans filtre amincissable retombait sur `datastore_list_rows`,
qui numérote TOUT le jeu (`row_number() OVER … ORDER BY rn`) avant de couper : un tri
complet de lignes entières, quel que soit `limit`. `datastore_page_mince` coupe sur les
clés (`LIMIT`/`OFFSET`, rang par `WITH ORDINALITY`), puis ne relit que la page.

Deux choses sont vérifiées : la FORME de la requête (ni fenêtre, ni `jsonb_each` sur
tout le tableau), et l'ÉQUIVALENCE des lignes rendues avec l'ancien chemin, sur un vrai
PostgreSQL. Le total ne change pas : exact par défaut, borné par `total_max`
(`test_total_borne.py`).

Données 100 % fictives.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest

SUB = "usr_page_mince_rows"


# ── forme de la requête, sans base ───────────────────────────────────────────

def _sql_de(monkeypatch, **kw) -> tuple[str, tuple]:
    from oto_mcp.db import datastore as dsdb

    vu: dict = {}

    class _Res:
        @staticmethod
        def fetchone():
            return {"lignes": []}

    class _Conn:
        @staticmethod
        def execute(sql, params=()):
            vu["sql"], vu["params"] = sql, params
            return _Res()

    @contextmanager
    def _faux_connect():
        yield _Conn()

    monkeypatch.setattr(dsdb, "_connect", _faux_connect)
    assert dsdb.datastore_page_mince(7, **kw) == []
    return vu["sql"], vu["params"]


@pytest.mark.parametrize("order_by", [None, "_created_at", "_updated_at", "_id"])
def test_la_page_mince_n_a_ni_fenetre_ni_lecture_de_tout_data(monkeypatch, order_by):
    sql, params = _sql_de(monkeypatch, offset=40, limit=20, order_by=order_by,
                          filters=[{"field": "_id", "op": "ne", "value": "r-x"}])
    bas = sql.lower()
    assert "row_number" not in bas and " over " not in bas and "window" not in bas
    assert "jsonb_each" not in bas and "materialized" not in bas
    assert "limit %s offset %s" in bas and "with ordinality" in bas
    # Le `ns_id` borne la coupe ET la jointure (row_id n'est unique que par tableau).
    assert params[0] == 7 and params[-3:] == (20, 40, 7)


def test_un_tri_sur_une_colonne_utilisateur_n_est_pas_servi(monkeypatch):
    from oto_mcp.db import datastore as dsdb

    with pytest.raises(ValueError, match="meta column"):
        dsdb.datastore_page_mince(7, limit=10, order_by="nom")


# ── aiguillage de `page_rows`, sans base ─────────────────────────────────────

class _FauxDb:
    def __init__(self):
        self.appels: list[tuple[str, dict]] = []

    def datastore_page_with_stats(self, ns_id, **kw):
        return None  # pas amincissable : le cas où le chemin mince s'applique

    def datastore_page_mince(self, ns_id, **kw):
        self.appels.append(("mince", kw))
        return []

    def datastore_list_rows(self, ns_id, **kw):
        self.appels.append(("liste", kw))
        return []

    def datastore_count_rows(self, ns_id, **kw):
        self.appels.append(("compte", kw))
        return 12

    def datastore_order_health(self, ns_id, **kw):
        return {"off_type": 0, "empty": 0}


@pytest.fixture
def aiguillage(monkeypatch):
    from oto_mcp.datastore import core as ds
    from oto_mcp.datastore import lecture as dsl

    faux = _FauxDb()
    monkeypatch.setattr(dsl, "db", faux)
    store = ds.DatastorePg.__new__(ds.DatastorePg)
    store._resolve = lambda ns, write=False: 1
    store._schema_of = lambda ns_id: None
    store._row_to_dict = lambda r, *a, **k: r
    return store, faux


@pytest.mark.parametrize("order_by", [None, "_created_at", "_updated_at", "_id"])
def test_le_tri_par_defaut_prend_le_chemin_mince(aiguillage, order_by):
    store, faux = aiguillage
    page = store.page_rows("t", order_by=order_by, offset=5, limit=10)
    assert [n for n, _ in faux.appels] == ["mince", "compte"]
    assert faux.appels[1][1]["jusqua"] is None, "sans `total_max`, le total reste exact"
    assert set(page) == {"rows", "total", "offset", "limit", "versions_servies"}
    assert page["total"] == 12


def test_total_max_borne_le_compte_du_chemin_mince(aiguillage):
    store, faux = aiguillage
    page = store.page_rows("t", limit=10, total_max=3)
    assert faux.appels[1] == ("compte", {"filters": None, "jusqua": 4})
    assert page["total"] == 3 and page["total_capped"] is True


@pytest.mark.parametrize("kw", [{"q": "nord"}, {"order_by": "nom"}])
def test_recherche_ou_tri_utilisateur_gardent_l_ancien_chemin(aiguillage, kw):
    store, faux = aiguillage
    store.page_rows("t", limit=10, **kw)
    assert faux.appels[0][0] == "liste"


# ── équivalence des lignes, vrai PostgreSQL ──────────────────────────────────

@pytest.fixture(scope="module")
def tableaux(live):
    """Deux tableaux qui portent les MÊMES `row_id` : la jointure ne doit pas les
    mêler. Horodatages en doublons pour que le départage par `row_id` compte."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    db.upsert_user(SUB, email=f"{SUB}@page-mince.invalid", name=SUB)
    ids = []
    for _ in range(2):
        ns_id = db.create_datastore("user", SUB, "tmince-" + uuid.uuid4().hex[:6])
        ids.append(ns_id)
        with _connect() as conn:
            for i in range(60):
                conn.execute(
                    "INSERT INTO datastore_rows (ns_id, row_id, data, created_at, "
                    "updated_at) VALUES (%s, %s, %s::jsonb, "
                    "'2026-09-01 10:00:00+00'::timestamptz + %s * interval '1 second', "
                    "'2026-09-02 10:00:00+00'::timestamptz - %s * interval '1 second')",
                    (ns_id, f"r-{(i * 37) % 60:03d}",
                     json.dumps({"nom": f"ligne {i}", "ns": ns_id}), i // 3, i // 4))
    return ids


@pytest.mark.parametrize("order_by", [None, "_created_at", "_updated_at", "_id"])
@pytest.mark.parametrize("order_dir", ["asc", "desc"])
@pytest.mark.parametrize("offset,limit", [(0, 25), (17, 10), (55, 25), (500, 10)])
def test_memes_lignes_que_l_ancien_chemin(tableaux, order_by, order_dir, offset, limit):
    from oto_mcp import db

    ns_id = tableaux[0]
    for filtres in (None, [{"field": "_id", "op": "ne", "value": "r-007"}]):
        mince = db.datastore_page_mince(ns_id, offset=offset, limit=limit,
                                        order_by=order_by, order_dir=order_dir,
                                        filters=filtres)
        ancien = db.datastore_list_rows(ns_id, offset=offset, limit=limit,
                                        order_by=order_by, order_dir=order_dir,
                                        filters=filtres)
        assert mince == ancien
        assert all(r["data"]["ns"] == ns_id for r in mince)


def test_page_rows_rend_la_meme_reponse_par_les_deux_chemins(tableaux, monkeypatch):
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store

    store = make_store(SUB)
    ns = str(tableaux[0])
    mince = store.page_rows(ns, offset=10, limit=20)
    monkeypatch.setattr(db, "datastore_page_mince",
                        lambda ns_id, **kw: db.datastore_list_rows(ns_id, **kw))
    ancien = store.page_rows(ns, offset=10, limit=20)
    assert mince == ancien and mince["total"] == 60
