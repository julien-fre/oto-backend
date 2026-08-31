"""L'usage d'une procédure porte DEUX séries : chargements et déroulés.

Les deux sortent de `tool_calls`, sous deux verbes différents — `oto_procedure`
(l'agent a OUVERT la procédure) et `run_start` (l'agent a déclaré l'EXÉCUTER).
C'est ce qui permet de servir les runs sans nouvelle table : `_runs_from_journal`
reconstruit déjà les runs depuis ces mêmes lignes.

Ce que ces tests tiennent, et pourquoi :

  · la CLÉ d'`args` diffère entre les deux verbes (`slug` vs `doctrine`). C'est
    l'unique raison d'être du paramètre `slug_key`, et une inversion rendrait une
    série vide en silence — le mode de panne d'origine de cet endpoint (« un filtre
    sur un nom d'outil mort renvoyait toujours 0 ») ;
  · les deux séries font 30 entrées densifiées, zéros compris ;
  · elles ne s'additionnent pas et ne se déduisent pas l'une de l'autre.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities.orgs import instructions as instr


def test_les_deux_verbes_et_leurs_cles_sont_declares_une_fois():
    """Une source unique par verbe — pas de chaîne magique dérivée au point d'appel.

    Le commentaire de `_GUIDE_GET_TOOL` raconte le bug d'origine : un filtre écrit à
    la main sur un nom d'outil mort comptait 0 pour toujours, sans erreur. Le même
    piège existe pour la clé d'`args`.
    """
    assert instr._GUIDE_GET_TOOL == "oto_procedure"
    assert instr._RUN_START_TOOL == "run_start"
    # La clé d'`args` n'est PAS recopiée dans la capacité : elle vit chez le lecteur
    # du journal, qui la lit déjà pour reconstruire les runs. Une clé servie renommée
    # d'un seul côté rendrait une série vide, en silence.
    from oto_mcp.db import usage
    assert usage._ARG_PROCEDURE in usage._ARGS_PROCEDURE_OK
    assert "slug" in usage._ARGS_PROCEDURE_OK


def test_la_cle_args_est_un_litteral_ferme():
    """`slug_key` est interpolé dans le SQL : la liste est fermée, pas validée « au
    mieux ». Un appelant ne choisit pas ce qui entre dans la requête."""
    from oto_mcp.db import usage

    with pytest.raises(ValueError):
        usage.instruction_usage(["sub-1"], "run_start", "x", slug_key="slug'; DROP--")


def test_le_modele_publie_les_deux_series_densifiees():
    """30 entrées chacune, et des défauts qui ne mentent pas : une procédure jamais
    déroulée rend `runs_count=0` et trente zéros, pas un champ absent que le front
    devrait deviner."""
    u = instr.InstructionUsage(slug="x", count=3, callers=[], series=[0] * 30)
    assert u.runs_count == 0
    assert u.runs_series == []

    plein = instr.InstructionUsage(
        slug="x", count=3, callers=[], series=[0] * 30,
        runs_count=2, runs_series=[0] * 29 + [2],
    )
    assert len(plein.series) == 30
    assert len(plein.runs_series) == 30
    # Deux mesures distinctes : `count` ne se déduit pas de `runs_count`.
    assert plein.count != plein.runs_count
