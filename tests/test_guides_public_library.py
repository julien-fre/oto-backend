"""La vitrine publique des guides ne sert QUE le scope plateforme.

Ces deux routes sont anonymes : elles alimentent le site et le rendent lisible par
un humain. Le risque n'est pas qu'elles ne marchent pas — c'est qu'elles servent
un jour un guide d'ORG (rédigé par un client, pour ses équipes) ou d'USER. Le
cloisonnement tient à deux détails faciles à défaire en refactorant :

- `list_guides_for()` appelé SANS `sub` ni `org_id` (les autres scopes sont alors
  hors de portée par construction, pas par filtre) ;
- `read_guide_scoped(..., scope='platform')` EXPLICITE — sans ce mot, la fonction
  retombe sur org puis user.

D'où ces tests : ils figent l'appel, pas seulement la réponse.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from oto_mcp import api_routes


def _req(path: str, **params):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1", "headers": [],
        "path_params": params,
    })


def _routes():
    """Les handlers publics, extraits de la fabrique de routes."""
    verifier = types.SimpleNamespace()
    out = {}
    for r in api_routes.make_routes(verifier):
        if getattr(r, "path", None) in ("/api/guides/library", "/api/guides/library/{slug}"):
            if "GET" in getattr(r, "methods", set()):
                out[r.path] = r.endpoint
    return out


def _body(resp) -> dict:
    return json.loads(bytes(resp.body).decode())


def test_la_liste_ne_demande_ni_user_ni_org(monkeypatch):
    seen = {}

    def _list(sub=None, org_id=None):
        seen["sub"], seen["org_id"] = sub, org_id
        return [{"slug": "claude-tag", "scope": "platform", "title": "T", "description": "d"}]

    monkeypatch.setattr(api_routes.guide_store, "list_guides_for", _list)
    resp = asyncio.run(_routes()["/api/guides/library"](_req("/api/guides/library")))
    assert seen == {"sub": None, "org_id": None}, "un scope non-plateforme deviendrait atteignable"
    assert _body(resp)["guides"][0]["scope"] == "platform"


def test_le_detail_epingle_le_scope_plateforme(monkeypatch):
    seen = {}

    def _read(slug, *, scope=None, org_id=None, sub=None):
        seen.update(slug=slug, scope=scope)
        return {"slug": slug, "scope": "platform", "title": "T",
                "description": "d", "body_md": "# corps"}

    monkeypatch.setattr(api_routes.guide_store, "read_guide_scoped", _read)
    resp = asyncio.run(_routes()["/api/guides/library/{slug}"](
        _req("/api/guides/library/claude-tag", slug="claude-tag")))
    assert seen == {"slug": "claude-tag", "scope": "platform"}, \
        "sans scope explicite, la route retomberait sur les guides d'org puis d'user"
    assert _body(resp)["body_md"] == "# corps"


def test_guide_inconnu_404(monkeypatch):
    monkeypatch.setattr(api_routes.guide_store, "read_guide_scoped",
                        lambda *a, **k: None)
    resp = asyncio.run(_routes()["/api/guides/library/{slug}"](
        _req("/api/guides/library/nope", slug="nope")))
    assert resp.status_code == 404
    assert _body(resp)["error"] == "unknown_guide"
