"""La FORME d'un schéma atteint le client MCP (table 966, 14/10/2026).

Trois colonnes `enum` portaient leur liste en `meta.options` : `data_set_schema`
ne disait sa grammaire qu'en prose, sans y nommer `options`, et renvoyait les
annotations vers `meta`. Le paramètre porte désormais la forme — chaque clé admise
est une propriété nommée, DÉRIVÉE de `schema_keys.NIVEAUX`.

Lu sur le MONTAGE (`register` → `tools/list`), jamais sur l'annotation : c'est
FastMCP qui décide de ce qui part sur le fil (même parti que
`test_param_description_servie`).
"""
from __future__ import annotations

import asyncio
import json

from fastmcp import FastMCP

from oto_mcp.capabilities import _mcp_adapter, registry
from oto_mcp.datastore import schema_keys as K

#: Ce que la forme coûte, par outil, en JSON compact. Elle part avec l'outil à chaque
#: tour de chaque agent (`test_call_axes_budget`) : la rallonger doit casser ici et se
#: discuter, pas glisser. Mesuré à la pose (14/10/2026) : 876 c. la colonne.
_BUDGET_FORME = 1000


def _servis() -> dict:
    from oto_mcp.tools import datastore as surface
    m = FastMCP("sonde")
    surface.register(m)
    _mcp_adapter.register(m, registry.CAPABILITIES)

    async def lire():
        out = {}
        for nom in ("data_set_schema", "data_create_datastore", "data_patch_schema"):
            outil = await m.get_tool(nom)
            out[nom] = outil.parameters
        return out

    return asyncio.run(lire())


def _sans_null(prop: dict) -> dict:
    """`Optional[...]` sert un `anyOf` avec `null` : la forme est l'autre branche."""
    for branche in prop.get("anyOf", [prop]):
        if branche.get("type") != "null":
            return branche
    raise AssertionError(prop)


def _colonne(nom: str, params: dict) -> dict:
    if nom == "data_patch_schema":
        return _sans_null(params["properties"]["fields"])["items"]
    return _sans_null(params["properties"]["schema"])["properties"]["fields"]["items"]


def test_chaque_cle_admise_d_une_colonne_est_une_propriete_servie():
    for nom, params in _servis().items():
        props = _colonne(nom, params)["properties"]
        assert set(props) == K.ADMISES["champ"], nom
        assert props["options"]["type"] == "array", nom


def test_la_tete_et_l_element_sont_servis_aussi():
    servis = _servis()
    tete = _sans_null(servis["data_set_schema"]["properties"]["schema"])["properties"]
    assert set(tete) == K.ADMISES["tete"]
    of = _colonne("data_set_schema", servis["data_set_schema"])["properties"]["of"]
    assert set(of["properties"]) == K.ADMISES["element"], "une multisélection voit options"


def test_la_forme_reste_OUVERTE():
    """Fermée, un client strict refuserait `read_only` avant le serveur — et l'agent
    perdrait « voulais-tu `readonly` ? »."""
    for nom, params in _servis().items():
        # La TÊTE des paramètres de l'outil est fermée par FastMCP, et c'est son
        # affaire ; ce qui se juge ici est la forme du schéma déclaré.
        cle = "fields" if nom == "data_patch_schema" else "schema"
        brut = json.dumps(params["properties"][cle])
        assert '"additionalProperties": false' not in brut, nom


def test_la_forme_tient_son_budget():
    for nom, params in _servis().items():
        taille = len(json.dumps(_colonne(nom, params), separators=(",", ":")))
        assert taille <= _BUDGET_FORME, (nom, taille)
