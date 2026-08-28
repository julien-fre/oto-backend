"""Appeler une capacité PAR SA ROUTE — helper partagé des tests datastore (#302).

Les chemins REST du datastore sont passés de routes écrites à la main à des capacités.
Les tests qui les gardaient exerçaient `api_routes_datastore.make_routes(...)` ; ils
exercent désormais la même chose un cran plus haut : la vraie chaîne de l'adaptateur
REST (authenticate → query/corps/path → refus de champ inconnu → autz → handler).

C'est volontairement le VRAI `_make_handler` et non une reformulation : un test qui
rejoue la logique à côté prouve que le test est d'accord avec lui-même. Ce qu'on veut
prouver ici est autre chose — que le fil rendu au dashboard n'a pas bougé.

Ce module n'est pas collecté (il ne commence pas par `test_`) ; `tests/` est sur le
`sys.path` de pytest grâce à `conftest.py`, donc `from _datastore_rest import …`.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from oto_mcp.capabilities import _rest_adapter
from oto_mcp.capabilities.registry import CAPABILITIES


def cap(key: str):
    """Le descripteur de capacité, par sa clé stable."""
    return next(c for c in CAPABILITIES if c.key == key)


def stub_authz(monkeypatch, *, org_id: int = 35, role: str = "member") -> None:
    """`SUB_ONLY` lit l'org active et le rôle : deux requêtes, hors sujet ici.

    On teste la LOGIQUE (refus, formes rendues) ; le chemin SQL est exercé par la
    suite complète au déploiement — c'est la convention du dépôt."""
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "current_org", lambda sub: org_id)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: role)


def call(key: str, *, path_params: Optional[dict] = None, body=None,
         query: bytes = b"", sub: str = "u-1", no_body: bool = False,
         binding_index: int = 0) -> tuple[int, dict]:
    """Joue la route de la capacité `key` et rend `(code HTTP, corps décodé)`.

    `no_body=True` = requête SANS corps du tout (le geste de supervision du dashboard,
    qui poste sans rien) — à ne pas confondre avec un corps `{}`.
    """
    capability = cap(key)
    binding = capability.rest_bindings()[binding_index]

    def _json_error(_req, status, code, detail=None):
        return JSONResponse({"error": code, "detail": detail}, status_code=status)

    def _json_response(_req, payload, status=200):
        return JSONResponse(payload, status_code=status)

    async def _auth(_req, _verifier, **kw):
        # `**kw` : l'adaptateur passe `allow_api_token=False` sur les bindings réservés
        # à une session interactive (gestion des jetons). Le stub l'accepte sans
        # l'appliquer — c'est `test_api_tokens_capability.py` qui vérifie qu'il PART.
        return sub, None

    handler = _rest_adapter._make_handler(capability, binding, None, _auth,
                                          _json_response, _json_error)
    # `bytes` passe TEL QUEL : c'est le seul moyen d'envoyer un corps réellement
    # malformé. Sérialisé, `"{pas du json"` deviendrait une CHAÎNE JSON valide — donc
    # `invalid_body` au lieu d'`invalid_json`, et le test prouverait autre chose que ce
    # qu'il annonce (le seam `json_body` distingue précisément ces deux cas).
    if isinstance(body, (bytes, bytearray)) and not no_body:
        brut = bytes(body)
    else:
        brut = b"" if (body is None or no_body) else json.dumps(body).encode()

    async def _receive():
        return {"type": "http.request", "body": brut, "more_body": False}

    req = Request({"type": "http", "method": binding.verb, "path": binding.path,
                   "headers": [], "query_string": query,
                   "path_params": path_params or {}}, _receive)
    rep = asyncio.run(handler(req))
    return rep.status_code, json.loads(bytes(rep.body))


class Boom:
    """Store dont tout geste lève — le chemin d'erreur, sans mise en scène."""

    def __init__(self, exc: Exception):
        self.exc = exc

    def __getattr__(self, _name):
        def _raise(*a, **k):
            raise self.exc
        return _raise
