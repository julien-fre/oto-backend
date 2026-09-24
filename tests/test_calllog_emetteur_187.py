"""Un appel porte son émetteur déclaré (otomata-tech/oto#187).

Le nom du logiciel client (`clientInfo`) était reçu à chaque handshake et gardé sur la
ligne `kind='protocol'` — jamais sur l'appel, et aucune lecture ne le rendait. Le jeton
nommé (`token_kind`) était perdu à la vérification du jeton MCP : NULL sur 100 % des
appels. Ces bancs tiennent les trois relais : l'écriture (un vrai client, un vrai
serveur), le jeton, et les lectures (liste, fiche, export d'audit, couverture).
"""
from __future__ import annotations

import asyncio
import types

import pytest

from oto_mcp import calllog


async def _drain():
    while calllog._PENDING:
        await asyncio.gather(*list(calllog._PENDING), return_exceptions=True)


def _serveur(rows):
    from fastmcp import FastMCP

    mcp = FastMCP("t187")

    @mcp.tool
    def echo(texte: str) -> str:
        return texte

    async def sink(row):
        rows.append(row)

    async def identite():
        return {"sub": "u-187"}

    mcp.add_middleware(calllog.ToolCallLogger(sink, server="oto", identity=identite))
    return mcp


# ── 1. l'écriture : le client déclaré atteint la ligne d'appel ───────────────

@pytest.mark.asyncio
async def test_un_appel_REEL_porte_le_client_declare_au_handshake():
    """Le chemin réel : le client se nomme à l'`initialize`, l'appel suivant porte ce
    nom — sans que l'appel ne le redise."""
    import mcp.types as mt
    from fastmcp import Client

    rows: list = []
    info = mt.Implementation(name="agent-cli", version="9.8.7")
    async with Client(_serveur(rows), client_info=info) as c:
        await c.call_tool("echo", {"texte": "bonjour"})
    await _drain()

    (ligne,) = [r for r in rows if r["tool"] == "echo"]
    assert ligne["args"]["_client"] == {"name": "agent-cli", "version": "9.8.7"}
    # L'argument réel reste là : l'émetteur s'ajoute, il ne remplace rien.
    assert ligne["args"]["texte"] == "bonjour"


def test_hors_session_MCP_aucun_emetteur_n_est_invente():
    """Hors requête MCP (REST, script) : pas de clé — une absence, jamais un nom deviné."""
    assert calllog.emetteur_declare() is None
    row = calllog.poser_emetteur({"args": {"a": 1}})
    assert row["args"] == {"a": 1}
    assert "token_kind" not in row


def test_le_jeton_nomme_est_verse_sur_la_ligne(monkeypatch):
    """Un jeton d'API porte `token_id`/`token_kind` dans ses claims : la ligne les
    reçoit. Une session OAuth n'en porte pas : rien n'est posé."""
    import fastmcp.server.dependencies as deps

    jeton = types.SimpleNamespace(claims={"sub": "u", "token_id": 42,
                                          "token_kind": "delegation"})
    monkeypatch.setattr(deps, "get_access_token", lambda: jeton)
    row = calllog.poser_emetteur({"args": None})
    assert (row["token_id"], row["token_kind"]) == (42, "delegation")

    oauth = types.SimpleNamespace(claims={"sub": "u", "azp": "app"})
    monkeypatch.setattr(deps, "get_access_token", lambda: oauth)
    row = calllog.poser_emetteur({"args": None})
    assert "token_id" not in row and "token_kind" not in row


@pytest.mark.asyncio
async def test_la_verification_du_jeton_d_API_garde_son_identifiant(monkeypatch):
    """La cause du 100 % NULL : `token_kind` voyageait, `token_id` était perdu à la
    reconstruction du jeton MCP."""
    from oto_mcp import server

    monkeypatch.setattr(server.db, "verify_api_token",
                        lambda t: {"sub": "u-187", "scopes": None, "token_id": 7,
                                   "token_kind": "user"})
    verifier = object.__new__(server._IatGatedVerifier)
    jeton = await verifier._verify_api_token("oto_abc")
    assert jeton.claims["token_id"] == 7
    assert jeton.claims["token_kind"] == "user"


# ── 2. les lectures : l'émetteur ressort de la base ──────────────────────────

ORG = 187_187


def _pose(tool, *, client=None, token_kind=None, org_id=ORG):
    from oto_mcp import db
    args = {"q": "x"}
    if client:
        args["_client"] = client
    db.insert_tool_call({"tool": tool, "sub": "u-187", "kind": "mcp", "ok": True,
                         "duration_ms": 5, "org_id": org_id, "args": args,
                         "token_kind": token_kind, "token_id": 3 if token_kind else None})


@pytest.fixture(scope="module")
def journal(live):
    _pose("t187_runner", client={"name": "oto-runner", "version": "1.2"},
          token_kind="delegation")
    _pose("t187_cli", client={"name": "claude-code", "version": "2.1"})
    _pose("t187_ancien")  # ligne antérieure au lot : aucun émetteur
    yield


def _par_outil(rows):
    return {r.get("tool_name") or r.get("tool"): r for r in rows}


def test_la_liste_rend_l_emetteur_sans_en_faire_un_argument(journal):
    from oto_mcp import db

    lignes = _par_outil(db.list_tool_calls(limit=50, org_id=ORG))
    runner = lignes["t187_runner"]
    assert (runner["client_name"], runner["client_version"], runner["token_kind"]) == \
        ("oto-runner", "1.2", "delegation")
    # La clé réservée n'est pas un argument de l'appel.
    assert runner["arg_keys"] == ["q"]
    assert lignes["t187_ancien"]["client_name"] is None


def test_la_fiche_rend_l_emetteur_et_le_jeton_nomme(journal):
    from oto_mcp import db

    (cid,) = [r["id"] for r in db.list_tool_calls(limit=50, org_id=ORG)
              if r["tool_name"] == "t187_runner"]
    fiche = db.get_tool_call(cid)
    assert fiche["client_name"] == "oto-runner"
    assert fiche["token_kind"] == "delegation"
    assert fiche["token_id"] == 3


def test_l_export_d_audit_rend_l_emetteur_jamais_les_arguments(journal):
    from oto_mcp import db

    page = db.export_tool_calls_for_org(ORG)
    lignes = _par_outil(page["calls"])
    assert lignes["t187_cli"]["client_name"] == "claude-code"
    assert lignes["t187_cli"]["client_version"] == "2.1"
    assert lignes["t187_runner"]["token_kind"] == "delegation"
    assert all("args" not in r for r in page["calls"])


def test_la_couverture_se_mesure_et_s_annonce(journal):
    from oto_mcp import db

    stats = db.tool_call_stats(since_days=7, org_id=ORG)
    assert stats["total_calls"] == 3
    assert stats["emitter_named_calls"] == 2
    par_client = {e["client_name"]: e["calls"] for e in stats["by_emitter"]}
    assert par_client == {"oto-runner": 1, "claude-code": 1, None: 1}
