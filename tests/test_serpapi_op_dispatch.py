"""Dispatch `engine=` / `op=` de la surface `serpapi_*` (ADR 0047 §Amendement,
appliqué au connecteur serpapi le 2026-08-11 : 13 tools → 6).

Ce module n'avait AUCUN test de surface : `_run` fait un `getattr(client, method)`
et `serpapi_search` construit le dict `params` envoyé à SerpApi. Deux façons de se
tromper en silence, qu'aucun boot ne rattraperait : appeler la mauvaise méthode du
client, et surtout **envoyer un filtre sous un nom que le moteur ignore** (SerpApi
n'erre pas sur un paramètre inconnu — il rend un résultat non filtré, donc faux,
sans erreur). D'où, pour chaque moteur : le dict `params` EXACT attendu ; pour
chaque op : la méthode client appelée ; et le refus explicite de ce qui n'est pas
mappé (jamais un nom deviné, jamais un fallback muet).
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import serpapi as S

    m = FastMCP("t")
    S.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    """Faux SerpAPIClient + clé résolue.

    Le patch vise la CLASSE dans oto-core : `register()` l'importe à l'appel, donc
    la fixture (jouée avant le corps du test, `_tool` inclus) est bien en amont de
    la capture par la closure `_client`.
    """
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.serpapi.client.SerpAPIClient",
                        lambda api_key=None: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda provider: ("k", False))
    return inst


# --- surface : l'inventaire est le contrat ------------------------------------

def test_the_connector_exposes_exactly_six_tools(client):
    from fastmcp import FastMCP
    from oto_mcp.tools import serpapi as S

    m = FastMCP("t")
    S.register(m)
    assert sorted(t.name for t in asyncio.run(m.list_tools())) == [
        "serpapi_google_finance", "serpapi_google_flights", "serpapi_google_hotels",
        "serpapi_google_trends", "serpapi_jobs", "serpapi_search",
    ]


# --- serpapi_search : un moteur par appel, params traduits --------------------

@pytest.mark.parametrize("engine,kwargs,expected", [
    # bing — q / cc / setlang / count, count par défaut à 10 (ex-serpapi_bing_search)
    ("bing", {"query": "pizza"}, {"q": "pizza", "count": 10}),
    ("bing", {"query": "pizza", "country": "fr", "language": "fr", "count": 5},
     {"q": "pizza", "cc": "fr", "setlang": "fr", "count": 5}),
    # youtube — search_query / gl / hl (ex-serpapi_youtube_search)
    ("youtube", {"query": "oto"}, {"search_query": "oto"}),
    ("youtube", {"query": "oto", "language": "fr", "country": "fr"},
     {"search_query": "oto", "hl": "fr", "gl": "fr"}),
    # walmart — query / page, page par défaut à 1 (ex-serpapi_walmart_search)
    ("walmart", {"query": "tv"}, {"query": "tv", "page": 1}),
    ("walmart", {"query": "tv", "page": 3}, {"query": "tv", "page": 3}),
    # amazon — k / amazon_domain / page (ex-serpapi_amazon_search)
    ("amazon", {"query": "tv"}, {"k": "tv", "amazon_domain": "amazon.com", "page": 1}),
    ("amazon", {"query": "tv", "domain": "amazon.fr", "page": 2},
     {"k": "tv", "amazon_domain": "amazon.fr", "page": 2}),
    # ebay — _nkw / ebay_domain / _pgn (ex-serpapi_ebay_search)
    ("ebay", {"query": "tv"}, {"_nkw": "tv", "ebay_domain": "ebay.com", "_pgn": 1}),
    ("ebay", {"query": "tv", "domain": "ebay.fr", "page": 4},
     {"_nkw": "tv", "ebay_domain": "ebay.fr", "_pgn": 4}),
    # google_events — convention Google, aucun défaut (ex-serpapi_google_events)
    ("google_events", {"query": "tech conferences in Paris",
                       "location": "Paris, France", "language": "fr", "country": "fr"},
     {"q": "tech conferences in Paris", "location": "Paris, France",
      "hl": "fr", "gl": "fr"}),
    ("google_events", {"query": "concerts"}, {"q": "concerts"}),
    # moteur sans raccourci = convention Google/SerpApi
    ("duckduckgo", {"query": "oto", "country": "fr", "language": "fr"},
     {"q": "oto", "gl": "fr", "hl": "fr"}),
])
def test_shared_args_are_translated_to_each_engine_native_names(
        client, engine, kwargs, expected):
    """Le nom natif diffère par moteur (`q` vs `search_query` vs `k` vs `_nkw`) :
    c'est TOUTE la valeur de la fusion, et donc ce qu'il faut verrouiller."""
    _tool("serpapi_search")(engine=engine, **kwargs)
    assert client.search.call_args.kwargs["engine"] == engine
    assert client.search.call_args.kwargs["params"] == expected


def test_generic_call_without_shared_args_stays_a_raw_passthrough(client):
    """L'échappatoire d'origine : engine + params bruts, rien d'autre."""
    _tool("serpapi_search")(engine="google_play", params={"q": "oto", "gl": "fr"})
    assert client.search.call_args.kwargs == {
        "engine": "google_play", "params": {"q": "oto", "gl": "fr"},
        "max_results": None, "results_key": None}


