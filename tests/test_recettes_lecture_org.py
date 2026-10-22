"""`GET /api/orgs/{id}/recipes` — les recettes d'une org, sans corps — et leur affichage
(`display`, posé par `oto_recipe op=describe`).

Ce que la page d'un processus lit pour savoir quelles recettes l'org possède et si
chacune est en service. Pur : le store est remplacé, seul le handler est éprouvé
(la porte « membre de l'org » est `ORG_MEMBER_OF`, éprouvée ailleurs).
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from oto_mcp.capabilities import recipes as cap
from oto_mcp.capabilities._authz import ORG_MEMBER_OF
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES
from oto_mcp.recipes import contrat


def _fiche(slug: str, published: int | None, latest: int) -> dict:
    return {"id": 1, "owner_type": "org", "owner_id": "7", "slug": slug, "title": slug.title(),
            "description": None, "display": None, "published_version": published, "created_by": "u",
            "created_at": dt.datetime(2026, 10, 1), "updated_at": dt.datetime(2026, 10, 2),
            "latest_version": latest}


def test_liste_les_recettes_de_l_org_avec_leur_statut(monkeypatch):
    vus = []

    def list_recipes(owners):
        vus.append(list(owners))
        return [{**_fiche("find-people", 2, 3),
                 "display": {"steps": [{"label": "Find", "detail": "",
                                        "credits": 1.5, "per": "company"}]}},
                _fiche("draft-one", None, 1)]

    monkeypatch.setattr(cap.db_recipes, "list_recipes", list_recipes)
    out = asyncio.run(cap._org_recipes(ResolvedCtx(sub="u", org_id=7, role="member"),
                                       cap.OrgRecipesInput(org_id=7)))
    assert vus == [[("org", "7")]]
    assert [r["status"] for r in out["recipes"]] == ["published", "proposed"]
    premiere = out["recipes"][0]
    assert premiere["published_version"] == 2 and premiere["latest_version"] == 3
    assert premiere["description"] == ""
    assert premiere["updated_at"] == "2026-10-02 00:00:00"
    assert premiere["display"]["steps"][0]["per"] == "company"
    assert out["recipes"][1]["display"] is None
    # Jamais de corps : celui d'une recette-code est du code.
    assert "body" not in premiere
    cap.OrgRecipes.model_validate(out)


def test_route_rest_membre_seulement_et_provisoire():
    capa = next(c for c in CAPABILITIES if c.key == "org.recipes")
    (liaison,) = capa.rest_bindings()
    assert (liaison.verb, liaison.path) == ("GET", "/api/orgs/{id}/recipes")
    assert liaison.provisoire is True
    assert capa.mcp is None
    assert capa.authz.__qualname__ == ORG_MEMBER_OF("org_id").__qualname__


# ── L'affichage : validé à l'écriture, posé par `describe` ──────────────────────


def test_affichage_valide_et_normalise():
    out = contrat.valider_affichage({
        "steps": [{"label": " Pick accounts ", "detail": "Tier A/B", "credits": 0.3,
                   "per": "company"}, {"label": "Write"}],
        "saves": "replaces ~300 tool calls a run with 9"})
    assert out == {"steps": [{"label": "Pick accounts", "detail": "Tier A/B",
                              "credits": 0.3, "per": "company"},
                             {"label": "Write", "detail": ""}],
                   "saves": "replaces ~300 tool calls a run with 9"}


@pytest.mark.parametrize("brut, attendu", [
    ("x", "must be an object"),
    ({"steps": []}, "1 to 10 steps"),
    ({"steps": [{"label": "a"}] * 11}, "1 to 10 steps"),
    ({"steps": [{"label": ""}]}, "label: 1 to 60"),
    ({"steps": [{"label": "a", "credits": 1}]}, "go together"),
    ({"steps": [{"label": "a", "credits": 1, "per": "seat"}]}, "per must be one of"),
    ({"steps": [{"label": "a", "credits": -1, "per": "row"}]}, "0 to 10000"),
    ({"steps": [{"label": "a", "credits": True, "per": "row"}]}, "0 to 10000"),
    ({"steps": [{"label": "a", "color": "red"}]}, "unknown keys"),
    ({"steps": [{"label": "a"}], "cost": 3}, "unknown keys"),
])
def test_affichage_refuse(brut, attendu):
    with pytest.raises(contrat.RecetteInvalide) as e:
        contrat.valider_affichage(brut)
    assert attendu in str(e.value)


def _ctx():
    return ResolvedCtx(sub="u", org_id=7, role="member")


def test_describe_pose_l_affichage_sans_toucher_aux_versions(monkeypatch):
    fiche = _fiche("find-people", 2, 3)
    poses = []
    monkeypatch.setattr(cap.db_recipes, "get_recipe", lambda *a: fiche)
    monkeypatch.setattr(cap.db_recipes, "describe_recipe",
                        lambda rid, **kw: poses.append((rid, kw)))
    monkeypatch.setattr(cap.db_recipes, "propose_version",
                        lambda *a, **k: pytest.fail("describe ne propose aucune version"))
    cap._gerer(_ctx(), cap.RecipeInput(op="describe", slug="find-people",
                                       display={"steps": [{"label": "Find"}]}))
    cap._gerer(_ctx(), cap.RecipeInput(op="describe", slug="find-people", display={}))
    assert poses == [(1, {"title": None, "description": None,
                          "display": {"steps": [{"label": "Find", "detail": ""}]}}),
                     (1, {"title": None, "description": None, "display": None})]


def test_describe_refuse_un_affichage_mal_forme_et_un_appel_vide(monkeypatch):
    monkeypatch.setattr(cap.db_recipes, "get_recipe", lambda *a: _fiche("x", 1, 1))
    monkeypatch.setattr(cap.db_recipes, "describe_recipe",
                        lambda *a, **k: pytest.fail("rien ne doit être écrit"))
    with pytest.raises(AuthzDenied) as e:
        cap._gerer(_ctx(), cap.RecipeInput(op="describe", slug="x",
                                           display={"steps": [{"label": "a", "credits": 1}]}))
    assert e.value.code == "invalid_display"
    with pytest.raises(AuthzDenied) as e:
        cap._gerer(_ctx(), cap.RecipeInput(op="describe", slug="x"))
    assert e.value.code == "nothing_to_describe"
