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
    assert params == ["email", "comment", "x"]


def test_filtering_a_bare_name_still_reads_the_value():
    """La régression qu'on ne veut pas : l'adressage de couche ne doit rien changer
    au chemin nu, qui porte tout l'existant."""
    clause, params = _clause("email")
    assert clause == f"{db.FIELD_VALUE_PARAM_SQL} = %s"
    assert params == ["email", "email", "x"]


def test_the_question_that_matters_is_expressible():
    """« Toutes les lignes dont l'email n'a pas de source » — la seule question qui
    transforme la provenance en garantie plutôt qu'en décoration."""
    clause, params = _clause("email.comment", op="empty", value=None)
    assert "IS NULL" in clause
    assert params[:2] == ["email", "comment"]


def test_a_layer_has_no_flat_fallback():
    """Pas de COALESCE sur une couche : sur une colonne scalaire elle est NULL, et
    c'est la BONNE réponse. Y retomber sur la valeur ferait répondre « la source est
    l'email lui-même » — un mensonge, précisément là où on cherche la vérité."""
    assert "COALESCE" not in dsdb.LAYER_VALUE_PARAM_SQL
    clause, _ = _clause("email.comment")
    assert "COALESCE" not in clause


def test_layers_sort_and_aggregate_like_values():
    """Même expression pour tous les usages : un `group_by` sur `email.source` compte
    les provenances, ce qui est la question de pilotage (« combien de valeurs
    déduites ? »)."""
    sql, params, _ = dsdb._build_aggregate(
        7, "email.comment", [{"op": "count"}], None, None, 500)
    assert params[:2] == ["email", "comment"]
