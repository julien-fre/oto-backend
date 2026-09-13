"""Les deux mots réservés dans un élément de LISTE — le trou qui a atteint la production.

Trouvé le 08/09/2026 par une campagne, **sur de la donnée servie et non dans un
journal** : sur une fiche réelle, `contacts[0].commentaire.comment` valait littéralement
`"@keep"`. L'agent avait écrit le mot au bon endroit — la couche d'un attribut
d'élément est un endroit parfaitement légitime — et la plateforme l'a pris pour du
texte. Une ligne de plus et la cliente lisait « @keep » dans le commentaire d'un
contact.

⚠️ **Le trou n'était pas là où j'avais regardé.** J'avais fermé `{"champ": "@keep"}` —
le mot posé sur une case entière — et mesuré cinq gestes pour le prouver. **Aucun des
cinq ne traversait une liste.** Une liste sans identité d'élément se REMPLACE en bloc :
`_merge_column` ne descend pas dedans, donc rien n'y résolvait les sentinelles.

**Les deux mots ne se traitent pas pareil, et la raison est structurelle :**

- `@empty` **se résout** — « vide-le » ne demande aucun passé ;
- `@keep` **se refuse** — « garde ce qui est là » exige de savoir QUEL élément
  précédent correspond. Sans identité déclarée, on ne le sait pas, et choisir au
  hasard sur des données de personnes est le mode d'échec qu'on s'est interdit.
  Le laisser tomber perdrait l'intention ; le stocker l'expédie chez la cliente.
  **Refuser en nommant le geste est la seule des trois issues qui ne ment pas.**
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore.columns import _merge_column
from oto_mcp.datastore.errors import RowValidationError

SANS_CLE = {"key": "contacts", "type": "list",
            "of": {"fields": [{"key": "nom"}, {"key": "commentaire"}]}}
AVEC_CLE = {"key": "contacts", "type": "list",
            "of": {"key": "role", "fields": [{"key": "role"},
                                             {"key": "commentaire"}]}}


def _avant() -> list:
    return [{"nom": "Guillaume",
             "commentaire": {"valeur": "ancien", "comment": "registre du 05/08"}}]


# ── ⚠️ Le cas exact mesuré en production ────────────────────────────────────

def test_le_mot_dans_la_COUCHE_d_un_attribut_d_element_est_refuse():
    """`contacts[0].commentaire.comment` — le chemin littéral de la fiche fautive."""
    pose = [{"nom": "Guillaume",
             "commentaire": {"valeur": "Mandataire au registre.",
                             "comment": dsl.GARDE}}]

    with pytest.raises(RowValidationError) as e:
        _merge_column(_avant(), pose, SANS_CLE)

    msg = str(e.value)
    assert "contacts[0].commentaire.comment" in msg, (
        "le refus doit nommer le CHEMIN exact, sinon il fait chercher dans la fiche")
    assert "of.key" in msg, "et dire le geste qui le ferait fonctionner"
    assert dsl.VIDE_DELIBERE in msg, "et dire lequel des deux mots marche ici"


def test_le_mot_sur_l_ATTRIBUT_lui_meme_est_refuse_aussi():
    """La seconde profondeur demandée : `liste[].sous_champ`, sans couche."""
    pose = [{"nom": dsl.GARDE, "commentaire": "x"}]

    with pytest.raises(RowValidationError) as e:
        _merge_column(_avant(), pose, SANS_CLE)

    assert "contacts[0].nom" in str(e.value)


def test_AUCUN_mot_reserve_n_atteint_le_stockage_par_une_liste():
    """La garde de dernier recours, sur les deux profondeurs à la fois. C'est
    l'assertion que mon premier banc aurait dû porter et ne portait pas."""
    for pose in (
        [{"nom": dsl.GARDE}],
        [{"nom": "G", "commentaire": {"valeur": dsl.GARDE}}],
        [{"nom": "G", "commentaire": {"valeur": "x", "comment": dsl.GARDE}}],
    ):
        with pytest.raises(RowValidationError):
            _merge_column(_avant(), pose, SANS_CLE)


# ── `@empty` tient sans identité, parce qu'il ne demande aucun passé ─────────

