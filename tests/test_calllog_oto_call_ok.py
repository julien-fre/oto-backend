"""`oto_call` ne lève jamais sur l'échec de sa cible (ADR 0036) — l'échec est rendu en
DONNÉE (`{"tool": ..., "ok": False, "error": ...}`), jamais en exception. Sans ce lot,
la ligne d'ENVELOPPE (`tool='oto_call'`) du journal marquait `ok: true` quoi qu'il
arrive : un filtre « erreurs » posé sur `oto_call` ne voyait alors rien d'un échec
relayé (oto-backend#784, trouvé sur 20 appels d'enrichissement en échec, invisibles
par ce chemin).

Logique pure : sink stubbé, aucun accès DB (convention `CLAUDE.md` §Tests) — même
patron que `test_calllog_call_discriminant.py`.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from oto_mcp import calllog


def _context(tool="oto_call", arguments=None):
    return types.SimpleNamespace(
        message=types.SimpleNamespace(name=tool, arguments=arguments or {}))


def _result(structured_content=None):
    return types.SimpleNamespace(structured_content=structured_content, content=[])


async def _drain():
    while calllog._PENDING:
        await asyncio.gather(*list(calllog._PENDING), return_exceptions=True)


def _logger(rows):
    async def sink(row):
        rows.append(row)
    return calllog.ToolCallLogger(sink, server="oto", identity=lambda: {"sub": "u-1"})


@pytest.mark.asyncio
async def test_ok_suit_la_cible_quand_le_dispatch_echoue():
    rows: list = []
    mw = _logger(rows)

    async def call_next(_ctx):
        return _result({"tool": "foncier_dpe", "ok": False, "error": "upstream 500"})

    await mw.on_call_tool(_context(arguments={"name": "foncier_dpe"}), call_next)
    await _drain()

    (ligne,) = rows
    assert ligne["ok"] is False, "un filtre « erreurs » sur oto_call doit voir cet échec"
    assert ligne["error"] == "upstream 500"


@pytest.mark.asyncio
async def test_ok_reste_vrai_quand_le_dispatch_reussit():
    rows: list = []
    mw = _logger(rows)

    async def call_next(_ctx):
        return _result({"tool": "foncier_dpe", "ok": True, "data": {"x": 1}})

    await mw.on_call_tool(_context(arguments={"name": "foncier_dpe"}), call_next)
    await _drain()

    (ligne,) = rows
    assert ligne["ok"] is True and ligne["error"] is None


@pytest.mark.asyncio
async def test_les_autres_outils_ne_sont_pas_touches():
    """Le rappel ne joue que sur `oto_call` — un outil ordinaire qui renverrait un
    dict portant par hasard une clé `ok` (son propre vocabulaire métier) ne doit pas
    voir son `ok` de journal réinterprété."""
    rows: list = []
    mw = _logger(rows)

    async def call_next(_ctx):
        return _result({"ok": False, "note": "vocabulaire métier, pas un échec relayé"})

    await mw.on_call_tool(_context(tool="un_outil_quelconque"), call_next)
    await _drain()

    (ligne,) = rows
    assert ligne["ok"] is True, "seul oto_call relit son résultat pour ok"


@pytest.mark.asyncio
async def test_forme_illisible_reste_ok_true_fail_open():
    """Un résultat sans `structured_content` exploitable (pas de dict, pas de clé
    `ok`) retombe sur le comportement D'AVANT ce lot — jamais une mesure fausse dans
    l'autre sens (un succès qui se prétendrait échec)."""
    rows: list = []
    mw = _logger(rows)

    for resultat in (_result(None), _result("pas un dict"), _result({"sans_cle_ok": 1})):
        rows.clear()

        async def call_next(_ctx, r=resultat):
            return r

        await mw.on_call_tool(_context(), call_next)
        await _drain()
        assert rows[0]["ok"] is True, resultat


def test_oto_call_outcome_directement():
    """La fonction pure, éprouvée sans passer par le middleware."""
    assert calllog._oto_call_outcome(_result({"ok": True})) == (True, None)
    assert calllog._oto_call_outcome(_result({"ok": False, "error": "boom"})) == (False, "boom")
    assert calllog._oto_call_outcome(_result({"ok": False})) == (False, "")
    assert calllog._oto_call_outcome(_result(None)) == (True, None)
    assert calllog._oto_call_outcome(None) == (True, None)
