"""Bulk create/update via sObject Collections (max 200 records/call, one HTTP
call instead of N) — validation + receipt shaping, same style as
test_salesforce_describe_projection.py (pure helpers, no client/network).
"""
import pytest
from mcp.shared.exceptions import McpError

from oto_mcp.tools.salesforce import (
    _bulk_receipt,
    _validate_bulk_items,
    _validate_update_items_have_id,
)


def test_empty_items_is_rejected():
    with pytest.raises(McpError, match="au moins un"):
        _validate_bulk_items([])


def test_over_200_items_is_rejected_without_a_call():
    items = [{"LastName": str(i)} for i in range(201)]
    with pytest.raises(McpError, match="200"):
        _validate_bulk_items(items)


def test_exactly_200_items_is_accepted():
    items = [{"LastName": str(i)} for i in range(200)]
    _validate_bulk_items(items)  # no raise


def test_update_requires_id_on_every_item():
    with pytest.raises(McpError, match=r"items\[1\]"):
        _validate_update_items_have_id(
            [{"Id": "003a", "LastName": "X"}, {"LastName": "Y"}])


def test_update_accepts_items_all_carrying_id():
    _validate_update_items_have_id([{"Id": "003a"}, {"Id": "003b"}])  # no raise


def test_receipt_indexes_in_the_same_order_as_the_response():
    raw = [
        {"id": "003a", "success": True, "errors": []},
        {"success": False, "errors": [{"statusCode": "REQUIRED_FIELD_MISSING"}]},
    ]
    out = _bulk_receipt(raw)
    assert out["total"] == 2
    assert out["succeeded"] == 1
    assert out["results"][0] == {"index": 0, "id": "003a", "success": True, "errors": []}
    assert out["results"][1]["index"] == 1
    assert out["results"][1]["success"] is False
