"""Le dessin gardé tel quel quand les étapes changent (08/11/2026).

On enregistre une procédure dont les étapes ont bougé, et la page montre l'ANCIEN
dessin : le marqueur remet le tracé stocké, la page renvoie un corps où le bloc n'a pas
bougé, et personne ne le redessine. Le cœur ne dessine pas — il DIT le retard, à
l'écriture (`diagram_warning`) et à la lecture (`diagram_stale`, `diagram_version`).

Bancs sur une vraie doctrine qui se dessine (`fixtures/drawings`), pour que le dessin
passe le lint et que seul l'avertissement de retard soit en jeu.
"""
from __future__ import annotations

from pathlib import Path

from oto_mcp import procedure_diagram as pd

CORPS = (Path(__file__).parent / "fixtures" / "drawings"
         / "accelerator-batch-to-deal-flow.md").read_text()


def _etapes_changees(corps: str) -> str:
    return corps.replace("## 8. A human triages the new entries",
                         "## 8. A human triages the new entries\n\n## 9. Report the run")


def _dessin_change(corps: str) -> str:
    debut, fin = pd.trouver_le_dessin(corps)
    bloc = corps[debut:fin]
    return corps[:debut] + bloc.replace("```\n", "```\n▪ Report the run\n", 1) + corps[fin:]


def test_la_fixture_a_un_dessin_propre():
    assert pd.le_dessin(CORPS) is not None
    assert pd.diagram_check(CORPS)["diagram_warning"] is None


def test_etapes_changees_dessin_garde_est_DIT():
    apres = _etapes_changees(CORPS)
    assert pd.dessin_garde_etapes_changees(CORPS, apres)
    w = pd.diagram_check(apres, perime_depuis=3)["diagram_warning"]
    assert "(v3)" in w and "Redraw" in w


def test_une_coquille_dans_un_paragraphe_ne_dit_rien():
    """Le squelette (titres, items numérotés) n'a pas bougé : le flux non plus."""
    apres = CORPS.replace("## Before the first run\n", "## Before the first run\n\nTypo fixd.\n")
    assert not pd.dessin_garde_etapes_changees(CORPS, apres)


def test_le_dessin_refait_ne_dit_rien():
    apres = _dessin_change(_etapes_changees(CORPS))
    assert not pd.dessin_garde_etapes_changees(CORPS, apres)


def test_sans_dessin_rien_a_dire():
    sans = "# P\n\n## 1. A\n"
    assert not pd.dessin_garde_etapes_changees(sans, sans + "## 2. B\n")
    assert pd.etat_du_dessin([{"version": 2, "body_md": sans}]) == {
        "diagram_version": None, "diagram_stale": None}


def test_la_lecture_remonte_a_la_version_ou_le_dessin_a_ete_pose():
    v1 = "# P\n\n## 1. A\n"                    # pas de dessin
    v2 = CORPS                                 # dessin posé
    v3 = CORPS.replace("Typo", "Typo")         # rien ne bouge
    v4 = _etapes_changees(CORPS)               # étapes changées, dessin gardé
    hist = [{"version": 4, "body_md": v4}, {"version": 3, "body_md": v3},
            {"version": 2, "body_md": v2}, {"version": 1, "body_md": v1}]
    assert pd.etat_du_dessin(hist) == {"diagram_version": 2, "diagram_stale": True}
    assert pd.etat_du_dessin(hist[1:]) == {"diagram_version": 2, "diagram_stale": False}


def test_un_dessin_refait_remet_le_compteur():
    v2 = _etapes_changees(CORPS)
    v3 = _dessin_change(v2)
    hist = [{"version": 3, "body_md": v3}, {"version": 2, "body_md": v2},
            {"version": 1, "body_md": CORPS}]
    assert pd.etat_du_dessin(hist) == {"diagram_version": 3, "diagram_stale": False}
