"""A platform key is posted as ONE raw value, even for a multi-field connector.

`/api/admin/platform-keys` stores the `api_key` it receives as is. For a connector
whose vault blob holds several fields (`forager`: api_key + account_id), that raw
string is not the JSON `unpack_secret` expects: every read of the key refused, and the
"Test the connection" probe answered a 500 for a key that works.
"""
import json

from oto_mcp import credentials_store


def test_a_raw_platform_key_fills_the_first_secret_field():
    assert credentials_store.platform_fields("forager", "fg-raw-key") == {"api_key": "fg-raw-key"}


def test_a_json_blob_still_unpacks_whole():
    blob = json.dumps({"api_key": "fg-key", "account_id": "12"})
    assert credentials_store.platform_fields("forager", blob) == {
        "api_key": "fg-key", "account_id": "12"}


def test_a_single_field_connector_is_unchanged():
    assert credentials_store.platform_fields("serper", "sk-1") == \
        credentials_store.unpack_secret("serper", "sk-1")
