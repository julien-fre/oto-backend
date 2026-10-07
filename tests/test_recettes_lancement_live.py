"""`start` (pull asynchrone, Apify) sur un vrai store : lancer UNE fois, suivre, lire par
décalage, et jamais relancer un travail payé parce qu'un jeton s'est perdu."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

import pytest

SUB = "sub-recettes-apify"
ELEMENTS = [{"placeId": f"p{i}", "title": f"Shop {i}"} for i in range(5)]


@pytest.fixture(scope="module")
def compte(live):
    from oto_mcp import db
    db.upsert_user(SUB, email=f"{SUB}@acme.test", name=SUB)
    return SUB


@pytest.fixture
def serveur(compte, monkeypatch):
    from fastmcp import FastMCP

    from oto_mcp import access
    from oto_mcp.auth import hooks
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(hooks, "current_user_sub_from_token", lambda: SUB)
    etat = {"statut": "RUNNING", "appels": []}
    m = FastMCP("t-recettes-apify")

    @m.tool()
    def apify_run(actor_id: str, run_input: Optional[dict] = None,
                  max_total_charge_usd: Optional[float] = None) -> dict:
        etat["appels"].append(("run", actor_id))
        return {"data": {"id": f"run{len(etat['appels'])}", "defaultDatasetId": "ds1"}}

    @m.tool()
    def apify_run_status(run_id: str) -> dict:
        etat["appels"].append(("status", run_id))
        return {"status": etat["statut"]}

    @m.tool()
    def apify_dataset_items(dataset_id: str, limit: Optional[int] = None,
                            offset: Optional[int] = None) -> Any:
        etat["appels"].append(("items", offset))
        return ELEMENTS[offset or 0:(offset or 0) + (limit or 100)]
    return m, etat


def _corps(**surcharge) -> dict:
    from oto_mcp.recipes import contrat
    corps = {"side_effects": True, "tool": "apify_dataset_items",
             "arguments": {"dataset_id": "{{job.dataset}}"},
             "start": {"tool": "apify_run",
                       "arguments": {"actor_id": "acme/maps-scraper",
                                     "run_input": {"q": "{{params.q}}"},
                                     "max_total_charge_usd": 2},
                       "id": "data.id", "keep": {"dataset": "data.defaultDatasetId"},
                       "status": {"tool": "apify_run_status",
                                  "arguments": {"run_id": "{{job.id}}"}, "path": "status",
                                  "ready": ["SUCCEEDED"], "failed": ["FAILED", "ABORTED"]}},
             "source": {"items": "", "pagination": {"type": "offset", "param": "offset",
                                                    "size": 2, "size_param": "limit"}},
             "map": {"title": "title"}, "key": {"column": "place_id", "template": "{{item.placeId}}"},
             "params": {"q": {"required": True}},
             "limits": {"max_units": 50}}
    corps.update(surcharge)
    return contrat.valider(corps)


def _recette() -> int:
    from oto_mcp.db import recipes as db_recipes
    fiche = db_recipes.create_recipe(owner_type="user", owner_id=SUB,
                                     slug=f"apify-{uuid.uuid4().hex[:6]}", title="t",
                                     description="", created_by=SUB, body={}, note=None)
    return fiche["id"]


def _tableau() -> str:
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    nom = f"apify-{uuid.uuid4().hex[:6]}"
    db.create_datastore("user", SUB, nom)
    make_store(SUB).set_schema(nom, {"key": "place_id", "fields": [
        {"key": "place_id", "type": "text"}, {"key": "title", "type": "text"}]})
    return nom


def _executer(m, corps, ns, rid, **kw):
    from oto_mcp.recipes import moteur
    kw.setdefault("budget_s", 600)
    return asyncio.run(moteur.executer(corps, {"q": "shops"}, fastmcp=m, sub=SUB,
                                       datastore=ns, cle_travail=(rid, "k"), **kw))


def test_lancer_une_fois_suivre_puis_lire_par_decalage(serveur):
    from oto_mcp.datastore.core import make_store
    m, etat = serveur
    rid, ns = _recette(), _tableau()
    assert _executer(m, _corps(), ns, rid)["stopped"] == "job_submitted"
    assert _executer(m, _corps(), ns, rid)["stopped"] == "job_running"
    # Sans jeton, une nouvelle exécution reprend le MÊME travail : jamais relancé.
    assert [a for a in etat["appels"] if a[0] == "run"] == [("run", "acme/maps-scraper")]
    etat["statut"] = "SUCCEEDED"
    recu = _executer(m, _corps(), ns, rid)
    assert recu["done"] and recu["written"] == 5, {k: v for k, v in recu.items() if k in ("stopped", "error", "job_status", "done", "pages")}
    assert [a[1] for a in etat["appels"] if a[0] == "items"] == [0, 2, 4]
    assert len(make_store(SUB).cursor_rows(ns, limit=10)["rows"]) == 5
    # Tout lu : la suivante lance un travail neuf.
    assert _executer(m, _corps(), ns, rid)["stopped"] == "job_submitted"


def test_un_travail_echoue_est_abandonne(serveur):
    m, etat = serveur
    rid, ns = _recette(), _tableau()
    _executer(m, _corps(), ns, rid)
    etat["statut"] = "FAILED"
    recu = _executer(m, _corps(), ns, rid)
    assert recu["stopped"] == "job_failed" and recu["job_status"] == "FAILED"
    assert _executer(m, _corps(), ns, rid)["stopped"] == "job_submitted"


def test_le_contrat_de_start():
    from oto_mcp.recipes import contrat
    with pytest.raises(contrat.RecetteInvalide) as e:
        _corps(side_effects=None, start={
            "tool": "apify_run", "arguments": {"actor_id": "{{params.q}}"}, "id": "data.id",
            "status": {"tool": "apify_run_status", "path": "status", "ready": ["SUCCEEDED"]}})
    probs = " ".join(e.value.problemes)
    for attendu in ("side_effects", "literally", "max_total_charge_usd"):
        assert attendu in probs, attendu


def test_deux_appels_simultanes_ne_lancent_qu_un_travail(serveur):
    from oto_mcp.db import recipes as db_recipes
    m, etat = serveur
    rid, ns = _recette(), _tableau()
    assert db_recipes.reserver_travail(rid, "k")  # un autre appelant a gagné la place
    assert _executer(m, _corps(), ns, rid)["stopped"] == "job_start_unknown"
    assert [a for a in etat["appels"] if a[0] == "run"] == []
