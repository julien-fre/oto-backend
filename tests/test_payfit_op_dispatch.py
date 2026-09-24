"""Connecteur PayFit — dispatch `op=`, forme des sorties, sonde.

Ce que ce fichier verrouille :
- la SURFACE (10 tools sur 3 modules) et le routage de chaque op vers la bonne
  méthode du client ;
- un argument requis manquant nommé, un argument non pertinent REFUSÉ — « fourni » se
  lit `is not None`, donc `limit=0` et `include_in_progress=False` comptent ;
- **aucune écriture n'est câblée** : éprouvé à part, op par op, dans
  `test_payfit_ecritures_non_cablees.py` ;
- la seule clé renommée (`absence_type` + `absence_category`), et le fait que tout le
  reste sort tel que l'amont l'envoie — le connecteur ne retire plus rien en dur ;
- les documents (PDF, export, virement) : leur VERROU est éprouvé à part, dans
  `test_payfit_documents_verrou.py` ;
- la sonde et la fabrique de client : une clé vide est refusée avant le client, un
  401/403 se classe `NonAutorise` sur `status_code`.

Toutes les valeurs sont factices.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError

K = "000000000000000000000a0a"
K2 = "000000000000000000000b0b"
MOIS = "202601"


@pytest.fixture
def client(monkeypatch):
    """Faux `PayfitClient` + clé résolue. `payfit_garde._client()` importe la classe À
    L'INTÉRIEUR de la fonction : patcher l'attribut du package suffit."""
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("pf-test", False))
    return inst


def _mcp():
    from fastmcp import FastMCP

    from oto_mcp.tools import payfit as P
    from oto_mcp.tools import payfit_paie as PP
    from oto_mcp.tools import payfit_social as PS

    m = FastMCP("t")
    P.register(m)
    PP.register(m)
    PS.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def test_the_surface_is_exactly_ten_tools(client):
    assert sorted(t.name for t in asyncio.run(_mcp().list_tools())) == [
        "payfit_absence", "payfit_collaborator", "payfit_company", "payfit_contract",
        "payfit_document", "payfit_insurance", "payfit_meal_voucher", "payfit_payroll",
        "payfit_payslip", "payfit_worked_time",
    ]


# --- routage des LECTURES ----------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,method", [
    ("payfit_company", {}, "get_company"),
    ("payfit_collaborator", {}, "list_collaborators"),
    ("payfit_collaborator", {"op": "get", "collaborator_id": K}, "get_collaborator"),
    ("payfit_contract", {}, "list_contracts"),
    ("payfit_contract", {"op": "get", "contract_id": K}, "get_contract"),
    ("payfit_absence", {}, "list_absences"),
    ("payfit_payslip", {"collaborator_id": K}, "list_payslips"),
    ("payfit_payroll", {"date": MOIS}, "get_payroll_status"),
    ("payfit_payroll", {"op": "accounting", "date": MOIS}, "list_accounting_entries"),
    ("payfit_worked_time", {"date": MOIS}, "list_worked_time"),
    ("payfit_meal_voucher", {"date": MOIS}, "list_meal_vouchers"),
    ("payfit_insurance", {}, "list_health_insurance_contracts"),
    ("payfit_insurance", {"kind": "provident"}, "list_provident_fund_contracts"),
    ("payfit_document", {}, "list_income_tax_documents"),
    ("payfit_document", {"op": "auto_enrolment"}, "list_auto_enrolment_documents"),
])
def test_read_ops_route_to_the_right_client_method(client, tool, kwargs, method):
    _tool(tool)(**kwargs)
    getattr(client, method).assert_called_once()


def test_every_default_op_is_a_read(client):
    """ADR 0047 §Amendement, règle 2 : le chemin paresseux ne doit jamais écrire."""
    ecritures = ("create_collaborator", "create_contract", "create_absence",
                 "cancel_absence", "set_health_insurance", "set_provident_fund",
                 "request_health_insurance_regularization")
    for tool, kwargs in [("payfit_company", {}), ("payfit_collaborator", {}),
                         ("payfit_contract", {}), ("payfit_absence", {}),
                         ("payfit_payslip", {"collaborator_id": K}),
                         ("payfit_payroll", {"date": MOIS}),
                         ("payfit_worked_time", {"date": MOIS}),
                         ("payfit_meal_voucher", {"date": MOIS}),
                         ("payfit_insurance", {}), ("payfit_document", {})]:
        _tool(tool)(**kwargs)
    appelees = {c[0] for c in client.method_calls}
    assert not appelees & set(ecritures), f"un défaut d'op écrit : {appelees}"


def test_list_defaults_are_sent(client):
    _tool("payfit_collaborator")()
    client.list_collaborators.assert_called_once_with(limit=50, cursor=None, email=None)
    _tool("payfit_contract")(fr=True, cursor="tok")
    client.list_contracts.assert_called_once_with(
        limit=50, cursor="tok", include_in_progress=None, fr=True)
    _tool("payfit_absence")(begin_date="2026-01-01", status=["all"], contract_id=K)
    client.list_absences.assert_called_once_with(
        limit=50, cursor=None, contract_id=K, status=["all"],
        begin_date="2026-01-01", end_date=None)
    _tool("payfit_worked_time")(date=MOIS, limit=10)
    client.list_worked_time.assert_called_once_with(MOIS, limit=10, cursor=None)


def test_get_contract_forwards_the_french_variant(client):
    _tool("payfit_contract")(op="get", contract_id=K, fr=True)
    client.get_contract.assert_called_once_with(K, fr=True)


def test_the_french_company_is_asked_for_explicitly(client):
    _tool("payfit_company")(fr=True)
    client.get_company.assert_called_once_with(fr=True)


# --- refus -------------------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,match", [
    ("payfit_collaborator", {"op": "get"}, "exige `collaborator_id`"),
    ("payfit_contract", {"op": "get"}, "exige `contract_id`"),
    ("payfit_payslip", {}, "exige `collaborator_id`"),
    ("payfit_payslip", {"op": "download", "collaborator_id": K}, "exige `contract_id`"),
    ("payfit_payroll", {}, "exige `date`"),
    ("payfit_document", {"op": "download"}, "exige `document_id`"),
])
def test_missing_required_argument_is_named(client, tool, kwargs, match):
    with pytest.raises(McpError, match=match):
        _tool(tool)(**kwargs)
    assert not client.method_calls


@pytest.mark.parametrize("tool,kwargs,match", [
    # Des valeurs « fausses » restent des valeurs fournies.
    ("payfit_collaborator", {"op": "get", "collaborator_id": K, "limit": 0}, "`limit`"),
    ("payfit_contract", {"op": "get", "contract_id": K, "include_in_progress": False},
     "`include_in_progress`"),
    ("payfit_collaborator", {"op": "get", "collaborator_id": K,
                             "email": "a@exemple.test"}, "`email`"),
    ("payfit_collaborator", {"collaborator_id": K}, "`collaborator_id`"),
    ("payfit_collaborator", {"first_name": "Ada"}, "`first_name`"),
    ("payfit_contract", {"contract_id": K}, "`contract_id`"),
    ("payfit_contract", {"op": "get", "contract_id": K, "fields": ["jobName"]},
     "`fields`"),
    ("payfit_payroll", {"date": MOIS, "fields": ["status"]}, "`fields`"),
    ("payfit_payslip", {"collaborator_id": K, "contract_id": K2}, "`contract_id`"),
    ("payfit_document", {"document_id": K}, "`document_id`"),
])
def test_an_argument_the_op_does_not_use_is_refused(client, tool, kwargs, match):
    with pytest.raises(McpError, match=f"n'utilise pas {match}"):
        _tool(tool)(**kwargs)
    assert not client.method_calls


@pytest.mark.parametrize("tool,kwargs", [
    ("payfit_collaborator", {"op": "delete"}),
    ("payfit_contract", {"op": "terminate"}),
    ("payfit_absence", {"op": "approve"}),
    ("payfit_payroll", {"op": "dsn", "date": MOIS}),
    ("payfit_payslip", {"op": "lines", "collaborator_id": K}),
    ("payfit_insurance", {"op": "unaffiliate", "contract_id": K,
                          "insurance_contract_ids": [K2]}),
    # ⚠️ L'op inconnue prime sur l'argument manquant : répondre « exige
    # collaborator_id » à `op="lines"` enverrait chercher un argument pour une
    # opération qui n'existe pas — l'agent fournirait l'argument et recommencerait.
    ("payfit_payslip", {"op": "lines"}),
    ("payfit_payroll", {"op": "dsn"}),
])
def test_an_op_the_api_does_not_have_is_refused(client, tool, kwargs):
    with pytest.raises(McpError, match="inconnu"):
        _tool(tool)(**kwargs)
    assert not client.method_calls


# --- forme des sorties -------------------------------------------------------------

def test_an_absence_renames_its_type_and_says_its_category(client):
    client.list_absences.return_value = {"meta": {"count": 2, "nextPageToken": "t2"},
                                         "absences": [
        {"id": "a1", "contractId": K, "type": "fr_conges_payes", "status": "approved"},
        {"id": "a2", "contractId": K, "type": "fr_maladie_ordinaire",
         "status": "approved"}]}
    out = _tool("payfit_absence")()
    assert out["count"] == 2 and out["next_cursor"] == "t2"
    lignes = out["absences"]
    # ⚠️ `type` ne doit exister NULLE PART : deux noms pour la même donnée, dont un
    # seul est visé par le filtre de champs, serait une passoire.
    assert all("type" not in ligne for ligne in lignes)
    assert lignes[0]["absence_type"] == "fr_conges_payes"
    assert lignes[0]["absence_category"] == "ordinary_leave"
    assert lignes[1]["absence_type"] == "fr_maladie_ordinaire"
    assert lignes[1]["absence_category"] == "restricted"


def test_an_unknown_absence_type_is_never_called_ordinary(client):
    """Un type ajouté demain chez PayFit ne doit pas devenir « congé ordinaire »
    parce que personne n'a mis la liste à jour."""
    client.list_absences.return_value = {"meta": {}, "absences": [
        {"id": "a3", "type": "fr_type_invente_demain"}]}
    assert _tool("payfit_absence")()["absences"][0]["absence_category"] == "restricted"


