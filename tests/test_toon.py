"""L'encodeur TOON rend exactement ce que rend l'implémentation de référence.

Les chaînes attendues de ce fichier ne sont pas écrites à la main : elles sortent de
`@toon-format/toon` 4.1.1 (spec v4.1), l'implémentation de référence, exécutée sur les
mêmes entrées le 08/09/2026. C'est ce qui donne leur valeur aux tests de citation :
ils décrivent le FORMAT tel qu'il est, pas la lecture qu'on en a faite. Une règle
devinée puis vérifiée contre sa propre devinette ne prouve rien — et il y en avait six
à deviner, dont trois contre-intuitives (`|` passe nu, `-` ne se cite qu'en tête,
`.5` n'est pas un nombre).

Le refus est un comportement testé au même titre que l'encodage : rendre `None` est
ce qui laisse le JSON en place, et le JSON est correct par construction.
"""
from __future__ import annotations

import json

from oto_mcp import toon

# ── Sorties de l'implémentation de référence ────────────────────────────────────

TABLE = {
    "rows": [
        {"id": 1, "nom": "Alice", "actif": True, "note": None},
        {"id": 2, "nom": "Bob, le second", "actif": False, "note": "1.5"},
        {"id": 3, "nom": "-tiret", "actif": True, "note": "a:b"},
    ],
    "count": 3,
    "next_cursor": None,
    "tags": ["a", "b"],
    "vide": [],
}

TABLE_TOON = (
    "rows[3]{id,nom,actif,note}:\n"
    "  1,Alice,true,null\n"
    '  2,"Bob, le second",false,"1.5"\n'
    '  3,"-tiret",true,"a:b"\n'
    "count: 3\n"
    "next_cursor: null\n"
    "tags[2]: a,b\n"
    "vide: []"
)

# Une valeur par ligne, dans l'ordre : la sortie attendue est celle de la référence.
CITATIONS = [
    "", " x", "x ", 'a"b', "a\\b", "a,b", "a:b", "a[b", "a{b",
    "-x", "#x", "a-b", "a#b", "true", "TRUE", "123", "00", "+1",
    "1e5", ".5", "0x1", "a|b", "a'b", "é", "l\nn", "t\tb",
]

CITATIONS_TOON = (
    "rows[26]{v}:\n"
    '  ""\n'
    '  " x"\n'
    '  "x "\n'
    '  "a\\"b"\n'
    '  "a\\\\b"\n'
    '  "a,b"\n'
    '  "a:b"\n'
    '  "a[b"\n'
    '  "a{b"\n'
    '  "-x"\n'
    '  "#x"\n'
    "  a-b\n"
    "  a#b\n"
    '  "true"\n'
    "  TRUE\n"
    '  "123"\n'
    '  "00"\n'
    '  "+1"\n'
    '  "1e5"\n'
    "  .5\n"
    "  0x1\n"
    "  a|b\n"
    "  a'b\n"
    "  é\n"
    '  "l\\nn"\n'
    '  "t\\tb"'
)


# ── Encodage ────────────────────────────────────────────────────────────────────

def test_forme_tabulaire_identique_a_la_reference():
    assert toon.encode(TABLE) == TABLE_TOON


def test_citations_identiques_a_la_reference():
    charge = {"rows": [{"v": v} for v in CITATIONS]}
    assert toon.encode(charge) == CITATIONS_TOON


def test_un_booleen_ne_se_rend_pas_en_entier():
    # En Python `True` EST un `int` : testé dans le mauvais ordre, la ligne dirait
    # `1` là où la donnée dit `true`, et la relecture par le modèle changerait de type.
    rendu = toon.encode({"rows": [{"v": True}, {"v": False}]})
    assert rendu.splitlines()[1:] == ["  true", "  false"]


def test_une_colonne_au_nom_ambigu_est_citee():
    # Un nom de colonne portant une virgule casserait l'en-tête en deux colonnes.
    rendu = toon.encode({"rows": [{"a,b": 1}]})
    assert rendu.startswith('rows[1]{"a,b"}:')


# ── Refus : ce qui rend `None` garde son JSON ───────────────────────────────────

def test_refuse_une_liste_non_uniforme():
    # Le cas qui a dicté la conception : une seule ligne à qui il manque une colonne.
    # La référence bascule alors en forme de LISTE, plus verbeuse que le JSON qu'elle
    # remplace — un encodage inconditionnel ferait donc GROSSIR la sortie.
    assert toon.encode({"rows": [{"a": 1, "b": 2}, {"a": 3}]}) is None


def test_refuse_une_valeur_imbriquee():
    assert toon.encode({"rows": [{"a": 1, "b": {"c": 2}}, {"a": 3, "b": {"c": 4}}]}) is None
    assert toon.encode({"rows": [{"a": 1, "b": [1, 2]}]}) is None


