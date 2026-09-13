"""Une couche s'atteint comme une valeur : `champ.source` se filtre, se trie, s'agrège.

C'est ce qui sépare une provenance VÉRIFIABLE d'une provenance décorative. La
question qui compte pour un assureur — *« toutes les lignes dont l'email n'a pas de
provenance »* — n'est posable que si la couche est adressable au même titre que la valeur.
La mission qui a motivé le chantier en avait la démonstration en creux : un champ JSON
portant la provenance de tous les champs, ni filtrable ni comptable, à côté de trois
colonnes plates qui marchaient.

Le vocabulaire est FERMÉ (`origine`, `comment`, `link`) : c'est ce qui rend
l'ambiguïté décidable sans deviner, et ce qui interdit au datastore d'interpréter
quoi que ce soit d'un nom de champ.
"""
from __future__ import annotations

import pytest

from oto_mcp import db
from oto_mcp.db import datastore as dsdb


# --- l'adressage ---------------------------------------------------------------

@pytest.mark.parametrize("field,attendu", [
    ("email", ("email", None)),
    ("email.comment", ("email", "comment")),
    ("email.origine", ("email", "origine")),
    ("email.link", ("email", "link")),
])
def test_a_layer_suffix_is_recognised(field, attendu):
    assert dsdb.split_layer(field) == attendu


@pytest.mark.parametrize("field", ["taux.2024", "a.b", "email.valeur", "email.COMMENT"])
def test_anything_else_stays_a_column_name(field):
    """⚠️ `valeur` n'est PAS une couche adressable : elle EST la colonne, on l'atteint
    par son nom nu. L'admettre ici ouvrirait deux façons de dire la même chose.

    Et un champ légitimement nommé `taux.2024` reste un nom entier — le vocabulaire
    fermé est ce qui permet de trancher sans deviner."""
    assert dsdb.split_layer(field) == (field, None)


def test_a_bare_leading_dot_is_not_a_layer():
    assert dsdb.split_layer(".comment") == (".comment", None)


# --- ce que ça donne dans une clause -------------------------------------------

def _clause(field, op="eq", value="x"):
    clauses, params = db._ds_filter_clauses([
        {"field": field, "op": op, "value": value}])
    return clauses[0], params


def test_filtering_a_layer_reads_the_layer():
    clause, params = _clause("email.comment")
    assert clause == f"{dsdb.LAYER_VALUE_PARAM_SQL} = %s"
    # Trois paramètres depuis le 08/09/2026 : la couche imbriquée (`email`, `comment`)
    # PUIS la clé littérale pointée (`email.comment`) — cf. le test ci-dessous.
    assert params == ["email", "comment", "email.comment", "x"]


def test_filtering_a_bare_name_still_reads_the_value():
    """La régression qu'on ne veut pas : l'adressage de couche ne doit rien changer
    au chemin nu, qui porte tout l'existant."""
    clause, params = _clause("email")
    assert clause == f"{db.FIELD_VALUE_PARAM_SQL} = %s"
    # Autant de fois le nom nu que la règle de valeur lit la case (oto#163) — la liste
    # vient de `paths.py`, et elle ne porte QUE le nom nu.
    assert params == db.field_read_sql("email")[1] + ["x"]
    assert set(params[:-1]) == {"email"}


def test_the_question_that_matters_is_expressible():
    """« Toutes les lignes dont l'email n'a pas de source » — la seule question qui
    transforme la provenance en garantie plutôt qu'en décoration."""
    clause, params = _clause("email.comment", op="empty", value=None)
    assert "IS NULL" in clause
    assert params[:2] == ["email", "comment"]


def test_a_layer_never_falls_back_on_the_VALUE():
    """Une couche ne retombe JAMAIS sur la valeur de sa colonne : sur une colonne
    scalaire elle est NULL, et c'est la BONNE réponse. Y retomber ferait répondre
    « la source est l'email lui-même » — un mensonge, précisément là où on cherche la
    vérité.

    ⚠️ **Ce banc interdisait le mot `COALESCE`, pas le danger qu'il vise** — une garde
    de FORME et non d'axe. Elle a mordu le 08/09/2026 sur un correctif qui ajoute un
    repli d'une tout autre nature : la clé LITTÉRALE pointée (`data->>'email.comment'`),
    une relique écrite au premier niveau avant la garde du 31/08, et dont il reste 765
    occurrences en production. Sans ce repli, un filtre sur `email.comment` rend **0
    sur une donnée présente** — y compris quand on s'en sert pour vérifier une
    destruction.

    `email.comment` ne peut pas être la valeur de `email` : le repli lit une autre
    clé, pas la même sous un autre nom. **Le mensonge que ce banc protège reste
    impossible** ; ce qui change, c'est qu'on regarde les deux endroits où la couche
    peut vivre.

    Le banc dit donc maintenant ce qu'il voulait dire : pas de repli sur la VALEUR."""
    _, params = _clause("email.comment")
    assert "email" not in params[2:3], (
        "le repli ne doit JAMAIS viser la colonne nue — ce serait le mensonge")
    assert params[2] == "email.comment", "il vise la clé littérale pointée"


def test_layers_sort_and_aggregate_like_values():
    """Même expression pour tous les usages : un `group_by` sur `email.source` compte
    les provenances, ce qui est la question de pilotage (« combien de valeurs
    déduites ? »)."""
    sql, params, _ = dsdb._build_aggregate(
        7, "email.comment", [{"op": "count"}], None, None, 500)
    assert params[:2] == ["email", "comment"]


# --- `empty` honore sa valeur ---------------------------------------------------

@pytest.mark.parametrize("op,value,attendu_vide", [
    ("empty", True, True),
    ("empty", None, True),          # sans valeur = l'opérateur nu
    ("empty", False, False),        # ⚠️ « pas vide »
    ("not_empty", True, False),
    ("not_empty", False, True),
])
def test_empty_reads_its_boolean(op, value, attendu_vide):
    """La valeur était JETÉE : `{"empty": false}` rendait le même jeu que
    `{"empty": true}`. Donc « quelles valeurs n'ont pas de provenance ? » et son
    contraire répondaient pareil, sans erreur — sur un contrôle de complétude, c'est
    une réponse plausible et fausse dans les deux sens.

    Défaut ANTÉRIEUR aux couches : il valait déjà sur une colonne plate."""
    clause, _ = _clause("email.comment", op=op, value=value)
    assert ("IS NULL" in clause) is attendu_vide


def test_the_completeness_question_answers_both_ways():
    """Les deux sens doivent rendre des jeux COMPLÉMENTAIRES, pas identiques."""
    vide, _ = _clause("email.comment", op="empty", value=True)
    plein, _ = _clause("email.comment", op="empty", value=False)
    assert vide != plein
