"""Un déroulé terminé sans action le DIT, et les réglages n'ont pas l'air acquis (#627).

Mesuré le 31/08/2026 sur un enrôlement incrémental : la réponse du déroulé
affirmait « 19/19 personnes ajoutées, ouverture conservée mot pour mot » avec une
liste d'actions VIDE. La campagne racontait l'inverse — personnes trouvées 52 →
71, contactées restées à 52, et 19 séquences neuves sans aucun destinataire.

⚠️ **Le pire assemblage possible pour un agent sans surveillance** : une prose de
succès et une trace vide. La prose vient du modèle d'en face et ne se contrôle
pas ; ce qui se contrôle, c'est qu'elle ne voyage pas seule — le fait mesurable
la contredit dans la même réponse.

⚠️ **La garde se TAIT sur ce qu'elle ne voit pas**, et c'est la moitié du lot. La
liste d'actions n'est pas dans le contrat documenté du fournisseur : on la cherche
à deux emplacements plausibles et on n'avertit que si on l'a trouvée vide. Une
garde qui devine une forme fabrique des fausses alertes, ce qui coûte exactement
la confiance qu'elle est censée servir.

⚠️ **Second défaut du même signal, plus discret** : les deux réglages passés à la
création étaient renvoyés en écho alors qu'ils ne s'appliquaient pas — l'agent
avait enrôlé dans une campagne EXISTANTE, dont les réglages propres gouvernent.
Un écho qui ne dit pas ce qu'il vaut se lit comme un acquis.

Éprouvé rouge le 2026-09-03 : la condition sur le statut retirée ⟹ le troisième
test constate qu'un déroulé encore EN COURS est accusé de n'avoir rien fait.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp.tools import origami
from oto_mcp.tools.origami import _dit_si_rien_n_a_ete_fait as garde

# Le déroulé du 31/08, réduit à ce qui produit le défaut.
_TERMINE_VIDE = {"status": "completed", "actions": [],
                 "response": {"text": "19/19 personnes ajoutées, opener conservé"}}


def test_un_deroule_TERMINE_sans_action_est_signale():
    out = garde(_TERMINE_VIDE)
    assert out["aucune_action"] is True
    assert "origami_campaigns" in out["aucune_action_hint"], (
        "il faut dire OÙ aller lire l'état réel, pas seulement qu'il y a doute")


def test_le_signalement_dit_le_SYMPTOME_a_chercher():
    """« Trouvées qui monte, contactées qui ne suit pas » est ce qui permet de
    reconnaître le cas sans connaître ce lot."""
    hint = garde(_TERMINE_VIDE)["aucune_action_hint"]
    assert "trouvées" in hint and "contactées" in hint


def test_un_deroule_EN_COURS_n_est_pas_accuse():
    """Une liste vide pendant l'exécution est normale — l'accuser ferait crier au
    loup à chaque sondage, et un avertissement permanent ne se lit plus."""
    assert "aucune_action" not in garde({"status": "running", "actions": []})


def test_la_garde_SE_TAIT_quand_la_forme_est_absente():
    """Le fournisseur ne documente pas cette liste. Deviner sa présence
    fabriquerait des alertes sur des déroulés parfaitement normaux."""
    assert "aucune_action" not in garde({"status": "completed"})
    assert "aucune_action" not in garde({"status": "completed", "steps": []})


def test_la_liste_est_cherchee_AUSSI_sous_la_reponse():
    """Le fournisseur la loge à l'un ou l'autre endroit selon le déroulé ; ne
    regarder qu'à la racine raterait la moitié des cas."""
    assert garde({"status": "completed",
                  "response": {"actions": []}})["aucune_action"] is True


def test_un_deroule_QUI_A_AGI_passe_sans_bruit():
    assert "aucune_action" not in garde(
        {"status": "completed", "actions": [{"type": "enrol"}]})


def test_l_entree_non_dict_traverse_intacte():
    """Le fournisseur peut rendre autre chose ; la garde ne doit jamais casser un
    appel qui marchait."""
    assert garde("texte") == "texte"
    assert garde(None) is None


@pytest.fixture(scope="module")
def prose() -> str:
    m = FastMCP("t")
    origami.register(m)
    return asyncio.run(m.get_tool("origami_campaign_create")).description or ""


def test_la_description_dit_que_les_REGLAGES_peuvent_ne_pas_s_appliquer(prose):
    """Le second défaut du signal : les réglages sont renvoyés en écho même quand
    la campagne visée est une autre, dont les réglages gouvernent."""
    plat = " ".join(prose.split())
    assert "CREATES" in plat and "NO effect" in plat


def test_la_description_dit_qu_AUCUN_verbe_ne_corrige_une_campagne_existante(prose):
    """Sans ça, l'agent cherche longtemps le verbe qui n'existe pas — c'est la
    seconde demande du signal, et la réponse est « c'est un geste humain »."""
    # La prose est repliée sur plusieurs lignes : on compare sur le texte aplati,
    # pas sur la mise en forme — sinon le banc rougit au prochain reformatage,
    # qui ne change rien à ce qu'il garde.
    plat = " ".join(prose.split())
    assert "Nothing in this connector updates an existing campaign's settings" in plat


def test_la_description_dit_de_NE_PAS_croire_la_prose_du_deroule(prose):
    plat = " ".join(prose.split())
    assert "not a measurement" in plat
    assert "aucune_action" in plat, "le nom du témoin doit être donné"
