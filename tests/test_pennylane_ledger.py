"""Grand livre côté outils — oto-backend#872, pièces A à D.

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


@pytest.fixture(autouse=True)
def registre_vierge():
    """Chaque épreuve part sans préparation en cours : sinon l'une validerait
    l'autre et la garde passerait pour bonne sans jamais refuser."""
    from oto_mcp.tools import pennylane_ledger as L
    L._PREPARES.clear()
    yield
    L._PREPARES.clear()


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


# --- écriture comptable : le geste sans brouillon --------------------------

def test_create_passe_les_champs_requis_et_les_lignes(client):
    lignes = [{"debit": "120.00", "credit": "0", "ledger_account_id": 11},
              {"debit": "0", "credit": "120.00", "ledger_account_id": 22}]
    prep = dict(date="2026-09-04", label="OD", journal_id=5, lines=lignes)
    jeton = _tool("pennylane_ledger_entry")(op="prepare", **prep)["jeton"]
    _tool("pennylane_ledger_entry")(op="create", jeton=jeton, **prep)
    client.create_ledger_entry.assert_called_once_with(
        date="2026-09-04", label="OD", journal_id=5, ledger_entry_lines=lignes,
        due_date=None, currency=None, piece_number=None)


@pytest.mark.parametrize("manquant,kwargs", [
    ("date", {"label": "OD", "journal_id": 5, "lines": [{}]}),
    ("label", {"date": "2026-09-04", "journal_id": 5, "lines": [{}]}),
    ("journal_id", {"date": "2026-09-04", "label": "OD", "lines": [{}]}),
    ("lines", {"date": "2026-09-04", "label": "OD", "journal_id": 5}),
])
def test_create_refuse_un_champ_requis_manquant_avant_tout_appel(client, manquant, kwargs):
    with pytest.raises(McpError, match=f"op='create' requiert {manquant}"):
        _tool("pennylane_ledger_entry")(op="create", **kwargs)
    client.create_ledger_entry.assert_not_called()


def test_un_refus_de_creation_leve_au_lieu_de_remonter_en_valeur(client):
    """Le geste le plus engageant du connecteur : un refus lu comme un succès
    laisserait croire qu'une écriture est passée."""
    client.create_ledger_entry.return_value = {
        "error": "422", "details": "Entry lines are not balanced", "status_code": 422}
    prep = dict(date="2026-09-04", label="OD", journal_id=5, lines=[{}])
    jeton = _tool("pennylane_ledger_entry")(op="prepare", **prep)["jeton"]
    with pytest.raises(McpError, match="Entry lines are not balanced"):
        _tool("pennylane_ledger_entry")(op="create", jeton=jeton, **prep)


def test_la_description_dit_qu_il_n_y_a_pas_de_brouillon(client):
    """Le reste du connecteur est brouillon-d'abord ; ici non. L'agent lit la
    description à chaque appel — c'est le seul endroit où cette asymétrie peut
    l'arrêter avant qu'il ne pose l'écriture."""
    from fastmcp import FastMCP
    import importlib

    m = FastMCP("t")
    importlib.import_module("oto_mcp.tools.pennylane_ledger").register(m)
    doc = asyncio.run(m.get_tool("pennylane_ledger_entry")).description or ""
    assert "brouillon" in doc.lower(), doc
    assert "update" in doc, "le seul recours doit être nommé"


# --- lettrage de lignes ----------------------------------------------------

def test_set_lettre_les_lignes(client):
    _tool("pennylane_ledger_lettering")(op="set", line_ids=[1, 2])
    client.letter_ledger_entry_lines.assert_called_once_with([1, 2], "none")


def test_unset_defait_le_lettrage(client):
    _tool("pennylane_ledger_lettering")(op="unset", line_ids=[1, 2])
    client.unletter_ledger_entry_lines.assert_called_once_with([1, 2], "none")


def test_le_defaut_refuse_un_lettrage_desequilibre(client):
    """Un défaut permissif passerait inaperçu."""
    _tool("pennylane_ledger_lettering")(op="set", line_ids=[1, 2])
    assert client.letter_ledger_entry_lines.call_args[0][1] == "none"


def test_un_op_de_lettrage_inconnu_est_refuse(client):
    with pytest.raises(McpError, match="'set'.*'unset'"):
        _tool("pennylane_ledger_lettering")(op="nope", line_ids=[1, 2])
    client.letter_ledger_entry_lines.assert_not_called()


def test_les_deux_lettrages_se_designent_l_un_l_autre():
    """Le mot « lettrage » recouvre deux gestes sur deux objets. La confusion a
    déjà coûté une conclusion fausse : chaque description doit nommer l'autre
    outil, sinon l'agent choisit au hasard sans jamais voir d'erreur."""
    from fastmcp import FastMCP
    import importlib

    m = FastMCP("t")
    for mod in ("pennylane", "pennylane_ledger"):
        importlib.import_module(f"oto_mcp.tools.{mod}").register(m)
    match = asyncio.run(m.get_tool("pennylane_match")).description or ""
    lettrage = asyncio.run(m.get_tool("pennylane_ledger_lettering")).description or ""
    assert "pennylane_ledger_lettering" in match, match
    assert "pennylane_match" in lettrage, lettrage


