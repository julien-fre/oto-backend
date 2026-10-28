"""Dictation: a recorded clip, transcribed by ElevenLabs through the normal call path
(`outils.executer` → `elevenlabs_act op=create_transcript`, as the person, in the viewed
org) on a transit reference — the audio never enters the tool's arguments. A refused
clip is the panel's fault (422); the call's own refusal is said as is (409)."""
from __future__ import annotations

import asyncio
import base64

import pytest

from oto_mcp.capabilities.copilot import dictee
from oto_mcp.capabilities.copilot.outils import Issue

_B64 = base64.b64encode(b"webm-bytes").decode()


@pytest.mark.parametrize("mime", ["audio/webm;codecs=opus", "audio/mp4", "AUDIO/OGG"])
def test_decoder_accepte_un_enregistrement(mime):
    data, m = dictee.decoder(_B64, mime)
    assert data == b"webm-bytes" and ";" not in m and m == m.lower()


@pytest.mark.parametrize("b64, mime", [
    (_B64, "text/html"), (_B64, ""), ("pas du base64 !", "audio/webm"), ("", "audio/webm"),
])
def test_decoder_refuse(b64, mime):
    with pytest.raises(dictee.DicteeRefusee):
        dictee.decoder(b64, mime)


def test_decoder_borne(monkeypatch):
    monkeypatch.setattr(dictee, "MAX_OCTETS", 4)
    with pytest.raises(dictee.DicteeRefusee, match="over"):
        dictee.decoder(_B64, "audio/webm")


@pytest.fixture
def appels(monkeypatch):
    vus: dict = {"deposes": [], "executes": []}
    reponse = {"issue": Issue(True, "{}", {"text": " Bonjour Tulina ", "credits": 33})}

    async def faux_outil():
        return object()

    def faux_deposer(sub, data, mime):
        vus["deposes"].append((sub, data, mime))
        return {"kind": "transit", "ref": "copilot-dictation/" + "b" * 32}

    async def faux_executer(b, sub, name, args, **k):
        vus["executes"].append((sorted(b.outils), sub, name, args, k))
        return reponse["issue"]
    monkeypatch.setattr(dictee, "_outil", faux_outil)
    monkeypatch.setattr(dictee, "_deposer", faux_deposer)
    monkeypatch.setattr(dictee, "executer", faux_executer)
    vus["reponse"] = reponse
    return vus


def test_transcrit_par_le_chemin_normal_sur_une_reference(appels):
    out = asyncio.run(dictee.transcrire("u-a", 7, b"webm-bytes", "audio/webm", 6.2, "fr"))
    assert out == {"ok": True, "text": "Bonjour Tulina"}
    assert appels["deposes"] == [("u-a", b"webm-bytes", "audio/webm")]
    [(outils, sub, name, args, k)] = appels["executes"]
    assert outils == ["elevenlabs_act"] and sub == "u-a" and name == "elevenlabs_act"
    assert args == {"op": "create_transcript", "audio_seconds": 6.2, "language_code": "fr",
                    "source": {"kind": "transit", "ref": "copilot-dictation/" + "b" * 32}}
    assert "webm-bytes" not in repr(args), "the audio never enters the tool's arguments"
    assert k == {"org": 7, "projet": None, "run_id": None}


def test_le_refus_de_l_appel_est_dit_tel_quel(appels):
    appels["reponse"]["issue"] = Issue(False, '{"ok": false, "error": "needs your own key"}',
                                       {"ok": False, "error": "needs your own key"})
    out = asyncio.run(dictee.transcrire("u-a", 7, b"x", "audio/webm", 1, None))
    assert out == {"ok": False, "message": "needs your own key"}
    assert "language_code" not in appels["executes"][0][3]
