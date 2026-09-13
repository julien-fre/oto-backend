"""Les motifs de `mail_obfuscation` lisent une page NON FIABLE : chacun reste linéaire.

Le 13/09/2026, `ADRESSE_RE` (partie locale en `+` libre) rendait `search`
quadratique sur une longue suite de caractères admis ; exécutée dans un thread de
travail, la recherche tenait le GIL et a gelé le processus de prod. Mesuré avant
le correctif : 40 000 caractères → 1,08 s, ×4 à chaque doublement, donc des
minutes sur une page d'un mégaoctet ; après : 0,005 s.

Chaque entrée fait un million de caractères. Le plafond (2 s) laisse plus d'un
ordre de grandeur au-dessus du linéaire mesuré (≤ 0,14 s) et reste très loin de
ce que coûte une forme quadratique à cette taille : il ne vacille pas sur une CI
lente, et ne laisse pas passer une régression.
"""
from __future__ import annotations

import time

import pytest

from oto_mcp.tools import mail_obfuscation as M

UN_MILLION = 1_000_000
PLAFOND_S = 2.0


def _page(motif: str) -> str:
    return (motif * (UN_MILLION // len(motif) + 1))[:UN_MILLION]


def _duree(fonction, page: str) -> float:
    debut = time.perf_counter()
    fonction(page)
    return time.perf_counter() - debut


def test_chercher_une_adresse_reste_lineaire():
    # La forme de l'incident : une longue suite de caractères admis, sans `@`.
    assert _duree(M.contient_adresse, "a" * UN_MILLION) < PLAFOND_S


# Chaque page ne rougit que sur la forme qu'elle vise (`mailto:` sans entité,
# balise joomla jamais fermée, nom d'attribut sans `=`) : une page que l'ancien
# motif traversait déjà en temps linéaire ne prouverait rien.
@pytest.mark.parametrize("page", [
    pytest.param(_page("mailto:"), id="mailto-sans-entite"),
    pytest.param(_page("<joomla-hidden-mail "), id="joomla-jamais-fermee"),
    pytest.param(_page("<joomla-hidden-mail " + "a" * 4096 + ">"), id="joomla-nom-sans-egal"),
])
def test_lire_une_page_piegee_reste_lineaire(page):
    assert _duree(M.lire, page) < PLAFOND_S


def test_les_bornes_reconnaissent_toujours_une_adresse():
    assert M.contient_adresse("Écrire à contact@exemple.fr pour adhérer.")
    assert M.ADRESSE_RE.fullmatch("a" * 64 + "@exemple.fr")
    # RFC 5321 : une partie locale ne dépasse pas 64 caractères.
    assert not M.ADRESSE_RE.fullmatch("a" * 65 + "@exemple.fr")


def test_les_motifs_obfusques_se_decodent_toujours():
    page = (
        '<joomla-hidden-mail is-link="1" first="YnVyZWF1" last="ZXhlbXBsZS5vcmc=">'
        "x</joomla-hidden-mail>"
        '<a href="mailto:&#99;&#111;&#110;&#116;&#97;&#99;&#116;&#64;&#101;&#120;&#101;'
        '&#109;&#112;&#108;&#101;&#46;&#102;&#114;?subject=x">écrire</a>'
        '<a href="mailto:clair@exemple.fr">clair</a>'
    )
    assert M.lire(page) == {
        "adresses": ["bureau@exemple.org", "contact@exemple.fr"],
        "motifs": ["joomla-hidden-mail", "mailto en entités HTML"],
    }
