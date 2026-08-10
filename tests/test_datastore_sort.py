"""Tri serveur de `data_rows` (`order_by`) et son régime de pagination.

La couche SQL portait déjà le tri (`db.datastore_list_rows(order_by=…)`, chemin
dashboard) ; ce qui manquait, c'est sa traduction depuis la face MCP. Sans elle,
répondre à « les 10 posts les plus récents » sur un gros vivier obligeait à
dumper le namespace puis à retrier en local.

Le point délicat n'est pas le tri, c'est la PAGINATION : le curseur keyset
(`row_id`) n'a de sens que dans l'ordre de création, donc un appel trié pagine
par offset. Les deux régimes produisent des curseurs opaques de même forme —
d'où le préfixe qui les distingue et les tests qui vérifient qu'on ne peut pas
les intervertir. Un curseur d'offset relu comme un `row_id` cadrerait
silencieusement sur les mauvaises lignes : mieux vaut lever.

Seams db monkeypatchés — logique pure, sans PG.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore as D


_ROWS = [{"row_id": f"r{i:02d}", "created_at": "t", "updated_at": "t", "data": {"n": i}}
         for i in range(1, 6)]


@pytest.fixture
def spy(monkeypatch):
    """Store dont les deux chemins SQL enregistrent leurs kwargs."""
    calls: dict = {}

    def _after(ns_id, **kw):
        calls["after"] = kw
        return _ROWS[: kw.get("limit", 100)]

    def _list(ns_id, **kw):
        calls["list"] = kw
        offset = kw.get("offset", 0)
        return _ROWS[offset: offset + (kw.get("limit") or 100)]

    monkeypatch.setattr(D.db, "datastore_list_rows_after", _after)
    monkeypatch.setattr(D.db, "datastore_list_rows", _list)
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 1)
    return s, calls


# ── le tri bascule de régime ──

def test_no_order_by_stays_on_the_keyset_path(spy):
    store, calls = spy
    store.cursor_rows("ns", limit=2)
    assert "list" not in calls                       # pas le chemin trié
    assert calls["after"]["limit"] == 2


def test_order_by_switches_to_the_sorted_path(spy):
    store, calls = spy
    store.cursor_rows("ns", order_by="posted_at", order_dir="asc", limit=2)
    assert "after" not in calls                      # pas le keyset
    assert calls["list"]["order_by"] == "posted_at"
    assert calls["list"]["order_dir"] == "asc"
    assert calls["list"]["offset"] == 0


def test_sorted_path_keeps_narrowing(spy):
    """Le tri ne doit pas perdre `filter`/`q` en route."""
    store, calls = spy
    store.cursor_rows("ns", order_by="n", q="sylvie",
                      filter={"statut": "won"})
    assert calls["list"]["q"] == "sylvie"
    assert calls["list"]["filters"] == [
        {"field": "statut", "op": "eq", "value": "won"}]


def test_sorted_pagination_advances_by_offset(spy):
    store, _ = spy
    p1 = store.cursor_rows("ns", order_by="n", limit=2)
    assert [r["n"] for r in p1["rows"]] == [1, 2]
    p2 = store.cursor_rows("ns", order_by="n", limit=2, cursor=p1["next_cursor"])
    assert [r["n"] for r in p2["rows"]] == [3, 4]
    p3 = store.cursor_rows("ns", order_by="n", limit=2, cursor=p2["next_cursor"])
    assert [r["n"] for r in p3["rows"]] == [5]
    assert p3["next_cursor"] is None                 # page partielle ⇒ fin


# ── les deux régimes de curseur ne se mélangent pas ──

def test_keyset_cursor_rejected_on_a_sorted_call(spy):
    store, _ = spy
    keyset = D._encode_cursor("r02")
    with pytest.raises(D.InvalidCursor):
        store.cursor_rows("ns", order_by="n", cursor=keyset)


def test_sorted_cursor_rejected_without_order_by(spy):
    store, _ = spy
    sorted_cursor = D._encode_offset_cursor(4)
    with pytest.raises(D.InvalidCursor):
        store.cursor_rows("ns", cursor=sorted_cursor)


def test_offset_cursor_roundtrip():
    assert D._decode_offset_cursor(D._encode_offset_cursor(12)) == 12


def test_garbled_offset_cursor_raises():
    with pytest.raises(D.InvalidCursor):
        D._decode_offset_cursor(D._encode_cursor("off:notanumber"))