def test_refuse_une_charge_sans_bloc_tabulaire():
    # Un corps markdown ou un enregistrement seul : mesuré à 0 % et 4 % de gain,
    # sous n'importe quel seuil. Diverger de format pour ça ne se paie pas.
    assert toon.encode({"body_md": "# titre\n\ndu texte", "version": 3}) is None
    assert toon.encode({"call": {"id": 1, "tool": "x"}}) is None


def test_refuse_ce_qui_n_est_pas_un_dict_de_premier_niveau():
    assert toon.encode([{"a": 1}]) is None
    assert toon.encode({}) is None
    assert toon.encode("texte") is None
    assert toon.encode(None) is None


def test_refuse_au_dela_du_plafond_de_lignes():
    trop = {"rows": [{"a": i} for i in range(toon.LIGNES_MAX + 1)]}
    assert toon.encode(trop) is None


# ── La décision par charge ──────────────────────────────────────────────────────

def test_choisir_prend_le_toon_quand_il_est_plus_court():
    texte = json.dumps(TABLE, ensure_ascii=False)
    assert toon.choisir(TABLE, texte) == TABLE_TOON


def test_choisir_garde_le_json_sur_une_charge_a_prose():
    # La régression que la décision par charge existe pour empêcher : des lignes
    # uniformes mais dont la valeur pèse bien plus que le nom de sa colonne. Le
    # tabulaire est alors à peine plus court, et sous la marge il ne part pas.
    prose = "  ".join(["phrase de contexte assez longue pour peser"] * 12)
    charge = {"rows": [{"id": i, "texte": prose} for i in range(8)]}
    texte = json.dumps(charge, ensure_ascii=False)
    assert toon.encode(charge) is not None      # la forme s'y prête
    assert toon.choisir(charge, texte) is None  # mais le gain n'y est pas


def test_choisir_ne_rend_jamais_plus_long_que_le_json():
    # Propriété, pas exemple : quelle que soit la charge, ce qui part est au plus
    # aussi long que ce qui partait avant.
    charges = [
        TABLE,
        {"rows": [{"v": v} for v in CITATIONS]},
        {"rows": [{"a": 1, "b": 2}, {"a": 3}]},
        {"body_md": "# titre", "version": 3},
        {"rows": []},
    ]
    for charge in charges:
        texte = json.dumps(charge, ensure_ascii=False)
        servi = toon.choisir(charge, texte)
        assert servi is None or len(servi) <= len(texte)


# ── Les NOMS suivent la règle inverse des valeurs ───────────────────────────────

def test_un_nom_de_colonne_suit_sa_propre_regle():
    """Sorties de la référence sur 38 noms : la règle des noms est une LISTE BLANCHE.

    Les deux lignes qui comptent sont les deux inversions — un nom passe nu là où la
    valeur se citerait, et se cite là où la valeur passerait nue. Prendre une seule
    règle pour les deux écrit un en-tête que la référence n'écrirait pas.
    """
    nu = ["simple", "a.b", "a_b", "true", "null", "TRUE"]
    cite = ["a b", "a-b", "a#b", "-ab", "#ab", "a/b", "a|b", "123", "1.5", " ab", "", "a,b"]
    for nom in nu:
        rendu = toon.encode({"rows": [{nom: 1}]})
        assert rendu.startswith("rows[1]{%s}:" % nom), nom
    for nom in cite:
        rendu = toon.encode({"rows": [{nom: 1}]})
        assert rendu.startswith('rows[1]{"'), nom


def test_une_cle_de_premier_niveau_suit_la_meme_regle_que_les_colonnes():
    # Sortie de la référence, verbatim : l'ordre des clés du dict est conservé.
    rendu = toon.encode({"a,b": 1, "rows": [{"x": 1}]})
    assert rendu == '"a,b": 1\nrows[1]{x}:\n  1' 


def test_un_caractere_de_controle_part_en_echappement_unicode():
    rendu = toon.encode({"rows": [{"v": "a\u0001b"}]})
    assert rendu.splitlines()[1] == '  "a\\u0001b"'


# ── Deux divergences ASSUMÉES avec l'implémentation de référence ────────────────

def test_un_grand_entier_n_est_pas_arrondi():
    """La référence tourne sur le modèle de nombre de JavaScript et ARRONDIT au-delà
    de 2^53 : 9007199254740993 y devient …992. Python tient l'entier, et on ne recopie
    pas une perte de donnée pour ressembler à la référence."""
    rendu = toon.encode({"rows": [{"v": 9007199254740993}]})
    assert rendu.splitlines()[1] == "  9007199254740993"


def test_un_float_entier_garde_sa_decimale():
    """Même origine : JavaScript n'a qu'un type de nombre et écrit `2` pour `2.0`."""
    rendu = toon.encode({"rows": [{"v": 2.0}]})
    assert rendu.splitlines()[1] == "  2.0"
