"""oto-backend#867 lot 2 — le rafraîchissement du jeton Google (12 outils) ne
bloque plus la boucle d'événements, et un Google lent rend une erreur nommée.

`_client_for_user` peut déclencher `google_oauth.credentials_for` →
`_refresh_access_token` (HTTP synchrone, 15s) quand l'access token stocké est
expiré. Les appels à l'API Google, eux, étaient déjà en `asyncio.to_thread` dans
chacun des 12 tools (`gmail_message`/`gmail_compose`, `drive_*`×2, `sheets_*`×2,
`tasks_*`×2, `calendar_*`×2, `chat_*`×2) — seule la CONSTRUCTION du client (donc
le refresh) tournait encore nûment dans la boucle.

Même méthode que les lots précédents (Unipile, FOD) : on OBSERVE le thread, et un
contrôle qui MORD (neutralisation vérifiée empiriquement, pas supposée).
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from oto_mcp.tools import calendar as T_calendar
from oto_mcp.tools import chat as T_chat
from oto_mcp.tools import drive as T_drive
from oto_mcp.tools import gmail as T_gmail
from oto_mcp.tools import sheets as T_sheets
from oto_mcp.tools import tasks as T_tasks
from oto_mcp.mcp_errors import McpError

MODULES = [T_gmail, T_drive, T_sheets, T_tasks, T_calendar, T_chat]


def _joue(coro):
    porteur: dict = {}

    async def _run():
        porteur["boucle"] = threading.current_thread()
        return await coro
    try:
        result = asyncio.run(_run())
        return porteur["boucle"], result, None
    except McpError as e:
        return porteur["boucle"], None, e


@pytest.mark.parametrize("module", MODULES, ids=[m.__name__ for m in MODULES])
def test_client_for_user_async_tourne_hors_boucle(monkeypatch, module):
    vu: dict = {}

    def _sync(account=None):
        vu["thread"] = threading.current_thread()
        return object()

    monkeypatch.setattr(module, "_client_for_user", _sync)
    boucle, result, err = _joue(module._client_for_user_async(None))
    assert err is None
    assert vu["thread"] is not boucle, (
        f"{module.__name__}._client_for_user a tourné dans le thread de l'event "
        "loop — un rafraîchissement de jeton Google lent gèlerait le processus "
        "(oto-backend#867)")


@pytest.mark.parametrize("module", MODULES, ids=[m.__name__ for m in MODULES])
def test_client_for_user_lent_rend_une_erreur_nommee(monkeypatch, module):
    monkeypatch.setattr(module, "_GOOGLE_CLIENT_TIMEOUT_S", 0.05)

    def _lent(account=None):
        import time
        time.sleep(1)

    monkeypatch.setattr(module, "_client_for_user", _lent)
    _, _, err = _joue(module._client_for_user_async(None))
    assert err is not None and "répondu" in err.error.message, (
        f"{module.__name__}: un Google lent doit rendre une McpError nommée, "
        f"pas un gel — reçu {err!r}")


def test_le_controle_mord__un_appel_NU_dans_la_boucle_est_detecte():
    """Contrôle négatif, comme aux lots précédents : la sonde de thread doit
    savoir dire « dans la boucle » — sinon un vert ne prouverait rien."""
    vu: dict = {}

    async def _nu():
        vu["thread"] = threading.current_thread()
        return 1

    boucle, _, _ = _joue(_nu())
    assert vu["thread"] is boucle, "la sonde elle-même doit savoir dire « dans la boucle »"
