"""Le harnais des tests des waterfalls — les fournisseurs simulés à LEUR frontière.

Chaque étape d'une cascade passe par `meta.executer_cible` : c'est là que le harnais
répond, outil par outil, et relève les appels faits. Le réglage `steps` de la carte et
les modes de clé (lus au coffre) sont posés par le test ; l'attente d'une étape
asynchrone est ramenée à zéro.

Ce module est un HELPER (préfixe `_`), pas un fichier de tests : il vit ici parce que
les tests du paquet (`tests/waterfall/`) et ceux du mode tableau
(`tests/test_waterfall_lignes.py`) s'en servent, et qu'un `conftest.py` l'imposerait à
tout le dépôt.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from oto_mcp.tools import forager, meta
from oto_mcp.tools import waterfall as outils
from oto_mcp.tools.meta import IssueCible
from oto_mcp.waterfall import appel, moteur, ordre

NAMESPACES = ("waterfall_email", "waterfall_phone", "waterfall_company", "waterfall_person",
              "waterfall_people", "waterfall_tech")


def _servi(issue):
    """Ce qu'`executer_cible` rend VRAIMENT : le `ToolResult` servi, jamais le dict brut
    de l'outil (une liste y est enveloppée en `{"result": [...]}`, comme FastMCP le
    fait). Un faux qui rendait le dict a caché que la cascade ne lisait rien en vrai."""
    from dataclasses import replace
    from fastmcp.tools.tool import ToolResult
    r = issue.result
    if not issue.ok or issue.retenu or not isinstance(r, (dict, list)):
        return issue
    return replace(issue, result=ToolResult(
        structured_content=r if isinstance(r, dict) else {"result": r}))


@pytest.fixture
def calls(monkeypatch):
    """Réponses par outil (`IssueCible`, liste d'`IssueCible` servies tour à tour,
    fonction des arguments, ou exception), réglage `steps`, modes de clé, ce que la clé partagée de Forager sert
    (`forager_ops`, le réglage `platform.ops` de l'instance) — et le relevé des appels
    faits."""
    state = {"answers": {}, "made": [], "setting": None, "modes": {},
             "forager_ops": forager.PLATFORM_OPS_DEFAULT}

    async def resoudre(_mcp, name):
        return name

    async def executer(tool, sub, name, demande, args):
        state["made"].append((name, args))
        answer = state["answers"].get(name, IssueCible(ok=True, result={}))
        if callable(answer) and not isinstance(answer, IssueCible):
            answer = answer(args)       # an answer per arguments (a job per row…)
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return _servi(answer)

    monkeypatch.setattr(meta, "resoudre_outil", resoudre)
    monkeypatch.setattr(meta, "executer_cible", executer)
    monkeypatch.setattr(moteur, "current_user_sub_from_token", lambda: "sub-test")
    monkeypatch.setattr(ordre, "_configured_steps", lambda ns: state["setting"])
    monkeypatch.setattr(ordre, "_key_modes", lambda sub, tools: {
        t: state["modes"].get(t, "platform") for t in tools})
    monkeypatch.setattr(forager, "platform_ops",
                        lambda: forager.parse_platform_ops(state["forager_ops"]))
    monkeypatch.setattr(appel, "POLL_FIRST_S", 0)
    monkeypatch.setattr(appel, "POLL_EVERY_S", 0)
    return state


def _run(ns, **kwargs):
    m = FastMCP("t")
    outils.register(m)
    return asyncio.run(asyncio.run(m.get_tool(ns)).fn(**kwargs))


def _made(calls):
    return [n for n, _ in calls["made"]]


# --- qualification par Jev ------------------------------------------------------------

_FIT = {"fit": {"type": "noul", "instructions": "Does this person decide on purchases?",
                "criteria": {"true": "a buyer or decision-maker", "false": "anyone else"}}}


def _gens(calls):
    calls["answers"]["serper_search"] = IssueCible(ok=True, result={"organic": [
        {"title": "Ann Boss – Acme", "subtitle": "Lyon · CEO · Acme",
         "link": "https://fr.linkedin.com/in/ann-boss"},
        {"title": "Bob Dev – Acme", "subtitle": "Lyon · Developer · Acme",
         "link": "https://fr.linkedin.com/in/bob-dev"}]})


def _verdicts(*ps):
    return IssueCible(ok=True, result={"model": "jev-2026-09", "usage": {"cost": 6e-05},
                                       "answers": [{"index": i, "key": str(i),
                                                    "answers": {"fit": {"type": "noul", "noul": p}}}
                                                   for i, p in enumerate(ps)]})
