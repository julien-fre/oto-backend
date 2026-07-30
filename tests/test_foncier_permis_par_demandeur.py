"""foncier_permis_search — filtre par demandeur SIREN/SIRET (feedback #323).

Le tool n'acceptait que la géo : « les permis de cette société » imposait de
paginer une commune entière puis de trier côté client (295 permis logements +
260 locaux pour la seule Amiens, réponse rendue incomplète). SIREN_DEM étant
filtrable côté serveur DiDo, une recherche par société est nationale et tient en
une requête — vérifié en live : 142 permis pour le SIREN 585980022.
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


@pytest.fixture()
def permis_search(monkeypatch):
    """Enregistre les tools foncier avec un client Sit@del qui capture ses kwargs."""
    calls = []

    class _Sitadel:
        def search(self, kind, **kw):
            calls.append({"kind": kind, **kw})
            return {"total": 142, "page": 1, "page_size": kw.get("page_size"), "permis": []}

    from oto_mcp import fod_foncier
    from oto_mcp.tools import foncier

    monkeypatch.setattr(fod_foncier, "sitadel", _Sitadel())
    reg = _Reg()
    foncier.register(reg)
    return reg.tools["foncier_permis_search"], calls


def test_siren_alone_is_a_valid_scope(permis_search):
    """Le cœur du correctif : plus besoin de commune ni de département."""
    fn, calls = permis_search
    out = fn(siren="585980022")
    assert calls[0]["siren"] == "585980022"
    assert calls[0]["communes"] is None and calls[0]["dept"] is None
    assert out["total"] == 142


def test_siret_alone_is_a_valid_scope(permis_search):
    fn, calls = permis_search
    fn(siret="58598002200012")
    assert calls[0]["siret"] == "58598002200012"


def test_siren_combines_with_geography(permis_search):
    fn, calls = permis_search
    fn(code_commune="80021", siren="585980022", kind="locaux")
    assert calls[0]["communes"] == "80021" and calls[0]["siren"] == "585980022"
    assert calls[0]["kind"] == "locaux"


def test_no_scope_at_all_still_refuses(permis_search):
    """Le garde-fou anti-scan national reste — il accepte juste un scope de plus."""
    fn, _ = permis_search
    with pytest.raises(ValueError) as e:
        fn()
    assert "siren" in str(e.value)
