"""Allègement de la réponse unipile_search (feedback #335) : dé-dup data/items + strip images."""
from oto_mcp.tools import unipile as U


def test_slim_dedup_and_strip_images():
    lst = [{"id": "1", "name": "X", "headline": "H",
            "public_picture_url": "a", "public_picture_url_large": "b",
            "background_picture_url": "c", "profile_picture_url": "d"}]
    res = {"data": lst, "items": lst, "next_cursor": "N", "cursor": "N", "total_count": 42}
    out = U._slim_search(res)
    # dé-duplication : plus que items + cursor + total_count
    assert set(out) == {"items", "cursor", "total_count"}
    assert out["cursor"] == "N" and out["total_count"] == 42
    it = out["items"][0]
    # toutes les URLs d'image retirées
    assert not any("picture_url" in k for k in it)
    # champs métier conservés
    assert it["name"] == "X" and it["headline"] == "H" and it["id"] == "1"


def test_slim_reads_data_when_items_absent():
    res = {"data": [{"id": "9", "public_picture_url": "x"}], "next_cursor": "C"}
    out = U._slim_search(res)
    assert out["items"][0]["id"] == "9" and "public_picture_url" not in out["items"][0]
    assert out["cursor"] == "C"


def test_slim_passthrough_non_dict():
    assert U._slim_search([1, 2]) == [1, 2]
    assert U._slim_search(None) is None
