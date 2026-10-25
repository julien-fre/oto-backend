"""The model stream, parsed: text and progress as they come, the whole message at the
end with every block as the API sent it (thinking signature kept), a provider error
scrubbed of the key. No real model call: a stub transport answers."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from oto_mcp.capabilities.copilot import modele

EVENTS = [
    {"type": "message_start", "message": {"usage": {"input_tokens": 10,
                                                    "cache_read_input_tokens": 5}}},
    {"type": "content_block_start", "index": 0,
     "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "thinking_delta", "thinking": "Checking"}},
    {"type": "content_block_delta", "index": 0,
     "delta": {"type": "signature_delta", "signature": "sig"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hi"}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2,
     "content_block": {"type": "tool_use", "id": "tu", "name": "data_rows", "input": {}}},
    {"type": "content_block_delta", "index": 2,
     "delta": {"type": "input_json_delta", "partial_json": '{"datastore":'}},
    {"type": "content_block_delta", "index": 2,
     "delta": {"type": "input_json_delta", "partial_json": " 4}"}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
     "usage": {"output_tokens": 30}},
    {"type": "message_stop"},
]


def _sse(events) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _brancher(monkeypatch, handler):
    monkeypatch.setattr(modele, "_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _jouer(cle="sk-test"):
    async def go():
        return [x async for x in modele.flux(cle, None, {"model": modele.MODELE})]
    return asyncio.run(go())


def test_flux_assemble_le_message(monkeypatch):
    vus = {}

    def handler(req: httpx.Request):
        vus["h"] = dict(req.headers)
        vus["corps"] = json.loads(req.content)
        return httpx.Response(200, content=_sse(EVENTS),
                              headers={"content-type": "text/event-stream"})
    _brancher(monkeypatch, handler)
    sorties = _jouer()
    assert ("thinking", "Checking") in sorties and ("text", "Hi") in sorties
    fin = sorties[-1]
    assert fin[0] == "end"
    msg = fin[1]
    assert msg["stop_reason"] == "tool_use"
    assert msg["content"][0] == {"type": "thinking", "thinking": "Checking",
                                 "signature": "sig"}
    assert msg["content"][2]["input"] == {"datastore": 4}
    assert msg["usage"] == {"input_tokens": 10, "cache_read_input_tokens": 5,
                            "output_tokens": 30}
    assert "thinking-display-updates-2026-08-18" in vus["h"]["anthropic-beta"]


def test_erreur_du_fournisseur_sans_la_cle(monkeypatch):
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "bad key sk-test here"}})
    _brancher(monkeypatch, handler)
    with pytest.raises(modele.ErreurModele) as e:
        _jouer("sk-test")
    assert "sk-test" not in e.value.message and e.value.statut == 400


def test_corps_de_requete():
    c = modele.corps_de_requete("sys", [{"name": "t"}], [{"role": "user", "content": "x"}])
    assert c["thinking"] == {"type": "adaptive", "display": "updates"}
    assert c["output_config"] == {"effort": "medium"} and c["model"] == "claude-opus-5-5"
    assert c["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert c["stream"] is True
