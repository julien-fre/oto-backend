"""Ce que déclare le schéma d'une colonne-tableau (oto#22 barreau 2, étape 2).

La DÉCLARATION existait déjà (`type: "list"` + `of:`, ADR 0046) — ce lot ajoute les
deux gardes qui la rendent sûre, et vérifie que ce dont le consommateur a besoin pour
dériver ses écrans traverse bien.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore_schema as dsv2


def _schema(**kw) -> dict:
    base = {"strict": True, "fields": [
        {"key": "siren", "type": "text"},
        {"key": "contacts", "type": "list", "max_items": 2, "label": "Interlocuteurs",
         "of": {"type": "object", "fields": [
             {"key": "nom", "type": "text", "label": "Nom",
              "description": "Nom d'usage, tel qu'il se présente"},
             {"key": "email", "type": "email"}]}}]}
    base.update(kw)
    return base


def _contacts(n: int) -> dict:
    return {"siren": "552081317",
            "contacts": [{"nom": f"C{i}"} for i in range(n)]}


# --- la borne du nombre d'items ----------------------------------------------------

def test_a_list_within_its_bound_passes():
    assert dsv2.validate_row(_schema(), _contacts(2)) == []


def test_going_over_the_bound_says_by_how_much():
    """Même forme que la borne de longueur : le CONSTATÉ autant que la borne. Un refus
    qui ne dit pas de combien on dépasse fait deviner — et sur un import, deviner
    signifie relancer le lot pour voir."""
    errs = dsv2.validate_row(_schema(), _contacts(5))
    assert errs and "5" in errs[0] and "2" in errs[0]


def test_no_bound_declared_means_no_bound():
    """`max_items` est OPT-IN comme le reste de la validation : un tableau qui ne
    l'a pas déclaré ne se met pas à refuser des lignes qu'il acceptait hier."""
    sans = {"strict": True, "fields": [
        {"key": "contacts", "type": "list", "of": {"type": "object", "fields": []}}]}
    assert dsv2.validate_row(sans, {"contacts": [{} for _ in range(50)]}) == []


def test_a_bound_of_zero_or_a_boolean_is_ignored():
    """`True` est un `int` en Python : une borne booléenne bornerait à 1 élément,
    silencieusement. C'est le piège qui a déjà coûté un filtre booléen jeté."""
    for bidon in (0, True, False, "2", None):
        s = {"strict": True, "fields": [
            {"key": "c", "type": "list", "max_items": bidon,
             "of": {"type": "object", "fields": []}}]}
        assert dsv2.validate_row(s, {"c": [{}, {}, {}]}) == [], f"borne {bidon!r}"


# --- une clé métier n'est jamais un sous-tableau -----------------------------------

@pytest.mark.parametrize("type_compose", ["list", "object"])
def test_a_business_key_cannot_be_composite(type_compose):
    """Refusé à la DÉCLARATION, pas à la première écriture : le tableau serait déjà
    peuplé de doublons quand on s'en apercevrait.

    La raison est mécanique — l'unicité porte sur une EXPRESSION textuelle : deux
    listes équivalentes d'ordre différent ne collisionneraient pas, donc la clé
    n'identifierait rien."""
    s = {"key": "contacts", "fields": [
        {"key": "contacts", "type": type_compose, "of": {"type": "text"},
         "fields": []}]}
    errs = dsv2.validate_schema_def(s)
    assert errs and "contacts" in errs[0] and type_compose in errs[0]


def test_a_scalar_business_key_is_fine():
    assert dsv2.validate_schema_def(
        {"key": "siren", "fields": [{"key": "siren", "type": "text"}]}) == []


def test_a_business_key_naming_no_declared_field_is_left_alone():
    """Un tableau libre déclare souvent sa clé sans lister ses champs — refuser ici
    casserait un usage courant pour un cas qui n'est pas celui qu'on ferme."""
    assert dsv2.validate_schema_def({"key": "email", "fields": []}) == []


# --- ce dont le consommateur a besoin traverse -------------------------------------

def test_the_declared_labels_and_descriptions_survive():
    """scout dérive TOUS ses écrans du schéma : sans `label`/`description` sur les
    attributs d'un item, il n'a rien à afficher sous un intitulé. Le schéma est stocké
    et rendu tel quel — ce test fige le fait, pour qu'un futur nettoyage de clés
    inconnues ne les emporte pas en silence."""
    attrs = _schema()["fields"][1]["of"]["fields"]
    assert attrs[0]["label"] == "Nom"
    assert attrs[0]["description"].startswith("Nom d'usage")
    assert dsv2.validate_schema_def(_schema()) == []


def test_item_attributes_are_validated_like_any_field():
    """Un attribut d'item est une feuille : son type vaut, sinon la déclaration ne
    protège rien là où la donnée est la plus dense."""
    errs = dsv2.validate_row(
        _schema(), {"siren": "1", "contacts": [{"nom": "A", "email": "pas-un-email"}]})
    assert errs and "email" in errs[0]
