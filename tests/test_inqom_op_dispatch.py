"""Dispatch des tools `inqom_*` (ADR 0047 §Amendement) et gardes du connecteur.

Ce que ce fichier verrouille :
- la SURFACE (7 tools) et le routage de chaque op vers la bonne méthode du client ;
- aucun défaut n'écrit : `inqom_entry_create` est en `dry_run=True` par défaut et
  n'atteint jamais `create_entries` sans `dry_run=False` ;
- une écriture mal formée ou déséquilibrée est refusée AVANT tout appel ;
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
    with pytest.raises(McpError, match="op='list' requiert company_id"):
        _tool("inqom_dossier")()
    client.list_dossiers.assert_not_called()


def test_dossier_get_exige_dossier_id(client):
    with pytest.raises(McpError, match="op='get' requiert dossier_id"):
        _tool("inqom_dossier")(op="get")
    client.get_dossier.assert_not_called()


def test_un_argument_hors_op_est_refuse_meme_a_zero(client):
    with pytest.raises(McpError, match="n'utilise pas company_id"):
        _tool("inqom_dossier")(op="get", dossier_id=12, company_id=0)
    client.get_dossier.assert_not_called()


def test_count_refuse_page_number_et_journal_id(client):
    with pytest.raises(McpError, match="n'utilise pas page_number"):
        _tool("inqom_entry_line")(op="count", dossier_id=12, start_date="2026-01-01",
                                  end_date="2026-01-31", page_number=2)
    client.count_entry_lines.assert_not_called()


def test_ref_refuse_les_filtres_de_comptes_hors_accounts(client):
    with pytest.raises(McpError, match="n'utilise pas number_prefix"):
        _tool("inqom_ref")(kind="journals", dossier_id=12, number_prefix="401")
    client.list_journals.assert_not_called()


# --- l'écriture -------------------------------------------------------------------

def test_entry_create_par_defaut_ne_pose_rien(client):
    client.list_journals.return_value = [{"Id": 3, "Name": "ACH"}]
    out = _tool("inqom_entry_create")(dossier_id=12, entries=[_entry()])
    assert out["dry_run"] is True and out["warnings"] == []
    assert out["entries"][0]["total_debit"] == "120.50"
    client.create_entries.assert_not_called()


def test_entry_create_dry_run_signale_un_journal_inconnu(client):
    client.list_journals.return_value = [{"Id": 3}]
    out = _tool("inqom_entry_create")(dossier_id=12, entries=[_entry(journal=99)])
    assert "JournalId 99" in out["warnings"][0]
    client.create_entries.assert_not_called()


def test_entry_create_pose_seulement_avec_dry_run_false(client):
    client.create_entries.return_value = [{"Id": 1}]
    out = _tool("inqom_entry_create")(dossier_id=12, entries=[_entry()], dry_run=False)
    client.create_entries.assert_called_once_with(12, [_entry()])
    assert out == {"dry_run": False, "dossier_id": 12, "inserted": [{"Id": 1}]}


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("entries,fragment", [
    ([], "liste non vide"),
    ([_entry(debit="100", credit="99.99")], "déséquilibrée"),
    ([{**_entry(), "Date": "15/01/2026"}], "Date requise"),
    ([{**_entry(), "JournalId": None}], "JournalId requis"),
    ([{**_entry(), "Lines": [_entry()["Lines"][0]]}], "au moins deux lignes"),
    ([{**_entry(), "Lines": [{**_entry()["Lines"][0], "CreditAmount": "1"},
                             _entry()["Lines"][1]]}], "exactement un"),
    ([_entry(debit="-5", credit="-5")], "négatif"),
])
def test_une_ecriture_invalide_est_refusee_avant_tout_appel(client, construits, dry_run,
                                                            entries, fragment):
    with pytest.raises(McpError, match=fragment):
        _tool("inqom_entry_create")(dossier_id=12, entries=entries, dry_run=dry_run)
    client.create_entries.assert_not_called()
    client.list_journals.assert_not_called()


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
    with pytest.raises(McpError, match="introuvable"):
        _tool("inqom_dossier")(op="get", dossier_id=12)


def test_un_texte_contenant_404_n_est_pas_un_404(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.get_dossier.side_effect = UpstreamHTTPError(400, "(404) dans le texte")
    with pytest.raises(McpError, match=r"refusé la requête \(HTTP 400\)"):
        _tool("inqom_dossier")(op="get", dossier_id=12)
