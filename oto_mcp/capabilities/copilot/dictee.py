"""The in-app agent's dictation: a clip the panel recorded, turned into words by
ElevenLabs (Scribe) — never by the browser's own recognition, which is not ours to
measure or fix and refuses some browsers outright.

**A system action, not a tool of the model**, like the spoken answer (`voix.py`): it goes
the NORMAL way of a call — the clip deposited in transit under the person
(`copilot-dictation`), then `elevenlabs_act op=create_transcript` on that reference
through `outils.executer` → `meta.executer_cible`, as the person, in the viewed org — so
the guards (activation, access level), the key's cascade (the org's own ElevenLabs key
first, else the shared key when it is granted), the quota and the metering all apply,
exactly as for an agent's call. The audio itself never enters a tool's arguments (which
the journal keeps): only the short transit reference does; `tmp/` is purged in a day.
"""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Optional

from starlette.concurrency import run_in_threadpool

from ...auth import hooks as auth_hooks
from .outils import Boite, executer, instance

logger = logging.getLogger(__name__)

OUTIL = "elevenlabs_act"
PREFIXE = "copilot-dictation"
#: A dictation is a sentence or two: two minutes is generous, and bounds what one
#: request can spend.
MAX_SECONDES = 120
MAX_OCTETS = 4 * 1024 * 1024
#: What the panel records with `MediaRecorder` (webm/opus on Chromium and Firefox,
#: mp4/aac on Safari). Parameters (`;codecs=opus`) are dropped before the check.
TYPES = frozenset({"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav",
                   "audio/x-wav", "audio/aac"})
_EXTENSION = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a",
              "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
              "audio/aac": "aac"}


class DicteeRefusee(ValueError):
    """The clip itself is refused (empty, not audio, too long): the panel's fault."""


def decoder(audio_base64: str, mime_type: str) -> tuple[bytes, str]:
    """`(octets, type)` of the clip, or `DicteeRefusee`."""
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime not in TYPES:
        raise DicteeRefusee(f"`mime_type` {mime_type!r} is not a recorded audio type.")
    try:
        data = base64.b64decode(audio_base64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise DicteeRefusee("`audio_base64` is not base64.") from None
    if not data:
        raise DicteeRefusee("The clip is empty.")
    if len(data) > MAX_OCTETS:
        raise DicteeRefusee(f"The clip is over {MAX_OCTETS // (1024 * 1024)} MB.")
    return data, mime


async def _outil():
    from fastmcp.server.providers.base import Provider
    for t in await Provider.list_tools(instance()):
        if t.name == OUTIL:
            return t
    return None


def _deposer(sub: str, data: bytes, mime: str) -> dict:
    from ... import transit
    with auth_hooks.sub_override(sub):
        return transit.deposer(PREFIXE, data, mime, f"dictation.{_EXTENSION[mime]}")["source"]


async def transcrire(sub: str, org: Optional[int], data: bytes, mime: str,
                     secondes: float, langue: Optional[str]) -> dict:
    """`{"ok": True, "text"}`, or `{"ok": False, "message"}` — the call's own refusal
    (no key, no grant, quota spent, ElevenLabs down), said as the tool said it."""
    tool = await _outil()
    if tool is None:
        return {"ok": False, "message": f"{OUTIL} is not served by this instance."}
    source = await run_in_threadpool(_deposer, sub, data, mime)
    b = Boite(frozenset({OUTIL}), {OUTIL: tool}, [])
    args = {"op": "create_transcript", "source": source, "audio_seconds": secondes}
    if langue:
        args["language_code"] = langue
    issue = await executer(b, sub, OUTIL, args, org=org, projet=None, run_id=None)
    donnees = issue.donnees if isinstance(issue.donnees, dict) else {}
    if not issue.ok:
        message = donnees.get("error") if isinstance(donnees.get("error"), str) else issue.texte
        logger.warning("copilot : dictée non transcrite (%s) : %s", sub, (message or "")[:300])
        return {"ok": False, "message": (message or "")[:600]}
    return {"ok": True, "text": str(donnees.get("text") or "").strip()}
