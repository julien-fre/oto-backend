"""Savoir ce qu'un `op=patch` emporte AVANT de l'écrire, et après (oto#171).

Un agent voulait remplacer un `###` ; les puces posées sous ce titre, sans autre titre
entre elles, lui appartenaient et sont parties avec. `op=patch` refusait `dry_run` : son
seul moyen de le savoir a été de créer une page jetable dans le projet du client, d'y
essayer, puis de la supprimer. Et la réponse d'un `replace` ne disait rien de ce qu'elle
retirait tant que la section n'avait pas de sous-titre.

Ce banc tient les deux demandes sur le chemin réel de la capacité (seul `db.update_doc`
est stubbé) : `dry_run` n'écrit RIEN et rend la région ; le vrai `replace` rend la même.

Éprouvé rouge le 2026-09-16 : la branche `if inp.dry_run:` retirée de `patch.py` ⟹
`test_dry_run_n_ecrit_rien` voit l'écriture passer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oto_mcp import doc_patch                               # noqa: E402
from oto_mcp.capabilities._types import AuthzDenied         # noqa: E402
from oto_mcp.capabilities.docs import core as D             # noqa: E402
from test_doc_preamble_and_delete import CTX, seams         # noqa: E402,F401

# Le cas du retour : des puces SOUS un `###`, sans sous-titre, suivies d'un titre de
# même niveau qui ferme la section.
PAGE = "\n".join([
    "> bandeau",               # 1
    "",                        # 2
    "## Équipe",               # 3
    "",                        # 4
    "### Contacts",            # 5
    "",                        # 6
    "- Alice",                 # 7
    "- Bruno",                 # 8
    "- Chloé",                 # 9
    "",                        # 10
    "### Horaires",            # 11
    "",                        # 12
    "9h-18h",                  # 13
])


@pytest.fixture
def page(seams):
    seams["body"] = PAGE
    return seams


def _patch(**kw):
    return D._doc(CTX, D.DocInput(op="patch", doc_id=662, **kw))


def test_dry_run_n_ecrit_rien(page):
    out = _patch(section="Contacts", body_md="- Denis", dry_run=True)
    assert page["appels"] == [], "un dry_run qui écrit est le pire des deux mondes"
    assert page["body"] == PAGE
    assert (out["dry_run"], out["written"]) == (True, False)


def test_dry_run_dit_que_les_PUCES_sans_sous_titre_partent(page):
    """Le relevé qui manquait : aucune sous-section, et pourtant 5 lignes emportées."""
    out = _patch(section="Contacts", body_md="- Denis", dry_run=True)
    assert out["removed"] == {"from_line": 6, "to_line": 10, "line_count": 5,
                              "subsections": []}


def test_le_vrai_replace_rend_le_MEME_releve_que_le_dry_run(page):
    annonce = _patch(section="Contacts", body_md="- Denis", dry_run=True)
    fait = _patch(section="Contacts", body_md="- Denis", expected_rev=annonce["rev"])
    assert fait["removed"] == annonce["removed"]
    assert page["appels"][0]["expected_rev"] == annonce["rev"], (
        "le rev rendu est celui qui verrouille la version annoncée")
    assert "- Alice" not in page["body"] and "### Horaires" in page["body"]


def test_delete_compte_le_titre_et_nomme_les_sous_sections(page):
    out = _patch(section="Équipe", mode="delete", dry_run=True)
    assert out["removed"] == {"from_line": 3, "to_line": 13, "line_count": 11,
                              "subsections": ["Contacts", "Horaires"]}


def test_append_et_prepend_ne_retirent_rien(page):
    for mode in ("append", "prepend"):
        out = _patch(section="Contacts", body_md="- Denis", mode=mode, dry_run=True)
        assert out["removed"] is None


def test_le_preambule_a_son_releve(page):
    out = _patch(region="preamble", body_md="> neuf", dry_run=True)
    assert out["removed"] == {"from_line": 1, "to_line": 2, "line_count": 2,
                              "subsections": []}


def test_une_section_vide_rend_des_bornes_nulles():
    corps = "## A\n## B\n"
    assert doc_patch.emprise(corps, "A", "replace") == {
        "from_line": None, "to_line": None, "line_count": 0, "subsections": []}


def test_dry_run_sur_une_cible_introuvable_refuse_comme_le_vrai(page):
    with pytest.raises(AuthzDenied) as e:
        _patch(section="Absente", body_md="x", dry_run=True)
    assert (e.value.status, e.value.code) == (404, "unknown_section")
    assert page["appels"] == []


def test_dry_run_sur_une_op_qui_ne_simule_pas_reste_refuse(page):
    with pytest.raises(AuthzDenied) as e:
        D._doc(CTX, D.DocInput(op="update", doc_id=662, body_md="x", dry_run=True))
    assert (e.value.status, e.value.code) == (400, "unsupported_dry_run")
