"""oto#206 — une famille BODACC inconnue est REFUSÉE, jamais un zéro silencieux.

Aucun étage en aval (service de données, lib `france-opendata`) ne valide `famille` :
la valeur part telle quelle dans la requête, et une valeur que le champ `familleavis`
ne porte pas y rend zéro annonce. Mesuré côté appelant : `famille="Modifications
diverses"` — le LIBELLÉ que la sortie sert, recopié en entrée — rendait
`annonces_total: 0`, lu comme « aucune de ces sociétés n'a eu de modification ».

Banc : le VRAI `register` de `tools/fr.py`, proxies FOD stubés ; le stub BODACC
enregistre ce qui lui parvient, pour prouver qu'un refus ne part pas en amont.
"""
from __future__ import annotations

import pytest


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


class _Bodacc:
    def __init__(self):
        self.appels = []

    def search_by_siren(self, siren, famille=None, limit=20):
        self.appels.append(("unitaire", famille))
        return {"results": [], "total_count": 0}

    def search_batch(self, sirens, famille=None):
        self.appels.append(("lot", famille))
        return {"annonces": [], "synthese": {"annonces_total": 0}}


class _Noop:
    def __init__(self, *a, **k): ...


@pytest.fixture()
def outils(monkeypatch):
    bodacc = _Bodacc()
    for nom in ("entreprises", "inpi", "egapro"):
        monkeypatch.setattr(f"oto_mcp.fod.fr.{nom}", _Noop())
    monkeypatch.setattr("oto_mcp.fod.fr.bodacc", bodacc)
    monkeypatch.setattr("oto.tools.sirene.SireneClient", _Noop)
    from oto_mcp.tools import fr
    reg = _Reg()
    fr.register(reg)
    reg.bodacc = bodacc
    return reg


@pytest.mark.parametrize("outil,args", [
    ("fr_events_batch", {"sirens": ["552032534"]}),
    ("fr_events", {"siren": "552032534"}),
])
@pytest.mark.parametrize("famille", ["Modifications diverses", "depot", "procedures"])
def test_une_famille_inconnue_est_refusee_en_nommant_les_admises(outils, outil, args,
                                                                  famille):
    from oto_mcp.mcp_errors import McpError
    with pytest.raises(McpError) as e:
        outils.tools[outil](famille=famille, **args)
    msg = str(e.value)
    assert repr(famille) in msg
    for admise in ("collective", "modification", "vente", "dpc"):
        assert admise in msg
    assert outils.bodacc.appels == [], "un refus ne doit rien envoyer en amont"


@pytest.mark.parametrize("famille", [None, "collective", "modification", "dpc"])
def test_une_famille_admise_ou_omise_passe_telle_quelle(outils, famille):
    outils.tools["fr_events_batch"](sirens=["552032534"], famille=famille)
    outils.tools["fr_events"](siren="552032534", famille=famille)
    assert outils.bodacc.appels == [("lot", famille), ("unitaire", famille)]


@pytest.mark.parametrize("outil", ["fr_events_batch", "fr_events"])
def test_les_familles_admises_sont_DECLAREES_au_schema(outils, monkeypatch, outil):
    """L'agent doit les lire AVANT d'appeler, pas les découvrir par un refus : le
    schéma SERVI (celui que FastMCP publie) porte l'énumération."""
    import asyncio
    import json
    from fastmcp import FastMCP
    from oto_mcp.tools import fr
    mcp = FastMCP("banc-206")
    fr.register(mcp)
    tool = asyncio.run(mcp.get_tool(outil))
    schema = json.dumps(tool.parameters["properties"]["famille"])
    for admise in ("collective", "conciliation", "creation", "divers", "dpc",
                   "immatriculation", "modification", "radiation",
                   "retablissement_professionnel", "vente"):
        assert f'"{admise}"' in schema
