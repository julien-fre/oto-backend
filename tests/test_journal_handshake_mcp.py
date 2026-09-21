"""Le journal du handshake MCP dit ce que le serveur a RÉELLEMENT servi.

Observabilité pure (`handshake_log`) : une ligne par `initialize` et par requête de
liste — client, protocole demandé/négocié, hôte, et le NOMBRE d'éléments rendus. Ce
qu'on garde ici, dans l'ordre de ce qui coûterait le plus cher :

1. **Le compte est celui que le client reçoit** — après l'ajout des alias dépréciés et
   le renommage de tenant, pas celui de la liste en amont.
2. **Aucun contenu ne fuit** : ni nom d'outil, ni description, ni `sub`, ni e-mail, ni
   jeton, ni organisation. Une valeur hostile venue du client ne forge pas une seconde
   ligne.
3. **Fail-open** : un journal en panne ne casse ni la liste ni le handshake, et la
   panne se voit.
4. Les trois listes non-outils (`prompts`, `resources`, `templates`) sont couvertes.

Logique pure : aucune base, aucun réseau (convention `CLAUDE.md` §Tests).
"""
from __future__ import annotations

import logging
import types

import mcp.types as mt
import pytest
from fastmcp.server.middleware import MiddlewareContext

from _mcp_app import static_mcp as _test_mcp

from oto_mcp import deprecations, handshake_log
from oto_mcp.middleware.alias import ToolAliasMiddleware

LOGGER = "oto_mcp.handshake_log"
HOTE = "mcp.example.test"


@pytest.fixture(autouse=True)
def _en_tete_http(monkeypatch):
    """L'hôte est lu sur la requête HTTP courante : ici, une requête fictive."""
    monkeypatch.setattr(handshake_log, "get_http_headers",
                        lambda **_kw: {"host": HOTE})
    monkeypatch.setattr("oto_mcp.middleware.alias.current_user_sub_from_token",
                        lambda: None)


def _lignes(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.INFO]


async def _renvoie(valeur):
    return valeur


def _ctx_init(client="codex-fictif", version="9.9", protocole="2025-11-25"):
    return types.SimpleNamespace(message=mt.InitializeRequest(
        method="initialize",
        params=mt.InitializeRequestParams(
            protocolVersion=protocole,
            capabilities=mt.ClientCapabilities(),
            clientInfo=mt.Implementation(name=client, version=version))))


def _ctx_liste(methode="tools/list", client="codex-fictif", version="9.9"):
    session = types.SimpleNamespace(client_params=mt.InitializeRequestParams(
        protocolVersion="2025-11-25", capabilities=mt.ClientCapabilities(),
        clientInfo=mt.Implementation(name=client, version=version)))
    return types.SimpleNamespace(
        method=methode, fastmcp_context=types.SimpleNamespace(session=session))


def _resultat_init(protocole="2025-06-18"):
    return mt.InitializeResult(
        protocolVersion=protocole, capabilities=mt.ServerCapabilities(),
        serverInfo=mt.Implementation(name="oto", version="1"))


# ── initialize ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_initialize_dit_client_protocole_demande_et_negocie_et_hote(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    rendu = _resultat_init("2025-06-18")

    sortie = await ToolAliasMiddleware().on_initialize(
        _ctx_init(protocole="2025-11-25"), lambda _c: _renvoie(rendu))

    assert sortie.protocolVersion == "2025-06-18"          # le résultat est rendu tel quel
    assert _lignes(caplog) == [
        "mcp.handshake initialize client=codex-fictif/9.9 "
        f"protocol_requested=2025-11-25 protocol_negotiated=2025-06-18 host={HOTE}"]


# ── tools/list : le compte est celui que le client REÇOIT ────────────────────

async def _outils_reels(n=5):
    return list((await _test_mcp().list_tools(run_middleware=False))[:n])


@pytest.mark.asyncio
async def test_tools_list_compte_la_liste_servie_alias_deprecies_compris(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=LOGGER)
    amont = await _outils_reels()
    cible = amont[0].name
    monkeypatch.setattr(deprecations, "TOOLS", {"ancien_nom_fictif": cible})

    servi = await ToolAliasMiddleware().on_list_tools(
        _ctx_liste(), lambda _c: _renvoie(amont))

    assert len(servi) == len(amont) + 1                    # l'alias est bien ajouté…
    assert _lignes(caplog) == [                            # …et le compte est le SERVI
        f"mcp.handshake list method=tools/list count={len(servi)} "
        f"client=codex-fictif/9.9 host={HOTE}"]


