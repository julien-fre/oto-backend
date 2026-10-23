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
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
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
    texte = ('<tool:oto_recipe> `op="run"`, `slug="recette-a"`, puis '
             '`slug="recette-b"` et encore `slug="recette-a"`')
    assert outils_cites.recettes_citees(texte) == ["recette-a", "recette-b"]
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
    assert m == {"job_id": 41, "trigger_id": 7, "tools": ["oto_recipe", "data_write"],
                 "missing": ["data_rows", "folk_record"]}


def test_manquants_lit_la_charge_du_travail_une_fois(monkeypatch):
    monkeypatch.setattr(outils_du_travail.verrou_org, "travail_de_la_requete",
                        lambda: types.SimpleNamespace(job_id=41))
    lectures = []
    monkeypatch.setattr(outils_du_travail.db, "charge_du_travail",
                        lambda job: lectures.append(job) or {"tools": [], "trigger_id": 7})
    assert outils_du_travail.manquants(["data_rows"])["missing"] == ["data_rows"]
    assert lectures == [41], "la liste permise et l'agent à compléter : une seule lecture"


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
                           "tools": ["oto_recipe", "data_write", "data_rows", "folk_record"]}
    assert "run_finish" in recu["error"] and "agent 7" in recu["error"]
    assert "fix" not in recu["error"], "l'agent s'arrête : élargir sa liste n'est pas son geste"


@pytest.fixture
def _agent_7(monkeypatch):
    """L'agent 7 de `porteur`, tel que le travail 41 l'a reçu. Rend ce que
    `update_trigger` a reçu."""
    from oto_mcp.capabilities import _acces_agent
    monkeypatch.setattr(_acces_agent, "niveaux",
                        lambda sub, org_id, agents: {int(t["id"]): "owner" for t in agents})
    monkeypatch.setattr("oto_mcp.db.connector_settings.get_connector_setting",
                        lambda *a, **k: None)
    monkeypatch.setattr(RT.db, "get_trigger", lambda i, o: {
        "id": i, "org_id": o, "sub": "porteur", "tools": ["oto_recipe", "data_write"]})
    monkeypatch.setattr(RT.tool_alias, "canonical_names", lambda noms, sub: list(noms))
    vu: dict = {}
    monkeypatch.setattr(RT.db, "update_trigger",
                        lambda i, o, champs, **k: vu.update(champs) or {"id": i, **champs})
    return vu


def test_le_fix_joue_sur_la_capacite_donne_a_l_agent_la_liste_entiere(
        _travail, _agent_7, monkeypatch):
    """Le `fix` est un vrai geste : rejoué tel quel sur `oto_trigger`, il pose la liste
    de l'agent PLUS les manquants — par une personne. Sous le jeton du travail, le même
    geste est refusé (`tools_human_only`) : un agent n'élargit pas sa propre liste."""
    monkeypatch.setattr(code.pont_bac, "executer_avec_pont", None)
    corps = {**_CORPS_CODE, "limits": {"max_calls": 5, "max_units": 1, "max_seconds": 10}}
    fix = asyncio.run(code.executer(corps, {}, fastmcp=None, sub="u", ecrire=True))["fix"]
    arguments = {k: v for k, v in fix.items() if k != "tool"}
    ctx = ResolvedCtx(sub="porteur", org_id=2)

    with pytest.raises(AuthzDenied) as e:
        asyncio.run(RT._triggers(ctx, RT.TriggerInput(**arguments)))
    assert (e.value.status, e.value.code) == (403, "tools_human_only")
    assert _agent_7 == {}, "rien n'est écrit sous le jeton du travail"

    monkeypatch.setattr(outils_du_travail.verrou_org, "travail_de_la_requete", lambda: None)
    asyncio.run(RT._triggers(ctx, RT.TriggerInput(**arguments)))
    assert _agent_7 == {"tools": ["oto_recipe", "data_write", "data_rows", "folk_record"]}


