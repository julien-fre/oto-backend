"""Le descriptif OpenAPI est DÉRIVÉ — il décrit le serveur, pas une intention.

On ne teste pas une liste de chemins figée (elle mentirait au premier ajout), mais
les propriétés qui rendent le document utilisable par un intégrateur : les capacités
y sont, le verbe caché dans `op` est visible, l'admin n'y est pas, et le document
reste un OpenAPI valide de forme.
"""
from __future__ import annotations

import json

from oto_mcp import openapi
from oto_mcp.capabilities import registry


class _FakeRoute:
    def __init__(self, path, methods):
        self.path = path
        self.methods = set(methods)


def test_document_shape():
    doc = openapi.build()
    assert doc["openapi"].startswith("3.1")
    assert doc["info"]["title"]
    assert doc["paths"], "aucun chemin dérivé du registre de capacités"
    assert "bearerAuth" in doc["components"]["securitySchemes"]
    json.dumps(doc)                      # sérialisable tel quel (servi en JSON)


def test_capabilities_are_described():
    doc = openapi.build()
    expected = {
        b.path for cap in registry.CAPABILITIES if cap.is_exposed()
        for b in cap.rest_bindings() if not b.path.startswith("/api/admin/")
    }
    missing = {p for p in expected if openapi._openapi_path(p) not in doc["paths"]}
    assert not missing, f"capacités REST absentes du descriptif : {sorted(missing)}"


def test_consolidated_verb_is_visible_in_the_schema():
    """ADR 0047 : le verbe vit dans le corps. Un intégrateur doit pouvoir LIRE les
    `op` possibles — c'est précisément ce que le sondage de chemins ne donne pas."""
    op = openapi.build()["paths"]["/api/me/projects"]["post"]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    ops = schema["properties"]["op"]["enum"]
    assert {"list", "get", "create", "runs"} <= set(ops)


def test_path_params_are_lifted_out_of_the_body():
    doc = openapi.build()
    item = doc["paths"].get("/api/me/guides/{scope}/{slug}")
    assert item, "capacité à paramètres de chemin absente"
    names = {p["name"] for p in item["get"]["parameters"] if p["in"] == "path"}
    assert names == {"scope", "slug"}


def test_get_capabilities_document_query_params():
    """Un GET de capacité lit sa query string : les champs doivent s'y retrouver."""
    params = openapi.build()["paths"]["/api/me/search"]["get"]["parameters"]
    assert {p["name"] for p in params if p["in"] == "query"}


def test_admin_surface_is_not_published():
    doc = openapi.build([_FakeRoute("/api/admin/platform-keys", ["GET"])])
    assert not [p for p in doc["paths"] if p.startswith("/api/admin/")]


def test_handwritten_routes_are_listed_without_schema():
    # ⚠️ Cet exemple a rouillé DEUX FOIS : `…/datastore/…/rows` d'abord (passé en
    # capacité, #302), puis `/api/me/tokens` (passé en capacité le 2026-08-27). Chaque
    # fois, l'exemple s'était mis à prouver le contraire de ce qu'il énonce.
    #
    # Le choix est donc désormais un chemin qui ne PEUT PAS devenir une capacité :
    # `/api/upload/{token}` n'a pas de JWT (le jeton scellé de l'URL fait foi) et reçoit
    # un corps BRUT ou un multipart — or l'adaptateur authentifie toujours et attend du
    # JSON. Il est classé NATURE, et le restera. Un exemple choisi pour ce qu'il est,
    # plutôt que pour ce qu'il n'a pas encore été.
    doc = openapi.build([
        _FakeRoute("/api/upload/{token}", ["GET", "POST", "OPTIONS"]),
    ])
    item = doc["paths"]["/api/upload/{token}"]
    assert set(item) == {"get", "post"}          # OPTIONS n'est pas une opération
    assert "requestBody" not in item["post"]     # forme non dérivable, dit comme tel
    assert item["get"]["tags"] == ["_legacy"]


def test_capability_wins_over_handwritten_on_the_same_path():
    """Une route encore montée à la main ET déclarée en capacité : c'est la capacité
    (avec son schéma) qui doit décrire le chemin, pas la coquille vide."""
    doc = openapi.build([_FakeRoute("/api/me/projects", ["POST"])])
    assert "requestBody" in doc["paths"]["/api/me/projects"]["post"]


def test_starlette_converters_are_stripped():
    doc = openapi.build([_FakeRoute("/api/me/projects/{project_id:int}/files", ["GET"])])
    assert "/api/me/projects/{project_id}/files" in doc["paths"]