def test_nothing_is_withheld_from_a_collaborator_any_more(client):
    """Le connecteur ne RETIRE plus : ce qui protège est le filtre de champs, posé
    au-dessus (cf. `test_payfit_field_filter_defaults.py`)."""
    brut = {"id": "c1", "firstName": "Ada", "socialSecurityNumber": "1NIRFACTICE",
            "iban": "FR7630000000000000000000000", "bic": "FAKEFRPP",
            "birthDate": "1990-01-01", "nationality": "FR", "gender": "FEMALE",
            "addresses": [{"address": "1 rue Factice", "type": "main"}]}
    client.get_collaborator.return_value = brut
    out = _tool("payfit_collaborator")(op="get", collaborator_id=K)
    assert out["collaborator"] == brut
    assert "défaut serveur" in out["redaction"]


def test_fields_can_only_narrow_a_page(client):
    client.list_collaborators.return_value = {"meta": {}, "collaborators": [
        {"id": "c1", "firstName": "Ada", "teamName": "Bloc A"}]}
    out = _tool("payfit_collaborator")(fields=["teamName"])
    assert out["collaborators"] == [{"id": "c1", "teamName": "Bloc A"}]


def test_a_non_paginated_list_does_not_fake_a_cursor(client):
    client.list_accounting_entries.return_value = [
        {"accountId": "641000", "accountName": "Salaires", "debit": 1000, "credit": None}]
    out = _tool("payfit_payroll")(op="accounting", date=MOIS)
    assert out["count"] == 1 and "next_cursor" not in out