@pytest.mark.parametrize("pose,attendu", [
    ([{"nom": "G", "commentaire": {"valeur": "x", "comment": dsl.VIDE_DELIBERE}}],
     {"valeur": "x", "comment": ""}),
    # oto#204, décision du plan : `@empty` sur la VALEUR assume le vide — le marqueur est
    # posé ; `@clear` efface sans l'assumer, et rend ce qu'`@empty` rendait avant.
    ([{"nom": "G", "commentaire": {"valeur": dsl.VIDE_DELIBERE}}],
     {"valeur": "", dsl.VIDE_ASSUME: True}),
    ([{"nom": "G", "commentaire": {"valeur": dsl.EFFACEMENT}}],
     {"valeur": ""}),
])
def test_vider_delibrement_fonctionne_dans_un_element(pose, attendu):
    out = _merge_column(_avant(), pose, SANS_CLE)

    assert out[0]["commentaire"] == attendu


# ── Avec une identité déclarée, `@keep` fonctionne ──────────────────────────

def test_avec_of_key_le_mot_est_TENU_et_non_refuse():
    """La contre-épreuve, et elle rend le refus honnête : le message dit « déclare
    `of.key` » — encore faut-il que ça marche alors."""
    avant = [{"role": "contact_rh",
              "commentaire": {"valeur": "ancien", "comment": "registre du 05/08"}}]
    pose = [{"role": "contact_rh",
             "commentaire": {"valeur": "Mandataire au registre.",
                             "comment": dsl.GARDE}}]

    out = _merge_column(avant, pose, AVEC_CLE)

    assert out[0]["commentaire"]["comment"] == "registre du 05/08"


# ── Et rien ne bouge pour une liste ordinaire ───────────────────────────────

def test_une_liste_SANS_mot_reserve_traverse_intacte():
    """Le chemin nominal, et la borne du lot : la garde ne doit rien coûter à qui ne
    l'emploie pas — une liste ordinaire est remplacée en bloc, comme depuis toujours."""
    pose = [{"nom": "Doe", "commentaire": "neuf"},
            {"nom": "Roe", "commentaire": {"valeur": "x", "comment": "source"}}]

    assert _merge_column(_avant(), pose, SANS_CLE) == pose


def test_un_element_qui_n_est_pas_une_fiche_traverse():
    """Une liste de scalaires n'a pas d'attributs — elle ne doit pas casser."""
    assert _merge_column(["a"], ["a", "b"], SANS_CLE) == ["a", "b"]


# ── le texte SERVI doit dire que le mot est SEUL ─────────────────────────────
# Mesuré le 08/09/2026 : un agent de campagne a employé `@keep` de lui-même, alors
# qu'AUCUNE de ses six procédures ne le mentionne — il l'avait lu dans la description
# de `data_write`. Il a écrit `"@keep ; site de la maison — catalogue : …"`, voulant
# dire « garde ce qui est là ET ajoute ceci », et la chaîne est partie en base.
#
# ⚠️ La garde au mot entier est JUSTE et ne bouge pas : mordre au milieu d'une chaîne
# effacerait une valeur sur la foi d'une sous-chaîne (`contact@keepcool.fr`). C'est le
# TEXTE qui était en cause — il montrait la forme sans dire qu'elle doit être seule.
# Un exemple servi sera produit ; s'il ne dit pas ses bornes, il sera produit hors
# d'elles.

def test_le_texte_servi_dit_que_le_mot_doit_etre_SEUL():
    import inspect

    from oto_mcp.tools import datastore as face_mcp

    src = inspect.getsource(face_mcp)
    assert "must be the ENTIRE sub-field, alone" in src
    assert "just text and get stored as such" in src


def test_le_texte_servi_distingue_empty_de_rien_trouve():
    """⚠️ Le contresens le plus coûteux : `@empty` sur une case remplie EFFACE. Un
    agent qui veut dire « je n'ai rien trouvé » et l'emploie détruit la donnée de la
    cliente. La forme juste — les couches seules, sans `valeur` — doit être servie."""
    import inspect

    from oto_mcp.tools import datastore as face_mcp

    src = inspect.getsource(face_mcp)
    assert 'does not mean "I found nothing"' in src
    assert "write the layers ALONE, with no `valeur` key" in src
