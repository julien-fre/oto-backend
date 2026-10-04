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
        usage.instruction_usage(7, "x", lectures={"run_start": "slug'; DROP--"})


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


# ── La lecture, contre un vrai PostgreSQL (#1145) ─────────────────────────────


def _poser(sub, org_id, tool, args, *, jours=0, ok=True):
    import json

    from oto_mcp.db._conn import _connect

    with _connect() as conn:
        conn.execute(
            "INSERT INTO tool_calls (created_at, kind, sub, tool, args, ok, org_id) "
            "VALUES (now() - make_interval(days => %s), 'mcp', %s, %s, %s::jsonb, %s, %s)",
            (jours, sub, tool, json.dumps(args), ok, org_id))


def test_une_passe_sous_l_org_bornee_a_la_fenetre(live):
    """Chargements et déroulés en UNE lecture : sous l'org du guide, réussis, dans la
    fenêtre — `count == sum(daily)`. Ni l'autre org, ni l'échec, ni le trop vieux,
    ni une autre procédure ne comptent."""
    import uuid

    from oto_mcp import db, org_store

    sub = "sub-iu-" + uuid.uuid4().hex[:6]
    org = org_store.create_org("Usage guide", created_by=sub)
    autre = org_store.create_org("Ailleurs", created_by=sub)
    db.upsert_user(sub, email=f"{sub}@exemple.test")
    lectures = {instr._GUIDE_GET_TOOL: "slug", instr._RUN_START_TOOL: usage_arg()}

    _poser(sub, org, "oto_procedure", {"slug": "relance"})
    _poser(sub, org, "oto_procedure", {"slug": "relance"}, jours=3)
    _poser(sub, org, "run_start", {"doctrine": "relance"}, jours=1)
    _poser(sub, autre, "oto_procedure", {"slug": "relance"})          # autre org
    _poser(sub, org, "oto_procedure", {"slug": "relance"}, ok=False)  # échec
    _poser(sub, org, "oto_procedure", {"slug": "relance"}, jours=45)  # hors fenêtre
    _poser(sub, org, "oto_procedure", {"slug": "autre"})              # autre procédure
    _poser(sub, org, "run_start", {"slug": "relance"})                # mauvaise clé

    lu = db.instruction_usage(org, "relance", lectures=lectures, days=30)
    chargements, deroules = lu["oto_procedure"], lu["run_start"]
    assert chargements["count"] == 2 == sum(chargements["daily"].values())
    assert chargements["callers"] == [f"{sub}@exemple.test"]
    assert deroules["count"] == 1 and len(deroules["daily"]) == 1

    base = db.instruction_usage(org, None, lectures=lectures, days=30)
    assert base["oto_procedure"]["count"] == 3     # toutes les procédures de l'org


def usage_arg() -> str:
    from oto_mcp.db import usage
    return usage._ARG_PROCEDURE
