"""L'identité du journal d'appels se résout HORS de la boucle (#1039, suite).

`ToolCallLogger` demande l'identité de chaque `initialize` et de chaque `tools/call`. Sous
drain d'alias, `current_user_sub_from_token` lit la base (`resolve_sub` + `upsert_user`)
dès que la portée d'identité du message est vide — pré-résolution en échec, refus non
mémorisé. Le journal l'appelait en synchrone, donc dans la boucle, et son `except
Exception` avalait la violation de la garde d'exécution : relevé par la fin de session
(`_hors_boucle.exiger_aucune_violation`), invisible avant.

Le banc prend `server._calllog_identity`, celle que `_build_mcp` branche sur le journal
(pas une copie) ; `test_le_serveur_branche_cette_identite` garde ce branchement.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp import calllog


@pytest.mark.asyncio
async def test_l_identite_du_journal_se_resout_hors_de_la_boucle(monkeypatch):
    from oto_mcp.auth import hooks
    vu = []

    def _resolution():
        try:
            asyncio.get_running_loop()
            vu.append("boucle")
        except RuntimeError:
            vu.append("thread")
        return "sub-canonique"

    monkeypatch.setattr(hooks, "current_user_sub_from_token", _resolution)
    from oto_mcp import server
    assert await server._calllog_identity() == {"sub": "sub-canonique"}, \
        "même identité journalisée"
    assert vu == ["thread"], (
        "la résolution d'identité du journal tourne dans la boucle : sous drain d'alias "
        "elle lit la base, et le serveur est mono-loop (docs/event-loop-perf.md)")


@pytest.mark.asyncio
async def test_un_echec_d_identite_ne_casse_pas_le_journal_mais_une_violation_remonte():
    async def _echec():
        raise RuntimeError("base indisponible")

    rows = []

    async def sink(row):
        rows.append(row)

    journal = calllog.ToolCallLogger(sink, server="oto", identity=_echec)
    row: dict = {"sub": None}
    await journal._poser_identite(row)
    assert row == {"sub": None}, "un échec d'identité laisse la ligne anonyme"

    from oto_mcp.db._hors_boucle import HorsBoucle

    async def _violation():
        raise HorsBoucle("accès base depuis la boucle")

    journal = calllog.ToolCallLogger(sink, server="oto", identity=_violation)
    with pytest.raises(HorsBoucle):
        await journal._poser_identite({})


def test_le_serveur_branche_cette_identite():
    """Le branchement lui-même : construire (synchrone, comme `test_server_construction`)
    et retrouver `_calllog_identity` sur l'unique journal d'appels."""
    from oto_mcp import server
    loggers = [m for m in server._build_mcp("noauth").middleware
               if isinstance(m, calllog.ToolCallLogger)]
    assert [m.identity for m in loggers] == [server._calllog_identity]
