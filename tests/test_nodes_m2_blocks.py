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

import json

import pytest

from oto_mcp.db.blocks import (CODE, TEXT, code_of, parse_blocks,
                               render_blocks, write_node_blocks)

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

# ⚠️ RETOURNÉ le 19/08 (#362) : un test affirmait ici que l'adresse d'un bloc
# était DÉRIVÉE de (nœud, rang) — l'identité positionnelle qui faisait qu'un
# paragraphe inséré en tête ré-identifiait TOUS les blocs en dessous (toute
# référence externe cassait au premier réordonnancement). L'identité est
# désormais un TIRAGE à la première projection, CONSERVÉ par rapprochement —
# les tests ci-dessous décrivent le monde d'après.

class _FauxConn:
    """Le strict nécessaire de write_node_blocks : SELECT des existants,
    DELETE, INSERT en lot, UPDATE du marqueur."""

    def __init__(self):
        self.blocs: list[tuple] = []   # (public_id, node_id, position, type, props)

    def execute(self, sql, params=None):
        class _R:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        if sql.lstrip().startswith("SELECT"):
            return _R([{"public_id": b[0], "type": b[3],
                        "md": json.loads(b[4]).get("md", "")}
                       for b in sorted(self.blocs, key=lambda x: x[2])])
        if sql.lstrip().startswith("DELETE"):
            self.blocs = []
        return _R([])

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def executemany(self, sql, params):
                conn.blocs.extend(params)

        return _Cur()


def _ids(conn):
    return [b[0] for b in sorted(conn.blocs, key=lambda x: x[2])]


def _mds(conn):
    return [json.loads(b[4])["md"] for b in sorted(conn.blocs, key=lambda x: x[2])]


def test_l_identite_survit_a_une_insertion_en_tete():
    """LE test de #362 : insérer un bloc en tête ⟹ les ids existants ne bougent
    pas — seul le bloc neuf tire une identité neuve."""
    conn = _FauxConn()
    write_node_blocks(conn, 7, "un\n\ndeux\n")
    avant = _ids(conn)
    write_node_blocks(conn, 7, "zéro\n\nun\n\ndeux\n")
    apres = _ids(conn)
    assert apres[1:] == avant, "les voisins intacts gardent leur identité"
    assert apres[0] not in avant and apres[0].startswith("blk_")


def test_l_identite_survit_a_un_deplacement():
    conn = _FauxConn()
    write_node_blocks(conn, 7, "un\n\ndeux\n\ntrois\n")
    par_md = dict(zip(_mds(conn), _ids(conn)))
    write_node_blocks(conn, 7, "trois\n\nun\n\ndeux\n")
    # ⚠️ le séparateur appartient au bloc du DESSUS : les sources exactes changent
    # pour « trois » (gagne \n\n) et « deux » (le perd) — seuls les blocs à source
    # INTACTE promettent leur identité. « un » est de ceux-là.
    assert dict(zip(_mds(conn), _ids(conn)))["un\n\n"] == par_md["un\n\n"]


def test_un_bloc_edite_prend_une_identite_neuve_sans_toucher_les_voisins():
    conn = _FauxConn()
    write_node_blocks(conn, 7, "un\n\ndeux\n\ntrois\n")
    avant = _ids(conn)
    write_node_blocks(conn, 7, "un\n\nDEUX ÉDITÉ\n\ntrois\n")
    apres = _ids(conn)
    assert apres[0] == avant[0] and apres[2] == avant[2], "voisins intacts"
    assert apres[1] != avant[1], "le bloc édité change d'adresse — une adresse " \
        "qui pointerait un texte qui n'est plus celui visé mentirait"


def test_les_doublons_s_apparient_dans_l_ordre():
    """Deux blocs de même source : chacun garde le sien (consommation unique,
    ordre des positions) — pas de vol d'identité croisé."""
    conn = _FauxConn()
    write_node_blocks(conn, 7, "pareil\n\npareil\n\nfin\n")
    avant = _ids(conn)
    write_node_blocks(conn, 7, "pareil\n\npareil\n\nfin\n")
    assert _ids(conn) == avant


def test_la_reprojection_d_un_corps_inchange_est_idempotente():
    """La propriété demandée au GO : même si le no-op blocks_md5 était contourné
    (refactor futur), re-projeter un corps inchangé ne change AUCUN id — pas de
    rotation d'identités silencieuse."""
    conn = _FauxConn()
    write_node_blocks(conn, 7, "# titre\nprose\n\n```py\ncode\n```\n")
    avant = _ids(conn)
    for _ in range(3):
        write_node_blocks(conn, 7, "# titre\nprose\n\n```py\ncode\n```\n")
    assert _ids(conn) == avant


def test_code_of_only_answers_for_code():
    (text,) = parse_blocks("juste du texte\n")
    assert code_of(text) is None
