"""The in-app agent's answer, spoken: once the turn ends with an answer and the request
asked for it (`speak`), the server reads that answer aloud ONCE with ElevenLabs and
sends the link (`audio` event, before `done`).

**A system action, not a tool of the model**: no `tool_use`, no card. But it goes the
NORMAL way of a call — `outils.executer` → `meta.executer_cible`, as the person, in the
viewed org, under the conversation's run — so the guards (axes, activation, access
level), the key's cascade (the org's own ElevenLabs key first, else the shared key when
it is granted), the metering (`tool_calls` row as `elevenlabs_act`, `quantity` = the
credits spent; `units` on the shared key) and the settlement all apply, exactly as for
`elevenlabs_act op=create_speech`. The tool is taken from the served instance rather
than the person's toolbox: the toolbox is what the MODEL is offered, a display filter
that `oto_call` crosses too.

**Never in the way of the answer**: no key, no grant, no credits, an upstream error or
more than `DELAI_S` seconds → no `audio` event, a warning in the log, the turn ends as
usual (the panel then reads the answer with the browser's voice). A synthesis still
running past the delay is not cancelled: it is settled like any call, its audio unused.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from .boucle import ev
from .outils import Boite, executer, instance
from .texte import texte_a_dire

logger = logging.getLogger(__name__)

OUTIL = "elevenlabs_act"
#: FR + EN, half a credit per character, low latency.
MODELE = "eleven_flash_v2_5"
FORMAT = "mp3_44100_128"
#: ElevenLabs' premade "Sarah" — one of the default voices every account can use,
#: multilingual on the flash models.
VOIX_PAR_DEFAUT = "EXAVITQu4vr4xnSDxMaL"
DELAI_S = 15.0


def voix() -> str:
    """The voice that reads the answers (`OTO_COPILOT_VOICE_ID`), read on each turn.
    Absent or empty: `VOIX_PAR_DEFAUT`."""
    # Name LITERAL: the inventory (`env_inventory.py`) finds its reads by the AST.
    return os.environ.get("OTO_COPILOT_VOICE_ID", "").strip() or VOIX_PAR_DEFAUT


async def _outil():
    from fastmcp.server.providers.base import Provider
    for t in await Provider.list_tools(instance()):
        if t.name == OUTIL:
            return t
    return None


async def _synthetiser(fil: dict, texte: str):
    tool = await _outil()
    if tool is None:
        raise LookupError(f"{OUTIL} is not served by this instance")
    b = Boite(frozenset({OUTIL}), {OUTIL: tool}, [])
    return await executer(b, fil["sub"], OUTIL, {
        "op": "create_speech", "text": texte, "voice_id": voix(), "model_id": MODELE,
        "output_format": FORMAT}, org=fil["org_id"], projet=fil.get("project_id"),
        run_id=fil.get("run_id"))


def _tardive(tache: asyncio.Task) -> None:
    if tache.cancelled():
        return
    if tache.exception() is not None:
        logger.warning("copilot : voix arrivée après le délai, en échec",
                       exc_info=tache.exception())


async def dire(fil: dict, contenu) -> Optional[dict]:
    """The `audio` event for the answer `contenu` (the assistant message's content), or
    None — never raises."""
    texte = texte_a_dire(contenu)
    if not texte:
        return None
    try:
        tache = asyncio.ensure_future(_synthetiser(fil, texte))
        fait, _ = await asyncio.wait({tache}, timeout=DELAI_S)
        if not fait:
            tache.add_done_callback(_tardive)
            logger.warning("copilot : voix non servie, plus de %ss (conversation %s)",
                           DELAI_S, fil.get("id"))
            return None
        issue = tache.result()
    except Exception:
        logger.warning("copilot : voix non servie (conversation %s)", fil.get("id"),
                       exc_info=True)
        return None
    donnees = issue.donnees if isinstance(issue.donnees, dict) else {}
    url = donnees.get("url")
    if not issue.ok or not isinstance(url, str) or not url.startswith("https://"):
        logger.warning("copilot : voix non servie (conversation %s) : %s", fil.get("id"),
                       (issue.texte or "")[:300] if not issue.ok else "pas d'URL https")
        return None
    return ev("audio", url=url, content_type=donnees.get("mimeType") or "audio/mpeg",
              chars=len(texte))
