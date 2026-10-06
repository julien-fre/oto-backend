"""Dispatch des tools `inqom_*` (ADR 0047 §Amendement) et gardes du connecteur.

Ce que ce fichier verrouille :
- la SURFACE (7 tools) et le routage de chaque op vers la bonne méthode du client ;
- AUCUNE écriture n'est câblée (30/09/2026) : `inqom_entry_create` rend le refus
  nommé `inqom_write_not_wired`, qui décrit les écritures, sans résoudre la clé, sans
  construire le client ni appeler Inqom — quels que soient les arguments ;
- `account_prefixes` : toute la période lue, les lignes gardées triées par date,
  chacune avec les comptes de tiers de son écriture, paginées sur le résultat ;
- un argument hors op est refusé même quand il vaut `False`/`0` (`is not None`) ;
- un champ de credential vide est refusé avant de construire le client (il
  retomberait sinon sur la résolution de secrets locale) — dans l'outil ET la sonde ;
- un 404 amont est reconnu sur `status_code`, jamais sur le texte.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError

_CREDS = {"client_id": "app", "client_secret": "s", "username": "u@exemple.fr",
          "password": "p"}


@pytest.fixture
def construits(monkeypatch):
    """Les kwargs de chaque construction de `InqomClient`, et le faux client."""
    inst, calls = MagicMock(), []

    def fabrique(**kw):
        calls.append(kw)
        return inst

    monkeypatch.setattr("oto.tools.inqom.InqomClient", fabrique)
    return inst, calls


@pytest.fixture
def client(construits, monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider: dict(_CREDS))
    return construits[0]


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import inqom as Q

    m = FastMCP("t")
    Q.register(m)
    return m


def _tool(name: str):
    return asyncio.run(_mcp().get_tool(name)).fn


def _entry(debit="120.50", credit="120.50", journal=3):
    return {"JournalId": journal, "Date": "2026-01-15", "Lines": [
        {"AccountNumber": "606100", "Label": "achat", "Currency": "EUR", "DebitAmount": debit},
        {"AccountNumber": "401000", "Label": "achat", "Currency": "EUR", "CreditAmount": credit},
    ]}


def test_la_surface_est_exactement_les_sept_tools(client):
    assert sorted(t.name for t in asyncio.run(_mcp().list_tools())) == [
        "inqom_balance", "inqom_company", "inqom_document", "inqom_dossier",
        "inqom_entry_create", "inqom_entry_line", "inqom_ref",
    ]


# --- routage ------------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs,method", [
    ("inqom_company", {}, "list_companies"),
    ("inqom_dossier", {"company_id": 7}, "list_dossiers"),
    ("inqom_dossier", {"op": "get", "dossier_id": 12}, "get_dossier"),
    ("inqom_ref", {"kind": "accounts", "dossier_id": 12}, "list_accounts"),
    ("inqom_ref", {"kind": "journals", "dossier_id": 12}, "list_journals"),
    ("inqom_ref", {"kind": "periods", "dossier_id": 12}, "list_accounting_periods"),
    ("inqom_balance", {"dossier_id": 12, "start_date": "2026-01-01",
                       "end_date": "2026-12-31"}, "list_balances"),
    ("inqom_entry_line", {"dossier_id": 12, "start_date": "2026-01-01",
                          "end_date": "2026-01-31"}, "list_entry_lines"),
    ("inqom_entry_line", {"op": "count", "dossier_id": 12, "start_date": "2026-01-01",
                          "end_date": "2026-01-31"}, "count_entry_lines"),
    ("inqom_document", {"dossier_id": 12, "document_id": 5}, "get_accounting_document"),
])
def test_chaque_op_atteint_sa_methode(client, tool, kwargs, method):
    getattr(client, method).return_value = []
    _tool(tool)(**kwargs)
    getattr(client, method).assert_called_once()
    client.create_entries.assert_not_called()


def test_le_credential_resolu_est_passe_tel_quel(client, construits):
    client.list_companies.return_value = []
    _tool("inqom_company")()
    assert construits[1] == [_CREDS]


def test_entry_line_list_commence_page_1(client):
    client.list_entry_lines.return_value = {}
    _tool("inqom_entry_line")(dossier_id=12, start_date="2026-01-01", end_date="2026-01-31")
    assert client.list_entry_lines.call_args.args == (12, "2026-01-01", "2026-01-31", 1)


# --- arguments requis et hors op --------------------------------------------------

def test_dossier_list_exige_company_id(client):
    with pytest.raises(McpError, match="op='list' requires company_id"):
        _tool("inqom_dossier")()
    client.list_dossiers.assert_not_called()


def test_dossier_get_exige_dossier_id(client):
    with pytest.raises(McpError, match="op='get' requires dossier_id"):
        _tool("inqom_dossier")(op="get")
    client.get_dossier.assert_not_called()


def test_un_argument_hors_op_est_refuse_meme_a_zero(client):
    with pytest.raises(McpError, match="does not use company_id"):
        _tool("inqom_dossier")(op="get", dossier_id=12, company_id=0)
    client.get_dossier.assert_not_called()


def test_count_refuse_page_number_et_journal_id(client):
    with pytest.raises(McpError, match="does not use page_number"):
        _tool("inqom_entry_line")(op="count", dossier_id=12, start_date="2026-01-01",
                                  end_date="2026-01-31", page_number=2)
    client.count_entry_lines.assert_not_called()


def test_ref_refuse_les_filtres_de_comptes_hors_accounts(client):
    with pytest.raises(McpError, match="does not use number_prefix"):
        _tool("inqom_ref")(kind="journals", dossier_id=12, number_prefix="401")
    client.list_journals.assert_not_called()


# --- l'écriture : NON CÂBLÉE -------------------------------------------------------

@pytest.fixture
def rien_ne_part(construits, monkeypatch):
    """Ni clé résolue ni client construit : un appel à l'un ou l'autre échoue."""
    def interdit(provider):
        raise AssertionError("la clé ne doit pas être résolue pour une écriture")

    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields", interdit)
    return construits


def _refus(**kwargs):
    with pytest.raises(McpError) as exc:
        _tool("inqom_entry_create")(**kwargs)
    return exc.value.error


def test_entry_create_ne_part_jamais_et_decrit_les_ecritures(rien_ne_part):
    err = _refus(dossier_id=12, entries=[_entry(), {**_entry(journal=4), "EntryRef": "F-7"}])
    assert err.data["code"] == "inqom_write_not_wired"
    assert err.data["retryable"] is False and err.data["op"] == "create"
    assert "this would have created 2 accounting entry(ies) in dossier 12" in err.message
    assert "nothing was sent to Inqom" in err.message
    assert err.data["would_have"]["ecritures"] == [
        {"JournalId": 3, "Date": "2026-01-15", "lignes": 2,
         "total_debit": "120.50", "total_credit": "120.50"},
        {"JournalId": 4, "Date": "2026-01-15", "EntryRef": "F-7", "lignes": 2,
         "total_debit": "120.50", "total_credit": "120.50"}]
    assert rien_ne_part[1] == []
    rien_ne_part[0].create_entries.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {},
    {"dossier_id": 12, "entries": []},
    {"dossier_id": 12, "entries": [_entry(debit="100", credit="99.99")]},
    {"dossier_id": 12, "entries": [{"Date": "15/01/2026"}, "pas un objet"]},
    {"dossier_id": 12, "entries": [_entry(debit="n/a")]},
])
def test_le_refus_ne_depend_pas_des_arguments(rien_ne_part, kwargs):
    err = _refus(**kwargs)
    assert err.data["code"] == "inqom_write_not_wired"
    assert rien_ne_part[1] == []
    rien_ne_part[0].create_entries.assert_not_called()


def test_le_refus_ne_recopie_ni_libelles_ni_comptes(rien_ne_part):
    """Le refus est journalisé : il ne porte que journal, date, référence et totaux."""
    err = _refus(dossier_id=12, entries=[_entry()])
    assert "606100" not in err.message and "achat" not in err.message
    assert "606100" not in repr(err.data)


def test_au_dela_de_vingt_ecritures_le_reste_se_compte(rien_ne_part):
    err = _refus(dossier_id=12, entries=[_entry()] * 25)
    assert len(err.data["would_have"]["ecritures"]) == 20
    assert err.data["would_have"]["ecritures_non_decrites"] == 5


def test_entry_create_n_a_plus_de_dry_run():
    schema = asyncio.run(_mcp().get_tool("inqom_entry_create")).parameters
    assert "dry_run" not in schema.get("properties", {})


# --- préfixes de compte ------------------------------------------------------------

def _ligne(id_, compte, entry, date="2026-09-10"):
    return {"Id": id_, "AccountNumber": compte, "Label": "l", "DebitAmount": 1,
            "CreditAmount": 0, "Entry": {"Id": entry, "Date": f"{date}T00:00:00"}}


def _periode(client, *pages):
    client.count_entry_lines.return_value = {"TotalPagesCount": len(pages)}
    client.list_entry_lines.side_effect = [{"EntryLines": p} for p in pages]


def test_prefixes_garde_trie_et_rattache_le_tiers(client):
    _periode(client, [
        _ligne(1, "606100", 10, "2026-09-20"), _ligne(2, "401DUPONT", 10, "2026-09-20"),
        _ligne(3, "706000", 11, "2026-09-05"), _ligne(4, "411CLIENT", 11, "2026-09-05"),
        _ligne(5, "512000", 12), _ligne(6, "601000", 12),
    ])
    out = _tool("inqom_entry_line")(dossier_id=12, start_date="2026-09-01",
                                    end_date="2026-09-30", account_prefixes=["6", "7"])
    lignes = out["page"]["EntryLines"]
    assert [ln["Id"] for ln in lignes] == [3, 6, 1]
    assert [ln["third_party_accounts"] for ln in lignes] == [["411CLIENT"], [], ["401DUPONT"]]
    assert out["count"] == {"TotalLinesCount": 3, "TotalPagesCount": 1}
    client.count_entry_lines.assert_called_once_with(12, "2026-09-01", "2026-09-30")
    assert client.list_entry_lines.call_args.kwargs == {"journal_id": None}


def test_prefixes_lit_jusqu_a_la_page_incomplete(client, monkeypatch):
    from oto_mcp.tools import inqom as Q

    monkeypatch.setattr(Q, "_PAGE", 2)
    _periode(client, [_ligne(1, "606", 1), _ligne(2, "401A", 1)],
             [_ligne(3, "606", 2)])
    out = _tool("inqom_entry_line")(op="count", dossier_id=12, start_date="2026-09-01",
                                    end_date="2026-09-30", account_prefixes=["6"])
    assert [c.args[3] for c in client.list_entry_lines.call_args_list] == [1, 2]
    assert out == {"count": {"TotalLinesCount": 2, "TotalPagesCount": 1}}


def test_prefixes_pagine_le_resultat_filtre(client, monkeypatch):
    from oto_mcp.tools import inqom as Q

    monkeypatch.setattr(Q, "_PAGE", 2)
    _periode(client, [_ligne(1, "606", 1), _ligne(2, "606", 2)], [_ligne(3, "606", 3)])
    out = _tool("inqom_entry_line")(dossier_id=12, start_date="2026-09-01",
                                    end_date="2026-09-30", account_prefixes=["6"],
                                    page_number=2)
    assert [ln["Id"] for ln in out["page"]["EntryLines"]] == [3]
    assert out["count"] == {"TotalLinesCount": 3, "TotalPagesCount": 2}


def test_prefixes_refuse_une_periode_trop_large_avant_de_lire(client):
    client.count_entry_lines.return_value = {"TotalPagesCount": 51}
    with pytest.raises(McpError, match="narrow"):
        _tool("inqom_entry_line")(dossier_id=12, start_date="2026-01-01",
                                  end_date="2026-12-31", account_prefixes=["6"])
    client.list_entry_lines.assert_not_called()


@pytest.mark.parametrize("kwargs,fragment", [
    ({"account_prefixes": ["6"], "account_number": "606100"}, "mutually exclusive"),
    ({"account_prefixes": []}, "non-empty list"),
    ({"account_prefixes": ["6", " "]}, "empty prefix"),
])
def test_prefixes_arguments_refuses_avant_tout_appel(client, kwargs, fragment):
    with pytest.raises(McpError, match=fragment):
        _tool("inqom_entry_line")(dossier_id=12, start_date="2026-09-01",
                                  end_date="2026-09-30", **kwargs)
    client.count_entry_lines.assert_not_called()
    client.list_entry_lines.assert_not_called()


# --- credential vide ----------------------------------------------------------------

@pytest.mark.parametrize("vide", ["client_id", "client_secret", "username", "password"])
def test_un_champ_vide_est_refuse_sans_construire_le_client(construits, monkeypatch, vide):
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider: {**_CREDS, vide: "  "})
    with pytest.raises(McpError, match=vide):
        _tool("inqom_company")()
    assert construits[1] == []


def test_la_sonde_refuse_un_champ_vide_sans_construire_le_client(construits):
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools.inqom import _verify

    with pytest.raises(connector_verify.NonAutorise, match="password"):
        _verify({**_CREDS, "password": ""})
    assert construits[1] == []


def test_la_sonde_classe_un_refus_amont_en_non_autorise(construits):
    from oto.tools.common.errors import UpstreamHTTPError
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools.inqom import _verify

    construits[0].list_companies.side_effect = UpstreamHTTPError(401, {"error": "x"})
    with pytest.raises(connector_verify.NonAutorise):
        _verify(dict(_CREDS))


# --- erreurs amont --------------------------------------------------------------------

def test_un_404_se_reconnait_au_status_code_pas_au_texte(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.get_dossier.side_effect = UpstreamHTTPError(404, "no body mentioning the code")
    with pytest.raises(McpError, match="not found"):
        _tool("inqom_dossier")(op="get", dossier_id=12)


def test_un_texte_contenant_404_n_est_pas_un_404(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.get_dossier.side_effect = UpstreamHTTPError(400, "(404) dans le texte")
    with pytest.raises(McpError, match=r"refused the request \(HTTP 400\)"):
        _tool("inqom_dossier")(op="get", dossier_id=12)
