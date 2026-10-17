"""Les outils « Custom API » (`tools/http_ops.py`) : lien de connexion SANS secret,
opérations décrites, appel par nom.

Ce qu'on remplace : la résolution de la clé (`access.resolve_credential`), la ligne
d'instance (`connector_instances`), et la CLASSE client d'oto-core — jamais la règle
qui les lit. Le client réel n'est jamais construit ; l'egress est simulé par son
résolveur d'adresses.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from oto_mcp import access, egress, links, roles
from oto_mcp.db import connector_instances as ci
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import http_ops

OPS = [{"name": "list_collaborators", "description": "List the collaborators.",
        "method": "GET", "path": "/organizations/{organizationId}/collaborators",
        "input": {"type": "object",
                  "properties": {"organizationId": {"type": "string"},
                                 "page": {"type": "integer"}},
                  "required": ["organizationId"]}},
       {"name": "create_absence", "description": "Create an absence.", "method": "POST",
        "path": "/absences", "input": {"type": "object", "properties": {
            "start": {"type": "string"}}, "required": ["start"]}}]

FIELDS = {"base_url": "https://payroll.example.test/api/public/v1",
          "auth_mode": "bearer", "token": "TOKEN-NEVER-ECHOED"}


def _publique(monkeypatch):
    monkeypatch.setattr(egress, "resolved_addresses",
                        lambda hote, port: {"127.0.0.1"} if hote.startswith("127.")
                        or hote == "localhost" else {"93.184.216.34"})
    monkeypatch.delenv(egress.ALLOW_VAR, raising=False)


@pytest.fixture()
def compte(monkeypatch):
    """Un compte `http` d'org (#35) résolu pour l'appel, avec son instance."""
    _publique(monkeypatch)
    etat = {"config": {"operations": list(OPS)}, "ecrit": []}
    rc = SimpleNamespace(entity_type="org", entity_id="35", account="paie", fields=FIELDS,
                         provider="http")
    monkeypatch.setattr(http_ops, "current_user_sub_from_token", lambda: "u-1")
    monkeypatch.setattr(access, "resolve_credential", lambda p, want="auto", **k: rc)
    monkeypatch.setattr(access, "current_org", lambda sub: 35)
    monkeypatch.setattr(ci, "live_instance_config",
                        lambda ot, oid, con, acc="": {"id": 9, "config": etat["config"]}
                        if (ot, oid, con, acc) == ("org", "35", "http", "paie") else None)

    def _set(iid, key, value):
        etat["ecrit"].append((iid, key, value))
        etat["config"] = {**etat["config"], key: value}
        return True
    monkeypatch.setattr(ci, "set_instance_config_key", _set)
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: True)
    return etat


def _tool(name):
    m = FastMCP("t")
    http_ops.register(m)
    return asyncio.run(m.get_tool(name))


# ── le lien de connexion ──────────────────────────────────────────────────────

def test_le_lien_ne_peut_porter_aucun_secret():
    """Aucun paramètre ne peut recevoir une clé : la liste est FERMÉE."""
    params = set(inspect.signature(_tool("http_setup_link").fn).parameters)
    assert params == {"label", "base_url", "auth_mode", "header_name", "query_param",
                      "doc_path"}
    assert not params & {"token", "api_key", "key", "password", "client_secret", "secret"}


def test_le_lien_porte_les_seuls_champs_non_secrets(monkeypatch, compte):
    monkeypatch.setattr(links, "link_for",
                        lambda kind, sub=None, **p: "https://app.example.test/connectors")
    out = _tool("http_setup_link").fn(label="Acme Payroll",
                                      base_url="https://payroll.example.test/api/public/v1/",
                                      auth_mode="bearer")
    assert out["url"] == ("https://app.example.test/connectors/http?label=Acme+Payroll"
                          "&auth_mode=bearer&base_url=https%3A%2F%2Fpayroll.example.test"
                          "%2Fapi%2Fpublic%2Fv1")
    assert "never" in out["instructions"] and "revoke" in out["instructions"]


@pytest.mark.parametrize("kw, motif", [
    ({"base_url": "https://payroll.example.test/v1?api_key=abc"}, "API root only"),
    ({"base_url": "https://user:pw@payroll.example.test"}, "no user"),
    ({"base_url": "http://127.0.0.1:8080"}, "127.0.0.1"),
    ({"auth_mode": "magic"}, "`auth_mode`"),
    ({"header_name": "Bearer sk_live_123"}, "header NAME"),
    ({"query_param": "api_key=abc"}, "parameter NAME"),
    ({"doc_path": "openapi.json"}, "relative"),
    ({"label": " "}, "`label` is required"),
])
def test_le_lien_refuse_ce_qui_porterait_un_secret_ou_une_adresse_interne(
        monkeypatch, compte, kw, motif):
    args = {"label": "Acme", **kw}
    with pytest.raises(McpError, match=motif):
        _tool("http_setup_link").fn(**args)


def test_sans_page_connecteurs_pas_de_lien_mais_la_raison(monkeypatch, compte):
    monkeypatch.setattr(links, "link_for", lambda kind, sub=None, **p: None)
    monkeypatch.setattr(links, "raison_sans_lien", lambda kind, sub=None, **p: "no page")
    out = _tool("http_setup_link").fn(label="Acme")
    assert out["url"] is None and out["url_missing"] == "no page"


def test_annotations_lecture_et_ecriture():
    assert _tool("http_setup_link").annotations.readOnlyHint is True
    assert _tool("http_operations").annotations.readOnlyHint is True
    for nom in ("http_call", "http_set_operations"):
        ann = _tool(nom).annotations
        assert ann is None or ann.readOnlyHint is not True, nom


# ── opérations : lister, poser, importer ──────────────────────────────────────

def test_lister_les_operations_du_compte_resolu(compte):
    out = _tool("http_operations").fn()
    assert out["account"] == "paie"
    assert [o["name"] for o in out["operations"]] == ["list_collaborators", "create_absence"]
    assert out["operations"][0]["input"]["required"] == ["organizationId"]


def test_poser_valide_puis_remplace(compte):
    out = _tool("http_set_operations").fn(operations=OPS[:1])
    assert out == {"account": "paie", "saved": 1, "names": ["list_collaborators"]}
    assert compte["ecrit"][0][:2] == (9, "operations")


def test_poser_une_liste_invalide_nomme_les_problemes(compte):
    with pytest.raises(McpError, match="duplicate name"):
        _tool("http_set_operations").fn(operations=[OPS[0], OPS[0]])
    assert compte["ecrit"] == []


def test_poser_et_importer_exigent_l_admin_du_compte(monkeypatch, compte):
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: False)
    with pytest.raises(McpError, match="reserved"):
        _tool("http_set_operations").fn(operations=[])
    with pytest.raises(McpError, match="reserved"):
        _tool("http_operations").fn(op="import", source="inline", document={})
    assert _tool("http_operations").fn()["operations"]   # la lecture reste ouverte


def test_importer_un_document_inline_ne_sauve_rien(compte):
    doc = {"openapi": "3.0.0", "paths": {"/teams": {"get": {"operationId": "listTeams"}}}}
    out = _tool("http_operations").fn(op="import", source="inline", document=doc)
    assert [o["name"] for o in out["operations"]] == ["list_teams"]
    assert out["saved"] is False and compte["ecrit"] == []


def test_importer_une_url_interne_est_refuse(compte):
    with pytest.raises(McpError, match="127.0.0.1"):
        _tool("http_operations").fn(op="import", source="url",
                                    url="http://127.0.0.1/openapi.json")


# ── appel par nom ─────────────────────────────────────────────────────────────

def test_dry_run_rend_la_requete_sans_appeler(compte):
    with patch("oto.tools.http.HttpConnectorClient") as cls:
        out = _tool("http_call").fn(operation="list_collaborators",
                                    args={"organizationId": "o 1", "page": 2},
                                    dry_run=True)
    assert out == {"dry_run": True, "method": "GET",
                   "path": "/organizations/o%201/collaborators", "query": {"page": 2},
                   "body": None}
    cls.assert_not_called()


def test_l_appel_passe_par_le_client_du_connecteur(compte):
    with patch("oto.tools.http.HttpConnectorClient") as cls:
        cls.return_value.request.return_value = {"id": "abs_1"}
        out = _tool("http_call").fn(operation="create_absence",
                                    args={"start": "2026-10-01"})
    assert out == {"id": "abs_1"}
    base, mode, fields = cls.call_args.args
    assert (base, mode) == (FIELDS["base_url"], "bearer")
    cls.return_value.request.assert_called_once_with(
        "POST", "/absences", params=None, json={"start": "2026-10-01"})


def test_une_traversee_est_refusee_avant_tout_appel(compte):
    with patch("oto.tools.http.HttpConnectorClient") as cls:
        with pytest.raises(McpError, match="single segment"):
            _tool("http_call").fn(operation="list_collaborators",
                                  args={"organizationId": "../admin"})
    cls.assert_not_called()


def test_operation_inconnue_nomme_les_disponibles(compte):
    with pytest.raises(McpError, match="list_collaborators, create_absence"):
        _tool("http_call").fn(operation="delete_everything")


def test_l_erreur_amont_est_traduite_comme_http_get(compte):
    import requests
    rep = requests.Response()
    rep.status_code, rep._content = 429, b"slow down"
    with patch("oto.tools.http.HttpConnectorClient") as cls:
        cls.return_value.request.side_effect = requests.HTTPError(response=rep)
        with pytest.raises(McpError, match="HTTP 429"):
            _tool("http_call").fn(operation="create_absence", args={"start": "x"})
