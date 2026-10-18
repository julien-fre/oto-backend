"""Un périmètre de réservation mal déclaré accuse le SCHÉMA, plus l'appelant.

Mesuré le 08/09/2026 par la session de flotte, sur un banc réel : un tableau déclare
`lifecycle.claimable` comme une liste là où le moteur attend un objet. Toute
réservation échoue — **60 refus sur 81 appels**.

⚠️ **Ce n'est pas le refus qui coûtait, c'est ce qu'il produisait chez qui le
recevait.** Il décrivait la forme attendue du paramètre, dans la réponse à un appel :
l'agent en concluait que c'était SON argument. Mesuré sur dix runs — il réessaie en
variant sa forme jusqu'au plafond de tours, puis conclut sereinement son travail en
expliquant « la structure exacte attendue ». **Aucun n'a compris qu'il n'y avait rien
à corriger de son côté.**

Un refus qui ne dit pas OÙ porter l'intention fait rejouer le même appel : ici huit
fois par run.

⚠️ Et le raisonnement qui justifiait le message était faux. La docstring affirmait
qu'une forme illisible « ne peut venir que d'une écriture hors surface », puisque la
pose la refuse. Le schéma fautif existe pourtant : il est **antérieur à la garde**. Un
schéma en base ne se repose jamais — *« c'est refusé à la pose, donc ça n'existe
pas »* est faux pour toute garde ajoutée après coup.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import non_applique as na
from oto_mcp.datastore import schema as dsv2

MAL_DECLARE = {"fields": [
    {"key": "statut", "type": "enum", "role": "status", "options": ["a_traiter"],
     "lifecycle": {"states": ["a_traiter"], "claimable": ["a_traiter"]}},
]}


def test_le_refus_dit_que_l_APPEL_n_est_pas_en_cause():
    """La phrase qui aurait épargné dix agents. Elle vient en premier parce que c'est
    la seule qui arrête le réessai."""
    with pytest.raises(ValueError) as e:
        dsv2.claimable_of(MAL_DECLARE, 45)

    msg = str(e.value)
    assert "Your call is not at fault" in msg
    assert "retrying will fail the same way" in msg, (
        "sans ça, l'agent peut croire à une contention passagère")


def test_le_refus_NOMME_le_tableau_et_le_geste_qui_repare():
    with pytest.raises(ValueError) as e:
        dsv2.claimable_of(MAL_DECLARE, 45)

    msg = str(e.value)
    assert "Table 45" in msg, "le porteur du défaut doit être désigné"
    assert "owner" in msg, "et qui peut le réparer"
    assert "data_patch_schema" in msg, "et par quel geste"
    assert "Report it rather than retrying" in msg


def test_le_refus_cite_toujours_la_valeur_DECLAREE():
    """C'est ce qui a permis à la flotte de trancher — la garder."""
    with pytest.raises(ValueError) as e:
        dsv2.claimable_of(MAL_DECLARE, 45)

    assert "['a_traiter']" in str(e.value)


def test_sans_numero_le_refus_reste_lisible():
    """Les lectures hors file n'ont pas le numéro sous la main ; le message ne doit
    pas s'en trouver amputé de son sens."""
    with pytest.raises(ValueError) as e:
        dsv2.claimable_of(MAL_DECLARE)

    assert "This table declares" in str(e.value)


# ── ⚠️ Un diagnostic ne lève jamais sur ce qu'il diagnostique ────────────────

def test_l_avertissement_ne_CASSE_PAS_sur_un_perimetre_illisible():
    """**Défaut introduit par mon propre lot, trouvé avant livraison.**

    L'avertissement du cycle de vie appelle la lecture du périmètre pour dire ce qui
    manque. Cette lecture LÈVE sur une forme illisible — ce qui est juste sur le
    chemin de la file, où ignorer rouvrirait le tableau en silence. Mais ici, la
    levée faisait échouer la LECTURE ENTIÈRE du schéma : un tableau au périmètre mal
    formé devenait illisible, et mon avertissement cassait sur exactement le genre de
    déclaration qu'il existe pour signaler."""
    sch = {"fields": [
        MAL_DECLARE["fields"][0],
        {"key": "suivi", "lifecycle": {"states": ["x"]}},
    ]}

    champs = na.lifecycle_hors_statut(sch)
    phrase = na.lifecycle_hors_statut_warning(champs, sch)

    assert champs == ["suivi"]
    assert phrase and "claim scope" in phrase, (
        "un périmètre illisible compte comme un cran manquant, pas comme un plantage")
