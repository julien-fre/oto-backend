"""Un outil qu'une recette ou une procédure appelle, absent de la liste de l'agent :
dit AVANT le run (`missing_tools` sur l'agent), et refusé UNE fois au run en les
nommant tous (`tool_not_in_job`, `missing_tools`, `fix`).

Né d'une soirée où deux agents d'une même chaîne se sont bloqués chacun sur un outil
qu'une recette de sa procédure appelait — l'un une lecture de CRM, l'autre une lecture
de tableau —, découverts un par un, au run.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from oto_mcp import outils_du_travail
from oto_mcp.capabilities import runner_triggers as RT
from oto_mcp.recipes import code, outils_cites

_CORPS_CODE = {"kind": "code", "sources": {"recipe.py": (
    "async def run(oto, params):\n"
    "    rows = await oto.call('data_rows', {'datastore': 1})\n"
    "    res = await oto.call(\"folk_record\", {'op': 'search'})\n"
    "    again = await oto.call('data_rows', {'datastore': 2})\n"
    "    name = 'serper_' + 'search'\n"
    "    await oto.call(name, {})\n")}}


# ── ce qu'une recette cite ───────────────────────────────────────────────────

def test_une_recette_code_cite_ses_appels_litteraux_une_fois_chacun():
    assert outils_cites.outils_cites(_CORPS_CODE) == ["data_rows", "folk_record"]


def test_une_recette_declarative_cite_son_outil():
    assert outils_cites.outils_cites({"source": {"tool": "apollo_search_people"}}) \
        == ["apollo_search_people"]


def test_un_corps_illisible_ne_cite_rien():
    assert outils_cites.outils_cites(None) == []
    assert outils_cites.outils_cites({"sources": {"a.py": 3}}) == []


def test_une_procedure_nomme_ses_recettes_seulement_si_elle_parle_d_oto_recipe():
    texte = ('<tool:oto_recipe> `op="run"`, `slug="tam-find-buyers"`, puis '
             '`slug="tam-hook-evidence"` et encore `slug="tam-find-buyers"`')
    assert outils_cites.recettes_citees(texte) == ["tam-find-buyers", "tam-hook-evidence"]
    assert outils_cites.recettes_citees('slug="x-y" sans recette') == []


# ── au run : un refus, tous les outils, le geste ─────────────────────────────

@pytest.fixture
def _travail(monkeypatch):
    verrou = types.SimpleNamespace(job_id=41)
    monkeypatch.setattr(outils_du_travail.verrou_org, "travail_de_la_requete", lambda: verrou)
    monkeypatch.setattr(outils_du_travail.db, "charge_du_travail",
                        lambda job: {"tools": ["oto_recipe", "data_write"],
                                     "trigger_id": 7})


def test_manquants_nomme_chaque_outil_hors_liste_et_l_agent(_travail):
    m = outils_du_travail.manquants(["data_rows", "folk_record", "data_write", "run_finish",
                                     "data_rows"])
    assert m == {"job_id": 41, "trigger_id": 7, "missing": ["data_rows", "folk_record"]}


def test_manquants_hors_travail_heberge_ne_dit_rien(monkeypatch):
    monkeypatch.setattr(outils_du_travail.verrou_org, "travail_de_la_requete", lambda: None)
    assert outils_du_travail.manquants(["folk_record"]) is None


def test_la_recette_code_est_refusee_avant_tout_appel_en_nommant_tout(_travail, monkeypatch):
    async def jamais(**kw):
        raise AssertionError("le code ne doit pas tourner")
    monkeypatch.setattr(code.pont_bac, "executer_avec_pont", jamais)
    corps = {**_CORPS_CODE, "limits": {"max_calls": 5, "max_units": 1, "max_seconds": 10}}
    recu = asyncio.run(code.executer(corps, {}, fastmcp=None, sub="u", ecrire=True))
    assert recu["ok"] is False and recu["calls"] == 0 and recu["done"] is False
    assert recu["code"] == "tool_not_in_job"
    assert recu["missing_tools"] == ["data_rows", "folk_record"]
    assert recu["fix"] == {"tool": "oto_trigger", "op": "update", "trigger_id": 7,
                           "tools_add": ["data_rows", "folk_record"]}
    assert "run_finish" in recu["error"] and "agent 7" in recu["error"]


# ── avant le run : sur l'agent ───────────────────────────────────────────────

def test_l_agent_dit_ce_que_sa_procedure_et_ses_recettes_appellent_sans_l_avoir(monkeypatch):
    corps_proc = ('Lire <tool:data_aggregate>, puis <tool:oto_recipe> `op="run"`, '
                  '`slug="find-buyers"`, et `slug="pas-une-recette"`.')
    monkeypatch.setattr(RT.db, "get_guide_db", lambda *a: {"body_md": corps_proc})
    fiches = {"find-buyers": {"id": 3, "published_version": 2}}
    monkeypatch.setattr(RT.db_recipes, "get_recipe", lambda ot, oid, slug: fiches.get(slug))
    monkeypatch.setattr(RT.db_recipes, "get_version",
                        lambda rid, v: {"body": _CORPS_CODE} if (rid, v) == (3, 2) else None)
    t = {"procedure": "tam", "tools": ["oto_recipe", "data_rows", "run_finish"]}
    assert RT._outils_non_accordes(77, t) == [
        ("data_aggregate", "procedure `tam`"), ("folk_record", "recipe `find-buyers`")]


def test_un_agent_sans_procedure_n_a_rien_a_ajouter():
    assert RT._outils_non_accordes(77, {"tools": ["x"]}) == []


def test_une_recette_non_publiee_n_est_pas_lue(monkeypatch):
    monkeypatch.setattr(RT.db, "get_guide_db",
                        lambda *a: {"body_md": 'oto_recipe slug="brouillon"'})
    monkeypatch.setattr(RT.db_recipes, "get_recipe",
                        lambda *a: {"id": 1, "published_version": None})
    monkeypatch.setattr(RT.db_recipes, "get_version",
                        lambda *a: pytest.fail("une version non publiée ne se lit pas"))
    assert RT._outils_non_accordes(77, {"procedure": "p", "tools": []}) == []
