"""Le tableau lui-même, en capacité : mêmes chemins, mêmes réponses, mêmes refus (#302).

Cinq chemins ont quitté les routes écrites à la main pour
`capabilities/datastore_namespaces.py`. Une migration de plomberie ne vaut que si on
peut MONTRER qu'elle n'a rien changé au fil : ces tests font tourner la vraie chaîne de
l'adaptateur REST (authenticate → validation → autz → handler) et lisent le code HTTP
et le corps rendus, pas la logique reformulée.

Un seul écart est attendu, et il est voulu : la validation de la couche capacité REFUSE
un champ inconnu (400 `unknown_fields`) là où ces routes l'ignoraient — dernier test.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from oto_mcp.capabilities import _rest_adapter, datastore_namespaces as dsn
from oto_mcp.capabilities.registry import CAPABILITIES
from oto_mcp.datastore import NamespaceExists, NamespaceForbidden, NamespaceNotFound


@pytest.fixture(autouse=True)
def _sans_db(monkeypatch):
    """`SUB_ONLY` lit l'org active et le rôle : deux requêtes, hors sujet ici.

    On teste la LOGIQUE de ces capacités (les refus, les formes rendues) ; le chemin
    SQL est exercé au déploiement par la suite complète du CI."""
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "current_org", lambda sub: 35)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: "member")


def _cap(key: str):
    return next(c for c in CAPABILITIES if c.key == key)


def _call(key, *, path_params=None, body=None, query=b"", sub="u-1"):
    """Exerce la capacité PAR SA ROUTE — la même chaîne que sert le serveur."""
    cap = _cap(key)
    binding = cap.rest_bindings()[0]

    def _json_error(_req, status, code, detail=None):
        return JSONResponse({"error": code, "detail": detail}, status_code=status)

    def _json_response(_req, payload, status=200):
        return JSONResponse(payload, status_code=status)

    async def _auth(_req, _verifier):
        return sub, None

    handler = _rest_adapter._make_handler(cap, binding, None, _auth,
                                          _json_response, _json_error)
    brut = b"" if body is None else json.dumps(body).encode()

    async def _receive():
        return {"type": "http.request", "body": brut, "more_body": False}

    req = Request({"type": "http", "method": binding.verb, "path": binding.path,
                   "headers": [], "query_string": query,
                   "path_params": path_params or {}}, _receive)
    rep = asyncio.run(handler(req))
    return rep.status_code, json.loads(bytes(rep.body))


class _Store:
    """Le store, tel que les handlers l'appellent — chaque test injecte son verdict."""

    def __init__(self, **verdicts):
        self.v = verdicts
        self.calls: list = []

    def _out(self, name, *a):
        self.calls.append((name, *a))
        out = self.v.get(name)
        if isinstance(out, Exception):
            raise out
        return out

    def list_namespaces(self):
        return self._out("list_namespaces")

    def create_namespace(self, namespace, *, owner_type=None, owner_id=None):
        self._out("create_namespace", namespace, owner_type, owner_id)
        return {"namespace": namespace, "id": 42,
                "url": "https://dashboard.oto.ninja/data/42"}

    def delete_namespace(self, namespace):
        return self._out("delete_namespace", namespace)

    def get_url(self, namespace):
        return self._out("get_url", namespace)

    def resolve_ns_id(self, namespace):
        return self._out("resolve_ns_id", namespace)


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(dsn, "make_store", lambda sub: s)
    # `ns_not_found` interroge la base pour son indice cross-org : sans stub il
    # tomberait dans son fail-open. On le rend explicite plutôt que fortuit.
    monkeypatch.setattr(dsn, "ns_not_found", dsn.ns_not_found)
    return s


# --- lister ---------------------------------------------------------------------

def test_la_liste_rend_lenveloppe_namespaces(store, monkeypatch):
    store.v["list_namespaces"] = [{"id": 1, "namespace": "vivier"}]
    monkeypatch.setattr(dsn.token_scopes, "filter_namespaces", lambda rows: rows)
    assert _call("me.datastore.list_namespaces") == (
        200, {"namespaces": [{"id": 1, "namespace": "vivier"}]})


