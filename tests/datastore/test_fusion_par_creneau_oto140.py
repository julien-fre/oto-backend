"""Une liste se fusionne par CRÉNEAU quand le schéma le déclare (oto#140).

Le trou que ça ferme, et il ne se voyait qu'en le cherchant. Sur une colonne de
premier niveau, la couche `origine` survit à une écriture — c'est toute sa raison
d'être. **Dans un élément de liste, elle ne survivait à rien** : une liste se
fusionnait en BLOC, donc un agent qui réémettait ses contacts pour corriger un seul
email effaçait les couches de TOUS les éléments, provenance comprise.

⚠️ **L'identité déclarée est celle d'un CRÉNEAU, pas d'une personne** (tranché par le
propriétaire du produit le 08/09/2026, et c'est ce qui rend le mécanisme sûr). Le bon
candidat est une catégorie stable et fermée — `contact_rh`, `contact_paie` — jamais un
email ni un nom. *« Ça ne me gêne pas si `contact_rh` était Jane et devient Doe après
une passe d'agent »* : remplacer l'occupant d'un créneau est le geste NORMAL que ce
mécanisme sert. Apparier des gens sur leur nom serait au contraire le mode d'échec
qu'on refuse — un appariement muet sur des données nominatives.

Sans déclaration `of.key`, rien ne change : la liste se remplace en bloc, comme depuis
toujours.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.columns import _merge_column
from oto_mcp.datastore.errors import RowValidationError

CHAMP = {"key": "contacts", "type": "list",
         "of": {"key": "role", "fields": [{"key": "role"}, {"key": "email"}]}}

SANS_CLE = {"key": "contacts", "type": "list",
            "of": {"fields": [{"key": "role"}, {"key": "email"}]}}


def _avant() -> list:
    """Deux créneaux, dont un qui porte une provenance et une version d'origine."""
    return [
        {"role": "contact_rh",
         "email": {"valeur": "jane@x.fr", "comment": "site officiel",
                   "origine": {"valeur": "jane@ancien.fr",
                               "comment": "fichier de la cliente"}}},
        {"role": "contact_paie", "email": "paie@x.fr"},
    ]


# ── Le cas qui a motivé le lot ───────────────────────────────────────────────

def test_l_occupant_change_et_l_origine_RESTE():
    """LE cas : l'agent trouve un meilleur contact RH et réémet la liste."""
    out = _merge_column(_avant(),
                        [{"role": "contact_rh", "email": "doe@x.fr"},
                         {"role": "contact_paie", "email": "paie@x.fr"}],
                        CHAMP)

    rh = next(i for i in out if i["role"] == "contact_rh")
    assert rh["email"]["valeur"] == "doe@x.fr", "le créneau accueille son nouvel occupant"
    assert rh["email"]["origine"] == {"valeur": "jane@ancien.fr",
                                      "comment": "fichier de la cliente"}, (
        "ce que la cliente avait remis ne doit pas disparaître avec l'occupant")


def test_le_commentaire_de_la_valeur_TOMBE_comme_ailleurs():
    """La contre-épreuve qui borne le lot : la fusion par créneau applique aux
    attributs la MÊME règle qu'aux colonnes, elle n'en invente pas une seconde. Le
    commentaire décrivait l'email de Jane ; il ne décrit pas celui de Doe."""
    out = _merge_column(_avant(), [{"role": "contact_rh", "email": "doe@x.fr"}], CHAMP)

    assert "comment" not in out[0]["email"]


def test_un_creneau_non_reemis_DISPARAIT():
    """⚠️ La borne à connaître : la fusion porte sur les éléments POSÉS, pas sur
    l'union. Réémettre une liste amputée ampute la liste — c'est ce que « poser une
    liste » veut dire, et le contraire ferait qu'aucun contact ne pourrait plus
    jamais être retiré."""
    out = _merge_column(_avant(), [{"role": "contact_rh", "email": "doe@x.fr"}], CHAMP)

    assert [i["role"] for i in out] == ["contact_rh"]


# ── Ce qui n'est apparié à rien ──────────────────────────────────────────────

def test_un_element_SANS_identite_entre_tel_quel():
    """Deviner à quoi il correspond serait inventer. Il entre, il n'hérite de rien."""
    out = _merge_column(_avant(),
                        [{"email": "inconnu@x.fr"},
                         {"role": "contact_rh", "email": "doe@x.fr"}],
                        CHAMP)

    assert out[0] == {"email": "inconnu@x.fr"}


