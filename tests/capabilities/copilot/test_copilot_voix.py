"""The answer read aloud: plain prose cut at a sentence end; ONE synthesis through the
normal call path (`outils.executer` → `elevenlabs_act`, as the person, in the viewed
org, under the run); the `audio` event before `done`; any failure or a slow synthesis
→ no event, and the turn still ends."""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp.capabilities.copilot import boucle, voix
from oto_mcp.capabilities.copilot.outils import Issue
from oto_mcp.capabilities.copilot.texte import PLAFOND_PAROLE, texte_a_dire


def _fil(**k):
    base = {"id": "thr_t", "sub": "u-a", "org_id": 7, "project_id": 3, "run_id": "run-1",
            "messages": [], "queue": [], "results": [], "pending": None, "remembered": [],
            "client_tools": [], "mode": "ask", "model_calls": 0, "_verrou": "j"}
    base.update(k)
    return base


# --- the text -------------------------------------------------------------------------

def test_le_markdown_devient_de_la_prose():
    contenu = [
        {"type": "text", "text": "## Résumé\n\nLa table **Leads** a `42` lignes. "
                                 "Voir [la table](https://app.test/t/3) ou https://x.test/y.\n"
                                 "- premier point\n- second point\n\n```sql\nselect 1\n```\n"
                                 "<next>Filtrer | Exporter</next>"},
        {"type": "tool_use", "id": "a", "name": "x", "input": {}}]
    assert texte_a_dire(contenu) == (
        "Résumé. La table Leads a 42 lignes. Voir la table ou. premier point. "
        "second point.")


def test_rien_a_dire():
    assert texte_a_dire([{"type": "text", "text": "<next>a | b</next>"}]) == ""
    assert texte_a_dire([]) == ""


def test_coupe_a_une_fin_de_phrase():
    phrase = "Une phrase de taille moyenne pour le test. "
    t = texte_a_dire(phrase * 100)
    assert len(t) <= PLAFOND_PAROLE and t.endswith("test.")
    sans_point = "mot " * 1000
    t = texte_a_dire(sans_point)
    assert len(t) <= PLAFOND_PAROLE and t.endswith("mot")


# --- the synthesis --------------------------------------------------------------------

@pytest.fixture
def appels(monkeypatch):
    vus: list = []
    reponse = {"issue": Issue(True, "{}", {"url": "https://media.test/speech.mp3?sig=x",
                                          "mimeType": "audio/mpeg", "character_count": 12})}

    async def faux_outil():
        return object()

    async def faux_executer(b, sub, name, args, **k):
        vus.append((sorted(b.outils), sub, name, args, k))
        if isinstance(reponse["issue"], BaseException):
            raise reponse["issue"]
        if reponse.get("lent"):
            await asyncio.sleep(1)
        return reponse["issue"]
    monkeypatch.setattr(voix, "_outil", faux_outil)
    monkeypatch.setattr(voix, "executer", faux_executer)
    return vus, reponse


def _dire(fil, contenu):
    return asyncio.run(voix.dire(fil, contenu))


def test_une_synthese_par_le_chemin_normal(appels, monkeypatch):
    vus, _ = appels
    monkeypatch.delenv("OTO_COPILOT_VOICE_ID", raising=False)
    e = _dire(_fil(), [{"type": "text", "text": "Bonjour **Julien**."}])
    assert e == {"type": "audio", "url": "https://media.test/speech.mp3?sig=x",
                 "content_type": "audio/mpeg", "chars": len("Bonjour Julien.")}
    assert vus == [(["elevenlabs_act"], "u-a", "elevenlabs_act", {
        "op": "create_speech", "text": "Bonjour Julien.", "voice_id": voix.VOIX_PAR_DEFAUT,
        "model_id": "eleven_flash_v2_5", "output_format": "mp3_44100_128"},
        {"org": 7, "projet": 3, "run_id": "run-1"})]


def test_la_voix_vient_du_reglage(appels, monkeypatch):
    vus, _ = appels
    monkeypatch.setenv("OTO_COPILOT_VOICE_ID", " voixXYZ ")
    _dire(_fil(), "Bonjour.")
    assert vus[0][3]["voice_id"] == "voixXYZ"