def test_la_liste_reste_filtree_par_la_portee_du_jeton(store, monkeypatch):
    """Seule réponse filtrée plutôt que refusée à un jeton porté : sans catalogue,
    une intégration n'a pas le schéma de son tableau."""
    store.v["list_namespaces"] = [{"namespace": "vivier"}, {"namespace": "prive"}]
    monkeypatch.setattr(dsn.token_scopes, "current",
                        lambda: {"namespaces": {"vivier": "read"}})
    code, corps = _call("me.datastore.list_namespaces")
    assert code == 200
    assert [r["namespace"] for r in corps["namespaces"]] == ["vivier"]
    assert corps["namespaces"][0]["can_govern"] is False


# --- créer ----------------------------------------------------------------------

def test_la_creation_rend_201_et_le_tableau(store):
    code, corps = _call("me.datastore.create_namespace", body={"namespace": " vivier "})
    assert code == 201                       # le code d'avant la migration, à l'octet
    assert corps == {"namespace": "vivier", "id": 42,
                     "url": "https://dashboard.oto.ninja/data/42"}
    assert store.calls[0] == ("create_namespace", "vivier", "user", "u-1")


def test_un_nom_vide_est_un_refus_nomme(store):
    assert _call("me.datastore.create_namespace", body={"namespace": "  "})[:1] == (400,)
    assert _call("me.datastore.create_namespace", body={})[1]["error"] == "missing_namespace"


def test_un_classeur_dorg_exige_lappartenance(store, monkeypatch):
    monkeypatch.setattr(dsn.roles, "is_org_member", lambda sub, org: False)
    code, corps = _call("me.datastore.create_namespace",
                        body={"namespace": "v", "owner": {"type": "org", "id": 81}})
    assert (code, corps["error"]) == (403, "not_org_member")
    assert store.calls == [], "rien n'est créé quand l'appartenance manque"


def test_un_classeur_dequipe_exige_la_lecture_de_lequipe(store, monkeypatch):
    monkeypatch.setattr(dsn.roles, "can_read_group", lambda sub, gid: True)
    code, _ = _call("me.datastore.create_namespace",
                    body={"namespace": "v", "owner": {"type": "group", "id": 7}})
    assert code == 201
    assert store.calls[0] == ("create_namespace", "v", "group", "7")


@pytest.mark.parametrize("owner,attendu", [
    ({"type": "org", "id": "pas-un-id"}, "invalid_owner_id"),
    ({"type": "org"}, "invalid_owner_id"),
    ({"type": "martien", "id": 1}, "invalid_owner_type"),
])
def test_les_proprietaires_mal_formes_sont_refuses(store, owner, attendu):
    code, corps = _call("me.datastore.create_namespace",
                        body={"namespace": "v", "owner": owner})
    assert (code, corps["error"]) == (400, attendu)


def test_un_nom_deja_pris_est_un_409(store, monkeypatch):
    monkeypatch.setattr(dsn, "make_store", lambda sub: _Boom(NamespaceExists("v")))
    code, corps = _call("me.datastore.create_namespace", body={"namespace": "v"})
    assert (code, corps["error"]) == (409, "namespace_exists")


class _Boom:
    """Store dont tout geste lève — le chemin d'erreur, sans mise en scène."""

    def __init__(self, exc):
        self.exc = exc

    def __getattr__(self, _name):
        def _raise(*a, **k):
            raise self.exc
        return _raise


# --- supprimer ------------------------------------------------------------------

def test_la_suppression_rend_ok_et_le_nom(store):
    assert _call("me.datastore.delete_namespace",
                 path_params={"namespace": "vivier"}) == (200, {"ok": True,
                                                                "namespace": "vivier"})


def test_la_suppression_sans_gouvernance_est_un_403(monkeypatch):
    monkeypatch.setattr(dsn, "make_store", lambda sub: _Boom(NamespaceForbidden("v")))
    code, corps = _call("me.datastore.delete_namespace", path_params={"namespace": "v"})
    assert (code, corps["error"]) == (403, "forbidden")


def test_un_tableau_absent_garde_son_404_qui_dit_ou_il_vit(monkeypatch):
    """L'indice cross-org du signal #316 traverse la migration : c'est lui qui évite
    de lire « namespace_not_found » comme « il n'existe pas »."""
    monkeypatch.setattr(dsn, "make_store", lambda sub: _Boom(NamespaceNotFound("v")))
    from oto_mcp.capabilities import datastore_common as dc
    monkeypatch.setattr(dc.org_store, "list_orgs_for_user",
                        lambda sub: [{"org_id": 81, "name": "Mūcho"}])
    monkeypatch.setattr(dc.db, "list_datastore_namespaces_for_owners",
                        lambda owners: [{"namespace": "v", "owner_type": "org",
                                         "owner_id": "81"}])
    code, corps = _call("me.datastore.delete_namespace", path_params={"namespace": "v"})
    assert (code, corps["error"]) == (404, "namespace_not_found")
    assert "X-Oto-Org: 81" in corps["detail"] and "Mūcho" in corps["detail"]


