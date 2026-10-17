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

from oto_mcp import access, credentials_store, egress, links, roles, session_org
from oto_mcp.db import connector_instances as ci
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import http_calls, http_ops

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


RH_OP = {"name": "list_teams", "description": "List the teams.", "method": "GET",
         "path": "/teams", "input": {"type": "object", "properties": {}}}


@pytest.fixture()
def compte(monkeypatch):
    """L'org #35 a DEUX connecteurs personnalisés : `paie` (deux actions, un en-tête
    constant, une variable) et `rh` (une action). `_account` choisit ; sans lui, la
    résolution par défaut sert `paie`."""
    _publique(monkeypatch)
    configs = {
        "paie": {"label": "Acme Payroll", "description": "The payroll tool.",
                 "operations": list(OPS), "headers": {"X-Api-Version": "2"},
                 "variables": {"organizationId": {"value": "org_9",
                                                  "description": "our org"}}},
        "rh": {"operations": [RH_OP]},
    }
    etat = {"configs": configs, "ecrit": [], "compte": None}
    ids = {"paie": 9, "rh": 10}

    def _rc(account):
        acc = account or etat["compte"] or "paie"
        return SimpleNamespace(entity_type="org", entity_id="35", account=acc,
                               fields=FIELDS, provider="http")
    monkeypatch.setattr(http_ops, "current_user_sub_from_token", lambda: "u-1")
    monkeypatch.setattr(session_org, "current_call_account", lambda: etat["compte"])
    monkeypatch.setattr(access, "resolve_credential",
                        lambda p, want="auto", account=None, **k: _rc(account))
    monkeypatch.setattr(access, "current_org", lambda sub: 35)
    monkeypatch.setattr(credentials_store, "list_accounts",
                        lambda et, eid, con: [{"account": a, "meta": {}} for a in configs])

    def _configs(ot, oid, con, account=None):
        return [{"id": ids[a], "account": a, "config": c} for a, c in configs.items()
                if (ot, oid, con) == ("org", "35", "http") and account in (None, a)]
    monkeypatch.setattr(ci, "live_instance_configs", _configs)

    def _merge(iid, patch):
        etat["ecrit"].append((iid, patch))
        acc = next(a for a, i in ids.items() if i == iid)
        configs[acc] = {**configs[acc], **patch}
        return True
    monkeypatch.setattr(ci, "merge_instance_config", _merge)
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: True)
    return etat


def _tool(name):
    m = FastMCP("t")
    http_ops.register(m)
    http_calls.register(m)
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
    for nom in ("http_call", "http_request", "http_set_definition"):
        ann = _tool(nom).annotations
        assert ann is None or ann.readOnlyHint is not True, nom


# ── découvrir : sans `_account`, tous les connecteurs personnalisés de l'org ─────

def test_sans_account_la_liste_rend_chaque_connecteur_de_l_org(compte):
    out = _tool("http_operations").fn()
    assert [(c["account"], c["label"], c["actions"]) for c in out["connectors"]] == [
        ("paie", "Acme Payroll", ["list_collaborators", "create_absence"]),
        ("rh", "rh", ["list_teams"])]
    assert out["connectors"][0]["description"] == "The payroll tool."
    assert out["connectors"][0]["actions_count"] == 2


def test_avec_account_le_detail_d_un_connecteur(compte):
    compte["compte"] = "paie"
    out = _tool("http_operations").fn()
    assert out["account"] == "paie" and out["label"] == "Acme Payroll"
    assert out["operations"][0]["input"]["required"] == ["organizationId"]
    assert out["variables"]["organizationId"]["value"] == "org_9"


# ── la définition : configurée une fois ───────────────────────────────────────

def test_poser_ne_change_que_les_parties_donnees(compte):
    out = _tool("http_set_definition").fn(operations=OPS[:1],
                                          description="Payroll, v2")
    assert out["saved"] == ["description", "operations"]
    assert out["actions"] == ["list_collaborators"]
    _, patch_ = compte["ecrit"][0]
    assert set(patch_) == {"description", "operations"}
    assert compte["configs"]["paie"]["headers"] == {"X-Api-Version": "2"}


@pytest.mark.parametrize("kw, motif", [
    ({"operations": [OPS[0], OPS[0]]}, "duplicate name"),
    ({"headers": {"Authorization": "Bearer x"}}, "authenticates"),
    ({"headers": {"Proxy-Foo": "x"}}, "authenticates"),
    ({"logo_domain": "https://acme.test"}, "bare domain"),
    ({"variables": {"org": "x"}}, "{value, description}"),
])
def test_une_definition_invalide_nomme_les_problemes(compte, kw, motif):
    with pytest.raises(McpError, match=motif):
        _tool("http_set_definition").fn(**kw)
    assert compte["ecrit"] == []


def test_l_en_tete_de_la_cle_ne_devient_pas_constant(compte, monkeypatch):
    champs = {**FIELDS, "auth_mode": "header", "header_name": "X-Key"}
    monkeypatch.setattr(access, "resolve_credential", lambda p, want="auto", account=None, **k:
                        SimpleNamespace(entity_type="org", entity_id="35", account="paie",
                                        fields=champs, provider="http"))
    with pytest.raises(McpError, match="authenticates"):
        _tool("http_set_definition").fn(headers={"x-key": "abc"})


def test_poser_et_importer_exigent_l_admin_du_compte(monkeypatch, compte):
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: False)
    with pytest.raises(McpError, match="reserved"):
        _tool("http_set_definition").fn(operations=[])
    compte["compte"] = "paie"
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


