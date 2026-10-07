"""Les deux listes fermées de `recipes/outils.py` ne mentent pas : chaque outil existe au
catalogue monté, chaque op permise est une op qu'il connaît, et aucune ne détruit."""
from __future__ import annotations

import pytest

from _mcp_app import static_mcp
from oto_mcp.recipes import outils as ou


async def _servis() -> dict:
    return {t.name: t for t in await static_mcp().list_tools(run_middleware=False)}


def _ops(tool) -> set:
    props = (getattr(tool, "parameters", None) or {}).get("properties") or {}
    op = props.get("op") or {}
    enum = list(op.get("enum") or [])
    for alt in op.get("anyOf") or []:
        enum += alt.get("enum") or []
    return {str(o) for o in enum}


@pytest.mark.asyncio
async def test_chaque_outil_nomme_existe_et_ses_ops_aussi():
    servis = await _servis()
    noms = set(ou.SOUMISSIONS) | set().union(*ou.SOUMISSIONS.values()) | set(ou.POUSSEES)
    assert not noms - set(servis), sorted(noms - set(servis))
    for nom, ops in ou.POUSSEES.items():
        if ops is None:
            assert not _ops(servis[nom]), f"{nom} a une `op` : nommer ses ops permises"
        else:
            assert ops <= _ops(servis[nom]), (nom, ops - _ops(servis[nom]))


def test_aucune_op_permise_ne_detruit():
    for nom, ops in ou.POUSSEES.items():
        for op in ops or ():
            assert op in ou.OPS_ECRITURE | {"search"}, (nom, op)
