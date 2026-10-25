"""The model call of the in-app agent: one streamed Messages request over HTTP.

The repo calls Anthropic with `httpx` directly (`anthropic_cle.py`); so does this module,
rather than adding an SDK dependency to the lock for one caller. It parses the event
stream into what the loop needs as it arrives — answer text, progress notes (the
model's between-tool notes, `thinking.display: "updates"`) — and the whole assistant
message at the end, every block kept as the API sent it (thinking blocks and their
signatures are replayed unchanged on the next request).
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)

URL = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"
MODELE = "claude-opus-5-5"
EFFORT = "medium"
#: Output bound of one call — also what a turn on a re-billed key reserves.
MAX_TOKENS = 32_000
#: Between-tool progress notes come back as `thinking` text under this beta.
BETAS = ("thinking-display-updates-2026-08-18",)
#: The hard bound of ONE model call, at its own level (a stream that stalls between two
#: events is cut by the read timeout; one that trickles forever, by this).
DUREE_MAX_S = 600
_DELAIS = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class ErreurModele(Exception):
    """The model call failed: `message` is safe to show (key scrubbed, bounded)."""

    def __init__(self, message: str, statut: Optional[int] = None):
        super().__init__(message)
        self.message, self.statut = message, statut


def corps_de_requete(system: str, outils: list[dict], messages: list[dict]) -> dict:
    """The request body: prompt caching on the system prompt and on the tools (the
    breakpoint the caller put on the last core tool), adaptive thinking with progress
    notes, effort set explicitly (this model's default is the same, but saying it keeps
    the request stable across model defaults)."""
    return {
        "model": MODELE,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": outils,
        "messages": messages,
        "thinking": {"type": "adaptive", "display": "updates"},
        "output_config": {"effort": EFFORT},
    }


def _client() -> httpx.AsyncClient:
    """The HTTP client — the seam the tests mount a stub transport on."""
    return httpx.AsyncClient(timeout=_DELAIS)


def _message_d_erreur(brut: bytes, statut: int, cle: str) -> str:
    try:
        corps = json.loads(brut or b"{}")
    except ValueError:
        corps = None
    erreur = corps.get("error") if isinstance(corps, dict) else None
    texte = str(erreur.get("message") or "") if isinstance(erreur, dict) else ""
    if cle:
        texte = texte.replace(cle, "…")
    return f"The model call failed ({statut}): {texte.strip()[:300] or 'no detail'}"


class Assembleur:
    """Rebuilds the assistant message from the stream's events. Pure: fed one decoded
    `data:` object at a time, it returns what to show now (`("text", s)`,
    `("thinking", s)`, `("block", type)`), and keeps the message for `message()`."""

    def __init__(self):
        self.blocs: dict[int, dict] = {}
        self._json: dict[int, str] = {}
        self.stop_reason: Optional[str] = None
        self.usage: dict = {}

    def nourrir(self, ev: dict) -> list[tuple]:
        t = ev.get("type")
        if t == "message_start":
            self.usage.update((ev.get("message") or {}).get("usage") or {})
        elif t == "content_block_start":
            i = int(ev.get("index", len(self.blocs)))
            bloc = dict(ev.get("content_block") or {})
            if bloc.get("type") == "tool_use":
                self._json[i] = ""
            self.blocs[i] = bloc
            return [("block", bloc.get("type"))]
        elif t == "content_block_delta":
            i = int(ev.get("index", 0))
            bloc = self.blocs.setdefault(i, {})
            d = ev.get("delta") or {}
            dt = d.get("type")
            if dt == "text_delta":
                bloc["text"] = bloc.get("text", "") + d.get("text", "")
                return [("text", d.get("text", ""))]
            if dt == "thinking_delta":
                bloc["thinking"] = bloc.get("thinking", "") + d.get("thinking", "")
                return [("thinking", d.get("thinking", ""))]
            if dt == "signature_delta":
                bloc["signature"] = d.get("signature", "")
            elif dt == "input_json_delta":
                self._json[i] = self._json.get(i, "") + d.get("partial_json", "")
        elif t == "content_block_stop":
            i = int(ev.get("index", 0))
            if i in self._json:
                brut = self._json.pop(i)
                try:
                    self.blocs[i]["input"] = json.loads(brut) if brut.strip() else {}
                except ValueError:
                    # A cut input (max_tokens): an empty object, the call then fails its
                    # schema and the model reads why — never a guessed argument.
                    self.blocs[i]["input"] = {}
        elif t == "message_delta":
            d = ev.get("delta") or {}
            if d.get("stop_reason"):
                self.stop_reason = d["stop_reason"]
            self.usage.update(ev.get("usage") or {})
        elif t == "error":
            err = ev.get("error") or {}
            raise ErreurModele(f"The model stream failed: {str(err.get('message') or '')[:300]}")
        return []

    def message(self) -> dict:
        return {"content": [self.blocs[i] for i in sorted(self.blocs)],
                "stop_reason": self.stop_reason, "usage": dict(self.usage)}


async def flux(cle: str, workspace: Optional[str], corps: dict) -> AsyncIterator[tuple]:
    """Streams one model call: yields `("text", s)`, `("thinking", s)`, `("block", type)`
    as they come, then `("end", message)`. Raises `ErreurModele`."""
    entetes = {"x-api-key": cle, "anthropic-version": VERSION,
               "anthropic-beta": ",".join(BETAS), "content-type": "application/json"}
    if workspace:
        entetes["anthropic-workspace-id"] = workspace
    assembleur = Assembleur()
    # No cancel scope around the yields (an async generator must not hold one across a
    # `yield`): the bound is checked on each line, the read timeout covers a silent stall.
    limite = time.monotonic() + DUREE_MAX_S
    try:
        async with _client() as client:
            async with client.stream("POST", URL, json=corps, headers=entetes) as r:
                if r.status_code != 200:
                    brut = await r.aread()
                    raise ErreurModele(_message_d_erreur(brut, r.status_code, cle),
                                       r.status_code)
                async for ligne in r.aiter_lines():
                    if time.monotonic() > limite:
                        raise ErreurModele("The model call took too long and was stopped.")
                    if not ligne.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(ligne[5:].strip())
                    except ValueError:
                        logger.warning("copilot : ligne du flux du modèle illisible, ignorée")
                        continue
                    for sortie in assembleur.nourrir(ev):
                        yield sortie
    except httpx.HTTPError as e:
        logger.warning("copilot : appel au modèle en échec (%s)", type(e).__name__)
        raise ErreurModele(f"The model could not be reached ({type(e).__name__}).") from None
    yield ("end", assembleur.message())
