"""Un refus de pose de schéma DIT pourquoi — il ne rend plus `invalid_schema` nu.

Le défaut, et il a failli coûter un développement inutile. La route de pose refusait
tout `ValueError` par un code seul : le corps complet du 400 faisait **vingt-six
caractères**, `{"error":"invalid_schema"}`. Le détail restait côté serveur.

Mesuré le 08/09/2026 : une session a tâtonné sur cinq essais, conclu que `pattern` ne
fonctionnait pas, et s'apprêtait à remonter au propriétaire du produit une demande de
capacité neuve. Le message existait, et il disait exactement quoi corriger —
« pattern exige max_length sur le même champ ». Personne ne l'a jamais vu.

⚠️ **Le silence était DÉLIBÉRÉ et sa raison était bonne** : un des refus de pose cite
des valeurs de données (un échantillon de doublons de clé métier). Mais il a été
appliqué à TOUS, y compris à ceux qui ne citent rien d'autre que la déclaration que
l'appelant vient d'envoyer.

D'où la ligne de partage que ce banc fige, et elle a deux côtés :

1. **un refus qui ne parle que du SCHÉMA POSÉ est rendu** — l'appelant l'a écrit, le
   lui rendre ne lui apprend rien qu'il n'ait déjà ;
2. **un refus qui cite des valeurs de LIGNES reste muet** — et sans ce second cas, la
   correction se lirait comme « on ouvre tout », ce qu'elle n'est pas.
"""
from __future__ import annotations

import inspect

import pytest

from oto_mcp.datastore import schema_ops
from oto_mcp.datastore.errors import SchemaDefinitionError


# ── Les refus qui ne parlent que de la déclaration envoyée ───────────────────

@pytest.mark.parametrize("nom,champ,attendu", [
    # Le cas d'origine (« pattern exige max_length ») n'est plus un refus depuis
    # oto#103 : un motif seul s'applique. Un motif au coût non majorable, si.
    ("un motif au coût non majorable",
     {"key": "notes", "type": "text", "pattern": r"(a+)+$"},
     "pattern"),
    ("un type inconnu",
     {"key": "notes", "type": "texte"},
     "type"),
    ("une liste sans déclaration d'élément",
     {"key": "contacts", "type": "list"},
     "of"),
])
def test_le_refus_NOMME_ce_qui_cloche(nom, champ, attendu):
    """Chacun de ces refus tient déjà un texte actionnable — ce banc garde qu'il
    porte le TYPE qui le fait sortir jusqu'à l'appelant."""
    from oto_mcp.datastore import schema as dsv2

    errs = dsv2.validate_schema_def({"fields": [champ]})

    assert errs, f"{nom} devrait être refusé"
    assert attendu in errs[0], f"le refus doit nommer `{attendu}` : {errs[0]!r}"


def test_le_type_du_refus_le_fait_SORTIR():
    """⚠️ Le cœur. Un refus de déclaration est un `SchemaDefinitionError`, et c'est
    ce type — et lui seul — que la route rend à l'appelant. Sans lui, le message
    existe et meurt côté serveur."""
    src = inspect.getsource(schema_ops)

    assert "SchemaDefinitionError(\"schéma invalide : \"" in src, (
        "le refus de définition doit porter le type qui le rend visible")


def test_la_route_REND_le_message():
    """La seconde moitié, et sans elle la première ne sert à rien : le type doit être
    attrapé À PART sur la route, avec son texte."""
    from oto_mcp.capabilities.datastore import schema as cap

    src = inspect.getsource(cap)

    assert "except SchemaDefinitionError" in src
    assert 'AuthzDenied(400, "invalid_schema", str(e))' in src, (
        "le message doit accompagner le code, pas rester côté serveur")


# ── ⚠️ La contre-épreuve : ce qui cite des DONNÉES reste muet ────────────────

def test_le_refus_qui_cite_des_VALEURS_DE_LIGNES_reste_muet():
    """La borne du lot, et elle est aussi importante que l'ouverture.

    Le refus d'une clé métier en doublon cite un échantillon des valeurs trouvées
    dans les lignes — donc des données du client. L'ouvrir serait un choix de
    produit, pas une correction de ce défaut-ci. Il reste un `ValueError` ordinaire,
    et la route continue de le taire.

    Sans ce banc, quelqu'un typerait un jour ce refus « pour être cohérent », et la
    correction d'un silence deviendrait une divulgation."""
    src = inspect.getsource(schema_ops)
    bloc = src.split("dups = db.datastore_key_dup_groups")[1][:600]

    assert "raise ValueError(" in bloc, (
        "le refus des doublons cite des valeurs de lignes : il doit rester muet "
        "sur la face publique")
    assert "SchemaDefinitionError" not in bloc


def test_la_route_garde_son_repli_muet():
    """Et la route garde la branche qui tait le reste — un `except` unique aurait
    ouvert les deux d'un coup."""
    from oto_mcp.capabilities.datastore import schema as cap

    src = inspect.getsource(cap)

    assert 'except ValueError:' in src
    assert src.count('AuthzDenied(400, "invalid_schema")') >= 1, (
        "le repli muet doit subsister pour les refus qui citent des données")
