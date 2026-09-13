"""Les deux mots qu'une écriture peut poser à la place d'un contenu (oto#140, palier 1).

Le contrat d'écriture d'une case, arrêté le 07/09/2026, dit qu'un sous-champ dit
TOUJOURS l'une de trois choses : un contenu (il remplace), `@keep` (je n'y touche pas,
sans avoir à le renvoyer), `@empty` (je le vide, délibérément).

**Ce que `@keep` répare.** Écrire une valeur faisait tomber le commentaire et le lien
qui l'accompagnaient — logique, ils décrivaient l'ancienne valeur. Un agent qui
corrigeait une coquille devait donc **retaper la provenance**. Or un agent qui retape
une provenance ne la recopie pas, il la reformule : à chaque passage, la source dérive
un peu. À l'échelle d'une campagne, c'est une dégradation lente et invisible de ce
qu'on pourra restituer à la cliente.

⚠️ **La règle change de NATURE, pas seulement de comportement.** Ce n'est plus « les
couches liées tombent avec la valeur », c'est **« ce que l'écriture NOMME décide »**.
Une couche nommée avec `@keep` est nommée : elle ne tombe pas. Une couche passée sous
silence tombe, exactement comme avant.

⚠️ **Et la moitié qui compte autant est la SECONDE : les gestes existants ne bougent
pas d'un caractère.** Sans ce banc, rien ne dirait qu'un lot annoncé « additif » l'est
vraiment — et une campagne en cours de mesure comparait ses colonnes contre un socle
qui aurait bougé sous elle sans que personne le sache.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore.columns import _merge_column


def _base() -> dict:
    """Une case complète : valeur, point de départ, provenance, attestation."""
    return {"valeur": "ACME", "origine": "DUPONT",
            "comment": "registre du 05/08", "link": "https://exemple.test/x"}


# ── Ce que `@keep` rend possible, et qui ne l'était pas ──────────────────────

def test_garder_la_provenance_en_corrigeant_la_valeur():
    """LE cas du palier : corriger une coquille sans retaper d'où vient la valeur."""
    out = _merge_column(_base(), {"valeur": "ACME SAS", "comment": dsl.GARDE})

    assert out["valeur"] == "ACME SAS"
    assert out["comment"] == "registre du 05/08", "la provenance ne doit pas dériver"
    assert out["origine"] == "DUPONT", "le point de départ survit, comme toujours"


def test_le_lien_TOMBE_s_il_n_est_pas_nommé():
    """La contre-épreuve qui borne le lot : `@keep` n'ouvre pas les vannes. Ce qui
    n'est pas dit tombe encore — sans quoi une attestation survivrait à la valeur
    qu'elle atteste, ce qui est le défaut d'origine, pas sa correction."""
    out = _merge_column(_base(), {"valeur": "ACME SAS", "comment": dsl.GARDE})

    assert "link" not in out


def test_garder_les_deux():
    out = _merge_column(_base(), {"valeur": "ACME SAS",
                                  "comment": dsl.GARDE, "link": dsl.GARDE})

    assert out["comment"] == "registre du 05/08"
    assert out["link"] == "https://exemple.test/x"


def test_garder_la_VALEUR_en_écrivant_à_côté():
    """`@keep` vaut sur n'importe quel sous-champ, la valeur comprise — c'est ce qui
    en fait une règle et non un cas particulier du commentaire."""
    out = _merge_column(_base(), {"valeur": dsl.GARDE, "comment": "vérifié à la source"})

    assert out["valeur"] == "ACME"
    assert out["comment"] == "vérifié à la source"
    assert out["link"] == "https://exemple.test/x", (
        "la valeur n'a pas changé : rien ne tombe")


