"""Purger une colonne d'un tableau (oto-backend#296).

Après un renommage de champs, l'ancienne colonne reste dans les données : le
schéma la sort de la vue, mais la clé est toujours dans chaque ligne, elle se rend
encore à la lecture, et son nom décrit souvent le contenu mieux que le nouveau
(`actualite_business` parle plus que `analyse2`). Trois agents successifs ont écrit
dedans en croyant viser juste. Sans geste de purge, le seul recours est de nommer
les colonnes mortes dans la procédure — de la dette portée à chaque contexte.

Deux étages : les GARDES du geste (destructif → stub suffit) et le SQL, exercé
contre un vrai PostgreSQL — `data - key` est ce qui distingue effacer de mettre à
`null`, et aucun stub ne le prouve.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import core as dsm
from oto_mcp.datastore.core import DatastorePg


STRICT = {"strict": True, "fields": [{"key": "siren", "type": "text"},
                                     {"key": "analyse1", "type": "text"}]}


@pytest.fixture()
def store(monkeypatch):
    st = DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    dropped = []
    monkeypatch.setattr(dsm.db, "get_datastore_namespace_by_id",
                        lambda ns_id: {"id": ns_id, "schema": STRICT})
    monkeypatch.setattr(dsm.db, "datastore_drop_column",
                        lambda ns_id, key: (dropped.append((ns_id, key)) or 12))
    return st, dropped


# ── gardes du geste ──────────────────────────────────────────────────────────

def test_without_confirm_nothing_is_touched(store):
    st, dropped = store
    with pytest.raises(ValueError, match="non confirmée"):
        st.drop_column("v", "actualite_sociale", confirm=False)
    assert dropped == []


def test_with_confirm_it_purges_and_counts(store):
    st, dropped = store
    out = st.drop_column("v", "actualite_sociale", confirm=True)
    assert dropped == [(7, "actualite_sociale")]
    assert out == {"namespace": "v", "key": "actualite_sociale", "rows": 12}


def test_a_field_still_declared_is_refused(store):
    """Un `confirm` ne protège pas d'une faute de nom : la colonne vivante est
    hors d'atteinte tant que le schéma la déclare."""
    st, dropped = store
    with pytest.raises(ValueError, match="encore DÉCLARÉE"):
        st.drop_column("v", "analyse1", confirm=True)
    assert dropped == []


def test_platform_columns_are_not_data(store):
    st, dropped = store
    with pytest.raises(ValueError, match="gérée par la plateforme"):
        st.drop_column("v", "_id", confirm=True)
    assert dropped == []


def test_empty_key_is_refused(store):
    st, _ = store
    with pytest.raises(ValueError, match="key requise"):
        st.drop_column("v", "   ", confirm=True)


# ── le SQL, contre un vrai PostgreSQL ────────────────────────────────────────

@pytest.fixture()
def pg_rows(pg_dsn, monkeypatch):
    """Une table `datastore_rows` minimale + `db.datastore._connect` redirigé
    dessus : c'est la VRAIE fonction et son vrai SQL qui s'exécutent."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    from oto_mcp.db import datastore as dbds

    conn = psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True)
    conn.execute("DROP TABLE IF EXISTS datastore_rows")
    conn.execute("CREATE TABLE datastore_rows ("
                 " ns_id BIGINT NOT NULL, row_id TEXT NOT NULL, data JSONB NOT NULL,"
                 " created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                 " PRIMARY KEY (ns_id, row_id))")

    class _Ctx:
        def __enter__(self): return conn
        def __exit__(self, *a): return False

    monkeypatch.setattr(dbds, "_connect", lambda *a, **k: _Ctx())
    try:
        yield conn, dbds
    finally:
        conn.close()


def _insert(conn, rows):
    import json
    for i, data in enumerate(rows):
        conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                     "VALUES (%s, %s, %s::jsonb)", (7, f"r{i}", json.dumps(data)))


def test_drop_column_erases_the_key_not_just_its_value(pg_rows):
    """LE point : `null` conserve la clé (donc elle se rend encore), `data - key`
    la fait disparaître."""
    conn, dbds = pg_rows
    _insert(conn, [{"siren": "1", "actualite_sociale": "a"},
                   {"siren": "2", "actualite_sociale": None}])
    assert dbds.datastore_drop_column(7, "actualite_sociale") == 2
    keys = [set(r["data"].keys()) for r in
            conn.execute("SELECT data FROM datastore_rows ORDER BY row_id").fetchall()]
    assert keys == [{"siren"}, {"siren"}]


def test_drop_column_only_rewrites_rows_that_carry_it(pg_rows):
    conn, dbds = pg_rows
    _insert(conn, [{"siren": "1", "vieux": "x"}, {"siren": "2"}, {"siren": "3"}])
    assert dbds.datastore_drop_column(7, "vieux") == 1     # une seule ligne touchée
    assert dbds.datastore_drop_column(7, "vieux") == 0     # rejeu = no-op


def test_drop_column_leaves_other_namespaces_alone(pg_rows):
    conn, dbds = pg_rows
    import json
    conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                 "VALUES (8, 'x', %s::jsonb)", (json.dumps({"vieux": "garde"}),))
    _insert(conn, [{"vieux": "purge"}])
    dbds.datastore_drop_column(7, "vieux")
    other = conn.execute("SELECT data FROM datastore_rows WHERE ns_id = 8").fetchone()
    assert other["data"] == {"vieux": "garde"}


def test_row_keys_lists_what_lives_in_the_data(pg_rows):
    conn, dbds = pg_rows
    _insert(conn, [{"siren": "1", "analyse1": "a"},
                   {"siren": "2", "actualite_sociale": "vieux"}])
    assert dbds.datastore_row_keys(7) == ["actualite_sociale", "analyse1", "siren"]


def test_set_schema_warns_about_the_columns_it_leaves_behind(pg_rows, monkeypatch):
    """L'avertissement tombe à la POSE du schéma — le moment où le renommage arme
    le piège."""
    conn, _ = pg_rows
    _insert(conn, [{"siren": "1", "actualite_sociale": "vieux"}])
    st = DatastorePg("u", acting_org=35)
    msg = st._orphan_columns_warning(7, STRICT)
    assert msg and "`actualite_sociale`" in msg and "data_drop_column" in msg
    # souple = le champ libre est un droit du contrat : rien à signaler
    assert st._orphan_columns_warning(7, {"fields": [{"key": "siren"}]}) is None
