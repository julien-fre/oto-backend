"""Une ligne de la timeline d'un déroulé peut s'OUVRIR — elle porte son identifiant.

Demandé par le dashboard produit : *« une ligne qui ne peut pas s'ouvrir est une
impasse »*. Mesuré avant d'écrire : la requête de la timeline sélectionnait six
colonnes et **aucun identifiant**, alors que la fiche d'un appel s'adresse justement
par cet identifiant. On voyait donc qu'un appel avait eu lieu sans pouvoir le lire.

⚠️ **Le défaut n'était pas une projection manquante, c'était une lecture manquante** —
et c'est ce qui le distingue du cas voisin. Sur le journal d'activité d'un tableau,
l'identifiant de ligne était **produit puis jeté** : déclarer le champ suffisait. Ici,
le SQL ne le ramenait pas. Déclarer sans élargir la requête aurait donné un contrat
juste et un `null` permanent — la panne la plus désagréable à diagnostiquer, parce que
tout a l'air correct des deux côtés.

⚠️ **Et il porte le MÊME nom que sur la fiche.** Deux noms pour la même ligne de
journal obligeraient chaque client à savoir laquelle des deux surfaces il lit.

Éprouvé rouge le 2026-09-02 : `id` retiré du SELECT ⟹ le second test le nomme ;
retiré du modèle ⟹ le premier.
"""
from __future__ import annotations

import inspect

from oto_mcp.capabilities.org_monitoring import CallDetail, RunCall
from oto_mcp.db import usage


def test_la_ligne_de_timeline_porte_un_identifiant():
    assert "id" in RunCall.model_fields, (
        "sans identifiant, une ligne de la timeline ne peut pas s'ouvrir")


def test_la_REQUÊTE_le_ramène_vraiment():
    """Le garde-fou qui compte. Un champ déclaré sur une requête inchangée vaut `null`
    pour toujours : le contrat serait juste et la donnée n'arriverait jamais."""
    sql = inspect.getsource(usage.get_run)
    assert "SELECT id," in sql, (
        "la timeline ne lit pas l'identifiant : `RunCall.id` resterait null")


def test_c_est_le_MÊME_nom_que_sur_la_fiche():
    """La timeline et la fiche parlent de la même ligne de journal. Les nommer
    différemment ferait porter au client la charge de savoir laquelle il lit — et
    c'est exactement ce qu'un contrat existe pour lui épargner."""
    assert "id" in CallDetail.model_fields

    # ⚠️ On compare le NOM et le type porté, pas l'optionalité : la fiche garantit son
    # identifiant (elle n'existe pas sans), la timeline le sert au milieu de champs
    # tous optionnels. Exiger la même annotation ferait rougir ce banc sur une
    # différence qui ne gêne personne — et un test trop strict finit contourné.
    assert "int" in str(RunCall.model_fields["id"].annotation)
    assert "int" in str(CallDetail.model_fields["id"].annotation)


def test_l_identifiant_est_DÉCRIT_et_pointe_vers_la_surface_qui_l_accepte():
    """Un identifiant servi sans dire ce qui le prend en entrée oblige à le deviner —
    et il y a deux surfaces plausibles ici (l'appel et le déroulé)."""
    desc = RunCall.model_json_schema()["properties"]["id"].get("description") or ""
    assert desc.strip(), "`RunCall.id` est servi sans description"
    assert "call_id" in desc, (
        "la description doit nommer la route qui accepte cet identifiant")
