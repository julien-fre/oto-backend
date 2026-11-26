"""`hubspot_object(op="update", associations=[...])` creates the links.

The v3 PATCH ignores an `associations` key and still answers 200: forwarding it there
(or dropping it, as the update branch did) reported a link that was never made. The
update now writes each association through the v4 association PUT, one request per
target, and says per target what was applied and what failed.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def client(monkeypatch):
    import oto.tools.hubspot.client as hs

    inst = MagicMock()
    inst.update_object.return_value = {"id": "1", "properties": {"a": "b"}}
    monkeypatch.setattr(hs, "HubSpotClient", lambda *a, **k: inst)
    monkeypatch.setattr("oto_mcp.auth.hubspot.bearer", lambda *a, **k: "k")
    return inst


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import hubspot as H

    m = FastMCP("t")
    H.register(m)
    return asyncio.run(m.get_tool("hubspot_object")).fn


LABEL = [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 279}]


def test_associations_only_update_links_an_existing_record(client):
    """The reported case: link an existing contact to a second company, no property
    change. Default association when `types` is omitted, labelled one otherwise."""
    out = _tool()(op="update", object_type="contacts", object_id="1",
                  to_object_type="companies",
                  associations=[{"to": {"id": "7"}}, {"to": {"id": "8"}, "types": LABEL}])
    client.update_object.assert_not_called()
    calls = client._request.call_args_list
    assert calls[0].args == (
        "PUT", "/crm/v4/objects/contacts/1/associations/default/companies/7")
    assert calls[1].args == ("PUT", "/crm/v4/objects/contacts/1/associations/companies/8")
    assert calls[1].kwargs == {"json": LABEL}
    assert [a["to_id"] for a in out["associations"]["applied"]] == ["7", "8"]
    assert out["associations"]["failed"] == []


def test_properties_and_associations_are_both_written(client):
    out = _tool()(op="update", object_type="contacts", object_id="1",
                  properties={"a": "b"}, to_object_type="companies",
                  associations=[{"to": {"id": "7"}}])
    assert client.update_object.call_args.args == ("contacts", "1", {"a": "b"})
    assert client._request.call_count == 1
    assert out["properties"] == {"a": "b"}
    assert out["associations"]["applied"][0]["to_id"] == "7"


def test_a_failed_target_is_reported_not_hidden(client):
    from oto.tools.common.errors import UpstreamHTTPError

    err = UpstreamHTTPError(404, "object not found", service="hubspot")
    client._request.side_effect = [None, err]
    out = _tool()(op="update", object_type="contacts", object_id="1",
                  to_object_type="companies",
                  associations=[{"to": {"id": "7"}}, {"to": {"id": "404"}}])
    assert [a["to_id"] for a in out["associations"]["applied"]] == ["7"]
    assert out["associations"]["failed"][0]["to_id"] == "404"


def test_nothing_changed_raises_instead_of_answering_success(client):
    from oto.tools.common.errors import UpstreamHTTPError

    err = UpstreamHTTPError(400, "bad association type", service="hubspot")
    client._request.side_effect = err
    with pytest.raises(McpError, match="no association was created"):
        _tool()(op="update", object_type="contacts", object_id="1",
                to_object_type="companies", associations=[{"to": {"id": "7"}}])


def test_update_associations_require_the_target_type(client):
    with pytest.raises(McpError, match="to_object_type"):
        _tool()(op="update", object_type="contacts", object_id="1",
                associations=[{"to": {"id": "7"}}])
    client._request.assert_not_called()
    client.update_object.assert_not_called()


@pytest.mark.parametrize("bad", [
    [], ["companies"], [{"to": {}}], [{"to": {"id": "7"}, "types": "x"}],
    [{"to": {"id": str(i)}} for i in range(101)],
])
def test_malformed_associations_are_refused_before_any_write(client, bad):
    with pytest.raises(McpError):
        _tool()(op="update", object_type="contacts", object_id="1",
                to_object_type="companies", associations=bad)
    client._request.assert_not_called()
    client.update_object.assert_not_called()


def test_update_without_associations_is_unchanged(client):
    out = _tool()(op="update", object_type="deals", object_id="1",
                  properties={"amount": "99"})
    assert out == {"id": "1", "properties": {"a": "b"}}
    client._request.assert_not_called()


def test_update_without_properties_nor_associations_still_requires_properties(client):
    with pytest.raises(McpError, match="properties"):
        _tool()(op="update", object_type="deals", object_id="1")
