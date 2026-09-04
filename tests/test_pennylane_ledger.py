"""Grand livre côté outils — oto-backend#872, pièces A et B (lecture).

Les épreuves visent ce qui casse en silence côté agent : un `op` routé vers la
mauvaise méthode, un argument obligatoire avalé, et surtout le référentiel des
journaux — sans lui, aucune écriture comptable n'est possible plus tard, puisque
`journal_id` est requis et propre à la société.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def client(monkeypatch):
    """`_client()` importe `PennylaneClient` depuis le PACKAGE à chaque appel :
    c'est l'attribut du package qu'on remplace."""
    import oto.tools.pennylane as pkg

    inst = MagicMock()
    monkeypatch.setattr(pkg, "PennylaneClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda *a, **k: ("k", False))
    return inst


def _tool(nom: str, module: str = "pennylane_ledger"):
    from fastmcp import FastMCP
    import importlib

    m = FastMCP("t")
    importlib.import_module(f"oto_mcp.tools.{module}").register(m)
    return asyncio.run(m.get_tool(nom)).fn


# --- routage ---------------------------------------------------------------

def test_list_passe_les_clauses_au_client(client):
    clauses = [{"field": "date", "operator": "gteq", "value": "2026-01-01"}]
    _tool("pennylane_ledger_entry")(op="list", clauses=clauses, max_pages=2)
    client.get_ledger_entries.assert_called_once_with(max_pages=2, clauses=clauses)


def test_get_lit_une_ecriture_par_son_id(client):
    _tool("pennylane_ledger_entry")(op="get", entry_id=42)
    client.get_ledger_entry.assert_called_once_with(42)


def test_lines_lit_les_lignes_de_l_ecriture(client):
    _tool("pennylane_ledger_entry")(op="lines", entry_id=42)
    client.get_ledger_entry_lines.assert_called_once_with(42, max_pages=None)


def test_lettered_part_d_une_LIGNE_et_non_d_une_ecriture(client):
    """Le piège du domaine : `lettered` prend l'id d'une LIGNE. Passer un id
    d'écriture rendrait le lettrage d'une autre ligne, sans erreur."""
    _tool("pennylane_ledger_entry")(op="lettered", line_id=7)
    client.get_lettered_lines.assert_called_once_with(7, max_pages=None)
    client.get_ledger_entry_lines.assert_not_called()


# --- arguments obligatoires ------------------------------------------------

@pytest.mark.parametrize("op,manquant", [("get", "entry_id"), ("lines", "entry_id"),
                                         ("lettered", "line_id")])
def test_un_op_sans_son_id_est_refuse_avant_tout_appel(client, op, manquant):
    with pytest.raises(McpError, match=f"op='{op}' requiert {manquant}"):
        _tool("pennylane_ledger_entry")(op=op)
    assert not client.method_calls, "rien ne doit partir sans son identifiant"


def test_un_op_inconnu_nomme_les_op_valides(client):
    with pytest.raises(McpError, match="'list'.*'get'.*'lines'.*'lettered'"):
        _tool("pennylane_ledger_entry")(op="nope")


def test_le_defaut_est_une_lecture_de_liste(client):
    _tool("pennylane_ledger_entry")()
    client.get_ledger_entries.assert_called_once()


# --- le référentiel des journaux -------------------------------------------

def test_pennylane_ref_sert_les_journaux(client):
    """Prérequis de toute écriture comptable : `journal_id` est requis et propre
    à la société. Sans ce référentiel, la pièce C est inutilisable."""
    _tool("pennylane_ref", module="pennylane")(kind="journals")
    client.get_journals.assert_called_once_with(max_pages=None)


def test_pennylane_ref_nomme_les_journaux_parmi_les_kinds_valides(client):
    with pytest.raises(McpError, match="journals"):
        _tool("pennylane_ref", module="pennylane")(kind="nope")
