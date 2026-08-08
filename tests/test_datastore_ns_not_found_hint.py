"""Un 404 datastore dit OÙ vit le tableau quand il est dans une autre org (signal #316).

L'API REST résout le store sur l'org ACTIVE du porteur du token ; viser le tableau
d'une autre org demande l'en-tête `X-Oto-Org`, qui n'était nommé nulle part. Un
namespace bien réel répondait « namespace_not_found » — lu comme « il n'existe pas ».
"""
import asyncio

from oto_mcp import api_routes_datastore as ard
from oto_mcp.datastore import NamespaceNotFound

ROWS_PATH = "/api/datastore/namespaces/{namespace}/rows"


class _FakeReq:
    def __init__(self, path_params):
        self.path_params = path_params
        self.query_params = {}
        self.headers = {}


class _MissingStore:
    """Tout geste sur un namespace lève : c'est le chemin d'erreur qu'on teste."""
    def page_rows(self, *a, **k):
        raise NamespaceNotFound("nope")


def _handlers(monkeypatch, *, orgs, namespaces):
    async def authenticate(request, verifier):
        return "u-1", None

    def json_response(request, payload, status=200):
        return ("ok", status, payload)

    def json_error(request, status, code, detail=None):
        return ("err", status, code, detail)

    def cors_headers(origin):
        return {}

    async def options_handler(request):
        return "opt"

    monkeypatch.setattr(ard, "make_store", lambda sub: _MissingStore())
    monkeypatch.setattr(ard.org_store, "list_orgs_for_user", lambda sub: orgs)
    monkeypatch.setattr(ard.db, "list_datastore_namespaces_for_owners",
                        lambda owners: namespaces)
    routes = ard.make_routes(None, authenticate, json_response, json_error,
                             cors_headers, options_handler)
    return {(r.path, m): r.endpoint for r in routes for m in r.methods}


def _get_rows(monkeypatch, *, orgs, namespaces, ns="leads-accords-dormants"):
    h = _handlers(monkeypatch, orgs=orgs, namespaces=namespaces)[(ROWS_PATH, "GET")]
    return asyncio.run(h(_FakeReq(path_params={"namespace": ns})))


def test_hint_names_the_org_and_the_header(monkeypatch):
    out = _get_rows(
        monkeypatch,
        orgs=[{"org_id": 2, "name": "Otomata Admin"}, {"org_id": 81, "name": "Mūcho"}],
        namespaces=[{"namespace": "leads-accords-dormants", "owner_type": "org",
                     "owner_id": "81"}],
    )
    kind, status, code, detail = out
    assert (kind, status, code) == ("err", 404, "namespace_not_found")
    assert "X-Oto-Org: 81" in detail
    assert "Mūcho" in detail          # nommer l'org, pas seulement son id


def test_no_hint_when_the_namespace_exists_nowhere(monkeypatch):
    out = _get_rows(monkeypatch,
                    orgs=[{"org_id": 2, "name": "Otomata Admin"}],
                    namespaces=[])
    assert out == ("err", 404, "namespace_not_found", None)


def test_no_hint_for_a_namespace_of_another_name(monkeypatch):
    """On ne suggère que le namespace DEMANDÉ — pas un voisin qui traîne."""
    out = _get_rows(monkeypatch,
                    orgs=[{"org_id": 2, "name": "Otomata Admin"}],
                    namespaces=[{"namespace": "autre-chose", "owner_type": "org",
                                 "owner_id": "2"}])
    assert out == ("err", 404, "namespace_not_found", None)


def test_lookup_failure_degrades_to_the_bare_404(monkeypatch):
    """Un indice ne doit jamais transformer un 404 en 500."""
    def boom(sub):
        raise RuntimeError("DB down")

    h = _handlers(monkeypatch, orgs=[], namespaces=[])
    monkeypatch.setattr(ard.org_store, "list_orgs_for_user", boom)
    out = asyncio.run(h[(ROWS_PATH, "GET")](
        _FakeReq(path_params={"namespace": "peu-importe"})))
    assert out == ("err", 404, "namespace_not_found", None)


def test_only_orgs_the_caller_belongs_to_are_probed(monkeypatch):
    """L'indice ne doit rien révéler que le porteur ne puisse déjà lister : les
    owners interrogés sont EXACTEMENT ses orgs."""
    seen = {}

    def capture(owners):
        seen["owners"] = owners
        return []

    h = _handlers(monkeypatch,
                  orgs=[{"org_id": 2, "name": "A"}, {"org_id": 81, "name": "B"}],
                  namespaces=[])
    # …APRÈS le montage : _handlers pose son propre stub sur cette fonction.
    monkeypatch.setattr(ard.db, "list_datastore_namespaces_for_owners", capture)
    asyncio.run(h[(ROWS_PATH, "GET")](_FakeReq(path_params={"namespace": "x"})))
    assert seen["owners"] == [("org", "2"), ("org", "81")]
