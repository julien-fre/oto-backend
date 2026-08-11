"""Lot M2 (#287) — le corps se parse en blocs, une fois et bien.

Le parseur est de la **logique pure** : il se teste sans base, et c'est là qu'il faut
le serrer. Sa propriété centrale n'est pas « le découpage est joli » mais **la
concaténation des blocs rend le corps au caractère près** — un parseur qui prétend
comprendre le markdown finit par en perdre un bout, et cette perte n'est visible
qu'au moment où quelqu'un ne reconnaît plus sa page.

L'écriture en base (rejouabilité, marqueur, adresses stables) est exercée contre un
vrai PostgreSQL dans `test_nodes_m2_conversion.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp.db.blocks import (CODE, TEXT, block_public_id, code_of, parse_blocks,
                               render_blocks)

CORPUS = [
    "",
    "un paragraphe",
    "un paragraphe\n",
    "deux\n\nparagraphes\n",
    "# Titre\n\nDu texte.\n",
    "# Titre\ncollé au titre\n",
    "## A\n\n1\n\n## B\n\n2\n",
    "avant\n\n```python\nprint('x')\n```\n\naprès\n",
    "```\nsans langue\n```",
    "```py\nclôture jamais fermée\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\n",
    "- un\n- deux\n- trois\n",
    "- un\n\n- deux séparé\n",
    "texte avec un [lien](http://x) et de l'*emphase* au milieu\n",
    "\n\ndu blanc devant\n",
    "des espaces en fin   \n\n\net trois lignes vides\n",
    "```md\n# un titre DANS du code\n\net une ligne vide\n```\n",
    "~~~\ntildes\n~~~\n",
    "````\n```\nimbriqué\n```\n````\n",
    "windows\r\n\r\nline endings\r\n",
    "accents éàü et emoji 🚀\n",
]


@pytest.mark.parametrize("md", CORPUS, ids=range(len(CORPUS)))
def test_the_body_survives_the_parse_character_for_character(md):
    """L'invariant, et la raison d'être de `props->>'md'` : chaque bloc porte sa
    SOURCE exacte. C'est ce qui rend le découpage vérifiable au lieu d'être cru."""
    assert render_blocks(parse_blocks(md)) == md


def test_an_empty_body_has_no_blocks():
    assert parse_blocks("") == []


def test_code_is_isolated_from_text():
    """0054-D2 : le code est un bloc à lui seul. Le noyer dans un paragraphe
    rendrait impossible de l'adresser — et c'est le premier bloc qu'un agent voudra
    remplacer sans toucher au reste."""
    blocks = parse_blocks("avant\n\n```python\nprint('x')\n```\n\naprès\n")
    assert [b["type"] for b in blocks] == [TEXT, CODE, TEXT]
    assert blocks[1]["lang"] == "python"
    assert code_of(blocks[1]) == "print('x')\n"


def test_a_fence_without_info_has_no_lang():
    """Pas de `lang` inventé : une clôture nue n'annonce pas de langue, et écrire
    `"lang": ""` ferait répondre vrai à un test de présence."""
    (block,) = parse_blocks("```\nsans langue\n```\n")
    assert block["type"] == CODE and "lang" not in block


def test_markdown_inside_a_fence_is_not_parsed():
    """Le piège classique : un titre ou une ligne vide DANS du code ne coupe rien.
    Sinon un extrait de markdown documenté se retrouve éparpillé en quatre blocs."""
    blocks = parse_blocks("```md\n# pas un titre\n\net pas une coupure\n```\n")
    assert [b["type"] for b in blocks] == [CODE]


def test_headings_open_their_own_block():
    """Le grain reproduit l'outline du document — ce qu'attend n'importe quel
    éditeur de blocs, et ce qui permettra de servir un plan sans re-parser."""
    blocks = parse_blocks("# Titre\n\nDu texte.\n\n## Sous-titre\nCollé.\n")
    assert [b["md"] for b in blocks] == [
        "# Titre\n\n", "Du texte.\n\n", "## Sous-titre\n", "Collé.\n"]


def test_a_heading_is_still_text():
    """Pas de genre « titre » : 0054-D2 ne connaît que texte/code/image/référence.
    Un genre de plus serait un concept de plus — exactement ce que le chantier
    retire."""
    assert {b["type"] for b in parse_blocks("# T\n\ntexte\n")} == {TEXT}


def test_inline_never_breaks_a_paragraph():
    """0054-D2, tranché le 05/08 : l'inline NU (lien, emphase, mention sans
    attribut) reste du markup dans le bloc texte. Couper un paragraphe en trois
    parce qu'il contient un lien serait une régression de lecture."""
    md = "texte avec un [lien](http://x) et de l'*emphase* au milieu\n"
    assert [b["md"] for b in parse_blocks(md)] == [md]


def test_blank_lines_stay_with_the_block_above():
    """Le séparateur appartient au bloc du dessus : sans cette règle on fabrique des
    blocs de blanc, qui n'ont ni contenu ni raison d'être adressés."""
    blocks = parse_blocks("un\n\n\ndeux\n")
    assert [b["md"] for b in blocks] == ["un\n\n\n", "deux\n"]
    assert all(b["md"].strip() for b in blocks)


def test_blank_after_a_fence_extends_the_code_block():
    """Même règle, au cas où elle produirait un bloc vide : le blanc qui suit une
    clôture prolonge le bloc de code plutôt que d'en ouvrir un creux."""
    blocks = parse_blocks("```\nx\n```\n\ntexte\n")
    assert [b["type"] for b in blocks] == [CODE, TEXT]
    assert blocks[0]["md"] == "```\nx\n```\n\n"


def test_an_unclosed_fence_runs_to_the_end():
    """Ce que fait aussi un rendu markdown. L'alternative — retomber en texte —
    couperait le contenu du bloc sur ses lignes vides, donc le déformerait."""
    blocks = parse_blocks("```py\njamais fermé\n\nencore\n")
    assert [b["type"] for b in blocks] == [CODE]


# ── l'adresse d'un bloc ──────────────────────────────────────────────────────

def test_a_block_address_is_stable_and_scoped_to_its_node():
    """Dérivée de (nœud, rang) : rejouer le parse ne fabrique pas d'identifiants
    neufs, donc pas de doublons — et l'adresse survit à la réécriture du texte, ce
    qui est le propre d'une adresse."""
    assert block_public_id("nod_a", 0) == block_public_id("nod_a", 0)
    assert block_public_id("nod_a", 0) != block_public_id("nod_a", 1)
    assert block_public_id("nod_a", 0) != block_public_id("nod_b", 0)
    assert block_public_id("nod_a", 0).startswith("blk_")


def test_code_of_only_answers_for_code():
    (text,) = parse_blocks("juste du texte\n")
    assert code_of(text) is None
