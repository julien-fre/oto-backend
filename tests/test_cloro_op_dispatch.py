"""Dispatch de la surface Cloro consolidée (ADR 0047 §Amendement, appliqué au
connecteur cloro : 8 tools → 2).

Le module n'avait AUCUN test : ses 6 tools moteurs étaient produits par une factory
(un tool par moteur, mêmes paramètres) et les deux tools Google étaient des
passe-plats. La consolidation déplace le risque exactement là où rien ne le
regardait : `engine=`/`op=` choisissent désormais la méthode client (`monitor` avec
son `provider`, `google`, `google_news`) — une valeur mal câblée interrogerait
SILENCIEUSEMENT le mauvais moteur (une veille de marque « ChatGPT » remplie par
Grok ne ressemble pas à une panne). D'où, pour chaque valeur : la méthode client
appelée ET le `provider` transmis, le refus explicite d'une valeur inconnue, et le
maillage include/params (les flags coûteux ou hors-scope ne doivent pas partir).
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools.cloro import _AI_ENGINES


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import cloro as C

    m = FastMCP("t")
    C.register(m)
    return asyncio.run(m.get_tool(name))


@pytest.fixture
def client(monkeypatch):
    """Faux CloroClient + clé résolue en BYO (is_platform=False)."""
    import oto.tools.cloro.client as cc

    inst = MagicMock()
    monkeypatch.setattr(cc, "CloroClient", MagicMock(return_value=inst))
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider: ("fake-key", False))
    monkeypatch.setattr("oto_mcp.access.record_platform_usage", MagicMock())
    return inst


# --- surface -------------------------------------------------------------------

def test_the_connector_exposes_exactly_two_tools(client):
    """8 → 2. La liste est le contrat : un tool qui réapparaîtrait (ou un moteur
    re-sorti en tool nommé) doit être un choix, pas un accident."""
    from fastmcp import FastMCP
    from oto_mcp.tools import cloro as C

    m = FastMCP("t")
    C.register(m)
    names = {t.name for t in asyncio.run(m._list_tools())}
    assert names == {"cloro_ask", "cloro_google"}


def test_every_engine_is_documented_in_the_tool_description(client):
    """Le dict `_AI_ENGINES` (validation) et la docstring (contrat LLM) sont deux
    sources : un moteur ajouté à l'un sans l'autre serait appelable mais invisible,
    ou annoncé mais refusé."""
    desc = _tool("cloro_ask").description
    for slug, label in _AI_ENGINES.items():
        assert f'"{slug}"' in desc, f"moteur {slug} absent de la docstring"
        assert label in desc, f"libellé de {slug} ({label}) absent de la docstring"


def test_required_params_stay_required_in_the_schema(client):
    """Les params obligatoires ne deviennent pas optionnels à la fusion : la surface
    consolidée n'a AUCUN argument conditionnel à un op (c'est ce qui la rend
    fusionnable), donc le schéma est le seul gardien — engine/prompt et query.
    Les rendre optionnels ferait partir un appel vide chez Cloro (crédits brûlés)."""
    assert set(_tool("cloro_ask").parameters["required"]) == {"engine", "prompt"}
    assert set(_tool("cloro_google").parameters["required"]) == {"query"}


# --- moteurs IA ----------------------------------------------------------------

@pytest.mark.parametrize("engine", list(_AI_ENGINES))
def test_each_engine_routes_to_monitor_with_its_provider_slug(client, engine):
    """Un tool nommé par moteur ne pouvait pas se tromper de moteur ; un paramètre,
    si. C'est LE risque introduit par la fusion — on le verrouille moteur par moteur."""
    _tool("cloro_ask").fn(engine=engine, prompt="que dit-on de la marque X ?")
    client.monitor.assert_called_once()
    assert client.monitor.call_args.kwargs["provider"] == engine
    assert client.monitor.call_args.kwargs["prompt"] == "que dit-on de la marque X ?"
    client.google.assert_not_called()
    client.google_news.assert_not_called()


