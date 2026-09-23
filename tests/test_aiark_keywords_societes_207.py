"""`account.keywords` est REFUSÉ sur `linkedin_aiark_search(op="companies")` (otomata-tech/oto#207).

Différentiel mesuré le 23/09/2026 (`size=1`, location France + employeeSize 11-50) :
témoin 131 281 ; `keywords` en liste nue, en `any.include` et sous le wrapper SMART
→ 131 281 chacun, au même premier id. Aucune forme ne mord : la base entière
revenait présentée comme un résultat filtré, et la vue par défaut retire `keywords`
des sociétés rendues, si bien que l'agent ne pouvait même pas le constater.

Le refus est LIMITÉ au point d'accès sociétés : sur la recherche de personnes,
`account.keywords` n'a pas été mesuré. Outil appelé tel qu'il est servi, seul
`requests.request` d'oto-core est doublé — aucun appel réel à AI Ark."""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, patch

import pytest

from oto_mcp.mcp_errors import McpError

_PAGE = {"content": [], "totalElements": 0, "totalPages": 0}
_BASE = {"location": {"any": {"include": ["France"]}},
         "employeeSize": {"type": "RANGE", "range": [{"start": 11, "end": 50}]}}
_FORMES = [
    ["packaging"],
    {"any": {"include": ["packaging"]}},
    {"any": {"include": {"mode": "SMART", "content": ["packaging"]}}},
]


def _appeler(args):
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import aiark

    reponse = MagicMock(status_code=200, ok=True, content=b"{}")
    reponse.json.return_value = _PAGE

    async def go():
        m = FastMCP("t")
        aiark.register(m)
        outil = next(t for t in await m._list_tools() if t.name == "linkedin_aiark_search")
        with patch("oto.tools.aiark.client.requests.request", return_value=reponse) as req, \
             patch.object(access, "resolve_api_key", return_value=("k", False)):
            try:
                await outil.run(args)
                return None, req
            except Exception as e:                      # noqa: BLE001 — objet du banc
                return e, req
    return asyncio.run(go())


@pytest.mark.parametrize("forme", _FORMES, ids=["liste", "any_include", "smart"])
def test_keywords_est_refuse_sur_les_societes_sous_toute_forme(forme):
    exc, req = _appeler({"op": "companies", "size": 1,
                         "account": {**_BASE, "keywords": forme}})
    assert isinstance(exc, McpError), f"refus attendu, obtenu {exc!r}"
    msg = str(exc)
    assert "account.keywords" in msg and "lookalike_domains" in msg, (
        "le refus doit nommer le filtre et le remplaçant")
    assert not req.called, "aucun crédit ne doit partir sur un filtre mort"


def test_les_filtres_qui_mordent_passent():
    exc, req = _appeler({"op": "companies", "size": 1, "account": dict(_BASE)})
    assert exc is None and req.called


def test_la_recherche_de_personnes_n_est_pas_touchee():
    """Non mesuré côté personnes : le refus ne doit PAS s'y étendre."""
    exc, req = _appeler({"op": "people", "size": 1,
                         "account": {**_BASE, "keywords": ["packaging"]}})
    assert exc is None and req.called


def test_la_description_ne_promet_plus_keywords():
    from oto_mcp.tools import aiark
    src = inspect.getsource(aiark)
    assert "foundedYear, technologies, keywords, funding" not in src
    assert "`website` and `linkedin_url`" in src
    assert "`domain`, `employeeSize`, `location`" in src
