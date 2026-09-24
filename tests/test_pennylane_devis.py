"""`pennylane_quote` — les devis Pennylane (demande du 24/09/2026).

Un client exigeait un devis, pas une pro forma, avant toute facture : le
connecteur ne savait faire que factures et avoirs. Ces épreuves câblent le
routage de chaque `op` vers le client oto-core, les arguments obligatoires,
le lien PDF, et le refus amont qui remonte au lieu de passer pour un succès.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError

LIGNE = {"label": "Accompagnement", "quantity": 2, "unit": "day",
         "raw_currency_unit_price": "700.00", "vat_rate": "FR_200"}


def _tool():
    from fastmcp import FastMCP
    from oto_mcp.tools import pennylane_devis as D

    m = FastMCP("t")
    D.register(m)
    return asyncio.run(m.get_tool("pennylane_quote")).fn


@pytest.fixture
def client(monkeypatch):
    import oto.tools.pennylane as pkg

    inst = MagicMock()
    monkeypatch.setattr(pkg, "PennylaneClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    return inst


def test_le_module_est_monte_par_le_registre():
    from oto_mcp import providers

    assert "pennylane_devis" in providers.REGISTRY["pennylane"].modules


def test_create_route_vers_create_quote_avec_texte_libre(client):
    _tool()(op="create", customer_id=12, date="2026-09-24", deadline="2026-10-24",
            lines=[LIGNE], free_text="Valable 30 jours", quote_template_id=3)
    client.create_quote.assert_called_once_with(
        customer_id=12, date="2026-09-24", deadline="2026-10-24", lines=[LIGNE],
        external_reference=None, pdf_free_text="Valable 30 jours", quote_template_id=3)


def test_create_exige_ses_champs(client):
    with pytest.raises(McpError, match="op='create' requiert deadline"):
        _tool()(op="create", customer_id=12, date="2026-09-24", lines=[LIGNE])
    client.create_quote.assert_not_called()


def test_list_passe_les_filtres(client):
    _tool()(op="list", status="accepted", customer_id=12, max_pages=1)
    client.list_quotes.assert_called_once_with(max_pages=1, status="accepted",
                                               customer_id=12)


def test_un_statut_inconnu_est_refuse_sans_appel(client):
    with pytest.raises(McpError, match="status inconnu"):
        _tool()(op="set_status", quote_id=7, status="signed")
    client.update_quote_status.assert_not_called()


def test_get_et_lines(client):
    _tool()(op="get", quote_id=7)
    client.get_quote.assert_called_once_with(7)
    _tool()(op="lines", quote_id=7)
    client.get_quote_lines.assert_called_once_with(7)


def test_pdf_rend_le_lien_du_devis(client):
    client.get_quote.return_value = {"quote_number": "D-2026-001",
                                     "public_file_url": "https://exemple.test/d.pdf",
                                     "filename": "D-2026-001.pdf"}
    assert _tool()(op="pdf", quote_id=7) == {
        "quote_id": 7, "quote_number": "D-2026-001",
        "public_file_url": "https://exemple.test/d.pdf", "filename": "D-2026-001.pdf"}


def test_pdf_sans_lien_leve_au_lieu_de_rendre_vide(client):
    client.get_quote.return_value = {"quote_number": "D-2026-001"}
    with pytest.raises(McpError, match="aucun lien PDF"):
        _tool()(op="pdf", quote_id=7)


def test_set_status(client):
    _tool()(op="set_status", quote_id=7, status="accepted")
    client.update_quote_status.assert_called_once_with(7, "accepted")


def test_to_invoice_cree_toujours_un_brouillon(client):
    _tool()(op="to_invoice", quote_id=7, customer_invoice_template_id=4)
    client.create_invoice_from_quote.assert_called_once_with(
        7, draft=True, external_reference=None, customer_invoice_template_id=4)


def test_un_refus_de_scope_remonte_actionnable(client):
    from oto.tools.common.errors import UpstreamHTTPError

    client.create_invoice_from_quote.side_effect = UpstreamHTTPError(
        403, {"error": "insufficient scope"}, service="pennylane")
    with pytest.raises(McpError, match="DROIT qui manque"):
        _tool()(op="to_invoice", quote_id=7)
