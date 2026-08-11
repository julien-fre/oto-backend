"""Dispatch `op=` des tools `sheets_*` (ADR 0047 §Amendement, appliqué au produit
`sheets` du connecteur `google` : 5 tools → 2).

Ce module n'avait AUCUN test : il passait le plat au `SheetsClient` d'oto-core, donc
une op mal câblée appellerait silencieusement la mauvaise méthode sans que rien ne
casse au boot. Et ici ce n'est pas un bug d'affichage — `write` ÉCRASE la plage visée
et `clear` en EFFACE les valeurs : une op de lecture qui atteindrait l'une des deux
détruit de la donnée utilisateur.

D'où, pour chaque op : la méthode client atteinte **et** l'absence d'appel aux voisines
dangereuses ; le refus d'une op inconnue (message qui NOMME les ops valides, aucun
client touché) ; les arguments obligatoires par op ; et les deux invariants de sûreté
câblés dans le module — le défaut de `op` est une LECTURE, et `range` n'a de valeur par
défaut que pour la lecture (une écriture/un effacement sans plage est refusé, jamais
élargi à toute la feuille).
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError

# Les méthodes du client qui MUTENT le tableau. Toute op non-mutante doit les laisser
# intactes : c'est l'assertion qui distingue « la bonne méthode a été appelée » de
# « aucune autre ne l'a été ».
WRITE_METHODS = ("write", "append", "clear")

EXPECTED_TOOLS = {"sheets_create", "sheets_spreadsheet"}


def _register():
    from fastmcp import FastMCP
    from oto_mcp.tools import sheets as S

    m = FastMCP("t")
    S.register(m)
    return m


def _tool(name: str):
    """La fonction nue derrière le tool — `async`, d'où l'`asyncio.run` des appels."""
    return asyncio.run(_register().get_tool(name)).fn


def _call(name: str, **kwargs):
    return asyncio.run(_tool(name)(**kwargs))


def _raises(name: str, **kwargs):
    with pytest.raises(McpError) as e:
        _call(name, **kwargs)
    return str(e.value)


@pytest.fixture
def client(monkeypatch):
    """Faux `SheetsClient` : on patche le résolveur du module (il fabrique le client à
    partir des credentials OAuth du user, hors de portée d'un test)."""
    from oto_mcp.tools import sheets as S

    inst = MagicMock()
    monkeypatch.setattr(S, "_client_for_user", lambda account=None: inst)
    return inst


# --- la surface elle-même ------------------------------------------------------

def test_surface_is_exactly_the_two_consolidated_tools():
    """Un tool oublié en route (ou resté en double) se voit ici, pas en prod."""
    assert {t.name for t in asyncio.run(_register()._list_tools())} == EXPECTED_TOOLS


# --- routage op → méthode client ------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("metadata", {}, "get_metadata"),
    ("read", {}, "read"),
    ("write", {"range": "A1:B2", "values": [["a"]]}, "write"),
    ("write", {"range": "A1:B2", "values": [["a"]], "append": True}, "append"),
    ("clear", {"range": "A1:B2"}, "clear"),
])
def test_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _call("sheets_spreadsheet", spreadsheet_id="sid", op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_create_routes_to_create_and_touches_nothing_else(client):
    client.create.return_value = {"id": "sid", "title": "T", "url": "u"}
    assert _call("sheets_create", title="T")["id"] == "sid"
    client.create.assert_called_once_with("T")
    for m in WRITE_METHODS:
        getattr(client, m).assert_not_called()


def test_account_is_forwarded_to_the_credential_resolver(monkeypatch):
    """Multi-compte : `account` sélectionne l'identité Google, sur les DEUX tools."""
    from oto_mcp.tools import sheets as S

    seen = []
    inst = MagicMock()
    monkeypatch.setattr(S, "_client_for_user",
                        lambda account=None: (seen.append(account), inst)[1])
    _call("sheets_create", title="T", account="a@b.c")
    _call("sheets_spreadsheet", spreadsheet_id="sid", account="d@e.f")
    assert seen == ["a@b.c", "d@e.f"]


# --- ops de LECTURE : aucune mutation ------------------------------------------

def test_default_op_is_a_read(client):
    """Un appel SANS `op` ne doit jamais écrire ni effacer : le défaut est `metadata`."""
    _call("sheets_spreadsheet", spreadsheet_id="sid")
    client.get_metadata.assert_called_once_with("sid")
    for m in WRITE_METHODS:
        getattr(client, m).assert_not_called()


def test_metadata_touches_no_write_method(client):
    _call("sheets_spreadsheet", spreadsheet_id="sid", op="metadata")
    for m in WRITE_METHODS:
        getattr(client, m).assert_not_called()


def test_read_defaults_to_the_whole_sheet_and_wraps_rows(client):
    """Contrat historique de `sheets_read` : plage omise = 'A:ZZ', retour {rows, count}."""
    client.read.return_value = [["a", "b"], ["c", "d"]]
    out = _call("sheets_spreadsheet", spreadsheet_id="sid", op="read")
    assert client.read.call_args.args == ("sid", "A:ZZ", "FORMATTED_VALUE")
    assert out == {"rows": [["a", "b"], ["c", "d"]], "count": 2}
    for m in WRITE_METHODS:
        getattr(client, m).assert_not_called()


def test_read_unformatted_switches_the_render_option(client):
    client.read.return_value = []
    _call("sheets_spreadsheet", spreadsheet_id="sid", op="read",
          range="Sheet1!A1:D20", formatted=False)
    assert client.read.call_args.args == ("sid", "Sheet1!A1:D20", "UNFORMATTED_VALUE")


# --- op="write" : ÉCRASE la plage ----------------------------------------------

def test_write_overwrites_and_never_appends_nor_clears(client):
    """`append=False` (défaut) = update, qui ÉCRASE la plage. Vérifier aussi qu'aucune
    des deux autres mutations n'est déclenchée : c'est le câblage qui peut déraper."""
    _call("sheets_spreadsheet", spreadsheet_id="sid", op="write",
          range="Sheet1!A1", values=[["x"]])
    client.write.assert_called_once_with("sid", "Sheet1!A1", [["x"]])
    client.append.assert_not_called()
    client.clear.assert_not_called()


def test_write_append_never_overwrites_nor_clears(client):
    """`append=True` = ajout APRÈS les données existantes : `write` (qui écraserait)
    ne doit surtout pas être appelée."""
    _call("sheets_spreadsheet", spreadsheet_id="sid", op="write",
          range="Sheet1!A1", values=[["x"]], append=True)
    client.append.assert_called_once_with("sid", "Sheet1!A1", [["x"]])
    client.write.assert_not_called()
    client.clear.assert_not_called()


@pytest.mark.parametrize("kwargs,missing", [
    ({"values": [["x"]]}, "range"),
    ({"range": "Sheet1!A1"}, "values"),
])
def test_write_refuses_without_its_required_args(client, kwargs, missing):
    """Pas de fallback : une écriture sans plage ne doit pas hériter du 'A:ZZ' de la
    lecture (elle écraserait la feuille entière), et sans valeurs elle n'a rien à dire."""
    msg = _raises("sheets_spreadsheet", spreadsheet_id="sid", op="write", **kwargs)
    assert missing in msg and "write" in msg
    for m in WRITE_METHODS:
        getattr(client, m).assert_not_called()


# --- op="clear" : EFFACE des cellules -------------------------------------------

def test_clear_clears_the_named_range_and_nothing_else(client):
    _call("sheets_spreadsheet", spreadsheet_id="sid", op="clear", range="Sheet1!A1:D20")
    client.clear.assert_called_once_with("sid", "Sheet1!A1:D20")
    client.write.assert_not_called()
    client.append.assert_not_called()


def test_clear_refuses_without_a_range(client):
    """Le cas qui coûte cher : `range` par défaut vaut 'A:ZZ' POUR LA LECTURE. Si
    `clear` en héritait, un appel sans plage viderait tout le tableau."""
    msg = _raises("sheets_spreadsheet", spreadsheet_id="sid", op="clear")
    assert "range" in msg and "clear" in msg
    client.clear.assert_not_called()


# --- refus d'une op inconnue ----------------------------------------------------

@pytest.mark.parametrize("op", ["nope", "delete", "METADATA", "", "Read"])
def test_unknown_op_is_refused_with_the_allowed_list(client, op):
    """Une op inconnue lève en NOMMANT les ops valides — et n'atteint aucune méthode
    du client (elle est refusée avant même que le client soit construit) : jamais un
    repli silencieux sur le défaut, que l'agent croirait honoré."""
    msg = _raises("sheets_spreadsheet", spreadsheet_id="sid", op=op)
    assert "op doit être" in msg
    for expected in ("metadata", "read", "write", "clear"):
        assert expected in msg
    assert client.method_calls == []


# --- la prose est le livrable : elle ne doit pas se perdre à la fusion ----------

def test_each_op_is_documented_with_its_warnings():
    """Le docstring est le contrat que lit le modèle. Les avertissements empiriques
    portés par les 5 anciens tools (écrasement, formatage conservé, notation A1,
    rendu formaté/brut) doivent survivre op par op à la consolidation."""
    doc = asyncio.run(_register().get_tool("sheets_spreadsheet")).fn.__doc__
    for op in ("metadata", "read", "write", "clear"):
        assert f'**"{op}"' in doc, f"op {op} non documentée"
    assert "OVERWRITES" in doc                    # write écrase la plage
    assert "keeps formatting" in doc              # clear garde le format, vide les valeurs
    assert "A1 notation" in doc                   # format de plage attendu
    assert "FORMATTED_VALUE" in doc               # formatted=True/False
