"""oto-backend#867 lot 2 — le callback OAuth Google ne bloque plus la boucle
d'événements, et un échange lent rend une erreur nommée.

`google_oauth_callback` (route Starlette `async def`, appelée par le navigateur
de l'utilisateur au retour du consentement Google) faisait `exchange_code` puis
`persist_token` (→ `_fetch_email`) — deux appels HTTP synchrones — nûment.
Même méthode que le callback Zoho déjà protégé (`api/zoho.py:85`).
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from starlette.requests import Request

from oto_mcp.api import datastore as datastore_routes


def _json_response(_request, payload, status=200):
    return {"status": status, "body": payload}


def _json_error(_request, status, code, message=None):
    return {"status": status, "error": code, "detail": message}


async def _options_handler(_request):
    return None


def _routes():
    return datastore_routes.make_routes(
        None, None, _json_response, _json_error, lambda origin: {}, _options_handler)


def _callback_endpoint():
    routes = _routes()
    return next(r.endpoint for r in routes if r.path == "/api/google/oauth/callback")


def _get(query: str) -> Request:
    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "GET", "path": "/api/google/oauth/callback",
                    "headers": [], "query_string": query.encode(), "path_params": {}},
                   receive=_receive)


def _joue(coro):
    porteur: dict = {}

    async def _run():
        porteur["boucle"] = threading.current_thread()
        return await coro
    result = asyncio.run(_run())
    return porteur["boucle"], result


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(datastore_routes.google_oauth, "verify_state",
                        lambda state: ("sub-1", 42))
    return datastore_routes


def test_echange_tourne_hors_boucle(monkeypatch, wired):
    vu: dict = {}

    def _exchange(code):
        vu["thread"] = threading.current_thread()
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    monkeypatch.setattr(wired.google_oauth, "exchange_code", _exchange)
    monkeypatch.setattr(wired.google_oauth, "persist_token", lambda sub, org, tok: "e@x.io")
    handler = _callback_endpoint()

    boucle, result = _joue(handler(_get("code=c&state=s")))
    assert vu["thread"] is not boucle, (
        "exchange_code a tourné dans le thread de l'event loop — un Google lent "
        "gèlerait le processus entier (oto-backend#867)")
    # Succès : une redirection, pas une erreur JSON.
    assert not isinstance(result, dict) or result.get("status", 200) < 400


def test_echange_lent_rend_un_504_nomme(monkeypatch, wired):
    monkeypatch.setattr(wired, "_OAUTH_EXCHANGE_TIMEOUT_S", 0.05)

    def _lent(code):
        import time
        time.sleep(1)

    monkeypatch.setattr(wired.google_oauth, "exchange_code", _lent)
    handler = _callback_endpoint()

    _, result = _joue(handler(_get("code=c&state=s")))
    assert result["status"] == 504 and result["error"].startswith("oauth_exchange_timeout"), (
        f"un échange OAuth lent doit rendre un 504 nommé, pas un gel — reçu {result!r}")


def test_le_controle_mord__un_appel_NU_dans_la_boucle_est_detecte():
    vu: dict = {}

    async def _nu():
        vu["thread"] = threading.current_thread()
        return 1

    boucle, _ = _joue(_nu())
    assert vu["thread"] is boucle, "la sonde elle-même doit savoir dire « dans la boucle »"
