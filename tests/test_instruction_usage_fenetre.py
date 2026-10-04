"""L'usage d'une procédure : une fenêtre, sous l'org, et une lecture groupée pour les
listes (oto-backend#1145, #1146).

Ce que ces bancs tiennent (vrai PostgreSQL, schéma réel) :

  · `callers` est trié du plus actif au moins actif ; un appelant sans compte `users`
    reste compté dans le total mais n'est pas nommé ;
  · seuls comptent les appels RÉUSSIS émis SOUS l'org, dans la fenêtre ;
  · la lecture groupée rend, par procédure, chargements et déroulés (clés d'`args`
    distinctes) avec le dernier de chacun, sans les additionner ; un appel qui ne nomme
    pas de procédure n'est rattaché à aucune ;
  · un verbe hors de la liste fermée, ou lu sous la clé d'un autre, est refusé ;
  · la lecture passe par `idx_tool_calls_org_tool_ok`.
"""
from __future__ import annotations

import json
import uuid

import pytest

from oto_mcp.db import usage

LECTURES = dict(usage._VERBES_USAGE)


def _org(nom: str) -> tuple[int, str]:
    from oto_mcp import org_store
    sub = "sub-" + uuid.uuid4().hex[:8]
    return org_store.create_org(nom, created_by=sub), sub


def _appel(org_id, sub, tool, args, *, jours=0, heures=1, ok=True):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tool_calls (created_at, kind, sub, tool, args, ok, org_id) "
            "VALUES (now() - make_interval(days => %s, hours => %s), 'mcp', %s, %s, "
            "%s::jsonb, %s, %s)",
            (jours, heures, sub, tool, json.dumps(args), ok, org_id))


def test_callers_tries_et_anonymes_comptes_mais_pas_nommes(live):
    from oto_mcp import db
    org, _ = _org("Appelants")
    actif, discret = "sub-a-" + uuid.uuid4().hex[:6], "sub-b-" + uuid.uuid4().hex[:6]
    db.upsert_user(actif, email=f"{actif}@exemple.test")
    db.upsert_user(discret, email=f"{discret}@exemple.test")
    for _ in range(3):
        _appel(org, actif, "oto_procedure", {"slug": "p"})
    _appel(org, discret, "oto_procedure", {"slug": "p"})
    _appel(org, "sub-sans-compte", "oto_procedure", {"slug": "p"})
    u = usage.instruction_usage(org, "p", lectures=LECTURES)["oto_procedure"]
    assert u["callers"] == [f"{actif}@exemple.test", f"{discret}@exemple.test"]
    assert u["count"] == 5


def test_le_perimetre_est_l_org_les_reussis_et_la_fenetre(live):
    org, sub = _org("Périmètre")
    autre, _ = _org("Voisine")
    _appel(org, sub, "oto_procedure", {"slug": "p"})
    _appel(org, sub, "oto_procedure", {"slug": "p"}, ok=False)
    _appel(autre, sub, "oto_procedure", {"slug": "p"})
    _appel(org, sub, "oto_procedure", {"slug": "p"}, jours=40)
    assert usage.instruction_usage(org, "p", lectures=LECTURES)["oto_procedure"]["count"] == 1
    groupe = usage.instructions_usage_by_slug(org, lectures=LECTURES)
    assert groupe["oto_procedure"]["p"]["count"] == 1


def test_la_lecture_groupee_rend_chaque_procedure_et_ses_deux_verbes(live):
    org, sub = _org("Groupée")
    _appel(org, sub, "oto_procedure", {"slug": "a"}, jours=3)
    _appel(org, sub, "oto_procedure", {"slug": "a"}, heures=2)
    _appel(org, sub, "oto_procedure", {"slug": "b"})
    _appel(org, sub, "run_start", {usage._ARG_PROCEDURE: "a"}, jours=1)
    _appel(org, sub, "run_start", {"slug": "b"})            # mauvaise clé : pas un déroulé de b
    _appel(org, sub, "oto_procedure", {"slug": "c"}, jours=45)
    _appel(org, sub, "oto_procedure", {})                   # une liste, pas un chargement nommé
    lu = usage.instructions_usage_by_slug(org, lectures=LECTURES, days=30)
    chargements, deroules = lu["oto_procedure"], lu["run_start"]
    assert set(chargements) == {"a", "b"} and set(deroules) == {"a"}
    assert chargements["a"]["count"] == 2 and deroules["a"]["count"] == 1
    assert chargements["a"]["last_at"] is not None


def test_la_capacite_rend_une_ligne_par_procedure(live, monkeypatch):
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import instructions as instr
    org, sub = _org("Capacité")
    _appel(org, sub, "oto_procedure", {"slug": "a"})
    _appel(org, sub, "run_start", {usage._ARG_PROCEDURE: "z"})
    out = instr._instructions_usage(ResolvedCtx(sub=sub, org_id=org), instr.EmptyInput())
    vue = instr.InstructionsUsage(**out)
    assert vue.days == 30
    assert [(r.slug, r.count, r.runs_count) for r in vue.usage] == [("a", 1, 0), ("z", 0, 1)]
    assert vue.usage[1].last_at is None and vue.usage[1].last_run_at is not None


def test_un_verbe_hors_liste_ou_sous_une_autre_cle_est_refuse():
    with pytest.raises(ValueError):
        usage.instructions_usage_by_slug(7, lectures={"oto_search": "slug"})
    with pytest.raises(ValueError):
        usage.instructions_usage_by_slug(7, lectures={"run_start": "slug"})
    with pytest.raises(ValueError):
        usage.instruction_usage(7, "p", lectures={"oto_procedure": usage._ARG_PROCEDURE})


def test_la_lecture_groupee_passe_par_l_index_de_l_org(live):
    """`enable_seqscan=off` ôte au planificateur l'excuse d'une petite table : si
    l'index n'était pas UTILISABLE pour cette forme, le plan resterait un Seq Scan."""
    from oto_mcp.db._conn import _connect
    org, sub = _org("Plan")
    _appel(org, sub, "oto_procedure", {"slug": "p"})
    with _connect() as conn:
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r["QUERY PLAN"] for r in conn.execute(
            f"EXPLAIN SELECT l.tool, count(*) FROM tool_calls l "
            f"WHERE l.org_id = %s AND l.tool = ANY(%s) AND l.ok "
            f"AND l.created_at >= {usage._DEBUT_FENETRE_UTC} GROUP BY l.tool",
            (org, list(LECTURES), 29)).fetchall())
    assert "idx_tool_calls_org_tool_ok" in plan, plan
