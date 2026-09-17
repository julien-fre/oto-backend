"""La césure conditionnelle (U+00AD) ne voyage plus jusqu'à l'agent (oto#208).

Certains CMS la posent pour couper les mots. Invisible à l'écran, elle arrivait au
milieu des mots dans le texte servi par `serper_scrape` et par le lecteur de pages —
noms propres compris. Mesuré le 12/09/2026 : 5 pages sur 266, et un nom de personne
recopié par un agent avec une lettre fautive à l'endroit de la coupure.

⚠️ **Écart assumé avec la demande** : l'issue voulait un banc sur une page réelle
capturée. Ce banc exerce un fragment construit à la forme d'un CMS (`&shy;` et le
caractère littéral) — aucune capture n'était disponible sans relancer un scrape
facturé. La garde qui compte, le vrai chemin de l'outil, est exercée.

Éprouvé rouge le 2026-09-16 : l'appel à `cesures` retiré de `serper_scrape` ⟹ le
test du vrai chemin rend `Stra\\xadnu\\xadmun\\xaddu`.
"""
from __future__ import annotations

import pytest

from oto_mcp.tools import cesures, web

NOM_COUPE = "Stra­nu­mun­du"


def test_le_lecteur_de_pages_retire_l_ENTITE_html():
    """La forme la plus courante chez un CMS : l'entité, que le parseur convertit."""
    texte, _ = web.extract_text("<p>Stra&shy;nu&shy;mun&shy;du éditions</p>")
    assert texte == "Stranumundu éditions"


def test_le_lecteur_de_pages_retire_le_caractere_LITTERAL():
    texte, _ = web.extract_text(f"<p>{NOM_COUPE} éditions</p>")
    assert "­" not in texte and "Stranumundu" in texte


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco(a[0]) if a and callable(a[0]) else deco


@pytest.fixture()
def scrape(monkeypatch):
    """Le VRAI `serper_scrape`, client amont remplacé : la garde se juge là où le
    texte sort, pas sur la fonction utilitaire seule."""
    class _Client:
        def __init__(self, *a, **k):
            ...

        def scrape_page(self, url, include_markdown=True, timeout_s=None):
            return {"markdown": f"# {NOM_COUPE}\n\nContact : {NOM_COUPE}",
                    "text": f"{NOM_COUPE} texte", "metadata": {"title": "t"}}

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p: ("k", False))
    from oto_mcp.tools import serper
    reg = _Reg()
    serper.register(reg)
    return reg.tools["serper_scrape"]


def test_serper_scrape_ne_sert_plus_la_cesure(scrape):
    res = scrape(url="https://example.test/page", format="markdown")
    assert "­" not in res["markdown"]
    assert "Stranumundu" in res["markdown"]


def test_le_format_both_nettoie_les_DEUX_representations(scrape):
    res = scrape(url="https://example.test/page", format="both")
    assert "­" not in res.get("markdown", "") and "­" not in res.get("text", "")


def test_seule_la_cesure_est_retiree_pas_les_invisibles_NON_MESURES():
    """L'issue le demande : ne retirer que ce qui est constaté. Un espace sans chasse
    (U+200B) n'a pas été mesuré sur ces pages — il n'est pas touché."""
    assert cesures.retirer("a​b­c") == "a​bc"


def test_un_texte_sans_cesure_revient_identique():
    s = "Rien à retirer ici."
    assert cesures.retirer(s) is s
