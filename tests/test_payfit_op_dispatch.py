"""Connecteur PayFit — dispatch `op=`, projection des données personnelles, sonde.

Ce que ce fichier verrouille :
- la SURFACE (4 tools, lecture seule) et le routage de chaque op vers la bonne
  méthode du client ;
- un argument requis manquant nommé, un argument non pertinent REFUSÉ — « fourni » se
  lit `is not None`, donc `limit=0` et `include_in_progress=False` comptent ;
- la projection en liste blanche : un collaborateur, un contrat, une absence ne rendent
  ni NIR, ni IBAN, ni naissance, ni adresse, ni e-mail personnel, ni motif médical,
  même quand l'API les envoie — et `fields=["*"]` n'y change rien ;
- la sonde et la fabrique de client : une clé vide est refusée avant le client, un
  401/403 se classe `NonAutorise` sur `status_code`.

Toutes les valeurs sont factices.
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError

K = "000000000000000000000a0a"
SENTINEL = "SENTINELLE-PAIE"


@pytest.fixture
def client(monkeypatch):
    """Faux `PayfitClient` + clé résolue. `register()` importe la classe À
    L'INTÉRIEUR de la fonction : patcher l'attribut du package suffit."""
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("pf-test", False))
    return inst


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import payfit as P

    m = FastMCP("t")
    P.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def test_the_surface_is_exactly_four_read_tools(client):
    assert sorted(t.name for t in asyncio.run(_mcp().list_tools())) == [
        "payfit_absence", "payfit_collaborator", "payfit_company", "payfit_contract",
    ]


# --- routage -----------------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,method", [
    ("payfit_company", {}, "get_company"),
    ("payfit_collaborator", {}, "list_collaborators"),
    ("payfit_collaborator", {"op": "get", "collaborator_id": K}, "get_collaborator"),
    ("payfit_contract", {}, "list_contracts"),
    ("payfit_contract", {"op": "get", "contract_id": K}, "get_contract"),
    ("payfit_absence", {}, "list_absences"),
])
def test_ops_route_to_the_right_client_method(client, tool, kwargs, method):
    _tool(tool)(**kwargs)
    getattr(client, method).assert_called_once()


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


def test_get_contract_forwards_the_french_variant(client):
    _tool("payfit_contract")(op="get", contract_id=K, fr=True)
    client.get_contract.assert_called_once_with(K, fr=True)


# --- refus -------------------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,match", [
    ("payfit_collaborator", {"op": "get"}, "exige `collaborator_id`"),
    ("payfit_contract", {"op": "get"}, "exige `contract_id`"),
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
    ("payfit_collaborator", {"op": "get", "collaborator_id": K, "email": "a@exemple.test"},
     "`email`"),
    ("payfit_collaborator", {"collaborator_id": K}, "`collaborator_id`"),
    ("payfit_contract", {"contract_id": K}, "`contract_id`"),
    ("payfit_contract", {"op": "get", "contract_id": K, "fields": ["jobName"]}, "`fields`"),
])
def test_an_argument_the_op_does_not_use_is_refused(client, tool, kwargs, match):
    with pytest.raises(McpError, match=f"n'utilise pas {match}"):
        _tool(tool)(**kwargs)
    assert not client.method_calls


def test_unknown_op_is_refused(client):
    with pytest.raises(McpError, match="op doit être"):
        _tool("payfit_collaborator")(op="delete", collaborator_id=K)


# --- projection des données personnelles -------------------------------------------

_PERSONAL_KEYS = {"socialSecurityNumber", "temporaryTechnicalNumber", "iban", "bic",
                  "birthDate", "birthName", "countryOfBirth", "nationality", "gender",
                  "addresses", "phoneNumbers", "matricule", "numeroSecuriteSociale",
                  "numeroTechniqueTemporaire", "motifRuptureDeContratDsn",
                  "healthInsuranceContractIds", "providentFundContractIds",
                  "standardWeeklyHours", "fullTimeEquivalent", "isFullTime",
                  "workingTimeModality", "contactEmail", "postalCode", "city", "address",
                  "probationEndDate", "firstName_deprecated"}


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _assert_clean(out, allowed=frozenset()):
    assert SENTINEL not in json.dumps(out)
    assert not (_PERSONAL_KEYS - allowed) & set(_keys(out))


def _raw_collaborator():
    return {
        "id": K, "firstName": "Prénom-Test", "lastName": "Nom-Test",
        "matricule": SENTINEL, "birthName": SENTINEL, "birthDate": "1900-01-01",
        "gender": "other", "nationality": SENTINEL, "countryOfBirth": SENTINEL,
        "socialSecurityNumber": SENTINEL, "temporaryTechnicalNumber": SENTINEL,
        "iban": SENTINEL, "bic": SENTINEL,
        "emails": [{"email": "pro@exemple.test", "type": "professional"},
                   {"email": SENTINEL, "type": "personal"},
                   {"email": SENTINEL, "type": "unknown"}],
        "phoneNumbers": [{"phoneNumber": SENTINEL, "type": "personal"}],
        "addresses": [{"address": SENTINEL, "postcode": SENTINEL, "city": SENTINEL,
                       "country": SENTINEL, "type": "personal"}],
        "managerId": K, "teamName": "Équipe-Test", "terminationDate": None,
        "contracts": [{"id": K, "startDate": "2020-01-01", "endDate": None,
                       "status": "ACTIVE", "salary": SENTINEL}],
        "field_added_tomorrow": SENTINEL,
    }


def test_collaborator_list_withholds_personal_data(client):
    client.list_collaborators.return_value = {
        "collaborators": [_raw_collaborator()],
        "meta": {"nextPageToken": "tok-2", "count": 1}}
    out = _tool("payfit_collaborator")()
    _assert_clean(out)
    row = out["collaborators"][0]
    assert row == {"id": K, "firstName": "Prénom-Test", "lastName": "Nom-Test",
                   "managerId": K, "teamName": "Équipe-Test", "terminationDate": None,
                   "emails": ["pro@exemple.test"],
                   "contracts": [{"id": K, "startDate": "2020-01-01", "endDate": None,
                                  "status": "ACTIVE"}]}
    assert out["next_cursor"] == "tok-2" and out["count"] == 1 and "withheld" in out


def test_collaborator_get_withholds_personal_data(client):
    client.get_collaborator.return_value = _raw_collaborator()
    out = _tool("payfit_collaborator")(op="get", collaborator_id=K)
    _assert_clean(out)
    assert out["collaborator"]["emails"] == ["pro@exemple.test"]


def _raw_contract_fr():
    return {
        "contractId": K, "companyId": K, "collaboratorId": K, "jobName": "Poste-Test",
        "status": "ACTIVE", "startDate": "2020-01-01", "endDate": None,
        "probationEndDate": SENTINEL, "standardWeeklyHours": 35,
        "fullTimeEquivalent": 1, "isFullTime": True, "workingTimeModality": "standard",
        "firstName": SENTINEL, "lastName": SENTINEL, "birthName": SENTINEL,
        "birthDate": "1900-01-01", "contactEmail": SENTINEL, "address": SENTINEL,
        "city": SENTINEL, "postalCode": SENTINEL,
        "natureContratDsn": "01", "statutConventionnelDsn": "04", "idcc": "0000",
        "motifRuptureDeContratDsn": "014", "estCadreDirigeant": False,
        "healthInsuranceContractIds": [SENTINEL], "providentFundContractIds": [SENTINEL],
        "numeroSecuriteSociale": SENTINEL, "numeroTechniqueTemporaire": SENTINEL,
    }


@pytest.mark.parametrize("fr", [True, None])
def test_contract_withholds_personal_data(client, fr):
    client.get_contract.return_value = _raw_contract_fr()
    out = _tool("payfit_contract")(op="get", contract_id=K, fr=fr)
    _assert_clean(out)
    base = {"contractId": K, "companyId": K, "collaboratorId": K, "jobName": "Poste-Test",
            "status": "ACTIVE", "startDate": "2020-01-01", "endDate": None}
    if fr:
        base.update(natureContratDsn="01", statutConventionnelDsn="04", idcc="0000")
    assert out["contract"] == base


@pytest.mark.parametrize("raw,served", [
    ("fr_conges_payes", "fr_conges_payes"),
    ("fr_rtt", "fr_rtt"),
    ("uk_annual_leave", "uk_annual_leave"),
    ("fr_maladie_ordinaire", "absence"),
    ("fr_accident_travail", "absence"),
    ("fr_maladie_professionnelle", "absence"),
    ("fr_temps_partiel_therapeutique", "absence"),
    ("fr_maternite", "absence"),
    ("fr_pathologique", "absence"),
    ("fr_enfant_malade", "absence"),
    ("fr_deces", "absence"),
    ("uk_sick_leave", "absence"),
    ("es_baja_medica", "absence"),
    ("es_visita_medica", "absence"),
    ("other", "absence"),
    ("type_ajoute_demain", "absence"),
])
def test_absence_type_is_served_only_for_ordinary_leave(client, raw, served):
    client.list_absences.return_value = {"absences": [{
        "id": K, "contractId": K, "type": raw, "status": "approved",
        "startDate": {"date": "2026-01-05", "moment": "beginning-of-day", "x": SENTINEL},
        "endDate": {"date": "2026-01-06", "moment": "end-of-day"},
        "comment": SENTINEL, "reason": SENTINEL,
    }], "meta": {"count": 1}}
    for fields in (None, ["*"]):
        out = _tool("payfit_absence")(fields=fields)
        assert SENTINEL not in json.dumps(out)
        assert out["absences"] == [{
            "id": K, "contractId": K, "status": "approved", "type": served,
            "startDate": {"date": "2026-01-05", "moment": "beginning-of-day"},
            "endDate": {"date": "2026-01-06", "moment": "end-of-day"}}]
        if served == "absence":
            assert raw not in json.dumps(out)


def test_company_is_whitelisted(client):
    client.get_company.return_value = {"id": K, "name": "Entreprise-Test", "country": "FR",
                                       "identificationNumber": "00000000000000",
                                       "nbActiveContracts": 3, "owner_iban": SENTINEL}
    out = _tool("payfit_company")()
    assert SENTINEL not in json.dumps(out)
    assert out["company"]["country"] == "FR"


# --- projection des listes (cliquet `test_sorties_listes_projetees`) ----------------

def test_fields_ne_garde_que_les_colonnes_demandees_et_l_id(client):
    client.list_contracts.return_value = {"contracts": [_raw_contract_fr()],
                                          "meta": {"count": 1}}
    out = _tool("payfit_contract")(fields=["jobName"])
    assert out["contracts"] == [{"contractId": K, "jobName": "Poste-Test"}]
    assert out["count"] == 1 and out["next_cursor"] is None


def test_fields_etoile_ou_nom_sensible_ne_rend_jamais_le_brut(client):
    client.list_collaborators.return_value = {"collaborators": [_raw_collaborator()],
                                              "meta": {}}
    for fields in (["*"], ["socialSecurityNumber", "iban", "birthDate", "addresses"]):
        out = _tool("payfit_collaborator")(fields=fields)
        _assert_clean(out)


def test_chaque_outil_de_liste_satisfait_le_cliquet_de_projection(client):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_sorties_listes_projetees import _pagine, _projette
    outils = asyncio.run(_mcp().list_tools())
    assert [t.name for t in outils if _pagine(t)] != []
    assert [t.name for t in outils if _pagine(t) and not _projette(t)] == []


# --- erreurs amont & clé -----------------------------------------------------------

def test_403_is_read_on_status_code_and_names_the_scopes(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.list_absences.side_effect = UpstreamHTTPError(403, {"error": "x"},
                                                         service="payfit")
    with pytest.raises(McpError, match="time:read"):
        _tool("payfit_absence")()


def test_client_value_error_becomes_invalid_params(client):
    client.get_collaborator.side_effect = ValueError("collaborator_id invalide")
    with pytest.raises(McpError, match="invalide"):
        _tool("payfit_collaborator")(op="get", collaborator_id="../x")


def test_an_empty_resolved_key_never_builds_a_client(monkeypatch):
    built = []
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: built.append(kw))
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("  ", False))
    with pytest.raises(McpError, match="aucune clé"):
        _tool("payfit_company")()
    assert not built


@pytest.mark.parametrize("fields", [{}, {"key": ""}, {"key": "   "}, {"key": None}])
def test_probe_refuses_an_empty_key_before_the_client(monkeypatch, fields):
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools import payfit as P

    built = []
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: built.append(kw))
    with pytest.raises(connector_verify.NonAutorise):
        P._verify(fields)
    assert not built


@pytest.mark.parametrize("status,expected", [(401, "NonAutorise"), (403, "NonAutorise"),
                                             (500, "UpstreamHTTPError")])
def test_probe_classifies_on_status_code(monkeypatch, status, expected):
    from oto.tools.common.errors import UpstreamHTTPError
    from oto_mcp.tools import payfit as P

    inst = MagicMock()
    inst.get_company.side_effect = UpstreamHTTPError(status, {"error": "x"},
                                                     service="payfit")
    monkeypatch.setattr("oto.tools.payfit.PayfitClient", lambda **kw: inst)
    with pytest.raises(Exception) as exc:
        P._verify({"key": "pf-test"})
    assert type(exc.value).__name__ == expected


def test_probe_calls_get_company_with_the_posed_key(monkeypatch):
    from oto_mcp.tools import payfit as P

    seen = {}
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.payfit.PayfitClient",
                        lambda **kw: seen.update(kw) or inst)
    P._verify({"key": " pf-test "})
    assert seen == {"api_key": "pf-test"}
    inst.get_company.assert_called_once_with()
