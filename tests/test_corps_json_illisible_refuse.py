"""B2, B3 et B4 de l'inventaire des silences (27/08) : un corps illisible est refusé.

Le patron `try: body = await request.json() / except Exception: body = {}` traduisait
« je n'ai pas compris ce que tu as écrit » en « tu n'as rien demandé » — donc les
valeurs par DÉFAUT. Trois sites où le défaut est plus dangereux que la demande :

- **B2** `POST /api/me/tokens` — `{"scopes": …}` mal formé ⇒ `scopes=None` ⇒ **jeton
  API NON PORTÉ** (droits pleins du sub) émis à la place du jeton borné demandé.
- **B3** `POST /api/admin/users/{sub}/tokens` — idem côté super-admin, plus
  `ttl_days=None` : jeton non porté **et sans expiration**, émis pour un tiers.
- **B4** `capabilities/_rest_adapter` — corps ignoré sur les ~200 routes de capacité
  générées, alors que le commentaire vingt lignes plus bas l'interdit mot pour mot.

Le seam `json_body.read_json_body` distingue le corps ABSENT (rien n'a été demandé :
`{}`, contrat inchangé) du corps ILLISIBLE (refus nommé) — c'est cette distinction
que les tests exercent, pas seulement le refus.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from oto_mcp import json_body


def _req(raw: bytes, *, method: str = "POST", path: str = "/api/me/tokens",
         path_params: dict = None, query: bytes = b""):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http", "method": method, "path": path, "query_string": query,
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1", "headers": [(b"content-type", b"application/json")],
    }
    if path_params:
        scope["path_params"] = path_params
    return Request(scope, receive)


def _body(resp) -> dict:
    return json.loads(bytes(resp.body).decode())


# ── le seam lui-même : trois cas, trois réponses ─────────────────────────────

def test_corps_absent_rend_un_dict_vide():
    """Rien n'a été demandé : les défauts SONT le contrat (un `POST /api/me/tokens`
    sans corps émet un jeton `cli` non porté, et c'est voulu)."""
    assert asyncio.run(json_body.read_json_body(_req(b""))) == {}
    assert asyncio.run(json_body.read_json_body(_req(b"   \n"))) == {}


def test_corps_lisible_passe():
    assert asyncio.run(json_body.read_json_body(_req(b'{"label": "ci"}'))) == {"label": "ci"}


def test_corps_illisible_leve_invalid_json():
    with pytest.raises(json_body.InvalidJsonBody) as e:
        asyncio.run(json_body.read_json_body(_req(b'{"scopes": ')))
    assert e.value.code == "invalid_json"


def test_corps_valide_mais_pas_un_objet_leve_invalid_body():
    with pytest.raises(json_body.InvalidJsonBody) as e:
        asyncio.run(json_body.read_json_body(_req(b'["scopes"]')))
    assert e.value.code == "invalid_body"


# ── B2 : un jeton NON PORTÉ ne s'émet jamais par accident ────────────────────

@pytest.fixture
def me_tokens(monkeypatch):
    """Route `me_tokens_create` montée avec ses seams stubbés ; on capte ce que
    `create_api_token` reçoit — c'est là que se lit le succès déguisé."""
    from oto_mcp import api_routes_datastore as D

    emis = []

    async def _auth(request, verifier, **kw):
        return "u-1", None

    monkeypatch.setattr(D.db, "create_api_token",
                        lambda sub, **kw: emis.append(kw) or "oto_secret")
    monkeypatch.setattr(D.db, "list_api_tokens", lambda sub: [])
    monkeypatch.setattr(D, "make_store", lambda sub: types.SimpleNamespace(
        list_namespaces=lambda: [{"namespace": "clients"}]))

    from oto_mcp.api_routes_base import (_cors_headers, _json, _json_error,
                                         options_handler)
    routes = D.make_routes(verifier=object(), authenticate=_auth,
                           json_response=_json, json_error=_json_error,
                           cors_headers=_cors_headers, options_handler=options_handler)
    handler = {r.name: r.endpoint for r in routes}["me_tokens_create"]
    return handler, emis


def test_scopes_illisibles_ne_produisent_pas_un_jeton_non_porte(me_tokens):
    handler, emis = me_tokens
    resp = asyncio.run(handler(_req(b'{"label": "ci", "scopes": {"namespaces": ["clients"')))
    assert resp.status_code == 400 and _body(resp)["error"] == "invalid_json"
    assert emis == [], "un jeton a été émis alors que la portée demandée était illisible"


def test_sans_corps_le_jeton_cli_par_defaut_reste_possible(me_tokens):
    handler, emis = me_tokens
    resp = asyncio.run(handler(_req(b"")))
    assert resp.status_code == 201
    assert emis and emis[0]["scopes"] is None and emis[0]["label"] == "cli"


# ── B3 : côté super-admin, non porté ET sans expiration ──────────────────────

@pytest.fixture
def admin_tokens(monkeypatch):
    from oto_mcp import api_routes_admin as A

    emis = []

    async def _auth(request, verifier, **kw):
        return "u-admin", None

    monkeypatch.setattr(A, "_authenticate", _auth)
    monkeypatch.setattr(A.access, "is_super_admin", lambda sub: True)
    monkeypatch.setattr(A.db, "get_user", lambda sub: {"sub": sub})
    monkeypatch.setattr(A.db, "create_api_token",
                        lambda sub, **kw: emis.append(kw) or "oto_secret")
    return A, emis


def test_admin_corps_illisible_n_emet_pas_de_jeton_eternel(admin_tokens):
    A, emis = admin_tokens
    resp = asyncio.run(A.admin_tokens_create(
        _req(b'{"ttl_days": 7, "scopes": {', path="/api/admin/users/u-2/tokens",
             path_params={"sub": "u-2"}),
        verifier=object()))
    assert resp.status_code == 400 and _body(resp)["error"] == "invalid_json"
    assert emis == [], "jeton non porté et sans expiration émis pour un tiers"


# ── B4 : l'adaptateur des ~200 routes de capacité ────────────────────────────

def test_l_adaptateur_refuse_un_corps_illisible():
    """Le commentaire de `_rest_adapter` interdit mot pour mot d'IGNORER ce qu'on ne
    comprend pas. La garde couvrait les champs inconnus ; elle laissait passer un
    corps qui ne parse pas."""
    from pydantic import BaseModel

    from oto_mcp.capabilities import _rest_adapter as RA
    from oto_mcp.capabilities._types import Capability, RawCtx, RestBinding

    appels = []

    class _In(BaseModel):
        label: str = "defaut"

    cap = Capability(key="test.echo", Input=_In,
                     authz=lambda ctx, inp: ctx,
                     handler=lambda ctx, inp: appels.append(inp.label) or {"ok": True},
                     rest=RestBinding(verb="POST", path="/api/test/echo"))

    async def _auth(request, verifier):
        return "u-1", None

    from oto_mcp.api_routes_base import _json, _json_error
    handler = RA._make_handler(cap, cap.rest, object(), _auth, _json, _json_error)

    resp = asyncio.run(handler(_req(b'{"label": ', path="/api/test/echo")))
    assert resp.status_code == 400 and _body(resp)["error"] == "invalid_json"
    assert appels == [], "la capacité a tourné sur des valeurs par défaut"

    # Corps ABSENT : le contrat des routes sans argument ne bouge pas.
    assert asyncio.run(handler(_req(b"", path="/api/test/echo"))).status_code == 200
    assert appels == ["defaut"]
