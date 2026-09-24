"""`pennylane_supplier_invoice` — lire, corriger et valider les factures d'achat.

Les factures importées avec `import_as_incomplete=True` restaient en
`validation_needed` : l'outil ne savait que lister et importer, il fallait les
passer une à une dans l'interface. Ces épreuves câblent le routage de chaque
`op` vers le client oto-core, les formes d'`invoice_lines` (liste à l'import,
objet à la correction), et le refus amont qui remonte au lieu de passer pour
un succès.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import pennylane_achats as A

    m = FastMCP("t")
    A.register(m)
    return asyncio.run(m.get_tool("pennylane_supplier_invoice")).fn


@pytest.fixture
def client(monkeypatch):
    import oto.tools.pennylane as pkg

    inst = MagicMock()
    monkeypatch.setattr(pkg, "PennylaneClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    return inst


def test_le_module_est_monte_par_le_registre():
    from oto_mcp import providers

    assert "pennylane_achats" in providers.REGISTRY["pennylane"].modules


def test_l_outil_ne_vit_plus_dans_le_module_principal():
    """Un seul domicile : monté deux fois, fastmcp garderait le dernier en silence."""
    from fastmcp import FastMCP
    from oto_mcp.tools import pennylane as P

    m = FastMCP("t")
    P.register(m)
    noms = {t.name for t in asyncio.run(m.list_tools())}
    assert "pennylane_supplier_invoice" not in noms


def test_validate_appelle_la_validation_comptable_de_cette_facture(client):
    client.validate_supplier_invoice_accounting.return_value = {
        "id": 42, "accounting_status": "complete"}
    r = _tool()(op="validate", invoice_id=42)
    client.validate_supplier_invoice_accounting.assert_called_once_with(42)
    assert r["accounting_status"] == "complete"


def test_validate_sans_invoice_id_est_refuse_avant_tout_appel(client):
    with pytest.raises(McpError, match="invoice_id"):
        _tool()(op="validate")
    client.validate_supplier_invoice_accounting.assert_not_called()


def test_update_pose_le_vat_rate_d_une_ligne_et_ne_transmet_que_les_champs_fournis(client):
    lignes = {"update": [{"id": 9, "vat_rate": "intracom_100"}]}
    _tool()(op="update", invoice_id=42, label="Hébergement", invoice_lines=lignes)
    client.update_supplier_invoice.assert_called_once_with(
        42, fields={"label": "Hébergement"}, invoice_lines=lignes)


def test_update_refuse_une_liste_de_lignes_en_disant_la_forme_attendue(client):
    with pytest.raises(McpError, match="OBJET"):
        _tool()(op="update", invoice_id=42, invoice_lines=[{"id": 9}])
    client.update_supplier_invoice.assert_not_called()


def test_update_traduit_le_refus_de_forme_du_client_en_erreur_nommee(client):
    client.update_supplier_invoice.side_effect = ValueError(
        "invoice_lines.update : chaque ligne exige son `id`")
    with pytest.raises(McpError, match="exige son `id`"):
        _tool()(op="update", invoice_id=42, invoice_lines={"update": [{"vat_rate": "x"}]})


def test_lines_et_get_lisent_la_facture(client):
    _tool()(op="get", invoice_id=42)
    _tool()(op="lines", invoice_id=42, max_pages=2)
    client.get_supplier_invoice.assert_called_once_with(42)
    client.get_supplier_invoice_lines.assert_called_once_with(42, max_pages=2)


def test_import_garde_eur_par_defaut_et_refuse_un_objet_de_lignes(client):
    base = dict(op="import", file_attachment_id=5, supplier_id=3, date="2026-09-01",
                deadline="2026-10-01", currency_amount_before_tax="100.00",
                currency_amount="120.00", currency_tax="20.00")
    _tool()(invoice_lines=[{"currency_amount": "120.00", "currency_tax": "20.00",
                            "vat_rate": "FR_200"}], **base)
    assert client.import_supplier_invoice.call_args.kwargs["currency"] == "EUR"
    with pytest.raises(McpError, match="LISTE"):
        _tool()(invoice_lines={"update": []}, **base)


def test_un_refus_amont_de_validation_remonte_et_ne_passe_pas_pour_un_succes(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.validate_supplier_invoice_accounting.side_effect = UpstreamHTTPError(
        422, {"error": "Entry lines are not balanced"}, service="pennylane")
    with pytest.raises(McpError, match="CONTENU"):
        _tool()(op="validate", invoice_id=42)