def test_garder_ce_qui_n_existe_pas_ne_CRÉE_rien():
    """⚠️ `@keep` sur une couche absente garde le néant — c'est ce que le mot promet.
    Poser `""` inventerait un « vide délibéré » que personne n'a demandé, et les deux
    ne se lisent pas pareil : l'un dit « on n'a pas cherché », l'autre « on a cherché
    et il n'y a rien »."""
    sans_lien = {"valeur": "ACME", "comment": "registre"}

    out = _merge_column(sans_lien, {"valeur": "ACME SAS", "link": dsl.GARDE})

    assert out == "ACME SAS", "aucune couche : la colonne redevient plate"


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
    """La garde de dernier recours : ces deux mots se résolvent à la fusion et nulle
    part ailleurs. Qu'un seul passe et il est stocké comme une valeur — un `@keep` en
    base serait servi à la cliente comme sa propre donnée."""
    for mot in dsl.SENTINELLES:
        for geste in ({"valeur": mot}, {"comment": mot},
                      {"valeur": "X", "comment": mot}, {"link": mot}):
            out = _merge_column(_base(), geste)
            plat = out if isinstance(out, dict) else {"valeur": out}
            assert mot not in plat.values(), f"{mot} stocké par {geste}"


# ── ⚠️ Le mot recopié AU MAUVAIS ENDROIT ─────────────────────────────────────
#
# Signalé par une campagne AVANT la mise en production, et c'est le trou que ce lot
# aurait ouvert. Un agent recopie ce qu'on lui montre — mesuré, cinquante emplois
# pour un exemple, zéro pour la forme équivalente non montrée — mais il peut le
# recopier au mauvais endroit : `{"champ": "@keep"}` au lieu de
# `{"champ": {"valeur": …, "comment": "@keep"}}`.
#
# Sans résolution sur la valeur nue, la chaîne `@keep` partait en base, puis chez la
# cliente comme sa propre donnée. On RÉSOUT plutôt qu'on refuse : l'intention est
# claire, et un refus ferait rejouer l'appel sans que l'agent comprenne.

def test_le_mot_pose_sur_TOUTE_la_case_est_resolu_aussi():
    """`@keep` nu = « n'y touche pas » : la case entière survit, couches comprises."""
    out = _merge_column(_base(), dsl.GARDE)

    assert out == _base(), "rien ne doit bouger, pas même une couche"


def test_vider_toute_la_case_par_le_mot_nu():
    """⚠️ Le point de DÉPART survit, et c'est toute sa raison d'être : vider la valeur
    courante n'efface pas ce que la cliente avait remis. Mon premier banc l'exigeait
    vide — il était faux, pas le code.

    oto#204, décision du plan : `@empty` ASSUME le vide — la case vidée porte le marqueur ;
    `@clear` efface sans l'assumer, et rend ce qu'`@empty` rendait avant."""
    assert _merge_column(_base(), dsl.VIDE_DELIBERE) == {"valeur": "",
                                                         "origine": "DUPONT",
                                                         dsl.VIDE_ASSUME: True}
    assert _merge_column(_base(), dsl.EFFACEMENT) == {"valeur": "",
                                                      "origine": "DUPONT"}


@pytest.mark.parametrize("texte", [
    "vu au registre @keep",          # le mot noyé dans une phrase
    "contact@keepcool.fr",           # ⚠️ une adresse parfaitement légitime
    "@keeper",                       # un mot qui COMMENCE par la sentinelle
    "@KEEP",                         # la casse compte
])
def test_une_sentinelle_ne_mord_JAMAIS_au_milieu(texte):
    """⚠️ Le test est au mot ENTIER, et cette borne vaut le lot entier.

    Un motif plus large que ce qu'il prétend viser est le défaut qu'on a traqué toute
    la nuit — une campagne a compté 22 faux positifs sur un `502` qui vivait à
    l'intérieur d'un autre nombre. Ici, `contact@keepcool.fr` est une adresse qu'une
    cliente peut réellement avoir : la transformer en « garde ce qui est là »
    détruirait sa donnée en silence."""
    out = _merge_column(_base(), texte)

    assert dsl.unwrap(out) == texte, "le texte doit arriver intact en base"
