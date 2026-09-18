"""oto-backend#585 — `data_write({"op": "list"})` : la forme d'un autre outil, envoyée
à `data_write`, qui n'a pas de paramètre `op`.

Le refus GÉNÉRIQUE y répond, sans registre de confusions : il nomme la clé en trop, le
champ requis absent, et pointe vers le schéma de l'outil. Une confusion d'outil est un
défaut de documentation ; le refus n'a pas à la reconnaître au cas par cas, il doit
seulement ne rien taire de ce qu'il a reçu. Le message cité par l'issue (« namespace
Missing required argument ») date d'avant ce refus.

⚠️ Ce banc passe par le VRAI chemin — un outil FastMCP, l'exception qu'il lève
réellement (même patron que test_arguments_renommes_135.py)."""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from oto_mcp import error_taxonomy as T

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


def test_op_list_sur_data_write_nomme_la_cle_en_trop_et_pointe_le_schema():
    msg = T._arg_error_message(_refus(op="list"))
    assert "champ(s) non reconnu(s) : op" in msg, msg
    assert "champ(s) requis absent(s) : datastore" in msg, msg
    assert 'oto_tool_schema(name="data_write")' in msg, msg
    assert "Unexpected keyword argument" not in msg, msg
