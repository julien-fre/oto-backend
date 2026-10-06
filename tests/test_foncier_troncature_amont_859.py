"""Une coupe en AMONT du seuillage ne se raconte pas comme un compte (#859).

Mesuré le 10/09/2026 sur un département entier : `min_mwh=2000, limit=60` répondait
`total: 2` avec `lignes_lues: 60`, et `limit=-1` sur la même requête rendait **61**
signaux. Le `total: 2` se lit comme « il n'y a que deux gros consommateurs
industriels ici » — **un gisement faux à 97 %**, sans la moindre erreur.

⚠️ **Et l'avertissement censé sauver la mise se trompait aussi.** Il annonçait
« `total` = 60 est le nombre de lignes RENDUES » : deux fois faux, puisque 60 est le
nombre de lignes LUES et que `total` valait 2. Une garde qui nomme la coupe mais
décrit mal ce qu'elle a coupé déplace le mensonge d'un cran au lieu de le fermer.

⚠️ **La cause est l'ORDRE** : `limit` plafonne la lecture, le seuil s'applique après.
Une lecture plafonnée ne seuille donc que ce qu'elle a eu le temps de lire, et
`total` devient un PLANCHER — jamais un compte.

Éprouvé rouge le 2026-09-10 : l'ancien texte unique rétabli ⟹ le premier test
constate qu'on annonce 60 lignes rendues là où il y en a 2.
"""
from __future__ import annotations

from oto_mcp.tools.foncier import _marquer_troncature


def test_la_coupe_sur_les_lignes_LUES_ne_parle_pas_de_lignes_rendues():
    """Le cas mesuré : deux retenus parmi soixante lues, borne à soixante."""
    res = _marquer_troncature({"total": 2, "lignes_lues": 60}, 60, compte=60)
    a = res["avertissement_troncature"]
    assert res["tronque"] is True
    assert "60 rows were READ" in a
    assert "RETURNED" not in a, "l'ancien texte parlait de lignes rendues — c'était faux"


def test_l_avertissement_dit_les_DEUX_nombres_et_ce_que_chacun_est():
    """Sans les deux, l'appelant ne peut pas savoir lequel il a sous les yeux."""
    a = _marquer_troncature({"total": 2}, 60, compte=60)["avertissement_troncature"]
    assert "`total` = 2" in a and "AMONG these 60" in a


def test_l_avertissement_nomme_le_GESTE_qui_donne_le_vrai_chiffre():
    """Dire qu'un chiffre est faux sans dire comment obtenir le bon laisse
    l'appelant exactement où il était."""
    a = _marquer_troncature({"total": 2}, 60, compte=60)["avertissement_troncature"]
    assert "limit=-1" in a


def test_l_ordre_est_NOMME_parce_que_c_est_la_cause():
    a = _marquer_troncature({"total": 2}, 60, compte=60)["avertissement_troncature"]
    assert "UPSTREAM of the thresholding" in a


def test_une_coupe_SANS_seuillage_garde_son_texte_d_origine():
    """La contre-épreuve : quand la coupe porte bien sur ce qui est rendu, l'ancien
    message est juste et ne doit pas changer — un lot qui corrige un cas ne doit pas
    réécrire le cas voisin qui allait bien."""
    a = _marquer_troncature({"total": 200}, 200)["avertissement_troncature"]
    assert "number of rows RETURNED" in a and "UPSTREAM" not in a


def test_un_compte_EGAL_au_total_retombe_sur_le_texte_simple():
    """Si la coupe porte sur le même nombre, il n'y a pas deux chiffres à
    distinguer : inventer une nuance ici ajouterait du bruit."""
    a = _marquer_troncature({"total": 60}, 60, compte=60)["avertissement_troncature"]
    assert "number of rows RETURNED" in a


def test_sous_la_borne_rien_n_est_annonce():
    res = _marquer_troncature({"total": 2, "lignes_lues": 12}, 60, compte=12)
    assert res["tronque"] is False and "avertissement_troncature" not in res
