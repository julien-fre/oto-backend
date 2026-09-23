"""`attempt_errors` traverse le CONTRAT, du SQL jusqu'à ce que le front reçoit.

⚠️ **Le banc du milieu.** La colonne est écrite par `complete_job` (banc en base)
et lue par l'écran (bancs purs côté front) — et entre les deux il y a un modèle
Pydantic de SORTIE qui, par construction, laisse tomber tout champ qu'il ne
déclare pas. Les deux extrémités peuvent être vertes pendant que la valeur est
jetée au dernier pas, sans une erreur nulle part.

C'est exactement la forme du défaut que ce lot répare d'un étage plus haut : la
voie Anthropic CAPTAIT la fin du tour, et le motif la jetait parce qu'il lisait
l'autre nom. Un champ capté puis jeté ne se voit dans aucun journal — il se voit
dans un écran qui n'affiche jamais rien, et qu'on finit par croire normal.

Ce que ces bancs figent :
- le modèle `Job` DÉCLARE `attempt_errors` (sinon la sérialisation le jette) ;
- la forme servie est celle que l'écran attend : `{attempt, at, error}` ;
- `[]` (rien n'a échoué) et `None` (pas lu) restent DISTINCTS à la traversée —
  ce ne sont pas la même affirmation, et les confondre ferait dire « aucune
  tentative n'a échoué » à un build qui n'en sait rien.
"""
from __future__ import annotations

import pytest


def test_le_modele_de_sortie_DECLARE_le_champ():
    """Sans cette déclaration, Pydantic jette la valeur en silence : la base
    l'aurait, l'API ne la rendrait pas, et rien ne le signalerait."""
    from oto_mcp.capabilities.runner_jobs import Job
    assert "attempt_errors" in Job.model_fields, (
        "un champ non déclaré sur le modèle de sortie est SUPPRIMÉ à la "
        "sérialisation — c'est le trou par lequel une colonne neuve n'arrive "
        "jamais à l'écran")


def test_une_ligne_de_base_traverse_avec_ses_tentatives():
    """La forme exacte que l'écran lit : `attempt`, `at`, `error`."""
    from oto_mcp.capabilities.runner_jobs import Job
    ligne = {
        "id": 42, "status": "failed", "attempts": 3, "max_attempts": 3,
        "last_error": "fin_anormale (max_tokens)",
        "attempt_errors": [
            {"attempt": 1, "at": "2026-09-22T09:19:00Z", "error": "fin_anormale (pause_turn)"},
            {"attempt": 2, "at": "2026-09-22T09:23:00Z", "error": "fin_anormale (max_tokens)"},
            {"attempt": 3, "at": "2026-09-22T09:27:00Z", "error": "fin_anormale (max_tokens)"},
        ],
    }
    servi = Job(**ligne).model_dump()
    assert servi["attempt_errors"] == ligne["attempt_errors"], (
        f"la liste traverse telle quelle — {servi.get('attempt_errors')!r}")
    assert [t["attempt"] for t in servi["attempt_errors"]] == [1, 2, 3], (
        "du plus ancien au plus récent : l'ordre est ce qui raconte l'histoire")


@pytest.mark.parametrize("valeur, attendu", [
    ([], []),          # un vrai vide : rien n'a échoué
    (None, None),      # pas lu : ce build n'en sait rien
])
def test_le_vide_et_l_inconnu_ne_se_confondent_pas(valeur, attendu):
    """⚠️ `[]` affirme « aucune tentative n'a échoué ». `None` n'affirme rien.
    Les aplatir l'un sur l'autre ferait dire à un backend qui ne sert pas encore
    la colonne quelque chose qu'il ne peut pas savoir — et l'écran, qui se tait
    sur `None` et parle sur `[]`, dirait alors le contraire de la vérité."""
    from oto_mcp.capabilities.runner_jobs import Job
    servi = Job(id=1, status="done", attempt_errors=valeur).model_dump()
    assert servi["attempt_errors"] == attendu


def test_la_liste_de_jobs_le_porte_aussi():
    """`op=list` est le chemin que la page Automatisations emprunte — pas `get`."""
    from oto_mcp.capabilities.runner_jobs import JobsOut
    out = JobsOut(jobs=[{"id": 7, "status": "failed",
                         "attempt_errors": [{"attempt": 1, "at": "2026-09-22T09:19:00Z",
                                             "error": "boum"}]}])
    (job,) = out.model_dump()["jobs"]
    assert job["attempt_errors"] == [
        {"attempt": 1, "at": "2026-09-22T09:19:00Z", "error": "boum"}]