def test_explicit_params_win_over_the_derived_ones(client):
    """`params` est fusionné EN DERNIER : c'est la sortie de secours quand la
    traduction ne convient pas (moteur exotique, param renommé en amont)."""
    _tool("serpapi_search")(engine="bing", query="pizza", params={"q": "sushi"})
    assert client.search.call_args.kwargs["params"] == {"q": "sushi", "count": 10}


def test_pagination_args_are_forwarded(client):
    _tool("serpapi_search")(engine="bing", query="pizza", max_results=50,
                            results_key="organic_results")
    assert client.search.call_args.kwargs["max_results"] == 50
    assert client.search.call_args.kwargs["results_key"] == "organic_results"


@pytest.mark.parametrize("arg,value", [("page", 2), ("count", 5), ("domain", "x.com")])
def test_unmapped_shared_arg_is_refused_instead_of_guessed(client, arg, value):
    """Un filtre envoyé sous un nom que le moteur ignore n'échoue PAS côté SerpApi :
    il rend un résultat non filtré. Refus explicite qui nomme l'échappatoire."""
    with pytest.raises(McpError, match=r"params"):
        _tool("serpapi_search")(engine="google", query="oto", **{arg: value})
    client.search.assert_not_called()


@pytest.mark.parametrize("engine,arg", [("bing", "page"), ("walmart", "count"),
                                        ("youtube", "domain")])
def test_a_shortcut_engine_still_refuses_what_it_does_not_map(client, engine, arg):
    with pytest.raises(McpError, match=r"params"):
        _tool("serpapi_search")(engine=engine, query="oto", **{arg: 1})


# --- serpapi_jobs : l'objet « offre d'emploi », verbe en op ------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("search", {"query": "data engineer Paris"}, "search_jobs"),
    ("search", {"company": "Otomata"}, "search_jobs"),
    ("details", {"job_id": "j1"}, "get_job_details"),
])
def test_job_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("serpapi_jobs")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_job_search_forwards_every_argument(client):
    """`search_jobs` est une méthode TYPÉE du client (elle construit le `q`,
    pagine sur `jobs_results`) — pas le `search` générique."""
    _tool("serpapi_jobs")(op="search", query="data engineer", company="Otomata",
                          location="Paris, France", country="fr", language="fr",
                          max_results=20, no_cache=True)
    assert client.search_jobs.call_args.kwargs == {
        "query": "data engineer", "company": "Otomata", "location": "Paris, France",
        "country": "fr", "language": "fr", "max_results": 20, "no_cache": True}
    client.search.assert_not_called()


def test_job_search_defaults_are_preserved(client):
    _tool("serpapi_jobs")(op="search", query="x")
    kw = client.search_jobs.call_args.kwargs
    assert (kw["language"], kw["max_results"], kw["no_cache"]) == ("en", 50, False)


def test_job_search_hands_back_the_freshness_block_untouched(client):
    """Le client oto-core garantit qu'un `jobs_results` vide a été CONSTATÉ frais
    (signal #456) et le dit dans `oto_freshness`. Cette garantie ne vaut que si
    l'agent la reçoit : une projection posée ici plus tard la ferait disparaître
    en silence, et un zéro redeviendrait ininterprétable."""
    client.search_jobs.return_value = {
        "jobs_results": [], "oto_freshness": {"age_seconds": 4, "refetched": True}}
    r = _tool("serpapi_jobs")(op="search", query="Editis")
    assert r["oto_freshness"] == {"age_seconds": 4, "refetched": True}


