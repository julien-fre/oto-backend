"""Serper à sec : un 400 « Not enough credits » est un solde vide, pas une entrée invalide.

Mesuré (signaux #1045, #1046, #1066) : Serper signale un compte épuisé par un **400**,
pas par un 402. Le correctif « crédits épuisés » (v1.368.0) lit le 402 — il ne voyait
donc pas Serper : l'agent recevait `invalid_input` (« corrige ton appel ») et la carte
du connecteur restait verte. Désormais :

- ce 400-là (et un 402 nu de Serper) passe par le MÊME chemin que tout 402 :
  `quota_exhausted`, clé BYO servie marquée `no_quota`, clé plateforme jamais marquée ;
- tout autre 400 reste une entrée invalide ;
- `web_read` saute son cran serper à sec (sans lever), le dit, et marque la clé ;
- la sonde classe le compte à sec en `no_quota` ;
- le refus « aucune clé » ne propose le prêt d'une clé plateforme que si oto en
  détient une (#1156).

Aucune base : le coffre est stubé au seam `credentials_store`.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp import credentials_store, error_taxonomy, session_org
from oto_mcp.connectors import health
from oto_mcp.connectors import verify as connector_verify
from oto_mcp.mcp_errors import McpError
from oto_mcp.middleware.error_envelope import ErrorEnvelopeMiddleware

A_SEC = RuntimeError("Serper search 400: Not enough credits")
BYO = ("org", "7", "serper", "")
PLATEFORME = ("platform", "defaut", "serper", "")


class _Reg:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        if a and callable(a[0]):
            return deco(a[0])
        return deco


@pytest.fixture()
def outils(monkeypatch):
    """Le connecteur serper monté sur un client stubé qui lève `etat["exc"]`."""
    etat = {"exc": A_SEC}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def search(self, *a, **k):
            raise etat["exc"]

        def scrape_page(self, *a, **k):
            raise etat["exc"]

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p: ("k", False))
    from oto_mcp.tools import serper
    serper._CLIENTS.clear()
    reg = _Reg()
    serper.register(reg)
    yield reg.tools, etat
    serper._CLIENTS.clear()


@pytest.fixture
def coffre(monkeypatch):
    rec = {"meta": []}
    health._SANS_MARQUE.clear()

    def _update_meta(et, eid, connector, account, patch, conn=None):
        rec["meta"].append(((et, eid, connector, account), patch))
        return True

    monkeypatch.setattr(credentials_store, "update_meta", _update_meta)
    monkeypatch.setattr(credentials_store, "clear_health_if_verdict",
                        lambda *a, **k: True)
    yield rec
    health._SANS_MARQUE.clear()


async def _appel(trace: dict, call_next):
    tok = session_org.set_call_trace(trace)
    try:
        return await ErrorEnvelopeMiddleware().on_call_tool(object(), call_next)
    finally:
        session_org.reset_call_trace(tok)


# --- la taxonomie voit le compte à sec ---------------------------------------------

def test_un_400_not_enough_credits_est_quota_exhausted(outils):
    fns, _ = outils
    with pytest.raises(McpError) as exc:
        fns["serper_search"](query="acme")
    info = error_taxonomy.classify(exc.value)
    assert info.code == "quota_exhausted" and info.retryable is False
    assert "à sec" in info.message


def test_un_402_nu_de_serper_est_quota_exhausted(outils):
    fns, etat = outils
    etat["exc"] = RuntimeError("Serper scrape 402: Payment required")
    with pytest.raises(McpError) as exc:
        fns["serper_scrape"]("https://example.com/page")
    assert error_taxonomy.classify(exc.value).code == "quota_exhausted"


def test_un_autre_400_reste_une_entree_invalide(outils):
    fns, etat = outils
    etat["exc"] = RuntimeError("Serper search 400: Missing fid/cid/placeId")
    with pytest.raises(McpError) as exc:
        fns["serper_search"](query="acme")
    info = error_taxonomy.classify(exc.value)
    assert info.code == "invalid_input"
    assert "Missing fid" in info.message


# --- l'enveloppe : même marquage que le 402 ---------------------------------------

def test_la_cle_byo_servie_est_marquee_no_quota(outils, coffre):
    fns, _ = outils

    async def _echoue(ctx):
        fns["serper_search"](query="acme")

    trace = {"credential_row": BYO, "resolved_connector": "serper"}
    with pytest.raises(McpError) as exc:
        asyncio.run(_appel(trace, _echoue))
    assert exc.value.error.data["oto"]["code"] == "quota_exhausted"
    assert [ligne for ligne, _ in coffre["meta"]] == [BYO]
    assert coffre["meta"][0][1]["health_verdict"] == "no_quota"


def test_la_cle_plateforme_n_est_jamais_marquee(outils, coffre):
    fns, _ = outils

    async def _echoue(ctx):
        fns["serper_search"](query="acme")

    trace = {"credential_row": PLATEFORME, "resolved_connector": "serper"}
    with pytest.raises(McpError) as exc:
        asyncio.run(_appel(trace, _echoue))
    assert exc.value.error.data["oto"]["code"] == "quota_exhausted"
    assert coffre["meta"] == []


# --- la sonde ---------------------------------------------------------------------

def test_la_sonde_classe_le_compte_a_sec_no_quota(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def search(self, *a, **k):
            raise A_SEC

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    from oto_mcp.tools import serper
    with pytest.raises(Exception) as exc:
        serper._verify({"key": "k"})
    assert connector_verify.classer(exc.value) == connector_verify.NO_QUOTA


# --- web_read : le cran serper est sauté, dit, et la clé marquée -------------------

def test_web_read_saute_le_cran_serper_a_sec(monkeypatch, coffre):
    from oto_mcp.tools import web as W

    class _Client:
        def __init__(self, *a, **k):
            pass

        def scrape_page(self, *a, **k):
            raise A_SEC

    import oto.tools.serper as _s
    from oto_mcp.tools import serper
    serper._CLIENTS.clear()
    monkeypatch.setattr(_s, "SerperClient", _Client)
    monkeypatch.setattr(W, "_cible_avec_repli_www", lambda url: (url, False))
    monkeypatch.setattr(W, "_fetch_http", lambda url: {"ok": False, "verdict": "timeout"})

    def _resout(p):
        session_org.note_call_trace(credential_row=BYO, resolved_connector="serper")
        return ("k", False)

    monkeypatch.setattr(W.access, "resolve_api_key", _resout)
    reg = _Reg()
    W.register(reg)
    trace: dict = {}
    tok = session_org.set_call_trace(trace)
    try:
        with pytest.raises(McpError) as exc:
            asyncio.run(reg.tools["web_read"](url="https://lent.fr"))
    finally:
        session_org.reset_call_trace(tok)
        serper._CLIENTS.clear()
    assert "à sec" in exc.value.error.message, "le cran sauté DIT pourquoi"
    assert [ligne for ligne, _ in coffre["meta"]] == [BYO]
    assert "credential_row" not in trace, (
        "retirée du relevé : un web_read réussi par un autre cran effacerait la marque")


# --- le refus « aucune clé » ------------------------------------------------------

def test_sans_cle_plateforme_le_refus_ne_propose_pas_de_pret(monkeypatch):
    from oto_mcp.access import indices
    monkeypatch.setattr(indices.links, "ou_poser_la_cle", lambda *a, **k: " (page X)")
    monkeypatch.setattr(indices.credentials_store, "list_platform_instances", lambda p: [])
    texte = indices._poser_ou_accorder("sub", 264, "serper")
    assert "Pose ta propre clé (page X)" in texte
    assert "prêter" not in texte and "grant" not in texte
    assert "ne fournit pas de clé plateforme `serper`" in texte


def test_avec_cle_plateforme_le_refus_propose_le_pret_par_oto(monkeypatch):
    from oto_mcp.access import indices
    monkeypatch.setattr(indices.links, "ou_poser_la_cle", lambda *a, **k: "")
    monkeypatch.setattr(indices.credentials_store, "list_platform_instances",
                        lambda p: [{"label": "defaut"}])
    texte = indices._poser_ou_accorder("sub", 264, "serper")
    assert "admins d'oto" in texte and "prêter" in texte


def test_un_hoquet_de_base_rend_le_refus_sans_proposer_le_pret(monkeypatch):
    """Fail-soft comme les autres indices : le refus reste, sans seconde proposition."""
    from oto_mcp.access import indices

    def _panne(p):
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(indices.links, "ou_poser_la_cle", lambda *a, **k: "")
    monkeypatch.setattr(indices.credentials_store, "list_platform_instances", _panne)
    assert indices._poser_ou_accorder("sub", 264, "serper") == "Pose ta propre clé."
