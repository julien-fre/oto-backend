"""Le mot réservé dans un élément de LISTE — le trou qui a atteint la production.

Trouvé le 08/09/2026 par une campagne, **sur de la donnée servie et non dans un
journal** : sur une fiche réelle, `contacts[0].commentaire.comment` valait littéralement
un mot réservé. L'agent avait écrit le mot au bon endroit — la couche d'un attribut
d'élément est un endroit parfaitement légitime — et la plateforme l'a pris pour du
texte.

Une liste sans identité d'élément se REMPLACE en bloc : `_merge_column` ne descend pas
dedans, donc rien n'y résolvait le mot. `@empty` s'y résout — « vide-le » ne demande
aucun passé. `@keep` et `@clear`, retirés, sont refusés AVANT la fusion
(`test_controler_garde_toutes_les_ecritures.py`) : ils n'atteignent plus ce chemin.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import couches as dsl
from oto_mcp.datastore.columns import _merge_column

SANS_CLE = {"key": "contacts", "type": "list",
            "of": {"fields": [{"key": "nom"}, {"key": "commentaire"}]}}
AVEC_CLE = {"key": "contacts", "type": "list",
            "of": {"key": "role", "fields": [{"key": "role"},
                                             {"key": "commentaire"}]}}


def _avant() -> list:
    return [{"nom": "Guillaume",
             "commentaire": {"valeur": "ancien", "comment": "registre du 05/08"}}]


# ── `@empty` tient sans identité, parce qu'il ne demande aucun passé ─────────

@pytest.mark.parametrize("pose,attendu", [
    ([{"nom": "G", "commentaire": {"valeur": "x", "comment": dsl.VIDE_DELIBERE}}],
     {"valeur": "x", "comment": ""}),
    # oto#204, décision du plan : `@empty` sur la VALEUR assume le vide — le marqueur est
    # posé.
    ([{"nom": "G", "commentaire": {"valeur": dsl.VIDE_DELIBERE}}],
     {"valeur": "", dsl.VIDE_ASSUME: True}),
])
def test_vider_delibrement_fonctionne_dans_un_element(pose, attendu):
    out = _merge_column(_avant(), pose, SANS_CLE)

    assert out[0]["commentaire"] == attendu


# ── Avec une identité déclarée, `@empty` se résout aussi ─────────────────────

def test_avec_of_key_un_element_NOUVEAU_resout_le_mot():
    """L'élément neuf d'une liste à clé n'a rien en place : son `@empty` se résout
    comme dans une liste sans identité, jamais stocké."""
    avant = [{"role": "contact_rh", "commentaire": "ancien"}]
    pose = [{"role": "contact_paie", "commentaire": dsl.VIDE_DELIBERE}]

    out = _merge_column(avant, pose, AVEC_CLE)

    assert out[0]["commentaire"] == {"valeur": "", dsl.VIDE_ASSUME: True}


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
# effacerait une valeur sur la foi d'une sous-chaîne (`contact@keepcalm.example`). C'est le
# TEXTE qui était en cause — il montrait la forme sans dire qu'elle doit être seule.
# Un exemple servi sera produit ; s'il ne dit pas ses bornes, il sera produit hors
# d'elles.

def test_le_texte_servi_dit_que_le_mot_doit_etre_SEUL():
    import inspect

    from oto_mcp.tools import datastore as face_mcp

    src = inspect.getsource(face_mcp)
    assert "must be the ENTIRE sub-field, alone" in src
    assert "is just text and gets stored as such" in src


def _servies(*noms: str) -> dict[str, str]:
    """Les descriptions telles que `tools/list` les sert — `Args:` retiré, désindenté."""
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from scripts.empreinte_servie import _monter

    return {t.name: t.description or "" for t in _monter() if t.name in noms}


def test_le_texte_servi_distingue_empty_de_rien_trouve():
    """⚠️ Le contresens le plus coûteux : `@empty` sur une case remplie EFFACE la valeur.
    Sur une case qui porte une valeur, l'agent la GARDE et écrit les couches seules, ou
    l'ÉCARTE avec `@empty` et la raison dans `comment` — `@empty` est la forme unique du
    vide assumé. Omettre le champ, c'est « pas à moi » ; un `comment` seul n'est pas
    « cherché, rien trouvé ». Le texte servi jusqu'au 13/09 présentait les couches seules
    comme la forme du « rien trouvé » : il ne doit plus l'être."""
    servi = " ".join(_servies("data_write")["data_write"].split())
    assert "keep it and write the layers alone" in servi
    assert "or discard it with `@empty` and the reason in `comment`" in servi
    assert "`valeurs_effacees`" in servi
    assert 'A field you leave out is "not mine"' in servi
    assert 'never "searched, nothing found"' in servi
    assert 'does not mean "I found nothing"' not in servi
    assert "write the layers ALONE, with no `valeur` key" not in servi


def test_la_relecture_sentinel_tient_dans_la_tete_servie():
    """Le runner hébergé coupe chaque description à 1 024 caractères (tête gardée) :
    une relecture `empties="sentinel"` décrite plus bas n'atteint pas le modèle, qui
    renvoie alors `""` et perd le vide assumé. Mesuré le 13/09 sur le runner servi."""
    for nom, desc in _servies("data_rows", "data_claim_next").items():
        fin = desc.find("refused on a required field")
        assert 0 <= desc.find('empties="sentinel"') < fin, nom
        assert fin + len("refused on a required field") <= 1024, (nom, fin)
