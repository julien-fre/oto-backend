"""`GET /api/orgs/{id}/recipes` — les recettes d'une org, sans corps.

Ce que la page d'un processus lit pour savoir quelles recettes l'org possède et si
chacune est en service. Pur : le store est remplacé, seul le handler est éprouvé
(la porte « membre de l'org » est `ORG_MEMBER_OF`, éprouvée ailleurs).
"""
from __future__ import annotations

import asyncio
import datetime as dt

from oto_mcp.capabilities import recipes as cap
from oto_mcp.capabilities._authz import ORG_MEMBER_OF
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES


def _fiche(slug: str, published: int | None, latest: int) -> dict:
    return {"id": 1, "owner_type": "org", "owner_id": "7", "slug": slug, "title": slug.title(),
            "description": None, "published_version": published, "created_by": "u",
            "created_at": dt.datetime(2026, 10, 1), "updated_at": dt.datetime(2026, 10, 2),
            "latest_version": latest}


def test_liste_les_recettes_de_l_org_avec_leur_statut(monkeypatch):
    vus = []

    def list_recipes(owners):
        vus.append(list(owners))
        return [_fiche("find-people", 2, 3), _fiche("draft-one", None, 1)]

    monkeypatch.setattr(cap.db_recipes, "list_recipes", list_recipes)
    out = asyncio.run(cap._org_recipes(ResolvedCtx(sub="u", org_id=7, role="member"),
                                       cap.OrgRecipesInput(org_id=7)))
    assert vus == [[("org", "7")]]
    assert [r["status"] for r in out["recipes"]] == ["published", "proposed"]
    premiere = out["recipes"][0]
    assert premiere["published_version"] == 2 and premiere["latest_version"] == 3
    assert premiere["description"] == ""
    assert premiere["updated_at"] == "2026-10-02 00:00:00"
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
