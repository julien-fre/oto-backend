"""Déclenchement d'une routine Claude Code — le seam `dispatch()` d'#268.

Ce que ces tests verrouillent n'est pas le POST (trivial) mais les trois règles qui
rendent ce chemin sûr, et qu'une réécriture distraite ferait sauter :

1. **le jeton et l'id de routine ne fuitent jamais dans un message d'erreur** — le
   message de `requests` porte l'URL, donc l'id ; il finirait dans le contexte de
   l'agent et dans Sentry ;
2. **les échecs sont typés** — un jeton révoqué (401) n'est pas réessayable, un amont
   en vrac (5xx, réseau) l'est ; confondre les deux fait boucler sur un mur ;
3. **l'appel ne prétend pas rendre le résultat du run** — il rend la session où le
   lire.
"""
from __future__ import annotations

import pytest
import requests

from oto_mcp import routine_fire


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.content = b"x" if (payload is not None or text) else b""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


_OK = {"type": "routine_fire",
       "claude_code_session_id": "session_01HJK",
       "claude_code_session_url": "https://claude.ai/code/session_01HJK"}


@pytest.fixture
def posted(monkeypatch):
    """Capture l'appel sortant sans réseau."""
    seen: dict = {}

    def _post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return seen.get("_resp") or _Resp(200, _OK)

    monkeypatch.setattr(routine_fire.requests, "post", _post)
    return seen


# ── le contrat d'appel ──

def test_fires_and_returns_the_session_to_watch(posted):
    out = routine_fire.fire("trig_01ABC", "sk-ant-oat01-xxx", text="ligne 42")
    assert out == {"fired": True,
                   "session_id": "session_01HJK",
                   "session_url": "https://claude.ai/code/session_01HJK"}


def test_the_beta_header_and_token_are_sent(posted):
    routine_fire.fire("trig_01ABC", "sk-ant-oat01-xxx")
    h = posted["headers"]
    assert h["Authorization"] == "Bearer sk-ant-oat01-xxx"
    assert h["anthropic-beta"] == "experimental-cc-routine-2026-04-01"


def test_the_routine_id_addresses_the_endpoint(posted):
    routine_fire.fire("trig_01ABC", "t")
    assert posted["url"].endswith("/routines/trig_01ABC/fire")


def test_an_empty_text_is_not_sent(posted):
    """Une routine planifiée n'a pas de contexte de run : n'envoyer `text` que s'il
    porte quelque chose, plutôt qu'un bloc payload vide côté agent."""
    routine_fire.fire("trig_01ABC", "t", text="   ")
    assert posted["json"] == {}


def test_the_call_is_bounded(posted):
    """Sans timeout, un amont muet immobilise un worker du threadpool à vie."""
    routine_fire.fire("trig_01ABC", "t")
    assert posted["timeout"] == (10, 30)


# ── les échecs, typés ──

def test_a_revoked_token_is_not_retryable(posted, monkeypatch):
    monkeypatch.setattr(routine_fire.requests, "post",
                        lambda url, **kw: _Resp(401, {"error": {"message": "nope"}}))
    with pytest.raises(routine_fire.RoutineFireError) as e:
        routine_fire.fire("trig_01ABC", "mort")
    assert e.value.status == 401 and e.value.retryable is False


def test_a_quota_is_retryable(posted, monkeypatch):
    monkeypatch.setattr(routine_fire.requests, "post", lambda url, **kw: _Resp(429, {}))
    with pytest.raises(routine_fire.RoutineFireError) as e:
        routine_fire.fire("trig_01ABC", "t")
    assert e.value.retryable is True


def test_a_network_failure_is_retryable_and_leaks_nothing(monkeypatch):
    """Le message de requests porte l'URL — donc l'id de routine. Il ne doit pas
    ressortir tel quel : il atterrirait dans le contexte de l'agent et dans Sentry."""
    def _boom(url, **kw):
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='api.anthropic.com'): "
            "/v1/claude_code/routines/trig_01SECRET/fire")
    monkeypatch.setattr(routine_fire.requests, "post", _boom)
    with pytest.raises(routine_fire.RoutineFireError) as e:
        routine_fire.fire("trig_01SECRET", "sk-ant-oat01-TOPSECRET")
    assert e.value.retryable is True
    assert "trig_01SECRET" not in str(e.value)
    assert "TOPSECRET" not in str(e.value)


def test_a_missing_credential_field_fails_before_any_call(monkeypatch):
    def _never(*a, **k):
        raise AssertionError("aucun appel ne doit partir sans credential complet")
    monkeypatch.setattr(routine_fire.requests, "post", _never)
    with pytest.raises(routine_fire.RoutineFireError):
        routine_fire.fire("", "t")
    with pytest.raises(routine_fire.RoutineFireError):
        routine_fire.fire("trig_01ABC", "")