# --- le brouillon porté par oto -------------------------------------------
#
# Pennylane pose une écriture immédiatement, ne sait pas la supprimer, et sa
# correction peut détruire des lignes. Le palier de validation est donc tenu
# ici. Ces épreuves visent la garde elle-même : elle ne vaut que si elle refuse.

LIGNES = [{"debit": "120.00", "credit": "0", "ledger_account_id": 11},
          {"debit": "0", "credit": "120.00", "ledger_account_id": 22}]
CREATION = dict(op="create", date="2026-09-04", label="OD", journal_id=5,
                lines=LIGNES)


def test_create_sans_jeton_est_refuse_et_renvoie_a_prepare(client):
    with pytest.raises(McpError, match="prepare"):
        _tool("pennylane_ledger_entry")(**CREATION)
    client.create_ledger_entry.assert_not_called()


def test_prepare_rend_le_detail_les_totaux_et_un_jeton(client):
    client.controler_ecriture.return_value = {
        "lignes": 2, "total_debit": "120.00", "total_credit": "120.00"}
    out = _tool("pennylane_ledger_entry")(**{**CREATION, "op": "prepare"})
    assert out["a_poser"]["journal_id"] == 5
    assert out["a_poser"]["lignes"] == LIGNES
    assert out["recapitulatif"]["total_debit"] == "120.00"
    assert out["jeton"]
    client.create_ledger_entry.assert_not_called(), "prepare ne doit RIEN écrire"


def test_prepare_puis_create_passe(client):
    client.controler_ecriture.return_value = {"lignes": 2}
    jeton = _tool("pennylane_ledger_entry")(**{**CREATION, "op": "prepare"})["jeton"]
    _tool("pennylane_ledger_entry")(**CREATION, jeton=jeton)
    client.create_ledger_entry.assert_called_once()


def test_un_jeton_emis_pour_un_AUTRE_detail_est_refuse(client):
    """Le cœur de la garde : l'accord porte sur un détail, pas sur l'intention.
    Préparer 120 € puis créer 999 € doit échouer."""
    client.controler_ecriture.return_value = {"lignes": 2}
    jeton = _tool("pennylane_ledger_entry")(**{**CREATION, "op": "prepare"})["jeton"]
    autres = [{"debit": "999.00", "credit": "0", "ledger_account_id": 11},
              {"debit": "0", "credit": "999.00", "ledger_account_id": 22}]
    with pytest.raises(McpError, match="ne correspond pas à CE détail"):
        _tool("pennylane_ledger_entry")(**{**CREATION, "lines": autres}, jeton=jeton)
    client.create_ledger_entry.assert_not_called()


def test_un_jeton_perime_est_refuse(client, monkeypatch):
    from oto_mcp.tools import pennylane_ledger as L

    client.controler_ecriture.return_value = {"lignes": 2}
    jeton = _tool("pennylane_ledger_entry")(**{**CREATION, "op": "prepare"})["jeton"]
    # On avance l'horloge plutôt que d'attendre : le test doit rester rapide,
    # mais éprouver la vraie borne, pas une borne raccourcie pour l'occasion.
    vrai = L.time.monotonic
    monkeypatch.setattr(L.time, "monotonic",
                        lambda: vrai() + L._VALIDITE_S + 1)
    with pytest.raises(McpError, match="périmé"):
        _tool("pennylane_ledger_entry")(**CREATION, jeton=jeton)
    client.create_ledger_entry.assert_not_called()


def test_un_jeton_jamais_prepare_est_refuse(client):
    """Le jeton est l'empreinte du détail, donc calculable par qui connaît le
    code. Le registre est ce qui empêche de le fabriquer : sans passage par
    `prepare`, il ne vaut rien."""
    from oto_mcp.tools import pennylane_ledger as L

    forge = L._jeton(L._detail_creation("2026-09-04", "OD", 5, LIGNES,
                                        None, None, None))
    with pytest.raises(McpError, match="Jeton inconnu"):
        _tool("pennylane_ledger_entry")(**CREATION, jeton=forge)
    client.create_ledger_entry.assert_not_called()


def test_la_correction_exige_aussi_un_jeton(client):
    """Mesuré sur le contrat de l'API : le PUT prend `create`/`update`/`delete`
    sur les lignes — corriger peut donc SUPPRIMER des lignes. Même régime que
    la création."""
    with pytest.raises(McpError, match="prepare"):
        _tool("pennylane_ledger_entry")(op="update", entry_id=42,
                                        fields={"label": "x"})
    client.update_ledger_entry.assert_not_called()


def test_les_lectures_ne_demandent_aucun_jeton(client):
    """Une garde qui déborde sur les lectures serait contournée en bloc."""
    for kwargs in ({"op": "list"}, {"op": "get", "entry_id": 1},
                   {"op": "lines", "entry_id": 1},
                   {"op": "lettered", "line_id": 1}):
        _tool("pennylane_ledger_entry")(**kwargs)


def test_le_lettrage_ne_demande_aucun_jeton(client):
    """Il est réversible : lui imposer le même palier banaliserait la garde là
    où elle compte."""
    _tool("pennylane_ledger_lettering")(op="set", line_ids=[1, 2])
    client.letter_ledger_entry_lines.assert_called_once()
