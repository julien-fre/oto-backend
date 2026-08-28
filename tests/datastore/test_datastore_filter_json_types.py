"""Un filtre sur une valeur non textuelle doit matcher ce que le JSON a stocké (#306).

`data ->> champ` extrait le JSON **en texte**, avec les conventions du JSON : un
booléen y ressort `"true"`. `str(True)` en Python rend `"True"` — majuscule. La
comparaison était donc `"true" = "True"`, fausse pour chaque ligne : **zéro résultat,
sans erreur**. Mesuré sur un tableau réel : 0 ligne contre 29 avec la chaîne.

C'est le mode d'échec le plus crédible qui soit — SQL compare deux chaînes valides,
« aucune correspondance » est une réponse honnête, et l'appelant conclut qu'il n'y a
pas de données.
"""
from __future__ import annotations

import pytest

from oto_mcp import db


def _eq(field, value):
    """(clause, valeur comparée) d'un filtre `eq`."""
    clauses, params = db._ds_filter_clauses([
        {"field": field, "op": "eq", "value": value}])
    # La VALEUR comparée est le dernier paramètre — le champ la précède, une
    # fois par branche du COALESCE (#318). Lire par la fin plutôt que par un
    # index fixe : c'est la valeur qui est le sujet, pas sa position.
    return clauses[0], params[-1]


# --- le défaut lui-même --------------------------------------------------------

def test_a_python_bool_is_compared_as_json_writes_it():
    """`True` doit produire `"true"`, pas `"True"` — c'est tout le bug."""
    assert _eq("a_reprendre", True)[1] == "true"
    assert _eq("a_reprendre", False)[1] == "false"


def test_the_string_form_keeps_working():
    """⚠️ Rétrocompatibilité NON négociable : des scripts contournent aujourd'hui en
    envoyant `"true"`, seul moyen d'obtenir le bon résultat. Les faire échouer
    remplacerait un piège silencieux par une régression chez ceux qui avaient trouvé
    la parade — les deux formes doivent viser le même booléen stocké."""
    assert _eq("a_reprendre", "true")[1] == "true"
    assert _eq("a_reprendre", "false")[1] == "false"


def test_an_integral_float_loses_its_decimal_point():
    """`str(1.0)` rend `"1.0"` là où un entier stocké ressort `"1"` — même famille."""
    assert _eq("effectif", 1.0)[1] == "1"
    assert _eq("effectif", 1.5)[1] == "1.5", "un vrai décimal garde sa partie"


def test_null_is_refused_with_the_operator_that_answers_it():
    """`data ->> champ` rend SQL NULL pour un JSON `null` COMME pour une clé absente :
    aucune comparaison textuelle ne les distingue. On refuse en nommant `empty`,
    plutôt que de rendre un zéro que l'appelant lirait comme « rien ne correspond »."""
    with pytest.raises(ValueError) as e:
        db._ds_filter_clauses([{"field": "x", "op": "eq", "value": None}])
    assert "empty" in str(e.value)


# --- ce qui ne doit PAS bouger -------------------------------------------------

@pytest.mark.parametrize("value", ["retenu", "sante_prevoyance", "2026-08-12", "42"])
def test_plain_strings_pass_through_untouched(value):
    """Les chaînes sont l'immense majorité des champs : c'est pour ça que le défaut
    est resté invisible. Rien ne doit changer pour elles."""
    assert _eq("statut", value)[1] == value


def test_numeric_comparisons_still_cast():
    """Les comparaisons ordonnées étaient DÉJÀ correctes (cast `::numeric` gardé) —
    le trou ne les concernait pas. Figé pour qu'un futur passage ne les emporte pas
    en croyant généraliser le correctif."""
    clauses, params = db._ds_filter_clauses([
        {"field": "ca", "op": "gte", "value": 1000000}])
    assert "::numeric >= %s::numeric" in clauses[0]
    assert params == ["ca"] * 4 + ["1000000"]


# --- les autres opérateurs qui passaient par la même conversion ----------------

def test_in_normalises_each_member():
    clauses, params = db._ds_filter_clauses([
        {"field": "actif", "op": "in", "value": [True, False, "peut-etre"]}])
    assert clauses[0] == f"{db.FIELD_VALUE_PARAM_SQL} = ANY(%s)"
    assert params[-1] == ["true", "false", "peut-etre"]


def test_ne_normalises_too():
    clauses, params = db._ds_filter_clauses([
        {"field": "a_reprendre", "op": "ne", "value": True}])
    assert params[-1] == "true"


def test_contains_normalises_too():
    clauses, params = db._ds_filter_clauses([
        {"field": "a_reprendre", "op": "contains", "value": True}])
    assert params[-1] == "%true%"


def test_bool_is_checked_before_int():
    """En Python `bool` hérite d'`int` : un test `isinstance(val, int)` posé avant
    celui du booléen rendrait `"1"` au lieu de `"true"`. L'ordre est le contrat."""
    assert db._ds_text(True) == "true" and db._ds_text(1) == "1"