def test_un_creneau_NEUF_n_herite_de_rien():
    out = _merge_column(_avant(),
                        [{"role": "contact_juridique", "email": "j@x.fr"}], CHAMP)

    assert out == [{"role": "contact_juridique", "email": "j@x.fr"}]


# ── ⚠️ Le doublon LÈVE, il ne se départage pas au hasard ─────────────────────

def test_une_identite_en_double_dans_la_liste_POSEE_refuse_en_la_NOMMANT():
    """Deux éléments qui se disent le même ne sont pas départageables. Prendre le
    premier apparierait au hasard — sur des données de personnes, c'est le seul mode
    d'échec inacceptable, parce qu'il est SILENCIEUX.

    ⚠️ Le refus porte sur la liste POSÉE, c'est-à-dire sur le geste de l'appelant, au
    moment où il peut encore le corriger."""
    with pytest.raises(RowValidationError) as e:
        _merge_column(None, [{"role": "contact_rh", "email": "a@x.fr"},
                             {"role": "contact_rh", "email": "b@x.fr"}], CHAMP)

    msg = str(e.value)
    assert "contact_rh" in msg, "le refus doit nommer la valeur en cause"
    assert "list being set" in msg


def test_un_doublon_DEJA_EN_PLACE_n_enferme_PAS_la_ligne():
    """⚠️ **Correctif du 08/09/2026, et j'avais posé la faute en la citant.**

    La liste EN PLACE levait aussi. Une ligne portant déjà un doublon n'acceptait donc
    plus AUCUNE écriture — **y compris celle qui l'aurait réparée** : la validation
    jugeait l'état, pas le geste. Le refus proposait deux sorties dont aucune ne
    marchait : « donne des valeurs distinctes » (impossible, tout était refusé) et
    « retire `of.key` » (qui ne se retire pas — mesuré). La fiche était dans une
    impasse.

    C'est exactement ce que ce module dénonce ailleurs : *une garde qui bloque le geste
    qui la lèverait*. Trouvé par la campagne sur un tableau jetable, avant que ça
    n'arrive sur une vraie fiche.

    Quand l'état est ambigu on ne peut pas apparier — donc on REMPLACE en bloc, ce qui
    est le comportement d'une liste sans clé. La liste envoyée, elle, est valide : le
    geste répare au lieu d'échouer."""
    avant = [{"role": "contact_rh", "email": "a@x.fr"},
             {"role": "contact_rh", "email": "b@x.fr"}]
    out = _merge_column(avant, [{"role": "contact_rh", "email": "c@x.fr"}], CHAMP)

    assert out == [{"role": "contact_rh", "email": "c@x.fr"}], (
        "la liste envoyée fait foi et la ligne est réparée")


def test_le_refus_ne_conseille_PLUS_un_geste_qui_n_existe_pas():
    """Le message renvoyait vers « retire `of.key` du schéma ». Mesuré : `of.key` ne
    se retire pas — `remove_attrs` ne descend pas dans `of`, et réémettre `of` sans
    `key` la conserve. Un refus qui nomme une sortie inexistante est pire qu'un refus
    muet : il fait perdre du temps à celui qui le suit."""
    with pytest.raises(RowValidationError) as e:
        _merge_column(None, [{"role": "x", "email": "a"}, {"role": "x", "email": "b"}],
                      CHAMP)

    assert "of.key" not in str(e.value)


# ── Sans déclaration, rien ne change ─────────────────────────────────────────

def test_sans_of_key_la_liste_se_remplace_EN_BLOC():
    """Le comportement de toujours, préservé : le mécanisme est OPT-IN. Sans ce banc,
    rien ne dirait qu'un tableau qui ne déclare rien n'a pas changé de sémantique
    d'écriture sous ses pieds."""
    out = _merge_column(_avant(), [{"role": "contact_rh", "email": "doe@x.fr"}],
                        SANS_CLE)

    assert out == [{"role": "contact_rh", "email": "doe@x.fr"}]


def test_sans_DÉCLARATION_du_tout_la_liste_se_remplace_aussi():
    """Le chemin qu'empruntent les tableaux libres — la majorité."""
    out = _merge_column(_avant(), [{"role": "contact_rh", "email": "doe@x.fr"}])

    assert out == [{"role": "contact_rh", "email": "doe@x.fr"}]