# ── appeler une action par son nom ───────────────────────────────────────────

def test_dry_run_variable_et_compte_trouve_par_le_nom(compte):
    """Ni `_account` ni `organizationId` : l'action n'existe que sur `paie`, la
    variable enregistrée remplit l'argument."""
    with patch("oto_mcp.tools.http.HttpConnectorClient") as cls:
        out = _tool("http_call").fn(operation="list_collaborators", args={"page": 2},
                                    dry_run=True)
    assert out == {"dry_run": True, "account": "paie", "method": "GET",
                   "path": "/organizations/org_9/collaborators", "query": {"page": 2},
                   "body": None}
    cls.assert_not_called()


def test_une_action_d_un_autre_connecteur_se_resout_seule(compte):
    out = _tool("http_call").fn(operation="list_teams", dry_run=True)
    assert out["account"] == "rh" and out["path"] == "/teams"


def test_un_nom_partage_est_refuse_en_nommant_les_candidats(compte):
    compte["configs"]["rh"]["operations"] = [RH_OP, OPS[1]]
    with pytest.raises(McpError, match="several custom connectors.*paie.*rh"):
        _tool("http_call").fn(operation="create_absence", args={"start": "x"})


def test_l_appel_passe_par_le_client_avec_les_en_tetes_constants(compte):
    with patch("oto_mcp.tools.http.HttpConnectorClient") as cls:
        cls.return_value.request.return_value = {"id": "abs_1"}
        out = _tool("http_call").fn(operation="create_absence",
                                    args={"start": "2026-10-01"})
    assert out == {"id": "abs_1"}
    base, mode, fields = cls.call_args.args
    assert (base, mode) == (FIELDS["base_url"], "bearer")
    cls.return_value.request.assert_called_once_with(
        "POST", "/absences", params=None, json={"start": "2026-10-01"})


def test_une_traversee_est_refusee_avant_tout_appel(compte):
    with patch("oto_mcp.tools.http.HttpConnectorClient") as cls:
        with pytest.raises(McpError, match="single segment"):
            _tool("http_call").fn(operation="list_collaborators",
                                  args={"organizationId": "../admin"})
    cls.assert_not_called()


def test_operation_inconnue_nomme_les_disponibles(compte):
    compte["compte"] = "paie"
    with pytest.raises(McpError, match="list_collaborators, create_absence"):
        _tool("http_call").fn(operation="delete_everything")


def test_l_erreur_amont_est_traduite_comme_http_get(compte):
    import requests
    rep = requests.Response()
    rep.status_code, rep._content = 429, b"slow down"
    with patch("oto_mcp.tools.http.HttpConnectorClient") as cls:
        cls.return_value.request.side_effect = requests.HTTPError(response=rep)
        with pytest.raises(McpError, match="HTTP 429"):
            _tool("http_call").fn(operation="create_absence", args={"start": "x"})


# ── appel brut : tous les verbes ──────────────────────────────────────────────

@pytest.mark.parametrize("verbe", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_http_request_porte_chaque_verbe(compte, verbe):
    with patch("oto_mcp.tools.http.HttpConnectorClient") as cls:
        cls.return_value.request.return_value = {"ok": True}
        assert _tool("http_request").fn(method=verbe.lower(), path="/teams/t1",
                                        body={"a": 1}) == {"ok": True}
    cls.return_value.request.assert_called_once_with(verbe, "/teams/t1", params=None,
                                                     json={"a": 1})


@pytest.mark.parametrize("kw, motif", [
    ({"method": "TRACE", "path": "/a"}, "`method`"),
    ({"method": "GET", "path": "a"}, "starting with"),
    ({"method": "GET", "path": "/a/../b"}, "`..`"),
    ({"method": "GET", "path": "/a?key=1"}, "no `"),
])
def test_http_request_refuse_avant_tout_appel(compte, kw, motif):
    with patch("oto_mcp.tools.http.HttpConnectorClient") as cls:
        with pytest.raises(McpError, match=motif):
            _tool("http_request").fn(**kw)
    cls.assert_not_called()


def test_http_request_dry_run(compte):
    out = _tool("http_request").fn(method="patch", path="/x", params={"a": 1},
                                   dry_run=True)
    assert out == {"dry_run": True, "account": "paie", "method": "PATCH", "path": "/x",
                   "query": {"a": 1}, "body": None}


# ── les en-têtes constants, sur le VRAI client d'oto-core ─────────────────────

def test_les_en_tetes_constants_s_ajoutent_sans_toucher_l_auth():
    from oto.tools.http import HttpConnectorClient
    from oto_mcp.connectors import http_operations
    client = http_operations.with_constant_headers(
        HttpConnectorClient("https://api.acme.test", "bearer", {"token": "T"}),
        {"X-Api-Version": "2", "Authorization": "ignored"})
    session = client._ready()
    assert session.headers["X-Api-Version"] == "2"
    assert session.headers["Authorization"] == "Bearer T"


def test_un_nom_d_en_tete_avec_soulignement_est_un_nom_valide(monkeypatch, compte):
    """`api_key` est un jeton HTTP (RFC 9110) — une vraie API l'attend tel quel."""
    monkeypatch.setattr(links, "link_for", lambda kind, sub=None, **p: "https://x.test/c")
    out = _tool("http_setup_link").fn(label="Pets", auth_mode="header",
                                      header_name="api_key")
    assert out["prefilled"]["header_name"] == "api_key"
    assert _tool("http_set_definition").fn(headers={"x_source": "e2e"})["saved"] == \
        ["headers"]
