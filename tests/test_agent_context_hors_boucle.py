"""Gel de prod du 2026-09-21 (~140 s) : `_agent_context` ne bloque pas la boucle.

`me.agent_context` est un handler `async def` : `execute()` ne met en thread que les
handlers SYNC, le corps d'un handler async tourne SUR la boucle. `session_layers` fait du
SQL synchrone (`_resolve_context`) : appelé nûment, il tenait toute la production le temps
de la lecture. Cf. `docs/event-loop-perf.md`, « le mode n°1 a une porte dérobée ».

On observe la boucle plutôt que le source : pendant une lecture qui dort 0,5 s, une tâche
incrémente un compteur toutes les 10 ms. Boucle tenue → le compteur n'avance pas.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from oto_mcp.capabilities import _types, agent_context


@pytest.mark.asyncio
async def test_la_lecture_des_couches_ne_gele_pas_la_boucle(monkeypatch):
    async def _guide(*_a, **_k):
        return {}

    async def _tools(_ctx):
        return {"available": False}

    def _lecture_base_lente(sub, org_id):
        time.sleep(0.5)                       # le SQL synchrone de `_resolve_context`
        return [{"body": "couche"}]

    monkeypatch.setattr(agent_context.orgs_instructions, "_get_guide", _guide)
    monkeypatch.setattr(agent_context, "_tools_view", _tools)
    monkeypatch.setattr(agent_context._instructions, "session_layers", _lecture_base_lente)

    ticks = 0

    async def _battement():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    tache = asyncio.create_task(_battement())
    await asyncio.sleep(0)                    # laisse le battement démarrer
    try:
        vue = await agent_context._agent_context(
            _types.ResolvedCtx(sub="u", org_id=1), agent_context.AgentContextInput())
    finally:
        tache.cancel()

    assert vue["layers"], "la lecture n'a pas été jouée : la garde serait inerte"
    assert ticks >= 20, (
        f"la boucle n'a battu que {ticks} fois pendant une lecture de 0,5 s (≥ 20 attendus) : "
        "`session_layers` (SQL synchrone) tourne dans la boucle — le serveur est mono-loop, "
        "tout gèle (cf. docs/event-loop-perf.md)")