@pytest.mark.parametrize("issue", [
    Issue(False, "ElevenLabs: the account has no credits left"),
    Issue(False, "elevenlabs_act op='create_speech' needs your own ElevenLabs key"),
    Issue(True, "{}", {"mimeType": "audio/mpeg"}),
    Issue(True, "{}", {"url": "http://pas-https.test/a.mp3"}),
    RuntimeError("boom"),
])
def test_un_echec_ne_donne_aucun_evenement(appels, issue, caplog):
    _, reponse = appels
    reponse["issue"] = issue
    assert _dire(_fil(), "Bonjour.") is None
    assert any("voix non servie" in r.getMessage() for r in caplog.records)


def test_rien_a_dire_rien_n_est_appele(appels):
    vus, _ = appels
    assert _dire(_fil(), [{"type": "text", "text": "<next>a</next>"}]) is None
    assert vus == []


def test_trop_lent_aucun_evenement(appels, monkeypatch, caplog):
    _, reponse = appels
    reponse["lent"] = True
    monkeypatch.setattr(voix, "DELAI_S", 0.05)
    assert _dire(_fil(), "Bonjour.") is None
    assert any("plus de" in r.getMessage() for r in caplog.records)


def test_l_outil_absent_aucun_evenement(monkeypatch):
    async def aucun():
        return None
    monkeypatch.setattr(voix, "_outil", aucun)
    assert _dire(_fil(), "Bonjour.") is None


# --- the loop: `audio` before `done`, only when asked ---------------------------------

def _conduire(fil, monkeypatch, audio):
    async def faux_un_appel(_fil, b, cle, sortie):
        sortie["message"] = {"content": [{"type": "text", "text": "C'est fait."}],
                             "stop_reason": "end_turn"}
        sortie["suggestions"] = ["Suite"]
        yield boucle.ev("text_delta", text="C'est fait.")

    async def rien(*a, **k):
        return None

    dits: list = []

    async def faux_dire(f, contenu):
        dits.append(contenu)
        return audio

    monkeypatch.setattr(boucle, "_un_appel", faux_un_appel)
    monkeypatch.setattr(boucle, "sauver", rien)
    monkeypatch.setattr(boucle.store, "prolonger", lambda *a, **k: None)
    monkeypatch.setattr(voix, "dire", faux_dire)

    async def go():
        from oto_mcp.capabilities.copilot.outils import Boite
        b = Boite(frozenset(), {}, [])
        return [e async for e in boucle.conduire(fil, b, None)]
    return asyncio.run(go()), dits


def test_audio_avant_done_quand_demande(monkeypatch):
    son = {"type": "audio", "url": "https://m/a.mp3", "content_type": "audio/mpeg",
           "chars": 11}
    evs, dits = _conduire(_fil(_speak=True), monkeypatch, son)
    assert [e["type"] for e in evs] == ["text_delta", "suggestions", "audio", "done"]
    assert evs[2] == son and len(dits) == 1


def test_sans_speak_aucune_voix(monkeypatch):
    evs, dits = _conduire(_fil(), monkeypatch, {"type": "audio"})
    assert [e["type"] for e in evs] == ["text_delta", "suggestions", "done"]
    assert dits == []


def test_voix_en_echec_le_tour_finit(monkeypatch):
    evs, _ = _conduire(_fil(_speak=True), monkeypatch, None)
    assert [e["type"] for e in evs] == ["text_delta", "suggestions", "done"]


def test_speak_au_contrat_des_trois_routes():
    from oto_mcp.capabilities.copilot import routes
    assert routes.TurnInput(text="x").speak is False
    assert routes.TurnInput(text="x", speak=True).speak is True
    assert routes.ApprovalInput(thread_id="t", approval_id="a", decision="approve").speak is False
    assert routes.ClientResultInput(thread_id="t", call_id="c", ok=True).speak is False
    e = routes.CopilotEvent(type="audio", url="https://m/a.mp3", content_type="audio/mpeg",
                            chars=3)
    assert e.model_dump(exclude_none=True) == {"type": "audio", "url": "https://m/a.mp3",
                                               "content_type": "audio/mpeg", "chars": 3}
