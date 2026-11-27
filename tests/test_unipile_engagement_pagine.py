"""`linkedin_unipile_post op=engagement` : pages suivies par `offset` jusqu'à une limite
déclarée, troncature dite, et le 429 d'Unipile en refus nommé (oto#177).

Avant : une seule page (20 personnes au plus), un `cursor` sans effet, et un 429 remonté
brut depuis un appel non enveloppé.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import unipile as U

    m = FastMCP("t")
    U.register(m)
    return asyncio.run(m.get_tool("linkedin_unipile_post")).fn


def _pages(total: int, size: int = 20):
    """Faux amont : `offset` → la tranche correspondante, page vide au-delà."""
    people = [{"id": f"p{i}"} for i in range(total)]

    def fetch(post_id, offset=None, comment_id=None, limit=None):
        o = offset or 0
        return {"items": people[o:o + size], "data": people[o:o + size]}
    return fetch


@pytest.fixture
def client(monkeypatch):
    from oto_mcp.tools import unipile as U

    inst = MagicMock()
    monkeypatch.setattr(U, "unipile_client", lambda *a, **k: inst)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    monkeypatch.setattr(U.time, "sleep", lambda s: None)  # a short 429 is waited out
    U._RATE_LIMIT_UNTIL.clear()
    return inst


def test_suit_les_pages_au_dela_de_20(client):
    client.list_reactions.side_effect = _pages(55)
    out = _tool()(op="engagement", post_id="urn:li:1", kind="reactions")
    assert out["count"] == 55 and out["truncated"] is False
    assert out["next_offset"] is None and out["stopped"] == "end"
    offsets = [c.kwargs["offset"] for c in client.list_reactions.call_args_list]
    assert offsets == [None, 20, 40, 55]


def test_s_arrete_a_la_limite_et_dit_ou_reprendre(client):
    client.list_comments.side_effect = _pages(80)
    out = _tool()(op="engagement", post_id="urn:li:1", limit=30)
    assert out["count"] == 30 and out["truncated"] is True
    assert out["next_offset"] == 30 and out["stopped"] == "limit"
    reprise = _tool()(op="engagement", post_id="urn:li:1", limit=30, offset=30)
    assert reprise["items"][0] == {"id": "p30"}


def test_comment_id_est_transmis(client):
    client.list_comments.side_effect = _pages(3)
    _tool()(op="engagement", post_id="urn:li:1", comment_id="c9")
    assert client.list_comments.call_args.kwargs["comment_id"] == "c9"


def test_limit_hors_bornes_refusee(client):
    with pytest.raises(McpError):
        _tool()(op="engagement", post_id="urn:li:1", limit=501)


def _rl(retry_after):
    from oto.tools.unipile.client import UnipileRateLimited
    return UnipileRateLimited("Unipile 429: We only allow 10 requests.",
                              retry_after=retry_after)


@pytest.mark.parametrize("retry_after,attendu", [(7, 7), (None, 30)])
def test_429_premiere_page_refus_nomme(client, retry_after, attendu):
    client.list_reactions.side_effect = _rl(retry_after)
    with pytest.raises(McpError) as e:
        _tool()(op="engagement", post_id="urn:li:1", kind="reactions")
    data = e.value.error.data
    assert data["code"] == "unipile_rate_limited" and data["retryable"] is True
    assert data["retry_after_seconds"] == attendu
    assert "unipile_rate_limited" in e.value.error.message


def test_429_apres_une_page_rend_le_partiel(client):
    first = _pages(60)

    def fetch(post_id, offset=None, comment_id=None, limit=None):
        if offset:
            raise _rl(5)
        return first(post_id)
    client.list_comments.side_effect = fetch
    out = _tool()(op="engagement", post_id="urn:li:1")
    assert out["count"] == 20 and out["truncated"] is True
    assert out["stopped"] == "rate_limited" and out["next_offset"] == 20
    assert out["retry_after_seconds"] == 5


def test_get_est_enveloppe(client):
    client.get_post.side_effect = _rl(3)
    with pytest.raises(McpError) as e:
        _tool()(op="get", post_id="urn:li:1")
    assert e.value.error.data["code"] == "unipile_rate_limited"
    assert client.get_post.call_count == 2  # a short 429 is waited out once, not twice
