"""The in-app agent's daily spend ceiling, and the interactive-session gate — without a
base (the store is stubbed). The served routes are replayed in `test_copilot_rest.py`."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from oto_mcp import geste
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.copilot import boucle, cle, depense, modele, routes


def test_le_modele_de_la_boucle_a_un_prix():
    assert modele.MODELE in depense.PRIX_PAR_MTOK


def test_cout_aux_quatre_comptes():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000,
             "cache_creation_input_tokens": 200_000, "cache_read_input_tokens": 5,
             "cache_creation": {"ephemeral_5m_input_tokens": 200_000}}
    # 4 $ + 2 $ + 1 $ + 1 µ$ (0.2 × 5), in millionths.
    assert depense.cout_micro_usd("claude-opus-5-5", usage) == 7_000_001


def test_un_modele_sans_prix_leve():
    with pytest.raises(KeyError):
        depense.cout_micro_usd("claude-inconnu", {"input_tokens": 1})


@pytest.mark.parametrize("brut,attendu", [(None, Decimal("20")), ("5", Decimal("5")),
                                          ("0.75", Decimal("0.75"))])
def test_plafond_lu(monkeypatch, brut, attendu):
    if brut is None:
        monkeypatch.delenv(depense.ENV_PLAFOND, raising=False)
    else:
        monkeypatch.setenv(depense.ENV_PLAFOND, brut)
    assert depense.plafond_usd() == attendu


@pytest.mark.parametrize("brut", ["", "abc", "0", "-3", "inf", "NaN"])
def test_plafond_illisible_leve(monkeypatch, brut):
    monkeypatch.setenv(depense.ENV_PLAFOND, brut)
    with pytest.raises(depense.PlafondIllisible):
        depense.plafond_usd()


def test_atteint_au_plafond(monkeypatch):
    monkeypatch.setenv(depense.ENV_PLAFOND, "2")
    monkeypatch.setattr(depense.store, "depense_du_jour", lambda sub: 1_999_999)
    assert not depense.atteint("s")
    monkeypatch.setattr(depense.store, "depense_du_jour", lambda sub: 2_000_000)
    assert depense.atteint("s")


@pytest.mark.parametrize("mode", ["off", "report", "enforce"])
def test_independant_du_mode_des_credits(monkeypatch, mode):
    from oto_mcp.access import credits as budget
    monkeypatch.setattr(budget, "mode", lambda: mode)
    monkeypatch.setenv(depense.ENV_PLAFOND, "1")
    monkeypatch.setattr(depense.store, "depense_du_jour", lambda sub: 1_000_000)
    assert depense.atteint("s")


def _un_appel(monkeypatch, flux, atteint=False):
    notes: list = []
    monkeypatch.setattr(depense, "atteint", lambda sub: atteint)
    monkeypatch.setattr(depense, "noter", lambda sub, m, usage: notes.append(dict(usage)))
    monkeypatch.setattr(cle, "reserver", lambda *a: None)
    monkeypatch.setattr(cle, "clore", lambda *a: None)
    monkeypatch.setattr(cle, "regler", lambda *a: None)
    monkeypatch.setattr(modele, "flux", flux)

    async def sauver(_):
        return None
    monkeypatch.setattr(boucle, "sauver", sauver)
    fil = {"id": "thr", "sub": "s", "org_id": 1, "model_calls": 0, "system": "",
           "messages": [], "client_tools": []}
    b = boucle.Boite(frozenset(), {}, [])

    async def go():
        async for _ in boucle._un_appel(fil, b, cle.Cle("sk", None, None), {}):
            pass
    return go, notes


def test_au_plafond_aucun_appel_ne_part(monkeypatch):
    async def jamais(*a, **k):
        raise AssertionError("the model was called")
        yield  # pragma: no cover
    go, notes = _un_appel(monkeypatch, jamais, atteint=True)
    with pytest.raises(cle.SansCle) as e:
        asyncio.run(go())
    assert (e.value.code, e.value.statut) == ("copilot_daily_cap", 429)
    assert notes == []


def test_un_appel_coupe_compte_ce_qu_il_a_consomme(monkeypatch):
    async def coupe(cle_, ws, corps, releve=None):
        releve["usage"] = {"input_tokens": 500}
        yield ("text", "partial")
        raise modele.ErreurModele("cut")
    go, notes = _un_appel(monkeypatch, coupe)
    with pytest.raises(modele.ErreurModele):
        asyncio.run(go())
    assert notes == [{"input_tokens": 500}]


def test_un_appel_complet_compte_l_usage_final(monkeypatch):
    async def complet(cle_, ws, corps, releve=None):
        usage = {"input_tokens": 10, "output_tokens": 3}
        releve["usage"] = usage
        yield ("end", {"content": [], "stop_reason": "end_turn", "usage": usage})
    go, notes = _un_appel(monkeypatch, complet)
    asyncio.run(go())
    assert notes == [{"input_tokens": 10, "output_tokens": 3}]


# --- the interactive-session gate ------------------------------------------------------

@pytest.mark.parametrize("source", [None, geste.API, geste.SYSTEM, geste.AGENT])
def test_hors_session_interactive_refuse(monkeypatch, source):
    monkeypatch.setenv("OTO_COPILOT_SUBS", "s")
    ctx = ResolvedCtx(sub="s", org_id=1)
    if source is None:
        with pytest.raises(AuthzDenied) as e:
            routes._garde(ctx)
    else:
        with geste.portee(source, "s"), pytest.raises(AuthzDenied) as e:
            routes._garde(ctx)
    assert (e.value.status, e.value.code) == (403, "api_token_forbidden")


def test_session_interactive_passe(monkeypatch):
    monkeypatch.setenv("OTO_COPILOT_SUBS", "s")
    with geste.portee(geste.CONSOLE, "s"):
        routes._garde(ResolvedCtx(sub="s", org_id=1))


def test_les_routes_refusent_un_jeton_a_l_authentification():
    from oto_mcp.capabilities.registry import CAPABILITIES
    caps = [c for c in CAPABILITIES if c.key.startswith("copilot.")]
    assert len(caps) == 6
    for c in caps:
        assert all(b.allow_api_token is False and b.allow_issuer_token is False
                   for b in c.rest_bindings()), c.key
