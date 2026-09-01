"""Cliquet : le verdict d'accès d'un connecteur déclare ce que `status_for` produit.

`GET /api/me` sert un bloc `providers` riche — mode de résolution, quotas, étape
restante, restriction RBAC, santé de la clé. Il était typé `dict[str, Any]` jusqu'au
2026-09-01 : servi, consommé, et nommé nulle part. Un front qui construit une colonne
« état » ne pouvait donc rien en dériver sans observer le payload (#669).

⚠️ **La réserve d'origine reste valable, et elle ne portait pas sur ce qu'on déclare
ici** : « un objet ouvert plutôt qu'une énumération qui mentirait au premier connecteur
ajouté » vise les CLÉS du dictionnaire, pas la forme d'une valeur. Les clés restent
ouvertes ; c'est la valeur qui est stable, et c'est elle qu'on nomme.

Ce fichier lit l'AST du producteur (`access/status.py`) et compare les deux listes dans
les deux sens. Motif éprouvé le même jour sur deux autres surfaces, pour la même raison :
un modèle qui DÉCRIT sans valider dérive en silence, dans les deux sens, sans qu'aucune
erreur ne se lève jamais.

⚠️ Pourquoi l'AST et pas un appel : produire les quatre familles d'entrées (à clé, sans
clé, `cookie`, `oauth`) demanderait quatre montages de coffre et une cascade complète.
Un banc qui ne monterait que la famille facile certifierait une couverture qu'il n'a
pas — et les champs qui manquent au contrat sont justement ceux des familles rares.

Éprouvé rouge le 2026-09-01 avant d'être posé : `health_reason` retiré du modèle ⟹
échec le nommant ; champ inventé ajouté ⟹ échec le nommant.
"""
from __future__ import annotations

import ast
import pathlib

from oto_mcp.capabilities.connectors.provider_status import ProviderStatus

PRODUCTEUR = (pathlib.Path(__file__).resolve().parents[1]
              / "oto_mcp" / "access" / "status.py")

# Le dict d'une entrée porte toujours `mode` : c'est ce qui le distingue des autres
# dicts du module (l'enveloppe `{"role": …, "providers": {}}`, les index de santé).
MARQUEUR = "mode"
# Les enrichissements posés APRÈS coup, entrée par entrée : `entry["health_ko"] = …`.
VARIABLE_ENTREE = "entry"


def _produites() -> set[str]:
    arbre = ast.parse(PRODUCTEUR.read_text(encoding="utf-8"))
    cles: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Dict):
            noms = [k.value for k in noeud.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if MARQUEUR in noms:
                cles.update(noms)
        elif isinstance(noeud, ast.Assign):
            for cible in noeud.targets:
                if (isinstance(cible, ast.Subscript)
                        and isinstance(cible.value, ast.Name)
                        and cible.value.id == VARIABLE_ENTREE
                        and isinstance(cible.slice, ast.Constant)
                        and isinstance(cible.slice.value, str)):
                    cles.add(cible.slice.value)
    return cles


def _declarees() -> set[str]:
    return set(ProviderStatus.model_fields)


def test_le_banc_voit_bien_les_quatre_familles_et_les_ajouts():
    """Témoin. Un extracteur devenu muet (une forme d'AST qui change, la variable
    d'enrichissement renommée) rendrait les deux tests suivants verts sans avoir rien
    lu — le mode d'échec propre aux bancs qui comparent des ensembles."""
    produites = _produites()
    assert len(produites) >= 15, sorted(produites)
    assert "mode" in produites                    # les quatre familles
    assert "identity_label" in produites          # famille cookie seule
    assert "rbac_restricted" in produites         # posé après coup, sur chaque entrée
    assert "health_reason" in produites           # posé après coup, conditionnel


def test_toute_cle_produite_est_declaree():
    """Le sens qui sert le client : une clé envoyée et non déclarée est une donnée
    qu'il faut découvrir en l'observant, et qu'aucune promesse ne couvre ensuite."""
    manquantes = _produites() - _declarees()
    assert not manquantes, (
        f"produites par status_for mais absentes de ProviderStatus : "
        f"{sorted(manquantes)}")


def test_aucun_champ_declare_ne_reste_jamais_produit():
    """L'autre sens : un champ promis que rien ne pose se lit `undefined` côté client,
    sans erreur ni log — le défaut le plus cher parce qu'il ressemble à une donnée
    absente plutôt qu'à un contrat faux."""
    fantomes = _declarees() - _produites()
    assert not fantomes, (
        f"déclarés par ProviderStatus mais jamais produits : {sorted(fantomes)}")


def test_les_trois_refus_restent_trois_champs_distincts():
    """« Aucune clé ne résout », « l'accès t'est refusé » et « la clé ne répond plus »
    sont trois états qu'un écran doit distinguer. Les fondre en un seul a déjà produit
    un mur « réservé à certaines équipes » devant quelqu'un que rien ne bloquait."""
    for champ in ("mode", "rbac_restricted", "health_ko"):
        assert champ in _declarees()
