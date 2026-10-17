"""Les routes REST des opérations d'un « Custom API » — par la VRAIE chaîne de
l'adaptateur (`_datastore_rest.call`) : query, corps, refus de champ inconnu, autz,
handler.

Même partage de droits que la clé de l'org (`orgs/secrets.py`) : un membre LIT, seul
un administrateur de l'org ÉCRIT ou IMPORTE. Sans compte `http` pour ce nom : 404.
"""
from __future__ import annotations

import pytest

from _datastore_rest import call

from oto_mcp import credentials_store, egress, roles
from oto_mcp.capabilities import _authz
from oto_mcp.db import connector_instances as ci

ORG = 35
OP = {"name": "list_teams", "description": "List the teams.", "method": "GET",
      "path": "/teams", "input": {"type": "object", "properties": {}}}


@pytest.fixture()
def org(monkeypatch):
    """L'org #35 a un compte `http` nommé `paie` ; u-admin l'administre, u-membre
    n'en est que membre, u-dehors n'en est pas."""
    etat = {"config": {"operations": [OP]}, "ecrit": []}
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: "member")
    # Le refus d'admin nomme les administrateurs (`detenteurs`) : une lecture en base.
    monkeypatch.setattr(_authz.detenteurs, "admins_de_l_org", lambda sub, o: None)
    monkeypatch.setattr(roles, "is_org_member",
                        lambda sub, o: sub in ("u-admin", "u-membre") and o == ORG)
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, o: sub == "u-admin" and o == ORG)
    monkeypatch.setattr(credentials_store, "list_accounts",
                        lambda et, eid, con: [{"account": "paie"}]
                        if (et, eid, con) == ("org", str(ORG), "http") else [])
    monkeypatch.setattr(ci, "live_instance_config",
                        lambda ot, oid, con, acc="": {"id": 4, "config": etat["config"]}
                        if acc == "paie" else None)

    def _set(iid, key, value):
        etat["ecrit"].append((iid, key, value))
        return True
    monkeypatch.setattr(ci, "set_instance_config_key", _set)
    return etat


def _get(sub, account=b"paie"):
    return call("connectors.http_operations.get", path_params={"id": str(ORG)},
                query=b"account=" + account, sub=sub)


def _put(sub, ops):
    return call("connectors.http_operations.set", path_params={"id": str(ORG)},
                query=b"account=paie", body={"operations": ops}, sub=sub)


def _import(sub, body):
    return call("connectors.http_operations.import", path_params={"id": str(ORG)},
                query=b"account=paie", body=body, sub=sub)


def test_un_membre_lit(org):
    code, out = _get("u-membre")
    assert code == 200 and out == {"account": "paie", "operations": [OP]}


def test_hors_de_l_org_refuse(org):
    assert _get("u-dehors")[0] == 403


def test_compte_inconnu_404(org):
    code, out = _get("u-membre", account=b"autre")
    assert code == 404 and out["error"] == "http_credential_not_found"


def test_seul_l_admin_ecrit(org):
    code, out = _put("u-membre", [OP])
    assert code == 403 and org["ecrit"] == []
    code, out = _put("u-admin", [OP])
    assert code == 200 and out == {"account": "paie", "operations": [OP]}
    assert org["ecrit"] == [(4, "operations", [OP])]


def test_liste_invalide_400_avec_chaque_probleme(org):
    code, out = _put("u-admin", [OP, {**OP, "path": "/a/../b"}])
    assert code == 400 and out["error"] == "invalid_operations"
    errs = out["details"]["errors"]
    assert any("duplicate name" in e for e in errs) and any("`..`" in e for e in errs)
    assert org["ecrit"] == []


def test_import_inline_rend_des_candidats_sans_ecrire(org):
    doc = {"openapi": "3.0.0", "paths": {
        "/teams": {"get": {"operationId": "listTeams"}, "head": {}}}}
    code, out = _import("u-admin", {"source": "inline", "document": doc})
    assert code == 200
    assert [o["name"] for o in out["operations"]] == ["list_teams"]
    assert out["skipped"] == [{"path": "/teams", "method": "HEAD",
                               "reason": "method HEAD is not supported"}]
    assert org["ecrit"] == []


def test_import_reserve_a_l_admin(org):
    assert _import("u-membre", {"source": "inline", "document": {}})[0] == 403


def test_import_url_interne_refuse(org, monkeypatch):
    monkeypatch.setattr(egress, "resolved_addresses", lambda h, p: {"10.0.0.5"})
    code, out = _import("u-admin", {"source": "url", "url": "https://docs.acme.test/o.json"})
    assert code == 400 and out["error"] == "import_failed"


def test_import_document_non_openapi_refuse(org):
    code, out = _import("u-admin", {"source": "inline", "document": {"hello": 1}})
    assert code == 400 and out["error"] == "import_failed"


def test_import_doc_path_lu_avec_l_auth_du_compte(org, monkeypatch):
    """La route de doc de la carte, lue par le MÊME client que les appels (auth,
    egress, redirections refusées), bornée en taille."""
    import json
    from unittest.mock import patch
    champs = {"base_url": "https://payroll.example.test/v1", "auth_mode": "bearer",
              "token": "T", "doc_path": "/openapi.json"}
    monkeypatch.setattr(credentials_store, "get_credential_with_meta",
                        lambda et, eid, con, acc="": {
                            "secret": credentials_store.pack_secret("http", champs)})
    monkeypatch.setattr(egress, "resolved_addresses", lambda h, p: {"93.184.216.34"})
    doc = {"openapi": "3.0.0", "paths": {"/teams": {"get": {"operationId": "listTeams"}}}}
    with patch("oto.tools.http.HttpConnectorClient") as cls:
        cls.return_value.get_raw.return_value = (json.dumps(doc).encode(), "application/json")
        code, out = _import("u-admin", {"source": "doc_path"})
    assert code == 200 and [o["name"] for o in out["operations"]] == ["list_teams"]
    assert cls.call_args.args[:2] == ("https://payroll.example.test/v1", "bearer")
    path, = cls.return_value.get_raw.call_args.args
    assert path == "/openapi.json"
    assert cls.return_value.get_raw.call_args.kwargs["max_bytes"] == 2 * 1024 * 1024
