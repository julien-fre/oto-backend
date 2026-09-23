"""oto#115 — le limiteur de débit du client Serper compte d'un appel à l'autre.

Le client d'oto-core porte son limiteur (un intervalle minimal entre deux requêtes)
dans l'INSTANCE. Le backend le reconstruisait à chaque appel d'outil : le compteur
repartait de zéro et la limite déclarée n'avait jamais d'effet. Ce banc fige :
- une clé = UNE instance, réutilisée par les outils `serper_*` ET le cran ② de
  `web_read` (les deux bouches serper du backend) ;
- deux clés = deux instances (la limite est celle d'une clé) ;
- avec le VRAI client d'oto-core, le second appel d'outil ATTEND : le limiteur agit.
Aucun appel réseau : le transport HTTP du client est doublé.
"""
import asyncio

import pytest


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import serper

    m = FastMCP("t")
    serper.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def cle(monkeypatch):
    """Cache vierge, clé résolue au choix du test, métrage neutralisé."""
    from oto_mcp.tools import serper as mod
    monkeypatch.setattr(mod, "_CLIENTS", {}, raising=False)
    etat = {"key": "k1"}
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda p, account=None: (etat["key"], False))
    monkeypatch.setattr("oto_mcp.session_org.note_call_trace", lambda **kw: None)
    return etat


@pytest.fixture
def fabrique(monkeypatch):
    construits = []

    class _Client:
        def __init__(self, api_key):
            construits.append(api_key)

        def search(self, **kw):
            return {"organic": [], "credits": 1}

        def scrape_page(self, url, include_markdown=True, timeout_s=None):
            return {"markdown": "du vrai contenu " * 20, "credits": 2}

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    return construits


def test_une_cle_UNE_instance_d_un_appel_a_l_autre(cle, fabrique):
    search = _tool("serper_search")
    search(query="a")
    search(query="b")
    _tool("serper_search")(query="c")   # même un outil ré-enregistré
    assert fabrique == ["k1"]


def test_deux_cles_deux_instances(cle, fabrique):
    search = _tool("serper_search")
    search(query="a")
    cle["key"] = "k2"
    search(query="b")
    cle["key"] = "k1"
    search(query="c")
    assert fabrique == ["k1", "k2"]


def test_web_read_partage_l_instance_des_outils_serper(cle, fabrique, monkeypatch):
    """Les deux bouches serper du backend tirent sur la même clé : deux instances,
    ce seraient deux limiteurs qui s'ignorent."""
    from oto_mcp.tools import web as W
    monkeypatch.setattr(W, "_cible_avec_repli_www", lambda url: (url, False))
    monkeypatch.setattr(W, "_fetch_http",
                        lambda url: {"ok": False, "verdict": "HTTP 403", "status": 403})
    _tool("serper_search")(query="a")

    class _Reg:
        tools = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    reg = _Reg()
    W.register(reg)
    out = asyncio.run(reg.tools["web_read"](url="https://acme.example/contact"))
    assert out["chemin"] == "serper"
    assert fabrique == ["k1"]


def test_le_limiteur_du_VRAI_client_agit_entre_deux_appels_d_outil(cle, monkeypatch):
    """Le fait mesuré par l'issue : sans instance partagée, le second appel ne
    dormait jamais. Transport doublé (aucun appel au fournisseur), horloge du client
    observée."""
    import oto.tools.serper.client as cli

    class _Rep:
        status_code = 200

        @staticmethod
        def json():
            return {"organic": [], "credits": 1}

    monkeypatch.setattr(cli.requests.Session, "post", lambda self, *a, **k: _Rep())
    dodos = []
    monkeypatch.setattr(cli.time, "sleep", lambda s: dodos.append(s))

    search = _tool("serper_search")
    search(query="a")
    search(query="b")
    assert len(dodos) == 1 and dodos[0] > 0, (
        "le second appel d'outil devait attendre l'intervalle du limiteur")
