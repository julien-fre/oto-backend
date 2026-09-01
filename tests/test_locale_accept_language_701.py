"""oto-backend#701 : capture de `Accept-Language` au 1er login REST.

`_locale_from_accept_language` (parsing pur, même repli que le dashboard —
`i18n.ts:detectBrowserLocale`) et son branchement dans `_authenticate` (chemin JWT
Logto, seul site avec un `Request` HTTP brut). La sémantique « ne jamais écraser un
choix explicite » est un COALESCE côté SQL — testée contre un vrai PostgreSQL dans
`test_upsert_user_locale_live_701.py`, pas ici.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from oto_mcp.api import base as api_base


# ── `_locale_from_accept_language` : parsing pur ─────────────────────────────

@pytest.mark.parametrize("header,attendu", [
    (None, None),
    ("", None),
    ("   ", None),
    ("fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7", "fr"),
    ("en-US,en;q=0.9,fr;q=0.8", "en"),
    ("fr", "fr"),
    ("FR-fr", "fr"),                       # casse : le dashboard fait pareil (toLowerCase)
    ("de-DE,de;q=0.9", "en"),              # langue non couverte → repli 'en', comme le front
    (" fr-FR ", "fr"),                     # espaces autour du 1er tag
    ("fr;q=0.5", "fr"),                    # q-value collée sans espace
])
def test_locale_from_accept_language(header, attendu):
    assert api_base._locale_from_accept_language(header) == attendu


# ── branchement dans `_authenticate` (chemin JWT) ────────────────────────────

class _FakeVerifier:
    async def verify_token(self, token):
        return types.SimpleNamespace(
            claims={"sub": "u-jwt", "email": "a@b.c", "name": "A"})


def _req(headers: dict[str, str]):
    from starlette.requests import Request
    encoded = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({
        "type": "http", "method": "GET", "path": "/api/me", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1", "headers": encoded,
    })


def _authenticate(request, **kw):
    return asyncio.run(
        api_base._authenticate(request, verifier=_FakeVerifier(), **kw))


@pytest.fixture
def capture_upsert(monkeypatch):
    appels = []
    monkeypatch.setattr(
        api_base.db, "upsert_user",
        lambda *a, **k: appels.append(k) or None)
    return appels


def test_authenticate_passe_la_locale_deduite_a_upsert_user(capture_upsert):
    headers = {"Authorization": "Bearer eyJ-un-jwt", "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}
    sub, err = _authenticate(_req(headers))
    assert (sub, err) == ("u-jwt", None)
    assert capture_upsert[-1]["locale"] == "fr"


def test_authenticate_sans_accept_language_ne_pose_rien(capture_upsert):
    sub, err = _authenticate(_req({"Authorization": "Bearer eyJ-un-jwt"}))
    assert (sub, err) == ("u-jwt", None)
    assert capture_upsert[-1]["locale"] is None