# ── avant le run : sur l'agent ───────────────────────────────────────────────

def test_l_agent_dit_ce_que_sa_procedure_et_ses_recettes_appellent_sans_l_avoir(monkeypatch):
    corps_proc = ('Lire <tool:data_aggregate>, puis <tool:oto_recipe> `op="run"`, '
                  '`slug="recette-a"`, et `slug="pas-une-recette"`.')
    monkeypatch.setattr(RT.db, "get_guide_db", lambda *a: {"body_md": corps_proc})
    fiches = {"recette-a": {"id": 3, "published_version": 2}}
    monkeypatch.setattr(RT.db_recipes, "get_recipe", lambda ot, oid, slug: fiches.get(slug))
    monkeypatch.setattr(RT.db_recipes, "get_version",
                        lambda rid, v: {"body": _CORPS_CODE} if (rid, v) == (3, 2) else None)
    t = {"procedure": "veille", "tools": ["oto_recipe", "data_rows", "run_finish"]}
    assert RT._outils_non_accordes(77, t) == [
        ("data_aggregate", "procedure `veille`"), ("folk_record", "recipe `recette-a`")]


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


def _procedure_et_recette(monkeypatch):
    """Une procédure qui cite un outil et lance une recette publiée ; rend les lectures."""
    lus: dict[str, list] = {"procedures": [], "recettes": []}
    monkeypatch.setattr(RT.db, "get_guide_db", lambda *a: lus["procedures"].append(a[2]) or {
        "body_md": 'Lire <tool:data_aggregate>, puis <tool:oto_recipe> `slug="recette-a"`.'})
    monkeypatch.setattr(RT.db_recipes, "get_recipe", lambda ot, oid, slug: lus["recettes"].append(
        slug) or {"id": 3, "published_version": 2})
    monkeypatch.setattr(RT.db_recipes, "get_version", lambda rid, v: {"body": _CORPS_CODE})
    monkeypatch.setattr(RT.tool_registry, "bound_instance", lambda: None)
    return lus


def test_un_agent_sans_outils_dit_tout_ce_qui_lui_manque(monkeypatch):
    """`tools=[]` : c'est l'agent à qui il manque TOUT — le dire, pas se taire."""
    _procedure_et_recette(monkeypatch)
    t = asyncio.run(RT._avec_tool_warnings(
        ResolvedCtx(sub="porteur", org_id=77),
        {"id": 1, "org_id": 77, "procedure": "veille", "tools": []}))
    assert t["missing_tools"] == ["data_aggregate", "oto_recipe", "data_rows", "folk_record"]
    assert [(a["tool"], a["issue"], a["needed_by"]) for a in t["tool_warnings"]][-1] == (
        "folk_record", "not_granted", "recipe `recette-a`")


def test_une_liste_lit_chaque_procedure_et_ses_recettes_une_fois(monkeypatch):
    """Trois agents sur deux procédures qui lancent la même recette : une lecture par
    procédure et une par recette pour toute la réponse, pas une par agent."""
    lus = _procedure_et_recette(monkeypatch)
    rep = {"triggers": [
        {"id": 1, "org_id": 77, "procedure": "veille", "tools": ["oto_recipe"]},
        {"id": 2, "org_id": 77, "procedure": "veille", "tools": ["data_rows"],
         "kind": "webhook"},
        {"id": 3, "org_id": 77, "procedure": "relance", "tools": []}]}
    out = asyncio.run(RT._ajouter_tool_warnings(ResolvedCtx(sub="porteur", org_id=77), rep))
    assert lus == {"procedures": ["veille", "relance"], "recettes": ["recette-a"]}
    manque = {t["id"]: t["missing_tools"] for t in out["triggers"]}
    assert manque[1] == ["data_aggregate", "data_rows", "folk_record"]
    assert manque[2] == ["data_aggregate", "oto_recipe", "folk_record"], (
        "la lecture est partagée, le calcul reste celui de CHAQUE agent")
