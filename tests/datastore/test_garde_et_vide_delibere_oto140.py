"""Le mot qu'une écriture peut poser à la place d'un contenu : `@empty` (oto#140).

Le contrat d'écriture d'une case tient en deux gestes : `null` efface, `@empty` dit
« cherché, rien » — un vide DÉCIDÉ, qui ne se confond pas avec l'absence. `@keep` et
`@clear` sont retirés : refusés à l'entrée de toute écriture
(`test_controler_garde_toutes_les_ecritures.py`), ils n'atteignent plus la fusion.

⚠️ **La moitié qui compte autant : les gestes existants ne bougent pas d'un caractère.**
Sans ce banc, rien ne dirait qu'un lot annoncé « additif » l'est vraiment.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore.columns import _merge_column


def _base() -> dict:
    """Une case complète : valeur, point de départ, provenance, attestation."""
    return {"valeur": "ACME", "origine": "DUPONT",
            "comment": "registre du 05/08", "link": "https://exemple.test/x"}


# ── `@empty` : le vide qu'on a DÉCIDÉ ────────────────────────────────────────

def test_vider_délibérément_n_est_pas_l_absence():
    """La distinction que le contrat tient à préserver : « cherché, rien trouvé » se
    dit, et ne se confond pas avec « jamais regardé »."""
    out = _merge_column(_base(), {"valeur": "ACME SAS", "comment": dsl.VIDE_DELIBERE})

    assert out["comment"] == "", "le vide posé est une valeur, pas une absence"


def test_vider_la_valeur_elle_même():
    out = _merge_column(_base(), {"valeur": dsl.VIDE_DELIBERE})

    assert dsl.unwrap(out) == ""


# ── ⚠️ La moitié qui protège l'existant ──────────────────────────────────────

@pytest.mark.parametrize("nom,geste,attendu", [
    ("valeur nue",
     "NEUVE", {"valeur": "NEUVE", "origine": "DUPONT"}),
    ("valeur en couches",
     {"valeur": "NEUVE"}, {"valeur": "NEUVE", "origine": "DUPONT"}),
    ("valeur et commentaire",
     {"valeur": "NEUVE", "comment": "neuf"},
     {"valeur": "NEUVE", "origine": "DUPONT", "comment": "neuf"}),
    ("commentaire seul",
     {"comment": "neuf"},
     {"valeur": "ACME", "origine": "DUPONT", "comment": "neuf",
      "link": "https://exemple.test/x"}),
    ("effacement de la valeur",
     {"valeur": None}, {"origine": "DUPONT"}),
])
def test_les_gestes_EXISTANTS_ne_bougent_pas(nom, geste, attendu):
    """⚠️ **Le banc qui autorise ce lot à partir pendant qu'une campagne mesure.**

    Un lot annoncé « additif » ne l'est que si quelqu'un le vérifie. Ces cinq gestes
    sont ceux qu'un agent pose réellement ; leur résultat doit être identique **au
    caractère près** à ce qu'il était avant les deux mots réservés. Sans ce banc, la
    comparaison colonne à colonne d'une campagne se ferait contre un socle qui aurait
    bougé sous elle, et l'écart lui serait attribué à elle."""
    assert _merge_column(_base(), geste) == attendu


def test_une_sentinelle_n_atteint_JAMAIS_le_stockage():
    """La garde de dernier recours : `@empty` se résout à la fusion. Qu'il passe et il
    est stocké comme une valeur, puis servi à la cliente comme sa propre donnée."""
    for mot in (dsl.VIDE_DELIBERE,):
        for geste in ({"valeur": mot}, {"comment": mot},
                      {"valeur": "X", "comment": mot}, {"link": mot}):
            out = _merge_column(_base(), geste)
            plat = out if isinstance(out, dict) else {"valeur": out}
            assert mot not in plat.values(), f"{mot} stocké par {geste}"


# ── Le mot posé sur TOUTE la case ────────────────────────────────────────────
#
# Un agent recopie ce qu'on lui montre, parfois au mauvais endroit : `{"champ": "@empty"}`
# au lieu de `{"champ": {"valeur": "@empty", …}}`. Résolu aussi, jamais stocké.

def test_vider_toute_la_case_par_le_mot_nu():
    """⚠️ Le point de DÉPART survit, et c'est toute sa raison d'être : vider la valeur
    courante n'efface pas ce que la cliente avait remis. Mon premier banc l'exigeait
    vide — il était faux, pas le code.

    oto#204, décision du plan : `@empty` ASSUME le vide — la case vidée porte le
    marqueur."""
    assert _merge_column(_base(), dsl.VIDE_DELIBERE) == {"valeur": "",
                                                         "origine": "DUPONT",
                                                         dsl.VIDE_ASSUME: True}


@pytest.mark.parametrize("texte", [
    "vu au registre @empty",         # le mot noyé dans une phrase
    "contact@emptyhouse.fr",         # ⚠️ une adresse parfaitement légitime
    "@emptyness",                    # un mot qui COMMENCE par la sentinelle
    "@EMPTY",                        # la casse compte
])
def test_une_sentinelle_ne_mord_JAMAIS_au_milieu(texte):
    """⚠️ Le test est au mot ENTIER, et cette borne vaut le lot entier.

    Un motif plus large que ce qu'il prétend viser est le défaut qu'on a traqué toute
    la nuit — une campagne a compté 22 faux positifs sur un `502` qui vivait à
    l'intérieur d'un autre nombre. Ici, `contact@emptyhouse.fr` est une adresse qu'une
    cliente peut réellement avoir : la vider détruirait sa donnée en silence."""
    out = _merge_column(_base(), texte)

    assert dsl.unwrap(out) == texte, "le texte doit arriver intact en base"
