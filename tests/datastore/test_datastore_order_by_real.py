"""Trier par une colonne user — contre un VRAI PostgreSQL, parce que c'est là que ça
cassait.

`data_rows(order_by="nom")` levait une erreur SQL en production : depuis que lire une
colonne est un `COALESCE` à deux emplacements (plate ou à couches), le tri n'en
fournissait qu'un seul paramètre. La requête ne partait donc jamais.

Le banc de tri existant ne pouvait pas le voir : il stubbe le SQL et vérifie quel
CHEMIN de code est pris — keyset ou offset —, jamais que la requête s'exécute. C'est la
lacune connue de ce dépôt, dans sa forme la plus coûteuse : le test était vert, la
fonctionnalité morte.
"""
from __future__ import annotations

import json

import psycopg
import pytest


def _ddl() -> str:
    from oto_mcp.db import _schema
    s = _schema._SCHEMA
    i = s.index("CREATE TABLE IF NOT EXISTS datastore_rows")
    return s[i:s.index("\n);", i) + 3].replace(
        "REFERENCES user_datastores(id) ON DELETE CASCADE", "")


@pytest.fixture()
def pg(pg_dsn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    from oto_mcp.db import _conn
    monkeypatch.setattr(_conn, "_database_url", lambda: pg_dsn)
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS datastore_rows")
        c.execute(_ddl())
        for rid, data in [
            ("plate", {"nom": "Bernard"}),
            ("couches", {"nom": {"valeur": "Alice", "origine": "socle"}}),
            ("liste", {"nom": "Zoe", "contacts": [{"nom": "Aaron"}]}),
        ]:
            c.execute("INSERT INTO datastore_rows (ns_id,row_id,data) "
                      "VALUES (1,%s,%s::jsonb)", (rid, json.dumps(data)))
        yield c
        c.execute("DROP TABLE IF EXISTS datastore_rows")


def _order(champ: str, sens: str = "asc") -> list:
    from oto_mcp import db
    return [r["row_id"] for r in db.datastore_list_rows(
        1, order_by=champ, order_dir=sens, limit=10)]


def test_sorting_by_a_user_column_runs(pg):
    """Le cas qui échouait : la requête part, et elle trie sur la VALEUR — donc une
    ligne à couches se range à sa place, pas d'après son enveloppe."""
    assert _order("nom") == ["couches", "plate", "liste"]


def test_sorting_descends(pg):
    assert _order("nom", "desc") == ["liste", "plate", "couches"]


def test_sorting_by_a_layer(pg):
    """Une couche se trie comme une valeur — les lignes sans provenance en queue."""
    assert _order("nom.origine")[0] == "couches"


def test_sorting_by_a_rank_of_a_list(pg):
    """Un chemin de rang est une valeur comme une autre : il se trie."""
    assert _order("contacts[0].nom")[0] == "liste"


def test_sorting_across_all_items_is_refused(pg):
    """N valeurs ne se trient pas — refus nommé, jamais un ordre reproductible et faux
    tiré du premier item."""
    from oto_mcp import db
    with pytest.raises(ValueError) as e:
        db.datastore_list_rows(1, order_by="contacts[].nom", limit=10)
    assert "contacts[0].nom" in str(e.value)


def test_system_columns_still_sort(pg):
    assert len(_order("_created_at")) == 3
    assert len(_order("_id")) == 3
