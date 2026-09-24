"""oto#135 — une clé inconnue qui RESSEMBLE à un paramètre de l'outil rend le geste à
rejouer, avec ce paramètre.

Le refus de signature nommait déjà la clé en trop (`test_arguments_renommes_135.py`) ;
il laissait l'agent chercher, dans le schéma entier, ce qu'il avait voulu écrire. Une
faute de frappe (`rows_data`, `datastor`) a une destination : le refus la dit, et le
paramètre ainsi mal écrit n'est plus annoncé « requis absent ».

⚠️ Ces bancs passent par le VRAI chemin — un outil FastMCP, l'exception qu'il lève, et
le middleware d'enveloppe qui lit les paramètres de l'outil dans le catalogue.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp import error_taxonomy as T
from oto_mcp.middleware.error_envelope import ErrorEnvelopeMiddleware

_mcp = FastMCP("banc-135-proche")


@_mcp.tool()
def data_write(datastore: str, row: dict | None = None, rows: list | None = None) -> dict:
    return {"ok": True}


def _refus(**arguments) -> Exception:
    try:
        asyncio.run(_mcp.call_tool("data_write", arguments))
    except Exception as exc:  # noqa: BLE001 — c'est elle qu'on examine
        return exc
    raise AssertionError("l'appel aurait dû être refusé")


def test_le_parametre_le_plus_proche_est_rendu_avec_le_geste():
    msg = T._arg_error_message(_refus(datastore="v", rows_data=[{}]),
                               ["datastore", "row", "rows"])
    assert "champ(s) non reconnu(s) : rows_data" in msg, msg
    assert "Rejoue `data_write(…)` avec `rows=` à la place de `rows_data`" in msg, msg


def test_un_parametre_requis_MAL_ECRIT_n_est_plus_dit_absent():
    msg = T._arg_error_message(_refus(datastor="v"), ["datastore", "row", "rows"])
    assert "`datastore=` à la place de `datastor`" in msg, msg
    assert "requis absent" not in msg, msg


def test_sans_parent_plausible_rien_n_est_invente():
    msg = T._arg_error_message(_refus(datastore="v", op="list"),
                               ["datastore", "row", "rows"])
    assert "Rejoue" not in msg and "non reconnu(s) : op" in msg, msg


def test_le_middleware_lit_les_parametres_de_l_outil():
    """De bout en bout : l'enveloppe trouve l'outil nommé par l'erreur et ses
    paramètres, sans qu'aucun appelant ne les lui passe."""
    m = FastMCP("banc-135-enveloppe")
    m.add_middleware(ErrorEnvelopeMiddleware())

    @m.tool()
    def data_write(datastore: str, rows: list | None = None) -> dict:  # noqa: F811
        return {"ok": True}

    with pytest.raises(Exception) as e:
        asyncio.run(m.call_tool("data_write", {"datastore": "v", "rows_data": []}))
    assert "Rejoue `data_write(…)` avec `rows=` à la place de `rows_data`" in str(e.value)
