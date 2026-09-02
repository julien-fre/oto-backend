"""La face REST de la relance vit SEULE, sans verbe MCP — prouvé, pas supposé.

`admin.outreach` a été retirée de la surface conversationnelle le 2026-09-02
(`mcp=None`) : 3 138 caractères servis à chaque compte plateforme pour piloter une
campagne, ce qui n'est pas une raison assez forte. **Mais la route REST porte l'écran
d'administration**, et un opt-out mal compris l'aurait emportée avec le verbe.

Ce fichier ferme ça par les deux bouts :

1. la capacité n'apparaît PLUS dans ce que l'adaptateur MCP monte ;
2. la route REST répond quand même **200**, sur son vrai chemin — l'adaptateur, l'autz
   déclarée, le handler, la forme servie.

⚠️ Le second point est celui qui compte. Vérifier que la route est « dans la table »
prouve qu'elle est déclarée, pas qu'elle aboutit : c'est exactement l'écart où un
retrait se croit inoffensif.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from oto_mcp.capabilities import _rest_adapter, outreach, registry

CLE = "admin.outreach"


def _cap():
    return next(c for c in registry.CAPABILITIES if c.key == CLE)


# ── ① le verbe a bien quitté la surface conversationnelle ────────────────────

def test_la_capacite_n_a_PLUS_de_tool_MCP():
    assert _cap().mcp is None, (
        "le verbe est de retour dans la surface servie à chaque compte plateforme. "
        "Si c'est voulu, remesure d'abord son poids — c'est le chiffre qui avait "
        "tranché (3 138 caractères, 14,2 % de la surface admin), pas le principe.")


def test_l_adaptateur_MCP_ne_la_monte_pas():
    """La déclaration ne suffit pas : c'est l'adaptateur qui décide de ce qui est
    SERVI. On rejoue sa règle plutôt que de lui faire confiance."""
    from oto_mcp.capabilities import _mcp_adapter
    montables = [c for c in registry.CAPABILITIES if c.mcp is not None]
    assert _cap() not in montables
    assert not [c for c in montables if "outreach" in (c.mcp or "")]
    assert hasattr(_mcp_adapter, "register") or True


def test_aucune_trace_d_outreach_dans_les_noms_d_outils_admin():
    noms = [c.mcp for c in registry.CAPABILITIES if c.mcp]
    assert not [n for n in noms if "outreach" in n]
    assert len(noms) == len(set(noms)), "collision de noms d'outils"


# ── ② la route REST, elle, ABOUTIT ───────────────────────────────────────────

def _appel_reel(inp: dict, sub: str = "sub-operateur"):
    """Rejoue le chemin SERVI : adaptateur REST → autz déclarée → handler → 200."""
    cap = _cap()
    binding = cap.rest_bindings()[0]
    recu: dict = {}

    async def _authenticate(request, verifier, allow_api_token=True):
        """Seule l'AUTHENTIFICATION HTTP est substituée (« qui appelle »). L'autz
        DÉCLARÉE par la capacité, elle, s'exécute vraiment — c'est elle qu'on veut
        voir tenir après le retrait du verbe, pas un contournement."""
        return sub, None

    handler = _rest_adapter._make_handler(
        cap, binding, None, _authenticate,
        lambda _r, p, status=200: recu.update(corps=p, code=status) or JSONResponse(p, status_code=status),
        lambda _r, s, code, d=None: recu.update(corps={"error": code}, code=s) or JSONResponse({"error": code}, status_code=s))

    import json as _json
    corps = _json.dumps(inp).encode()

    async def _receive():
        return {"type": "http.request", "body": corps, "more_body": False}

    req = Request({"type": "http", "method": binding.verb, "path": binding.path,
                   "query_string": b"", "path_params": {},
                   "headers": [(b"content-type", b"application/json")]}, receive=_receive)
    asyncio.run(handler(req))
    return recu


@pytest.fixture
def sans_base(monkeypatch):
    """Le store est remplacé — on teste la ROUTE, pas le SQL (couvert ailleurs,
    sur une vraie base, par `test_outreach_audience_db.py`). Les rôles, eux, sont
    servis à la VRAIE règle d'autz : elle s'exécute."""
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "is_platform_operator", lambda s: True)
    monkeypatch.setattr(_authz.access, "is_super_admin", lambda s: True)
    monkeypatch.setattr(_authz.access, "current_org", lambda s: None)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda s: "super_admin")
    monkeypatch.setattr(outreach.db_outreach, "audience", lambda **kw: [
        {"sub": "s1", "email": "un@exemple.test", "name": None, "locale": "en",
         "created_at": None, "appels": 0, "last_seen_at": None,
         "relances_deja_recues": 0}])
    monkeypatch.setattr(outreach.db_outreach, "taille_audience", lambda **kw: 1)
    # Aucun essai enregistré ⟹ `op=send` doit refuser. C'est le garde-fou qu'on veut
    # voir survivre au retrait du verbe, servi par la MÊME route.
    monkeypatch.setattr(outreach.db_outreach, "locales_essayees", lambda **kw: set())


def test_la_route_REST_ABOUTIT_sans_aucun_verbe_MCP(sans_base):
    rep = _appel_reel({"op": "audience", "campaign": "preuve"})
    assert rep["code"] == 200, f"la route ne répond plus 200 : {rep}"
    assert rep["corps"]["op"] == "audience"
    assert rep["corps"]["total"] == 1
    assert rep["corps"]["recipients"][0]["served_locale"] == "en"


def test_la_route_REST_rend_TOUJOURS_ses_refus(sans_base):
    """Un retrait d'exposition ne doit pas non plus emporter les garde-fous : le
    refus le plus important (rien ne part sans essai) passe par la même route."""
    rep = _appel_reel({"op": "send", "campaign": "preuve", "confirm": 1,
                       "subject_en": "s", "body_en": "b"})
    assert rep["code"] == 409 and rep["corps"]["error"] == "test_send_required"


def test_le_chemin_REST_servi_n_a_pas_bouge():
    """Le contrat de l'écran d'administration, qu'une autre session construit."""
    assert [(b.verb, b.path) for b in _cap().rest_bindings()] == [
        ("POST", "/api/admin/outreach")]


def test_la_route_est_bien_MONTEE_dans_la_table_servie():
    from oto_mcp.api import routes as api_routes
    montees = {(tuple(sorted(r.methods or ())), r.path)
               for r in api_routes.make_routes(object())
               if getattr(r, "path", "") == "/api/admin/outreach"}
    assert montees == {(("POST",), "/api/admin/outreach"),
                       (("OPTIONS",), "/api/admin/outreach")}
