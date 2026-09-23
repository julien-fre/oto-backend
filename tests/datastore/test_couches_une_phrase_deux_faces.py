"""La phrase des couches est UNE, servie par les deux faces d'écriture (oto#91).

La face MCP (`data_write`) portait le vocabulaire des couches ; la face REST
(`POST`/`PATCH …/rows`) n'en avait qu'un renvoi vers un guide. Deux textes pour une
même vérité, dont un sans le vocabulaire : c'est ce qui avait fait écrire la
provenance dans `origine`. La phrase vit dans `couches.DESCRIPTION_ECRITURE` et chaque
face l'insère — ce test tient les trois textes servis contre elle.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import registry
from oto_mcp.datastore import couches


@pytest.mark.parametrize("cle", ["me.datastore.append_row", "me.datastore.update_row"])
def test_la_face_REST_sert_la_phrase_des_couches(cle):
    import oto_mcp.capabilities  # noqa: F401 — peuple le registre
    assert couches.DESCRIPTION_ECRITURE in registry.by_key(cle).description


def test_la_face_MCP_sert_la_meme_phrase():
    from oto_mcp.tools import datastore as t_ds

    servie = _description_servie("data_write")
    assert couches.DESCRIPTION_ECRITURE in servie
    assert t_ds._MARQUE_COUCHES not in servie


def _description_servie(nom: str) -> str:
    import asyncio

    from fastmcp import FastMCP
    from oto_mcp.tools import datastore as t_ds

    m = FastMCP("t")
    t_ds.register(m)
    return asyncio.run(m.get_tool(nom)).description
