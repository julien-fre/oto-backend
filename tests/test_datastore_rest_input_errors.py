"""Un refus d'ENTRÉE du store est un 400 sur REST, jamais un 500 (#390).

Trouvé au smoke prod de v1.80.0, invisible aux tests du store : la garde `_id`
protégeait bien (rien n'était inséré), mais la face REST rendait « Internal Server
Error ». Le store lève `ValueError`, que `data_write` traduisait côté MCP
(`INVALID_PARAMS`) et que les routes n'attrapaient pas — deux faces du même métier,
une seule sachant dire pourquoi elle refuse.

Deux conséquences, et la seconde est la pire : l'appelant reçoit une erreur opaque
là où le message dit exactement quoi corriger, et Sentry compte une faute d'appel
comme un bug backend (le 500 remonte, le 400 non).

Couvre aussi la collision de clé métier sur PATCH, qui passait par le même trou.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp import api_routes_datastore as ard

NS_PATH = "/api/datastore/namespaces/{namespace}/rows"
ROW_PATH = "/api/datastore/namespaces/{namespace}/rows/{row_id}"


class _Req:
    def __init__(self, path_params, body):
        self.path_params = path_params
        self._body = body
        self.headers = {}

    async def json(self):
        return self._body


def _handlers(monkeypatch, store):
    async def authenticate(request, verifier):
        return "u-1", None

    def json_response(request, payload, status=200):
        return ("ok", status, payload)

    def json_error(request, status, code, detail=None, *a, **k):
        return ("err", status, code, detail)

    def cors_headers(origin):
        return {}

    async def options_handler(request):
        return "opt"

    monkeypatch.setattr(ard, "make_store", lambda sub: store)
    routes = ard.make_routes(None, authenticate, json_response, json_error,
                             cors_headers, options_handler)
    return {(r.path, m): r.endpoint for r in routes for m in r.methods}


class _Store:
    """Store qui refuse comme le vrai : `ValueError` sur une entrée invalide."""

    MSG = ("`_id` ('019f-x') posé DANS `row` : il y serait ignoré et ton écriture "
           "INSÉRERAIT une nouvelle ligne au lieu de modifier celle-là.")

    def append_row(self, namespace, data, *, trace=None):
        raise ValueError(self.MSG)

    def update_row(self, namespace, row_id, patch, *, trace=None):
        raise ValueError(self.MSG)

    def off_schema_report(self):
        return {}


def test_append_refusal_is_an_actionable_400(monkeypatch):
    h = _handlers(monkeypatch, _Store())[(NS_PATH, "POST")]
    kind, status, code, detail = asyncio.run(h(_Req(
        path_params={"namespace": "160"}, body={"_id": "019f-x", "statut": "e"})))
    assert (kind, status, code) == ("err", 400, "invalid_row_input")
    # le message du store arrive JUSQU'À l'appelant : c'est ce qui rend la reprise
    # mécanique, et c'est précisément ce que le 500 mangeait.
    assert "INSÉRERAIT" in detail


def test_patch_refusal_is_an_actionable_400(monkeypatch):
    h = _handlers(monkeypatch, _Store())[(ROW_PATH, "PATCH")]
    kind, status, code, detail = asyncio.run(h(_Req(
        path_params={"namespace": "160", "row_id": "row-1"},
        body={"_id": "019f-autre"})))
    assert (kind, status, code) == ("err", 400, "invalid_row_input")
    assert detail


def test_business_key_collision_on_patch_is_also_a_400(monkeypatch):
    """Même trou, autre cause : `update_row` convertit la violation d'unicité en
    `ValueError` actionnable — elle ressortait en 500."""
    class _Collide(_Store):
        def update_row(self, namespace, row_id, patch, *, trace=None):
            raise ValueError("un autre enregistrement porte déjà siren=111 "
                             "(clé métier unique) — impossible de dupliquer")

    h = _handlers(monkeypatch, _Collide())[(ROW_PATH, "PATCH")]
    kind, status, code, detail = asyncio.run(h(_Req(
        path_params={"namespace": "160", "row_id": "row-1"}, body={"siren": "111"})))
    assert (kind, status, code) == ("err", 400, "invalid_row_input")
    assert "clé métier unique" in detail
