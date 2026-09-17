"""oto-backend#585 — un refus qui reconnaît la forme d'un AUTRE outil du même
domaine le nomme.

Observé le 29/08/2026 : `data_write({"op": "list"})` — la forme de `data_rows`/des
outils consolidés (ADR 0047), envoyée à `data_write`, qui n'a jamais eu de
paramètre `op`. Avant ce lot, le refus disait « champ(s) requis absent(s) :
datastore · valeur(s) refusée(s) : op (Unexpected keyword argument) », vrai mais
muet sur la vraie confusion.

⚠️ Ces bancs passent par le VRAI chemin — un outil FastMCP, l'exception qu'il lève
réellement (même patron que test_arguments_renommes_135.py)."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from oto_mcp import error_taxonomy as T
from oto_mcp import tool_confusions

_mcp = FastMCP("banc-585")


@_mcp.tool()
def data_write(datastore: str, row: dict | None = None) -> dict:
    return {"ok": True}


def _refus(**arguments) -> Exception:
    try:
        asyncio.run(_mcp.call_tool("data_write", arguments))
    except Exception as exc:  # noqa: BLE001 — c'est elle qu'on examine
        assert T._is_arg_validation_error(exc), "le vrai chemin doit mener au message"
        return exc
    raise AssertionError("l'appel aurait dû être refusé")


def test_op_sur_data_write_nomme_la_confusion_data_rows():
    msg = T._arg_error_message(_refus(datastore="v", op="list"))
    assert "`data_write` n'a pas de paramètre `op`" in msg, msg
    assert "data_rows" in msg
    assert "requis absent" not in msg, "la confusion prime sur le refus générique"
    assert "Unexpected keyword argument" not in msg


def test_le_refus_ne_depend_pas_de_la_valeur_de_op():
    """Le cas observé était `op='list'` ; la confusion (mauvais outil) est la même
    quelle que soit la valeur — `op='draft'`, `op='get'`… tous confondent le même
    couple d'outils."""
    msg_list = T._arg_error_message(_refus(datastore="v", op="list"))
    msg_get = T._arg_error_message(_refus(datastore="v", op="get"))
    assert msg_list == msg_get


def test_un_couple_non_reference_ne_declenche_rien():
    assert tool_confusions.refus_forme_dun_autre_outil("data_write", "filtre") is None
    assert tool_confusions.refus_forme_dun_autre_outil(None, "op") is None


def test_un_autre_outil_avec_la_meme_cle_nest_pas_touche():
    """La confusion est scopée à (outil, clé) — un couple non déclaré ne renvoie
    rien, même si la clé `op` existe ailleurs dans le domaine."""
    assert tool_confusions.refus_forme_dun_autre_outil("data_rows", "op") is None
