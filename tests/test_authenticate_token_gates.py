"""Les deux crans posés sur les jetons `oto_`, testés là où ils s'appliquent.

`test_token_scopes.py` fige la logique pure ; ici on exerce `_authenticate`, le
point de passage unique de TOUTE route REST (routes écrites main *et* adaptateur de
capacités). C'est lui qui décide, donc c'est lui qu'il faut tenir.

⚠️ La portée est portée par une ContextVar : elle vit dans la TÂCHE qui a authentifié
(le handler lit ce que son `await authenticate(...)` a posé). Un `asyncio.run` par
appel donnerait un contexte neuf à chaque fois — les scénarios qui observent la
ContextVar tournent donc dans UNE seule coroutine, comme une vraie requête.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from oto_mcp import api_routes
from oto_mcp.auth import token_scopes


def _req(method: str, path: str, token: str = "oto_deadbeef"):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": method, "path": path, "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


class _FakeVerifier:
    """JWTVerifier minimal : un JWT valide porteur d'un `sub`."""

    async def verify_token(self, token):
        return types.SimpleNamespace(claims={"sub": "u-jwt", "email": "a@b.c"})


def _auth(request, **kw):
    return asyncio.run(api_routes._authenticate(request, verifier=_FakeVerifier(), **kw))


def _body(response) -> dict:
    return json.loads(bytes(response.body).decode())


@pytest.fixture
def token_row(monkeypatch):
    """Stub du lookup DB : `verify_api_token` rend `{sub, scopes}` (ou None)."""
    box = {"row": {"sub": "u-1", "scopes": None}}
    monkeypatch.setattr(api_routes.db, "verify_api_token", lambda t: box["row"])
    monkeypatch.setattr(api_routes.db, "upsert_user", lambda *a, **k: None)
    yield box
    token_scopes.set_current(None)


# ── Cran 1 : un jeton ne fabrique pas de jeton ───────────────────────────────

def test_api_token_cannot_reach_token_management(token_row):
    sub, err = _auth(_req("POST", "/api/me/tokens"), allow_api_token=False)
    assert sub is None
    assert err.status_code == 403
    assert _body(err)["error"] == "api_token_forbidden"


def test_token_management_stays_open_to_an_interactive_session(token_row):
    """Le refus vise le PORTEUR DE JETON, pas la route : un JWT y passe toujours."""
    sub, err = _auth(_req("POST", "/api/me/tokens", token="eyJ-un-jwt"),
                     allow_api_token=False)
    assert err is None and sub == "u-jwt"


# ── Cran 2 : la portée est appliquée au point de passage ─────────────────────

def test_unscoped_token_is_unchanged(token_row):
    sub, err = _auth(_req("GET", "/api/me"))
    assert (sub, err) == ("u-1", None)


def test_scoped_token_reads_its_table(token_row):
    token_row["row"] = {"sub": "u-1", "scopes": {"namespaces": {"leads": "read"}}}

    async def scenario():
        sub, err = await api_routes._authenticate(
            _req("GET", "/api/datastore/namespaces/leads/rows"), _FakeVerifier())
        # Le handler qui suit, DANS LA MÊME TÂCHE, doit voir la portée : c'est ce
        # qui permet à `ds_list_ns` de filtrer son catalogue.
        return sub, err, token_scopes.current()

    sub, err, scope = asyncio.run(scenario())
    assert (sub, err) == ("u-1", None)
    assert scope == {"namespaces": {"leads": "read"}}


def test_scoped_token_is_forbidden_elsewhere_in_the_org(token_row):
    token_row["row"] = {"sub": "u-1", "scopes": {"namespaces": {"leads": "read"}}}
    for method, path in (("GET", "/api/datastore/namespaces/autre/rows"),
                         ("PATCH", "/api/datastore/namespaces/leads/rows/1"),
                         ("GET", "/api/me"),
                         ("GET", "/api/me/tokens"),
                         ("POST", "/api/me/projects")):
        sub, err = _auth(_req(method, path))
        assert sub is None and err.status_code == 403, path
        assert _body(err)["error"] == "token_scope_forbidden"


def test_denial_names_the_tables_the_token_does_open(token_row):
    """Un refus muet coûte une session de debug à l'intégrateur."""
    token_row["row"] = {"sub": "u-1", "scopes": {"namespaces": {"leads": "read"}}}
    _, err = _auth(_req("GET", "/api/me"))
    assert "leads" in _body(err)["detail"]


def test_scope_never_survives_the_previous_authentication(token_row):
    """La ContextVar est posée à CHAQUE authentification, y compris à None : dans une
    même tâche, une requête non portée ne doit rien hériter de la précédente."""
    async def scenario():
        token_row["row"] = {"sub": "u-1", "scopes": {"namespaces": {"leads": "read"}}}
        await api_routes._authenticate(
            _req("GET", "/api/datastore/namespaces/leads/rows"), _FakeVerifier())
        first = token_scopes.current()
        token_row["row"] = {"sub": "u-1", "scopes": None}
        await api_routes._authenticate(_req("GET", "/api/me"), _FakeVerifier())
        return first, token_scopes.current()

    first, second = asyncio.run(scenario())
    assert first == {"namespaces": {"leads": "read"}}
    assert second is None


def test_jwt_never_carries_a_token_scope(token_row):
    async def scenario():
        token_row["row"] = {"sub": "u-1", "scopes": {"namespaces": {"leads": "read"}}}
        await api_routes._authenticate(
            _req("GET", "/api/datastore/namespaces/leads/rows"), _FakeVerifier())
        await api_routes._authenticate(
            _req("GET", "/api/me", token="eyJ-un-jwt"), _FakeVerifier())
        return token_scopes.current()

    assert asyncio.run(scenario()) is None


def test_invalid_token_clears_any_scope(token_row):
    async def scenario():
        token_row["row"] = {"sub": "u-1", "scopes": {"namespaces": {"leads": "read"}}}
        await api_routes._authenticate(
            _req("GET", "/api/datastore/namespaces/leads/rows"), _FakeVerifier())
        token_row["row"] = None
        out = await api_routes._authenticate(
            _req("GET", "/api/datastore/namespaces/leads/rows"), _FakeVerifier())
        return out, token_scopes.current()

    (sub, err), scope = asyncio.run(scenario())
    assert sub is None and err.status_code == 401
    assert scope is None


def test_missing_bearer_is_still_a_401():
    from starlette.requests import Request
    req = Request({"type": "http", "method": "GET", "path": "/api/me",
                   "query_string": b"", "root_path": "", "scheme": "http",
                   "server": ("test", 80), "http_version": "1.1", "headers": []})
    sub, err = _auth(req)
    assert sub is None and err.status_code == 401
