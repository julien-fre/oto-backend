"""Écrire une colonne à couches : la valeur se juge déballée, l'origine survit (#318).

Deux règles, et elles ont la même raison — l'agent ne doit avoir à penser à rien :

- **la validation déballe** : un schéma strict qui déclare `email` en `text` doit
  juger la VALEUR, pas l'enveloppe. Sans ça, la primitive est inutilisable
  précisément sur les tableaux qu'on recommande de rendre stricts ;
- **l'origine survit** à une écriture ordinaire. C'est la protection contre
  l'ACCIDENT, pas contre l'intention : un geste qui vise l'origine la remplace, il
  suffit de l'écrire.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore_schema as dsv2
from oto_mcp.datastore import _merge_column


# --- la validation juge la valeur ----------------------------------------------

_STRICT = {"strict": True, "fields": [
    {"key": "email", "type": "email"},
    {"key": "effectif", "type": "number"},
    {"key": "statut", "type": "enum", "options": ["a", "b"]},
    {"key": "nom", "type": "text", "required": True, "max_length": 5},
]}


def _err(row):
    return dsv2.validate_row(_STRICT, row)


def test_a_layered_value_passes_the_type_check():
    """Le cas qui débloque tout : un objet là où le schéma attend un e-mail."""
    assert _err({"nom": "ACME", "email": {"valeur": "a@b.c", "comment": "hunter"}}) == []


def test_a_layered_value_is_still_JUDGED():
    """Déballer n'est pas dispenser : une valeur fausse reste fausse."""
    errs = _err({"nom": "ACME", "email": {"valeur": "pas-un-email", "comment": "x"}})
    assert errs and "email" in errs[0]


def test_options_are_checked_on_the_value():
    assert _err({"nom": "ACME", "statut": {"valeur": "a"}}) == []
    errs = _err({"nom": "ACME", "statut": {"valeur": "zzz"}})
    assert errs and "hors options" in errs[0]


def test_a_bound_measures_the_value_not_the_envelope():
    """`max_length: 5` doit mesurer « ACME », pas le JSON qui l'enveloppe — sinon
    toute écriture en couches dépasserait, quelle que soit la valeur."""
    assert _err({"nom": {"valeur": "ACME", "comment": "registre"}}) == []
    errs = _err({"nom": {"valeur": "BEAUCOUP TROP LONG", "comment": "x"}})
    assert errs and "nom" in errs[0]


def test_a_required_field_empty_in_its_layers_is_missing():
    errs = _err({"nom": {"valeur": "", "comment": "x"}})
    assert errs and "requis" in errs[0]


def test_a_flat_row_is_judged_exactly_as_before():
    assert _err({"nom": "ACME", "email": "a@b.c", "effectif": 3}) == []
    assert _err({"nom": "ACME", "email": "pas-un-email"})


# --- l'origine survit -----------------------------------------------------------

def test_an_ordinary_write_keeps_the_origin():
    """LE cas : l'agent écrit une valeur nue, sans savoir qu'il y a des couches."""
    out = _merge_column({"valeur": "ancien", "origine": "import"}, "nouveau")
    assert out == {"valeur": "nouveau", "origine": "import"}


def test_the_other_layers_follow_the_value():
    """`comment`/`link` décrivent LA VALEUR : les garder au-dessus d'une valeur
    remplacée ferait affirmer une provenance fausse — le défaut qu'on élimine, une
    couche plus haut."""
    out = _merge_column(
        {"valeur": "a@b.c", "origine": "import", "comment": "hunter",
         "link": "https://x"},
        "autre@x.fr")
    assert out == {"valeur": "autre@x.fr", "origine": "import"}


def test_an_explicit_gesture_replaces_the_origin():
    """Pas de verrou : viser l'origine suffit à la remplacer. Un ré-import repose
    simplement une nouvelle valeur de départ."""
    out = _merge_column({"valeur": "x", "origine": "vieux"},
                        {"valeur": "y", "origine": "neuf"})
    assert out["origine"] == "neuf"


def test_writing_layers_without_an_origin_keeps_the_existing_one():
    out = _merge_column({"valeur": "x", "origine": "import"},
                        {"valeur": "y", "comment": "registre"})
    assert out == {"valeur": "y", "comment": "registre", "origine": "import"}


def test_a_column_without_an_origin_is_replaced_plainly():
    """Rien à préserver ⇒ le comportement d'avant, à l'identique. C'est le cas des
    43 782 lignes existantes, et il ne doit pas coûter une ligne de logique."""
    assert _merge_column("ancien", "nouveau") == "nouveau"
    assert _merge_column(None, "nouveau") == "nouveau"
    assert _merge_column({"a": 1}, "nouveau") == "nouveau"
    assert _merge_column({"valeur": "x", "comment": "s"}, "nouveau") == "nouveau"
