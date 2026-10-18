"""`force: [chemins]` — forcer les colonnes NOMMÉES, au lieu de tout l'appel (oto#140).

Le forçage existait en booléen : il valait pour l'appel entier, donc un lot de cinq
cents lignes forçait tout ce qu'il portait. Nommer les cibles resserre la PORTÉE.

⚠️ **Et pas la retenue — le contrat le note lui-même, mesuré : sur 57 appels portant le
forçage, 56 étaient à « oui ».** Tout paramètre offert au modèle sera réglé par lui. Ce
qui TIENT un forçage, c'est son palier : propriétaire du tableau ou qui le gouverne, un
accès en écriture partagé ne suffit pas. Ce banc fige les deux moitiés — ce que le
paramètre change, et ce qu'il ne change pas.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.forcage import Forcage, arbitrer, chemins_forces


# ── la portée ─────────────────────────────────────────────────────────────────

def test_seule_la_colonne_NOMMEE_est_forcee():
    f = Forcage(demande=True, autorise=True, chemins=frozenset({"raison_sociale"}))

    assert f.actif_sur("raison_sociale")
    assert not f.actif_sur("effectif")


def test_nommer_une_COUCHE_vise_sa_colonne():
    """`raison_sociale.origine` désigne la version d'origine de cette colonne — et
    c'est bien cette colonne-là que la garde interroge."""
    f = Forcage(demande=True, autorise=True,
                chemins=frozenset({"raison_sociale.origine"}))

    assert f.actif_sur("raison_sociale")
    assert not f.actif_sur("effectif")


def test_sans_cibles_le_forcage_vise_TOUT():
    """La forme historique du booléen ne change pas : un appelant qui ne nomme rien
    garde exactement le comportement d'avant."""
    assert Forcage(demande=True, autorise=True).actif_sur("n_importe_quoi")


# ── ce que le paramètre NE change PAS ────────────────────────────────────────

def test_nommer_une_cible_ne_donne_AUCUN_droit():
    """⚠️ La moitié qui compte. Sans le palier, nommer ses cibles ne force rien —
    sinon on aurait transformé une restriction de portée en élargissement de droit."""
    f = Forcage(demande=True, autorise=False, chemins=frozenset({"raison_sociale"}))

    assert not f.actif_sur("raison_sociale")
    assert arbitrer(f, "raison_sociale", "a", "b") is not None, "doit refuser"


# ── ce que le refus DIT ──────────────────────────────────────────────────────

def test_le_refus_sur_une_colonne_NON_nommee_ne_parle_pas_de_droit():
    """⚠️ Sans cette branche, l'appelant qui A le palier relit « forcer est réservé
    à… » et part chercher un droit qu'il possède déjà, au lieu de corriger sa liste.
    Un refus exact mais qui désigne la mauvaise cause coûte autant qu'un refus muet."""
    f = Forcage(demande=True, autorise=True, chemins=frozenset({"raison_sociale"}))
    refus = arbitrer(f, "effectif", "a", "b")

    assert "your `force` list does not name it" in refus
    assert "open to" not in refus, "il a le palier : ne pas l'envoyer le chercher"
    assert "effectif" in refus, "le refus nomme la colonne à ajouter"


# ── la validation du paramètre ───────────────────────────────────────────────

def test_une_liste_VIDE_est_refusee():
    """`force=[]` est un geste délibéré qui ne veut rien dire. Le lire comme une
    absence ferait passer une écriture refusée pour un refus ordinaire, et l'appelant
    chercherait un droit qu'il vient de demander."""
    with pytest.raises(ValueError) as e:
        chemins_forces([])
    assert "non-empty" in str(e.value)


@pytest.mark.parametrize("mauvais", [[1], [""], [None]])
def test_un_chemin_qui_n_est_pas_une_chaine_est_refuse_en_le_NOMMANT(mauvais):
    with pytest.raises(ValueError) as e:
        chemins_forces(mauvais)
    assert "is not a path" in str(e.value)


def test_rien_demande_vaut_None_et_non_un_ensemble_vide():
    """⚠️ `None` et `frozenset()` ne veulent pas dire la même chose : le premier laisse
    la portée d'appel entier, le second ne viserait RIEN. Les confondre désarmerait
    silencieusement tous les forçages qui ne nomment pas leurs cibles."""
    assert chemins_forces(None) is None


def test_le_texte_servi_dit_ce_qui_ne_change_PAS():
    """Laisser croire qu'on a resserré un droit ferait relâcher l'attention sur le seul
    mécanisme qui le tient — le palier."""
    from oto_mcp.datastore.forcage import description_parametre_cibles

    t = description_parametre_cibles()
    assert "PORTÉE, pas le droit" in t
    assert "réservé à" in t