# --- erreurs amont, sonde, fabrique ------------------------------------------------

def test_an_upstream_error_is_classified_on_its_status_code(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.list_absences.side_effect = UpstreamHTTPError(403, {"error": "nope"},
                                                         service="payfit")
    with pytest.raises(McpError, match="HTTP 403"):
        _tool("payfit_absence")()


def test_an_empty_credential_never_falls_back_to_a_server_secret(monkeypatch):
    """Vide, `PayfitClient` résoudrait `PAYFIT_API_KEY` dans l'environnement du
    SERVEUR et travaillerait sur une AUTRE entreprise."""
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("   ", False))
    construit = []
    monkeypatch.setattr("oto.tools.payfit.PayfitClient",
                        lambda **kw: construit.append(kw))
    with pytest.raises(McpError, match="aucune clé API posée"):
        _tool("payfit_company")()
    assert not construit


def test_the_probe_refuses_an_empty_key():
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools import payfit_garde

    with pytest.raises(connector_verify.NonAutorise, match="vide"):
        payfit_garde.verify({"key": "  "})


@pytest.mark.parametrize("status", [401, 403])
def test_the_probe_classifies_a_refusal_on_the_status_code(monkeypatch, status):
    from oto.tools.common.errors import UpstreamHTTPError

    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools import payfit_garde

    faux = MagicMock()
    faux.get_company.side_effect = UpstreamHTTPError(status, {"error": "x"},
                                                     service="payfit")
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: faux)
    with pytest.raises(connector_verify.NonAutorise):
        payfit_garde.verify({"key": "pf-test"})


def test_a_server_error_is_not_swallowed_by_the_probe(monkeypatch):
    """Une panne amont n'est pas un refus d'autorisation : la classer `NonAutorise`
    ferait croire à une clé invalide."""
    from oto.tools.common.errors import UpstreamHTTPError

    from oto_mcp.tools import payfit_garde

    faux = MagicMock()
    faux.get_company.side_effect = UpstreamHTTPError(503, {}, service="payfit")
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: faux)
    with pytest.raises(UpstreamHTTPError):
        payfit_garde.verify({"key": "pf-test"})
