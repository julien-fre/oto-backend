"""Le double-service d'une migration : les anciens noms résolus, jamais écrits.

Le premier tableau à basculer est regardé quotidiennement par une cliente. La bascule
ne peut donc pas être un basculement : la colonne-tableau devient la vérité, et les
anciens noms plats restent SERVIS EN LECTURE le temps que chaque écran bascule.

Le gabarit est **DÉCLARÉ**. Résoudre `contact1_nom` vers `contacts[0].nom` en le
devinant rouvrirait l'interprétation de motif de nom que le barreau 1 vient de fermer
— exécuter une déclaration n'est pas deviner une convention. Il n'y a pas non plus de
gabarit par défaut : le défaut évident (`{key}{n}_{attr}`) rend `contacts1_nom` et pas
`contact1_nom`, comme la relecture du consommateur l'a montré.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore_schema as dsv2
from oto_mcp.datastore import RowValidationError, _refuse_flat_writes


def _schema(gabarit: str = "contact{n}_{attr}", type_: str = "list") -> dict:
    return {"fields": [
        {"key": "siren", "type": "text"},
        {"key": "contacts", "type": type_, "flat_alias": gabarit,
         "of": {"type": "object", "fields": [
             {"key": "nom", "type": "text"}, {"key": "email", "type": "email"}]}}]}


# --- résoudre un ancien nom --------------------------------------------------------

@pytest.mark.parametrize("nom,attendu", [
    ("contact1_nom", ("contacts", 0, "nom")),
    ("contact2_email", ("contacts", 1, "email")),
    ("contact10_nom", ("contacts", 9, "nom")),
])
def test_an_old_name_resolves_to_its_path(nom, attendu):
    assert dsv2.resolve_flat_name(_schema(), nom) == attendu


def test_the_layer_suffix_composes():
    """L'alias mappe le PRÉFIXE de chemin, la couche suit. Sans ça, les marques de
    provenance disparaîtraient des écrans pendant toute la fenêtre, sans message."""
    assert dsv2.resolve_flat_name(_schema(), "contact1_email.comment") == (
        "contacts", 0, "email.comment")


@pytest.mark.parametrize("etranger", [
    "siren", "contacts", "contact_nom", "contactX_nom", "nom", "contact1"])
def test_anything_else_is_left_alone(etranger):
    """Un nom qui ne correspond PAS au gabarit reste une colonne ordinaire — sinon la
    résolution mangerait des colonnes légitimes."""
    assert dsv2.resolve_flat_name(_schema(), etranger) is None


def test_without_a_declared_template_nothing_resolves():
    """Pas de gabarit ⟹ pas de double-service. Aucun repli sur une convention
    supposée : c'est la contrainte du barreau 1, tenue au même endroit."""
    sans = {"fields": [{"key": "contacts", "type": "list", "of": {"type": "text"}}]}
    assert dsv2.resolve_flat_name(sans, "contact1_nom") is None
    assert dsv2.flat_alias_of(sans) == {}


def test_a_template_is_literal_not_a_pattern():
    """Un gabarit est déclaré par un humain : ses caractères spéciaux sont du texte,
    pas une expression — sinon un point ou un plus se mettrait à tout matcher."""
    s = _schema("c.{n}+{attr}")
    assert dsv2.resolve_flat_name(s, "c.1+nom") == ("contacts", 0, "nom")
    assert dsv2.resolve_flat_name(s, "cX1Ynom") is None


def test_the_name_is_generated_one_indexed():
    """⚠️ Le `{n}` est 1-indexé — l'humain lit « contact1 » — alors que l'adressage
    compte à partir de 0. Asymétrie assumée, et c'est ici qu'elle coûterait le plus."""
    assert dsv2.flat_name("contact{n}_{attr}", 0, "nom") == "contact1_nom"
    assert dsv2.flat_name("contact{n}_{attr}", 3, "email") == "contact4_email"


def test_generating_and_resolving_are_inverse():
    """La propriété qui compte : ce qu'on SERT doit se relire. Deux implémentations
    d'un même gabarit finiraient par diverger d'un cran."""
    for rang in range(5):
        nom = dsv2.flat_name("contact{n}_{attr}", rang, "email.origine")
        assert dsv2.resolve_flat_name(_schema(), nom) == (
            "contacts", rang, "email.origine")


# --- ce que la déclaration exige ---------------------------------------------------

def test_a_template_only_makes_sense_on_a_list():
    errs = dsv2.validate_schema_def(_schema(type_="text"))
    assert errs and "flat_alias" in errs[0] and "list" in errs[0]


@pytest.mark.parametrize("bancal", [
    "contact_{attr}", "contact{n}_", "contact{n}{n}_{attr}", "c{attr}{attr}{n}"])
def test_a_template_missing_a_slot_is_refused(bancal):
    """Un gabarit sans `{n}` projetterait tous les rangs sur le même nom, et
    l'écraserait rang après rang — refusé à la DÉCLARATION, avant toute donnée."""
    assert dsv2.validate_schema_def(_schema(bancal))


def test_a_well_formed_declaration_passes():
    assert dsv2.validate_schema_def(_schema()) == []


# --- on ne l'écrit pas -------------------------------------------------------------

def test_writing_an_old_name_is_refused_and_says_where_to_write():
    """Accepter l'écriture créerait une colonne libre du même nom : la lecture
    continuerait de rendre la valeur PROJETÉE, et ce qui vient d'être écrit serait
    invisible tout en ayant été accepté — un accusé de réception pour un travail qui
    n'atteint rien."""
    with pytest.raises(RowValidationError) as e:
        _refuse_flat_writes(_schema(), {"contact1_nom": "Dupont"})
    msg = str(e.value)
    assert "contacts[0].nom" in msg, f"le refus doit dire où écrire : {msg}"


def test_writing_a_layer_of_an_old_name_is_refused_too():
    with pytest.raises(RowValidationError) as e:
        _refuse_flat_writes(_schema(), {"contact2_email.origine": "hunter"})
    assert "contacts[1].email.origine" in str(e.value)


def test_ordinary_columns_are_untouched():
    _refuse_flat_writes(_schema(), {"siren": "552081317", "contacts": []})
    _refuse_flat_writes(None, {"contact1_nom": "Dupont"})