@pytest.mark.asyncio
async def test_tools_list_compte_la_liste_apres_renommage_de_tenant(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=LOGGER)
    amont = await _outils_reels()
    monkeypatch.setattr("oto_mcp.middleware.alias.tool_alias.prefix_for",
                        lambda _sub: "acme")

    servi = await ToolAliasMiddleware().on_list_tools(
        _ctx_liste(), lambda _c: _renvoie(amont))

    assert f"count={len(servi)} " in _lignes(caplog)[0]


@pytest.mark.asyncio
async def test_tools_list_vide_se_journalise_a_zero(caplog):
    """Le cas qu'on cherche : un client qui reçoit ZÉRO outil doit laisser `count=0`."""
    caplog.set_level(logging.INFO, logger=LOGGER)

    servi = await ToolAliasMiddleware().on_list_tools(
        _ctx_liste(), lambda _c: _renvoie([]))

    assert list(servi) == []
    assert " count=0 " in _lignes(caplog)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("methode,hook", [
    ("prompts/list", "on_list_prompts"),
    ("resources/list", "on_list_resources"),
    ("resources/templates/list", "on_list_resource_templates"),
])
async def test_listes_non_outils_journalisees(caplog, methode, hook):
    caplog.set_level(logging.INFO, logger=LOGGER)
    items = ["a", "b", "c"]                                # seul `len()` est lu

    rendu = await getattr(ToolAliasMiddleware(), hook)(
        _ctx_liste(methode), lambda _c: _renvoie(items))

    assert rendu is items
    assert _lignes(caplog) == [
        f"mcp.handshake list method={methode} count=3 client=codex-fictif/9.9 host={HOTE}"]


@pytest.mark.asyncio
async def test_session_sans_clientinfo_dit_client_inconnu(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    ctx = types.SimpleNamespace(method="tools/list", fastmcp_context=None)

    await ToolAliasMiddleware().on_list_tools(ctx, lambda _c: _renvoie([]))

    assert " client=-/- " in _lignes(caplog)[0]


# ── aucune fuite ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aucun_contenu_ne_fuit(caplog, monkeypatch):
    """Ni nom d'outil, ni description, ni sub, ni e-mail, ni jeton, ni organisation."""
    caplog.set_level(logging.DEBUG)                        # TOUT logger, pas seulement le nôtre
    secrets = ["sub-SECRET-42", "fictif@exemple.test", "tok-SECRET-99", "OrgFictiveSA"]
    monkeypatch.setattr("oto_mcp.middleware.alias.current_user_sub_from_token",
                        lambda: secrets[0])
    monkeypatch.setattr(handshake_log, "get_http_headers", lambda **_kw: {
        "host": HOTE, "authorization": f"Bearer {secrets[2]}",
        "x-oto-email": secrets[1], "x-oto-org": secrets[3]})
    amont = await _outils_reels()

    await ToolAliasMiddleware().on_initialize(
        _ctx_init(), lambda _c: _renvoie(_resultat_init()))
    await ToolAliasMiddleware().on_list_tools(_ctx_liste(), lambda _c: _renvoie(amont))

    sortie = "\n".join(r.getMessage() for r in caplog.records if r.name == LOGGER)
    assert sortie                                          # une sortie vide prouverait moins
    for interdit in secrets:
        assert interdit not in sortie
    for outil in amont:
        assert outil.name not in sortie
        assert not outil.description or outil.description[:30] not in sortie


@pytest.mark.asyncio
async def test_une_valeur_hostile_ne_forge_pas_une_seconde_ligne(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    ctx = _ctx_init(client="x\nmcp.handshake list count=999", version="v" * 500)

    await ToolAliasMiddleware().on_initialize(ctx, lambda _c: _renvoie(_resultat_init()))

    (ligne,) = _lignes(caplog)
    assert "\n" not in ligne and " count=999" not in ligne
    assert len(ligne) < 400


# ── fail-open ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_un_journal_en_panne_ne_casse_ni_la_liste_ni_le_handshake(caplog, monkeypatch):
    def panne(**_kw):
        raise RuntimeError("panne de lecture des en-têtes")

    monkeypatch.setattr(handshake_log, "get_http_headers", panne)
    amont = await _outils_reels()

    servi = await ToolAliasMiddleware().on_list_tools(_ctx_liste(), lambda _c: _renvoie(amont))
    rendu = _resultat_init()
    init = await ToolAliasMiddleware().on_initialize(_ctx_init(), lambda _c: _renvoie(rendu))

    assert len(servi) == len(amont) and init is rendu
    assert any(r.levelno == logging.WARNING and "fail-open" in r.getMessage()
               for r in caplog.records)                    # la panne SE VOIT
