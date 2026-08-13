"""La lecture descend dans une colonne-tableau (oto#22 barreau 2, étape 1).

La garantie du datastore est que **le nom nu rend la valeur, jamais la structure
interne**. Elle tenait au premier niveau et s'arrêtait là : un item de liste dont un
attribut porte une provenance ressortait enveloppé, si bien que
`row["contacts"][0]["email"]` rendait `{"valeur": …, "origine": …}` au lieu d'un
e-mail. Le consommateur qui lit un e-mail casserait le jour où quelqu'un pose une
source sur un contact — silencieusement, et sur le chemin qu'on recommande.

C'est le prérequis de tout le reste : aucune des fonctions natives ne tient si la
forme servie n'est pas celle-là. Scout la rebranche à partir de ce contrat.
"""
from __future__ import annotations

from oto_mcp import datastore_schema as dsv2
from oto_mcp.datastore import DatastorePg


def _lu(data: dict) -> dict:
    return DatastorePg._row_to_dict(
        {"row_id": "r1", "created_at": "t", "updated_at": "t", "data": data})


_CONTACTS = {"contacts": [
    {"nom": "Dupont",
     "email": {"valeur": "d@x.fr", "origine": "socle client", "comment": "hunter"}},
    {"nom": {"valeur": "Martin"}, "email": None},
]}


# --- la forme servie ---------------------------------------------------------------

def test_an_item_attribute_reads_as_its_value():
    """LE contrat : un attribut d'item est une feuille, il rend sa valeur."""
    contacts = _lu(_CONTACTS)["contacts"]
    assert contacts[0]["email"] == "d@x.fr"
    assert contacts[1]["nom"] == "Martin"


def test_the_layers_of_an_item_are_flattened_inside_the_item():
    """La règle du premier niveau, un cran plus bas — pas un second vocabulaire :
    qui sait lire `row["email.origine"]` sait lire `item["email.origine"]`."""
    contact = _lu(_CONTACTS)["contacts"][0]
    assert contact["email.origine"] == "socle client"
    assert contact["email.comment"] == "hunter"


def test_empty_layers_are_not_rendered_in_an_item():
    """On ne rend pas du vide : un item sans provenance reste identique à ce qu'il
    était, sinon toute liste existante se met à porter des clés en plus."""
    item = _lu({"contacts": [{"nom": "Dupont", "email": "d@x.fr"}]})["contacts"][0]
    assert item == {"nom": "Dupont", "email": "d@x.fr"}


def test_a_hole_is_an_empty_object_never_null():
    """Le rang est RÉSERVÉ, pas absent. Un `null` obligerait chaque consommateur à
    garder son itération, alors qu'il veut lire `item.get("nom")` et passer."""
    contacts = _lu({"contacts": [{}, {"nom": "Martin"}]})["contacts"]
    assert contacts[0] == {}
    assert contacts[1]["nom"] == "Martin"


def test_a_list_of_scalars_stays_a_list_of_scalars():
    """`of` peut déclarer un scalaire : rien ne doit être transformé."""
    assert _lu({"tags": ["a", "b"]})["tags"] == ["a", "b"]


def test_a_layered_column_whose_value_is_a_list_is_served_deep():
    """Une colonne-tableau peut elle-même porter des couches : on descend quand
    même — l'enveloppe du dessus ne doit pas masquer les feuilles du dessous."""
    out = _lu({"contacts": {"valeur": [{"email": {"valeur": "a@b.c"}}],
                            "origine": "import"}})
    assert out["contacts"][0]["email"] == "a@b.c"
    assert out["contacts.origine"] == "import"


# --- ce qui ne change pas ----------------------------------------------------------

def test_the_first_level_is_unchanged():
    """Tout l'existant passe par là : la descente ne doit rien déplacer au-dessus."""
    out = _lu({"email": {"valeur": "a@b.c", "comment": "hunter"}, "nom": "ACME"})
    assert out["email"] == "a@b.c"
    assert out["email.comment"] == "hunter"
    assert out["nom"] == "ACME"


def test_a_plain_json_column_is_untouched():
    """Un `json` métier n'est pas une liste de fiches — on n'y descend pas."""
    brut = {"a": 1, "b": {"c": 2}}
    assert _lu({"config": brut})["config"] == brut


def test_unwrap_still_returns_the_raw_value():
    """`unwrap` juge, `served_value` sert : la VALIDATION doit continuer de recevoir
    la valeur brute, sans les couches aplaties qu'elle prendrait pour des champs
    inconnus. Deux usages, deux fonctions — les confondre ferait refuser une écriture
    valide au motif d'un `email.origine` qu'on aurait fabriqué soi-même."""
    item = {"email": {"valeur": "a@b.c", "origine": "x"}}
    assert dsv2.unwrap({"contacts": [item]}) == {"contacts": [item]}
    assert dsv2.served_value([item]) == [{"email": "a@b.c", "email.origine": "x"}]


def test_the_flattening_has_a_single_implementation():
    """Le premier niveau et les items appellent le MÊME aplatisseur : deux copies
    exposeraient deux formes de la même chose, et c'est le consommateur qui paierait
    la différence."""
    couches = {"valeur": "v", "origine": "o", "comment": "", "link": None}
    assert dsv2.flat_layers("x", couches) == {"x.origine": "o"}
    assert _lu({"x": couches})["x.origine"] == "o"
    assert _lu({"l": [{"x": couches}]})["l"][0]["x.origine"] == "o"