def test_job_details_passes_the_job_id(client):
    _tool("serpapi_jobs")(op="details", job_id="abc")
    assert client.get_job_details.call_args.kwargs == {"job_id": "abc"}


def test_job_search_refuses_without_query_nor_company(client):
    """Sans l'un des deux le client lève un ValueError opaque : on refuse ici,
    en nommant les deux voies."""
    with pytest.raises(McpError, match="query ou company"):
        _tool("serpapi_jobs")(op="search")
    client.search_jobs.assert_not_called()


def test_job_details_refuses_without_job_id(client):
    with pytest.raises(McpError, match="job_id"):
        _tool("serpapi_jobs")(op="details")
    client.get_job_details.assert_not_called()


def test_unknown_op_is_refused_with_the_allowed_list(client):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être"):
        _tool("serpapi_jobs")(op="nope")
    client.search_jobs.assert_not_called()
    client.get_job_details.assert_not_called()


# --- verticaux restés seuls : leur contrat natif est figé ---------------------

def test_trends_maps_geo_and_date(client):
    _tool("serpapi_google_trends")(query="oto", data_type="GEO_MAP", country="FR",
                                   date="today 12-m")
    assert client.search.call_args.kwargs == {
        "engine": "google_trends",
        "params": {"q": "oto", "data_type": "GEO_MAP", "geo": "FR",
                   "date": "today 12-m"}}


def test_trends_defaults_to_timeseries(client):
    _tool("serpapi_google_trends")(query="oto")
    assert client.search.call_args.kwargs["params"] == {
        "q": "oto", "data_type": "TIMESERIES"}


def test_finance_omits_window_when_absent(client):
    _tool("serpapi_google_finance")(query="GOOGL:NASDAQ")
    assert client.search.call_args.kwargs == {
        "engine": "google_finance", "params": {"q": "GOOGL:NASDAQ"}}


def test_flights_one_way_sets_type_2(client):
    """Sans `return_date`, SerpApi exige `type=2` — un aller simple demandé sans
    ce marqueur revient en aller-retour."""
    _tool("serpapi_google_flights")(departure_id="CDG", arrival_id="JFK",
                                    outbound_date="2026-09-01")
    p = client.search.call_args.kwargs["params"]
    assert p["type"] == 2 and "return_date" not in p


def test_flights_round_trip_has_no_type_marker(client):
    _tool("serpapi_google_flights")(departure_id="CDG", arrival_id="JFK",
                                    outbound_date="2026-09-01",
                                    return_date="2026-09-08", country="fr")
    p = client.search.call_args.kwargs["params"]
    assert p["return_date"] == "2026-09-08" and "type" not in p and p["gl"] == "fr"


def test_hotels_carries_the_stay(client):
    _tool("serpapi_google_hotels")(query="Paris hotels", check_in_date="2026-09-01",
                                   check_out_date="2026-09-03", adults=3,
                                   currency="EUR")
    assert client.search.call_args.kwargs == {
        "engine": "google_hotels",
        "params": {"q": "Paris hotels", "check_in_date": "2026-09-01",
                   "check_out_date": "2026-09-03", "adults": 3, "currency": "EUR"}}


# --- quota plateforme --------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs", [
    ("serpapi_search", {"engine": "bing", "query": "pizza"}),
    ("serpapi_jobs", {"op": "search", "query": "x"}),
    ("serpapi_jobs", {"op": "details", "job_id": "j1"}),
    ("serpapi_google_trends", {"query": "oto"}),
])
def test_platform_key_usage_is_counted_on_every_path(monkeypatch, client, tool, kwargs):
    """La clé plateforme est quotaée : un chemin qui oublie de compter offre un
    appel gratuit, et le quota ne protège plus rien."""
    seen: list[str] = []
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda provider: ("k", True))
    monkeypatch.setattr("oto_mcp.access.record_platform_usage", seen.append)
    _tool(tool)(**kwargs)
    assert seen == ["serpapi"]


def test_user_key_is_not_counted_against_the_platform_quota(monkeypatch, client):
    seen: list[str] = []
    monkeypatch.setattr("oto_mcp.access.record_platform_usage", seen.append)
    _tool("serpapi_search")(engine="bing", query="pizza")
    assert seen == []
