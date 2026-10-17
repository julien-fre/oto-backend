"""Un document OpenAPI 3 / Swagger 2 → opérations CANDIDATES (`custom_ops_openapi`).

Le cas fondateur : un outil de paie SaaS dont l'API publique s'authentifie par une
clé bearer et liste les collaborateurs d'une organisation, paginés. Ce qui ne
s'exprime pas dans le format est ÉCARTÉ avec sa raison, jamais approché.
"""
from __future__ import annotations

import pytest

from oto_mcp import custom_ops, custom_ops_openapi as oa

# Une API de paie fictive, de la forme de celle qui a motivé le connecteur.
PAIE = {
    "openapi": "3.0.3",
    "info": {"title": "Payroll public API", "version": "1"},
    "servers": [{"url": "https://payroll.example.test/api/public/v1"}],
    "components": {
        "parameters": {
            "Page": {"name": "page", "in": "query", "schema": {"type": "integer"},
                     "description": "Page number"},
        },
        "schemas": {
            "Absence": {"type": "object", "required": ["start"],
                        "properties": {"start": {"type": "string", "format": "date"},
                                       "kind": {"type": "string",
                                                "enum": ["paid", "sick"]}}},
        },
    },
    "paths": {
        "/organizations/{organizationId}/collaborators": {
            "parameters": [{"name": "organizationId", "in": "path", "required": True,
                            "schema": {"type": "string"}}],
            "get": {
                "operationId": "listCollaborators",
                "summary": "List the collaborators of an organization",
                "parameters": [{"$ref": "#/components/parameters/Page"},
                               {"name": "limit", "in": "query",
                                "schema": {"type": "integer", "maximum": 100}}],
            },
            "post": {
                "operationId": "createAbsence",
                "requestBody": {"content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Absence"}}}},
            },
            "options": {"operationId": "preflight"},
        },
        "/exports": {
            "get": {"parameters": [{"name": "X-Tenant", "in": "header",
                                    "required": True, "schema": {"type": "string"}}]},
            "post": {"operationId": "uploadExport", "requestBody": {"content": {
                "multipart/form-data": {"schema": {"type": "object"}}}}},
        },
    },
}


def test_l_operation_de_liste_paginee_est_convertie():
    cands, skipped = oa.from_openapi(PAIE)
    op = next(c for c in cands if c["name"] == "list_collaborators")
    assert op["method"] == "GET"
    assert op["path"] == "/organizations/{organizationId}/collaborators"
    assert op["description"] == "List the collaborators of an organization"
    assert op["input"]["required"] == ["organizationId"]
    assert op["input"]["properties"]["page"] == {"type": "integer",
                                                 "description": "Page number"}
    assert op["input"]["properties"]["limit"] == {"type": "integer"}
    # Utilisable tel quel : le plan d'appel route chemin et query.
    plan = custom_ops.plan_call(op, {"organizationId": "o1", "page": 2, "limit": 50})
    assert plan["path"] == "/organizations/o1/collaborators"
    assert plan["query"] == {"page": 2, "limit": 50}


def test_le_corps_json_devient_des_arguments_body():
    cands, _ = oa.from_openapi(PAIE)
    op = next(c for c in cands if c["name"] == "create_absence")
    assert op["description"] == "POST /organizations/{organizationId}/collaborators"
    assert set(op["input"]["required"]) == {"organizationId", "start"}
    assert op["input"]["properties"]["kind"]["enum"] == ["paid", "sick"]
    plan = custom_ops.plan_call(op, {"organizationId": "o1", "start": "2026-10-01"})
    assert plan["body"] == {"start": "2026-10-01"} and plan["query"] == {}


def test_ce_qui_ne_s_exprime_pas_est_ecarte_avec_sa_raison():
    _, skipped = oa.from_openapi(PAIE)
    raisons = {(s["method"], s["path"]): s["reason"] for s in skipped}
    assert "not supported" in raisons[("OPTIONS",
                                       "/organizations/{organizationId}/collaborators")]
    assert "header parameter `X-Tenant`" in raisons[("GET", "/exports")]
    assert "only application/json" in raisons[("POST", "/exports")]


def test_les_candidats_passent_la_validation_du_format():
    cands, _ = oa.from_openapi(PAIE)
    assert custom_ops.validate_operations(cands) == cands


def test_swagger_2_et_noms_dedoublonnes():
    doc = {"swagger": "2.0", "paths": {
        "/users/{id}": {"parameters": [
            {"name": "id", "in": "path", "required": True, "type": "string"}],
            "get": {"operationId": "getUser"}, "delete": {"operationId": "getUser"}},
        "/users": {"post": {"parameters": [{"name": "body", "in": "body", "schema": {
            "type": "object", "properties": {"email": {"type": "string"}},
            "required": ["email"]}}]}},
    }}
    cands, skipped = oa.from_openapi(doc)
    names = [c["name"] for c in cands]
    assert names == ["get_user", "get_user_2", "post_users"]
    assert cands[2]["input"]["required"] == ["email"]
    assert not skipped


@pytest.mark.parametrize("doc", [{}, {"openapi": "2.0"}, [], "x"])
def test_un_document_qui_n_est_pas_openapi_est_refuse(doc):
    with pytest.raises(oa.NotAnOpenApiDocument):
        oa.from_openapi(doc)


def test_ref_cyclique_ne_boucle_pas():
    doc = {"openapi": "3.1.0", "components": {"schemas": {"A": {"$ref": "#/components/schemas/A"}}},
           "paths": {"/a": {"post": {"operationId": "a", "requestBody": {"content": {
               "application/json": {"schema": {"$ref": "#/components/schemas/A"}}}}}}}}
    cands, skipped = oa.from_openapi(doc)
    assert cands == [] and "not a JSON object" in skipped[0]["reason"]
