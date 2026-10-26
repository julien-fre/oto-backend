"""`POST /api/me/recipes` — la capacité `me.recipe` sur la face REST.

Ce qui est éprouvé : le BINDING tel que le registre le sert (verbe, chemin,
provisoire), que la garde reste celle de la face MCP (la même règle par op, pas une
seconde), et que la route est déclarée lourde (un `run` appelle un outil page après
page). Le comportement des ops est celui de la capacité, éprouvé par les autres
`test_recettes_*` : la face REST n'en ajoute aucun, sauf son PÉRIMÈTRE — trois ops
(`list`, `get`, `run`), toute autre refusée nommément (400 `op_not_served_over_rest`),
éprouvé ici sur la route montée par l'adaptateur réel.
"""
from __future__ import annotations

import dataclasses
import typing

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp.api import base as api_base
from oto_mcp.api import routes_lourdes as rl
from oto_mcp.capabilities import recipes as cap  # noqa: F401 — enregistre la capacité
from oto_mcp.capabilities.registry import CAPABILITIES


def _capacite():
    (c,) = [c for c in CAPABILITIES if c.key == "me.recipe"]
    return c


def test_la_capacite_est_servie_en_rest_sous_post_api_me_recipes():
    c = _capacite()
    assert c.rest is not None
    assert (c.rest.verb, c.rest.path) == ("POST", "/api/me/recipes")
    # Provisoire comme la capacité : publié `x-oto-provisoire` dans l'OpenAPI.
    assert c.rest.provisoire is True
    # Le corps est l'Input entier (`{op, slug, params, datastore…}`), pas un champ.
    assert c.rest.body_field is None


def test_les_deux_faces_partagent_la_meme_garde():
    c = _capacite()
    # Une seule capacité, une seule règle : la face REST ne porte pas de garde à elle.
    assert c.mcp == "oto_recipe"
    assert c.authz is not None


def test_la_route_est_declaree_lourde():
    lourdes = [r for r in rl.ROUTES_LOURDES if (r.methode, r.gabarit) == ("POST", "/api/me/recipes")]
    assert len(lourdes) == 1
    assert lourdes[0].concurrence > 0 and lourdes[0].par_minute > 0


# ── Le périmètre REST : list, get, run ─────────────────────────────────────────

def _toutes_les_ops() -> set:
    return set(typing.get_args(cap.RecipeInput.model_fields["op"].annotation))


@pytest.fixture
def route(monkeypatch):
    """La route `POST /api/me/recipes` telle que l'adaptateur REST la monte, sans base :
    l'authentification et la règle sont doublées (ctx sans org, donc ni borne ni
    suspension à lire), le handler est le VRAI ; `op=list` lit une doublure du store."""
    from oto_mcp.capabilities import _rest_adapter
    from oto_mcp.capabilities._types import ResolvedCtx

    async def authentifie(request, verifier, **kw):
        return "u-rest", None

    monkeypatch.setattr(cap, "_gerer", lambda ctx, inp: {"recipes": [], "ctx": ctx.channel})
    c = dataclasses.replace(_capacite(),
                            authz=lambda raw, inp: ResolvedCtx(sub=raw.sub, org_id=None))
    routes = _rest_adapter.make_routes(None, authentifie, api_base._json,
                                       api_base._json_error, api_base.options_handler, [c])
    return TestClient(Starlette(routes=routes))


def test_le_perimetre_rest_est_list_get_run():
    assert set(cap.OPS_REST) == {"list", "get", "run"}
    assert set(cap.OPS_REST) <= _toutes_les_ops()


def test_une_op_du_perimetre_est_servie(route):
    r = route.post("/api/me/recipes", json={"op": "list"})
    assert r.status_code == 200, r.text
    assert r.json()["ctx"] == "rest"


@pytest.mark.parametrize("op", sorted(_toutes_les_ops() - {"list", "get", "run"}))
def test_une_op_hors_perimetre_recoit_le_refus_nomme(route, op):
    r = route.post("/api/me/recipes", json={"op": op, "slug": "x"})
    assert r.status_code == 400, r.text
    corps = r.json()
    assert corps["error"] == "op_not_served_over_rest"
    # Le refus renvoie à l'outil qui sert l'op.
    assert "oto_recipe" in corps["detail"] and op in corps["detail"]


def test_le_refus_est_declare():
    assert ((400, "op_not_served_over_rest")
            in {(e.status, e.code) for e in _capacite().errors})
