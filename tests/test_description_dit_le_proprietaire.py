"""La description servie nomme le PROPRIÉTAIRE que le code donne vraiment (04/09/2026).

`data_create_namespace` annonçait à l'agent, dans le texte relu à chaque appel, un
identifiant « unique **per user** » — pendant que `DatastorePg.create_namespace`
posait le tableau chez l'**org active**, lisible de tous ses membres. Le geste le plus
banal du produit créait donc du contenu d'org, et son instruction enseignait l'inverse.

Ce banc ne relit pas la phrase : il lit le DÉFAUT réel dans le code, puis exige que la
description servie nomme ce défaut-là. Écrit dans l'autre sens, il se périmerait au
premier changement de comportement — en restant vert, ce qui est le pire des cas.

⚠️ La description est lue sur le MONTAGE (`register` → `tools/list`), jamais sur le
`__doc__` de la fonction : c'est FastMCP qui décide de ce qui part sur le fil, et un
banc posé sur le docstring dirait que le texte est bien écrit sans rien dire de ce que
le modèle reçoit (même piège que `test_param_description_servie.py`).
"""
from __future__ import annotations

import asyncio
import inspect

from fastmcp import FastMCP


def _descriptions_servies() -> dict:
    from oto_mcp.tools import datastore as surface
    mcp = FastMCP("sonde")
    surface.register(mcp)

    async def _lire():
        return {t.name: (t.description or "")
                for t in await mcp.list_tools(run_middleware=False)}

    return asyncio.run(_lire())


def test_le_defaut_de_propriete_du_datastore_est_bien_l_ORG():
    """Le fait, lu dans le code et non supposé — si ce banc tombe, c'est le
    COMPORTEMENT qui a changé, et la description du suivant doit changer avec lui."""
    from oto_mcp.datastore import core

    src = inspect.getsource(core.DatastorePg._default_owner)
    assert '"org"' in src, (
        "le défaut de propriété d'un tableau n'est plus l'org : mets à jour la "
        "description servie de `data_create_namespace` AVANT de rendre ce banc vert.")


def test_data_create_namespace_DIT_que_le_tableau_est_celui_de_l_org():
    """Ce que l'agent lit doit être ce qui se passe. « unique per user » était
    exactement l'inverse, et c'est une phrase qui rassure au moment où il faudrait
    hésiter."""
    d = _descriptions_servies()["data_create_namespace"]
    assert "ORG" in d, "le propriétaire réel doit être nommé, en clair"
    assert "EVERY member" in d, "et la population qui lira, aussi"
    assert "per user" not in d.replace("per owner", ""), (
        "la formule qui mentait ne doit pas revenir : le tableau n'est pas personnel")


def test_le_texte_dit_aussi_ou_aller_pour_un_espace_a_soi():
    """Un avertissement sans issue ne fait qu'inquiéter. La phrase nomme la sortie —
    ici la route REST avec `owner: {type: "user"}`, la seule qui existe aujourd'hui."""
    d = _descriptions_servies()["data_create_namespace"]
    assert 'owner: {type: "user"}' in d
