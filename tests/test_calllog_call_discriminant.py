"""Le discriminant PAR APPEL du journal (#117).

Pourquoi ces tests existent : `session_id` désigne une conversation entière et
`run_id` est souvent NULL — rien n'identifiait UN appel. Donc on ne pouvait ni
prouver qu'une réponse était partie à la mauvaise requête, ni prouver qu'un
correctif avait fermé le trou. Trois champs le rendent possible : `request_id`
(l'identifiant de la requête entrante), `call_uid` (le nôtre, frappé à l'entrée)
et `effective_sub` (le compte relu APRÈS exécution).

Ce sont des INSTRUMENTS DE MESURE : une régression silencieuse ne casse rien, elle
produit des données fausses — et on conclurait « pas de cross-talk » sur un champ
qui ne se remplit plus. D'où des tests de forme sur la ligne émise.

Logique pure : sink stubbé, aucun accès DB (convention `CLAUDE.md` §Tests).
"""
import asyncio
import types

import pytest

from oto_mcp import calllog


def _context(tool="fr_get", arguments=None):
    """`on_call_tool` reçoit directement les params (≠ `on_initialize`)."""
    return types.SimpleNamespace(
        message=types.SimpleNamespace(name=tool, arguments=arguments or {}))


async def _drain():
    while calllog._PENDING:
        await asyncio.gather(*list(calllog._PENDING), return_exceptions=True)


def _logger(rows, identity=None):
    async def sink(row):
        rows.append(row)
    return calllog.ToolCallLogger(sink, server="oto",
                                  identity=identity or (lambda: {"sub": "user-1"}))


@pytest.mark.asyncio
async def test_chaque_appel_porte_son_propre_uid():
    """Deux appels de la MÊME session doivent être distinguables — c'est tout l'objet."""
    rows: list = []
    mw = _logger(rows)

    async def call_next(_ctx):
        return "ok"

    await mw.on_call_tool(_context(), call_next)
    await mw.on_call_tool(_context(), call_next)
    await _drain()

    assert len(rows) == 2
    uids = [r["call_uid"] for r in rows]
    assert all(uids), "un appel sans discriminant n'est pas corrélable"
    assert uids[0] != uids[1], "deux appels partagent le même uid : rien ne les distingue"


@pytest.mark.asyncio
async def test_l_uid_est_frappe_avant_l_execution_pas_apres():
    """Il doit exister même si le handler explose — sinon les appels en échec, qui
    sont précisément les suspects, sortiraient du journal sans discriminant."""
    rows: list = []
    mw = _logger(rows)

    async def call_next(_ctx):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await mw.on_call_tool(_context(), call_next)
    await _drain()

    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["call_uid"], "l'appel en échec n'a pas de discriminant"


@pytest.mark.asyncio
async def test_l_uid_survit_a_une_identite_indisponible():
    """La capture d'identité est best-effort (try/except) : elle ne doit pas emporter
    le discriminant avec elle."""
    rows: list = []

    def _boom():
        raise RuntimeError("pas de token")

    mw = _logger(rows, identity=_boom)

    async def call_next(_ctx):
        return "ok"

    await mw.on_call_tool(_context(), call_next)
    await _drain()

    assert rows[0]["call_uid"]
    assert rows[0]["sub"] is None


@pytest.mark.asyncio
async def test_le_handshake_ne_porte_pas_de_discriminant_d_appel():
    """`on_initialize` journalise un événement de PROTOCOLE, pas un appel d'outil :
    lui frapper un uid laisserait croire qu'on peut corréler une réponse d'outil."""
    import mcp.types as mt

    rows: list = []
    mw = _logger(rows)
    ctx = types.SimpleNamespace(message=mt.InitializeRequest(
        method="initialize",
        params=mt.InitializeRequestParams(
            protocolVersion="2025-11-25", capabilities=mt.ClientCapabilities(),
            clientInfo=mt.Implementation(name="claude.ai", version="1.0"))))

    async def call_next(_ctx):
        return "ok"

    await mw.on_initialize(ctx, call_next)
    await _drain()

    assert rows[0]["kind"] == "protocol"
    assert "call_uid" not in rows[0]


def test_l_insert_transporte_les_trois_champs(monkeypatch):
    """Garde-fou de bout en bout : les champs collectés doivent atteindre le SQL.

    Sans ça, on peut très bien capturer un discriminant que l'INSERT laisse tomber —
    le journal serait alors muet exactement là où on compte sur lui.
    """
    from oto_mcp.db import usage

    captured: dict = {}

    class _Conn:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(usage, "_connect", lambda: _Conn())
    usage.insert_tool_call({
        "tool": "fr_get", "ok": True, "sub": "user-1",
        "request_id": "req-42", "call_uid": "abc123", "effective_sub": "user-1",
    })

    for col in ("request_id", "call_uid", "effective_sub"):
        assert col in captured["sql"], f"{col} absent de l'INSERT"
    assert "req-42" in captured["params"]
    assert "abc123" in captured["params"]
    # Autant de placeholders que de valeurs — une colonne ajoutée sans son %s
    # décalerait TOUTES les valeurs en silence.
    assert captured["sql"].count("%s") == len(captured["params"])