def test_ask_refuses_an_unknown_engine_and_names_the_valid_ones(client):
    """Jamais de repli sur un moteur par défaut : une veille attribuée au mauvais
    moteur est un faux résultat, pas une erreur visible."""
    with pytest.raises(McpError, match="engine doit être"):
        _tool("cloro_ask").fn(engine="mistral", prompt="x")
    client.monitor.assert_not_called()


def test_ask_error_lists_every_valid_engine(client):
    with pytest.raises(McpError) as e:
        _tool("cloro_ask").fn(engine="ai_mode", prompt="x")
    for slug in _AI_ENGINES:
        assert f"'{slug}'" in str(e.value)


def test_ask_include_markdown_by_default_and_no_search_queries(client):
    """`searchQueries` COÛTE des crédits supplémentaires : il ne part que si on l'a
    demandé — un flag qui fuiterait par défaut se paierait à chaque appel."""
    _tool("cloro_ask").fn(engine="chatgpt", prompt="x")
    assert client.monitor.call_args.kwargs["include"] == {"markdown": True}


def test_ask_passes_markdown_false_and_search_queries(client):
    _tool("cloro_ask").fn(engine="grok", prompt="x", markdown=False,
                          search_queries=True)
    assert client.monitor.call_args.kwargs["include"] == {
        "markdown": False, "searchQueries": True}


def test_ask_passes_country_through(client):
    t = _tool("cloro_ask")
    t.fn(engine="perplexity", prompt="x", country="FR")
    assert client.monitor.call_args.kwargs["country"] == "FR"
    t.fn(engine="perplexity", prompt="x")
    assert client.monitor.call_args.kwargs["country"] is None


# --- Google SERP / News ---------------------------------------------------------

def test_google_defaults_to_serp(client):
    """`op` omis = l'ancien `cloro_google_serp`, defaults d'include compris."""
    _tool("cloro_google").fn(query="meilleur CRM")
    client.google.assert_called_once()
    assert client.google.call_args.kwargs["query"] == "meilleur CRM"
    assert client.google.call_args.kwargs["include"] == {
        "aiOverview": True, "organicResults": True, "peopleAlsoAsk": False}
    client.google_news.assert_not_called()


def test_google_serp_include_flags_map_to_the_api_names(client):
    _tool("cloro_google").fn(query="q", op="serp", country="FR", ai_overview=False,
                             organic=False, people_also_ask=True)
    kw = client.google.call_args.kwargs
    assert kw["country"] == "FR"
    assert kw["include"] == {"aiOverview": False, "organicResults": False,
                             "peopleAlsoAsk": True}


def test_google_news_routes_to_google_news_without_include(client):
    """News ne prend que query+country amont : lui passer un `include` (les flags
    sont SERP-only) enverrait un corps que l'API ne connaît pas."""
    _tool("cloro_google").fn(query="q", op="news", country="FR")
    client.google_news.assert_called_once_with(query="q", country="FR")
    client.google.assert_not_called()


def test_google_refuses_an_unknown_op_and_names_the_valid_ones(client):
    with pytest.raises(McpError, match="op doit être 'serp' ou 'news'"):
        _tool("cloro_google").fn(query="q", op="nope")
    client.google.assert_not_called()
    client.google_news.assert_not_called()


# --- comptabilité plateforme (inchangée par la fusion) --------------------------

def test_platform_usage_is_counted_only_on_a_platform_key(client, monkeypatch):
    """Le décompte de quota est porté par `_run` (partagé par les 3 chemins) : la
    fusion ne doit ni le perdre ni le déclencher sur une clé BYO."""
    rec = MagicMock()
    monkeypatch.setattr("oto_mcp.access.record_platform_usage", rec)

    _tool("cloro_ask").fn(engine="chatgpt", prompt="x")
    rec.assert_not_called()

    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider: ("platform-key", True))
    _tool("cloro_ask").fn(engine="chatgpt", prompt="x")
    _tool("cloro_google").fn(query="q")
    _tool("cloro_google").fn(query="q", op="news")
    assert rec.call_count == 3
    assert {c.args[0] for c in rec.call_args_list} == {"cloro"}
