"""Slack — un message peut partir SANS aperçu de liens (`unfurl=False`).

Des agents postent des liens dans un canal et ne veulent pas que Slack déplie
une carte sous chacun. Le client oto-core porte `unfurl` ; l'outil le transmet
seulement quand l'appelant le demande, pour qu'un appel ordinaire reste
exactement celui d'avant.
"""
import asyncio
from unittest.mock import patch

from oto_mcp.tools import slack as slack_tools


def _monte(monkeypatch):
    class _RC:
        fields = {"bot_token": "xoxb-1", "user_token": None}
        key = None
        is_platform = False

    monkeypatch.setattr(slack_tools.access, "resolve_credential", lambda *a, **k: _RC())
    from fastmcp import FastMCP
    patcher = patch("oto.tools.slack.client.SlackClient")
    cls = patcher.start()
    m = FastMCP("t")
    slack_tools.register(m)
    client = cls.return_value
    client.post_message.return_value = {"ok": True, "ts": "1.2"}
    client.channel_info.return_value = {"ok": True, "channel": {
        "id": "C1", "name": "seo", "is_im": False, "is_ext_shared": False}}
    return m, client, patcher


def _poste(m, **kw):
    return asyncio.run(m.get_tool("slack_post_message")).fn(channel="C1", text="https://example.com", **kw)


def test_unfurl_false_est_transmis_au_client(monkeypatch):
    m, client, patcher = _monte(monkeypatch)
    try:
        _poste(m, unfurl=False)
    finally:
        patcher.stop()
    assert client.post_message.call_args.kwargs["unfurl"] is False


def test_par_defaut_l_appel_au_client_ne_change_pas(monkeypatch):
    m, client, patcher = _monte(monkeypatch)
    try:
        _poste(m)
    finally:
        patcher.stop()
    assert "unfurl" not in client.post_message.call_args.kwargs


def test_le_parametre_est_annonce_dans_la_description():
    from fastmcp import FastMCP
    m = FastMCP("t")
    slack_tools.register(m)
    tool = asyncio.run(m.get_tool("slack_post_message"))
    prop = tool.parameters["properties"]["unfurl"]
    assert "preview" in (prop.get("description") or "")
