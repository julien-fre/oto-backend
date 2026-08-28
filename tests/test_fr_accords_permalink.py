"""#343 — le breaking permalien FOD (#335) traverse fr_accords_get.

Le service FOD sert désormais `permalien` (data.oto.zone, vérifiable, 404
franc) + `lien_construit` (Légifrance, patron best-effort) et `source_url` a
DISPARU des fonds accords/conventions. Le backend est passthrough partout sauf
la projection de la fiche enrichie, qui recopiait le champ mort et mangeait
les deux vivants — 2e occurrence en 24 h de la classe « projection qui ment
par omission » (#341, même fichier).

Les fonds juris/codes GARDENT `source_url` (hors périmètre du breaking,
incohérence assumée côté service, documentée dans #335) — un test fige cette
frontière pour que le lot ne déborde pas, aujourd'hui ni plus tard.
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


_ACCO = {"id": "ACCOTEXT000012345678", "numero": "T07523067890",
         "titre": "Accord d'entreprise — témoin"}

# La réponse texte POST-breaking : les deux champs neufs, plus de source_url.
_TEXT = {"texte": "Article 1 — …", "texte_chars": 14, "tronque": False,
         "next_offset": None,
         "permalien": "https://data.oto.zone/acco/ACCOTEXT000012345678",
         "lien_construit": "https://www.legifrance.gouv.fr/acco/id/ACCOTEXT000012345678"}


@pytest.fixture()
def tools(monkeypatch):
    monkeypatch.setattr("oto_mcp.fod.fr.get_acco", lambda x: dict(_ACCO))
    monkeypatch.setattr("oto_mcp.fod.ccn.accords_text",
                        lambda x, offset=0: dict(_TEXT))
    from oto_mcp.tools import fr
    reg = _Reg()
    fr.register(reg)
    return reg.tools


def test_la_fiche_enrichie_sert_les_deux_liens(tools):
    out = tools["fr_accords_get"](id_or_numero="T07523067890", include_text=True)
    assert out["permalien"] == _TEXT["permalien"], \
        "le lien vérifiable doit traverser la projection (#343)"
    assert out["lien_construit"] == _TEXT["lien_construit"]


def test_le_champ_mort_ne_sort_plus(tools):
    out = tools["fr_accords_get"](id_or_numero="T07523067890", include_text=True)
    assert "source_url" not in out, \
        "source_url a disparu du fond : le servir à null est un mensonge de plus"


def test_les_docstrings_disent_les_champs_vivants(tools):
    """Le contrat LLM : une docstring qui promet un champ disparu fait chercher
    l'agent au mauvais endroit — même classe que la projection."""
    doc = tools["fr_accords_text"].__doc__ or ""
    assert "source_url" not in doc
    assert "permalien" in doc and "lien_construit" in doc


def test_ccn_article_dit_les_champs_vivants():
    from oto_mcp.tools import droit
    reg = _Reg()
    droit.register(reg)
    doc = reg.tools["ccn_article"].__doc__ or ""
    assert "source_url" not in doc
    assert "permalien" in doc and "lien_construit" in doc


def test_juris_et_codes_gardent_source_url():
    """La FRONTIÈRE du lot : ces fonds sont hors périmètre du breaking FOD
    (#335, incohérence assumée côté service) — leur contrat ne change pas."""
    from oto_mcp.tools import droit
    reg = _Reg()
    droit.register(reg)
    assert "source_url" in (reg.tools["juris_decision"].__doc__ or "")
