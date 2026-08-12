"""Un champ inconnu est REFUSÉ, jamais ignoré.

Pydantic ignore par défaut les clés qu'il ne connaît pas. Un client qui se trompe de
forme reçoit donc un 200 et un comportement de repli, sans le moindre signal.

Vécu deux fois en une semaine, sur des surfaces différentes :
- 28/07 : `aiark_company_search(account=…)` — l'argument métier `account` avalé par le
  jeton de contexte du même nom. AI Ark renvoyait sa base entière, 72 millions de
  lignes, sans erreur ;
- 05/08 : un front envoyait `{app, scope}` au premier niveau alors que l'`Input`
  déclare `params: dict`. Les deux jetés en silence, le scope retombé sur sa valeur par
  défaut, le retour OAuth parti chez le mauvais front. Une demi-journée pour le trouver.

Même famille, même remède : refuser plutôt qu'ignorer, au seam partagé par les ~200
routes générées — pas connecteur par connecteur.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from oto_mcp.capabilities import _rest_adapter


class _Input(BaseModel):
    name: str
    params: dict | None = None


async def _appeler(corps: dict) -> tuple[int, dict]:
    """Exerce le VRAI handler de l'adaptateur — pas une reformulation de sa logique.

    Un test qui se contente de relire le source prouve que le code n'a pas bougé, pas
    qu'il refuse. On construit donc une capacité factice, on lui envoie un corps, et on
    lit la réponse."""
    from oto_mcp.capabilities._types import Capability, RestBinding

    vus: list = []

    def _core(ctx, inp):
        vus.append(inp)
        return {"ok": True}

    def _json_error(_req, status, code, message=None):
        return JSONResponse({"error": code, "detail": message}, status_code=status)

    def _json_response(_req, payload, status=200):
        return JSONResponse(payload, status_code=status)

    async def _auth(_req, _verifier):
        return "sub-test", None

    cap = Capability(key="test.cap", handler=_core, Input=_Input,
                     authz=lambda raw, inp: raw,
                     rest=RestBinding(verb="POST", path="/api/x"))
    handler = _rest_adapter._make_handler(cap, cap.rest, None, _auth,
                                          _json_response, _json_error)
    brut = json.dumps(corps).encode()

    async def _receive():
        return {"type": "http.request", "body": brut, "more_body": False}

    req = Request({"type": "http", "method": "POST", "path": "/api/x", "headers": [],
                   "query_string": b"", "path_params": {}}, _receive)
    rep = await handler(req)
    return rep.status_code, json.loads(bytes(rep.body)), vus


# --- le contrat ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_un_champ_inconnu_est_refuse_et_nomme():
    """LE cas du 05/08 : la forme plate au lieu de l'imbriquée. Avant, 200 + silence."""
    code, corps, vus = await _appeler({"name": "salesforce", "app": "tulina", "scope": "org"})
    assert code == 400 and corps["error"] == "unknown_fields"
    assert "app" in corps["detail"] and "scope" in corps["detail"]
    assert not vus, "le handler a tourné alors que l'entrée était mal formée"


@pytest.mark.asyncio
async def test_le_refus_dit_ce_qui_etait_attendu():
    """Sans les attendus, un client qui s'est trompé de FORME ne peut pas deviner."""
    _, corps, _ = await _appeler({"name": "x", "app": "tulina"})
    assert "params" in corps["detail"] and "name" in corps["detail"]


@pytest.mark.asyncio
async def test_la_forme_correcte_passe():
    code, corps, vus = await _appeler({"name": "salesforce", "params": {"scope": "org"}})
    assert code == 200 and corps == {"ok": True}
    assert vus and vus[0].params == {"scope": "org"}


def test_la_garde_est_bien_au_seam_partage():
    """TRIPWIRE. Si elle migrait dans une capacité particulière, les ~200 autres
    routes retomberaient dans le silence — c'est tout l'intérêt d'un seam."""
    import inspect
    src = inspect.getsource(_rest_adapter)
    assert "set(data) - set(cap.Input.model_fields)" in src, (
        "la garde de champ inconnu a quitté l'adaptateur REST")
    assert "unknown_fields" in src


def test_le_refus_nomme_aussi_les_champs_attendus():
    """Sans la liste des attendus, un client qui s'est trompé de FORME (imbriqué vs
    plat) ne peut pas deviner la bonne — c'était précisément le cas du 05/08."""
    import inspect
    src = inspect.getsource(_rest_adapter)
    assert "Attendus" in src


@pytest.mark.parametrize("cle", ["_org", "app", "scope", "foo"])
def test_aucune_cle_nest_tolérée_par_exception(cle):
    """Pas d'allowlist. Une exception « juste pour celle-là » rouvre le trou : c'est
    ainsi qu'un jeton de contexte a fini par manger un argument métier."""
    import inspect
    src = inspect.getsource(_rest_adapter)
    bloc = src[src.index("inconnus = "):src.index("try:\n            inp = cap.Input")]
    assert cle not in bloc, f"« {cle} » est traité en cas particulier dans la garde"
