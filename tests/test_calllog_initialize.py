"""`ToolCallLogger.on_initialize` — journalisation du handshake (kind='protocol').

Cette ligne est un INSTRUMENT DE MESURE (cadence de re-handshake par client), pas
du monitoring d'usage : une régression silencieuse produirait des données fausses
plutôt qu'une erreur. D'où ce test de forme sur la ligne émise.

Logique pure : sink stubbé, aucun accès DB (convention `CLAUDE.md` §Tests).
"""
import asyncio
import types

import mcp.types as mt
import pytest

from oto_mcp import calllog


def _context(client_name="claude.ai", client_version="1.2.3", protocol="2025-11-25"):
    """Vrai `InitializeRequest` — PAS un stub maison.

    fastmcp passe à `on_initialize` la requête ENTIÈRE (params sous `.params`), là
    où `on_call_tool` reçoit directement les params. Un stub à plat reproduisait la
    mauvaise forme : le test passait et la prod écrivait des NULL. On construit donc
    le type réel, pour que la structure ne puisse plus diverger en silence.
    """
    return types.SimpleNamespace(
        message=mt.InitializeRequest(
            method="initialize",
            params=mt.InitializeRequestParams(
                protocolVersion=protocol,
                capabilities=mt.ClientCapabilities(),
                clientInfo=mt.Implementation(name=client_name, version=client_version),
            ),
        )
    )


async def _drain():
    """Le sink part en create_task (fire-and-forget) : on attend les tâches en vol."""
    while calllog._PENDING:
        await asyncio.gather(*list(calllog._PENDING), return_exceptions=True)


def _logger(rows):
    async def sink(row):
        rows.append(row)

    return calllog.ToolCallLogger(
        sink, server="oto", identity=lambda: {"sub": "user-42", "email": "a@b.c"}
    )


@pytest.mark.asyncio
async def test_initialize_emet_une_ligne_protocol():
    rows: list[dict] = []
    sentinel = object()

    async def call_next(_ctx):
        return sentinel

    result = await _logger(rows).on_initialize(_context(), call_next)
    await _drain()

    assert result is sentinel, "le handshake doit passer inchangé (middleware transparent)"
    assert len(rows) == 1
    row = rows[0]
    # Discriminateur : isole du monitoring d'outils, dont les lectures filtrent kind='mcp'.
    assert row["kind"] == "protocol"
    assert row["tool"] == "initialize"
    assert row["ok"] is True
    assert row["sub"] == "user-42"
    # Les 3 axes qui rendent la mesure exploitable par client.
    assert row["args"] == {
        "client_name": "claude.ai",
        "client_version": "1.2.3",
        "protocol_version": "2025-11-25",
    }
    assert isinstance(row["duration_ms"], int)


@pytest.mark.asyncio
async def test_initialize_sans_client_info_ne_casse_pas():
    """Un client qui n'annonce pas son identité doit produire une ligne, pas une erreur."""
    rows: list[dict] = []

    async def call_next(_ctx):
        return object()

    # Client exotique : requête sans params exploitables (repli du hook sur `msg`).
    ctx = types.SimpleNamespace(message=types.SimpleNamespace(params=None))
    await _logger(rows).on_initialize(ctx, call_next)
    await _drain()

    assert rows[0]["args"] == {
        "client_name": None,
        "client_version": None,
        "protocol_version": None,
    }


@pytest.mark.asyncio
async def test_initialize_en_echec_est_journalise_puis_relance():
    """Un handshake refusé (401, version non supportée…) doit rester visible."""
    rows: list[dict] = []

    async def call_next(_ctx):
        raise RuntimeError("handshake refusé")

    with pytest.raises(RuntimeError):
        await _logger(rows).on_initialize(_context(), call_next)
    await _drain()

    assert rows[0]["ok"] is False
    assert "handshake refusé" in rows[0]["error"]