# --- renommer -------------------------------------------------------------------

def test_le_renommage_exige_un_nom(store):
    code, corps = _call("me.datastore.rename_namespace",
                        path_params={"namespace": "v"}, body={"name": " "})
    assert (code, corps["error"]) == (400, "name_required")


def test_le_renommage_passe_par_la_gouvernance_pas_par_un_role(store, monkeypatch):
    """ADR 0030 : `can_govern` (owner ∪ escalade), jamais « est admin de l'org »."""
    vus: list = []
    store.v["resolve_ns_id"] = 42
    monkeypatch.setattr("oto_mcp.capabilities.datastore_common.make_store",
                        lambda sub: store)
    monkeypatch.setattr("oto_mcp.capabilities.datastore_common.ownership.can_govern",
                        lambda sub, rt, rid: vus.append((sub, rt, rid)) or False)
    code, corps = _call("me.datastore.rename_namespace",
                        path_params={"namespace": "v"}, body={"name": "neuf"})
    assert (code, corps["error"]) == (403, "forbidden")
    assert vus == [("u-1", "datastore_namespace", "42")]


def test_le_renommage_rend_le_nouveau_nom(store, monkeypatch):
    store.v["resolve_ns_id"] = 42
    monkeypatch.setattr("oto_mcp.capabilities.datastore_common.make_store",
                        lambda sub: store)
    monkeypatch.setattr("oto_mcp.capabilities.datastore_common.ownership.can_govern",
                        lambda *a: True)
    monkeypatch.setattr(dsn.db, "rename_datastore_namespace_by_id", lambda i, n: None)
    assert _call("me.datastore.rename_namespace", path_params={"namespace": "v"},
                 body={"name": "neuf"}) == (200, {"ok": True, "namespace": "neuf"})


def test_une_collision_de_nom_rend_409_avec_le_message_du_store(store, monkeypatch):
    store.v["resolve_ns_id"] = 42
    monkeypatch.setattr("oto_mcp.capabilities.datastore_common.make_store",
                        lambda sub: store)
    monkeypatch.setattr("oto_mcp.capabilities.datastore_common.ownership.can_govern",
                        lambda *a: True)

    def _boom(i, n):
        raise ValueError("namespace 'neuf' already exists")
    monkeypatch.setattr(dsn.db, "rename_datastore_namespace_by_id", _boom)
    code, corps = _call("me.datastore.rename_namespace", path_params={"namespace": "v"},
                        body={"name": "neuf"})
    assert code == 409 and "already exists" in corps["error"]


# --- deep-link ------------------------------------------------------------------

def test_lurl_rend_le_deep_link(store):
    store.v["get_url"] = "https://dashboard.oto.ninja/data/42"
    assert _call("me.datastore.url", path_params={"namespace": "v"}) == (
        200, {"url": "https://dashboard.oto.ninja/data/42"})


# --- le seul écart, et il est voulu ---------------------------------------------

def test_un_champ_inconnu_est_desormais_refuse(store):
    """Ces routes l'ignoraient. La couche capacité le NOMME — c'est le gain du lot,
    et le seul changement observable pour un client déjà correct."""
    code, corps = _call("me.datastore.create_namespace",
                        body={"namespace": "v", "owner_type": "org"})
    assert (code, corps["error"]) == (400, "unknown_fields")
    assert "owner_type" in corps["detail"] and "owner" in corps["detail"]


def test_les_cinq_capacites_declarent_leur_sortie():
    """La moitié de la valeur du lot : sans `Output`, `/openapi.json` ne dit rien de
    la réponse, et un intégrateur doit la deviner avec un compte et de la donnée."""
    cles = [c.key for c in CAPABILITIES if c.key.startswith("me.datastore.")
            and c.key.endswith(("_namespace", "_namespaces", "url"))]
    assert len(cles) >= 5
    for cle in cles:
        cap = _cap(cle)
        assert cap.Output is not None, cle
        assert cap.mcp is None, f"{cle} : ce lot ne migre que la face REST"
