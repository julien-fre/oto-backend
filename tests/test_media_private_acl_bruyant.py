"""B1 de l'inventaire des silences (27/08) : « privé » ne se dit pas quand l'ACL a échoué.

`make_public` LÈVE une `MediaError` depuis toujours ; `make_private` avalait la même
panne et rendait `None`. L'asymétrie était le bug : sur `PUT /api/projects/{p}/files/{f}/public
{"public": false}`, l'ACL S3 restait `public-read` — donc l'URL permanente du fichier
restait ouverte — pendant que la base écrivait `public=false` et que l'API rendait
`{"ok": true}`. L'écran affichait « privé », le fichier ne l'était pas.

Les deux tests ci-dessous décrivent le SYSTÈME (ce que le seam lève, ce que la route
persiste), pas l'intention : ils tombent tous les deux sur le code d'avant.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from oto_mcp.api import projects as P
from oto_mcp import media_store


class _AclRefusee:
    """Client S3 dont le `put_object_acl` échoue — la panne exacte du scénario B1."""

    def put_object_acl(self, **kw):
        raise RuntimeError("AccessDenied")


def test_make_private_leve_comme_make_public(monkeypatch):
    monkeypatch.setattr(media_store, "_get_client", lambda: _AclRefusee())
    monkeypatch.setattr(media_store, "_bucket", lambda: "b")
    with pytest.raises(media_store.MediaError) as e:
        media_store.make_private("k/abc/doc.pdf")
    assert e.value.status == 500 and e.value.code == "acl_failed"
    # Symétrie : la même panne sur la bascule inverse porte le MÊME code.
    with pytest.raises(media_store.MediaError) as pub:
        media_store.make_public("k/abc/doc.pdf")
    assert pub.value.code == e.value.code


def _req(body: dict):
    from starlette.requests import Request

    payload = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({
        "type": "http", "method": "PUT", "path": "/api/projects/7/files/1/public",
        "query_string": b"", "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1", "headers": [(b"content-type", b"application/json")],
        "path_params": {"project_id": "7", "file_id": "1"},
    }, receive)


@pytest.fixture
def route(monkeypatch):
    """Toutes les gardes en amont ouvertes : on n'observe QUE la bascule d'ACL."""
    monkeypatch.setattr(P, "_authenticate", lambda *a, **k: _ok("u1"))
    monkeypatch.setattr(P.db, "get_project_file",
                        lambda fid: {"id": 1, "project_id": 7, "s3_key": "k/abc/doc.pdf",
                                     "title": "Brief", "filename": "doc.pdf"})
    monkeypatch.setattr(P, "_project_org_context_error", lambda *a, **k: None)
    ecrits = []
    monkeypatch.setattr(P.db, "set_project_file_public",
                        lambda *a, **k: ecrits.append(a) or {"id": 1})
    monkeypatch.setattr(P.db, "log_project_activity", lambda *a, **k: None)
    return ecrits


async def _ok(sub):
    return sub, None


def test_la_route_ne_dit_pas_prive_quand_l_acl_a_echoue(route, monkeypatch):
    import oto_mcp.ownership as ownership
    monkeypatch.setattr(ownership, "can_access", lambda *a, **k: True)
    monkeypatch.setattr(media_store, "_get_client", lambda: _AclRefusee())
    monkeypatch.setattr(media_store, "_bucket", lambda: "b")

    resp = asyncio.run(P.project_file_public(_req({"public": False}),
                                             verifier=types.SimpleNamespace()))

    assert resp.status_code == 500
    assert json.loads(bytes(resp.body).decode())["error"] == "acl_failed"
    # …et surtout : la base n'a PAS enregistré « privé » sur un objet resté public.
    assert route == []
