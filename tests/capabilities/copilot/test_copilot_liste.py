"""`OTO_COPILOT_SUBS`: named accounts, or `*` for every account — empty admits nobody, and
`*` never lifts the other gates (an interactive session only)."""
from __future__ import annotations

import pytest

from oto_mcp import geste
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx
from oto_mcp.capabilities.copilot import routes

SUB = "user-s"


@pytest.mark.parametrize("liste,admis", [("*", True), ("other, *", True), (SUB, True),
                                         ("", False), ("other", False)])
def test_liste(monkeypatch, liste, admis):
    monkeypatch.setenv("OTO_COPILOT_SUBS", liste)
    ctx = ResolvedCtx(sub=SUB, org_id=1)
    with geste.portee(geste.CONSOLE, SUB):
        if admis:
            routes._garde(ctx)
        else:
            with pytest.raises(AuthzDenied) as e:
                routes._garde(ctx)
            assert (e.value.status, e.value.code) == (403, "copilot_not_enabled")


def test_absente_n_admet_personne(monkeypatch):
    monkeypatch.delenv("OTO_COPILOT_SUBS", raising=False)
    with geste.portee(geste.CONSOLE, SUB), pytest.raises(AuthzDenied) as e:
        routes._garde(ResolvedCtx(sub=SUB, org_id=1))
    assert e.value.code == "copilot_not_enabled"


def test_etoile_n_admet_pas_un_jeton(monkeypatch):
    monkeypatch.setenv("OTO_COPILOT_SUBS", "*")
    with geste.portee(geste.API, SUB), pytest.raises(AuthzDenied) as e:
        routes._garde(ResolvedCtx(sub=SUB, org_id=1))
    assert e.value.code == "api_token_forbidden"
