"""Chaque appel serper trace, comme `quantity`, les crédits que Serper a DÉDUITS.

Le consommateur du métrage lit `tool_calls.quantity` ; l'unité écrite ici est le crédit
SERPER, tel que l'amont le déclare — aucun taux de conversion n'est codé dans le
backend, un taux est une décision commerciale.
Avant ce lot, `_run` comptait UN usage plateforme par appel d'outil et ne traçait
aucune quantité : un recensement Maps (jusqu'à grid² × max_pages pages à 100 résultats)
coûtait autant qu'une recherche. Ce fichier fige :
- la quantité vient de la RÉPONSE (`credits`, ou `credits_used` pour les méthodes qui
  paginent), jamais d'une règle codée ici ;
- repli à 1 quand l'amont ne dit rien, et à `pages_fetched` sur un oto-core antérieur ;
- le métrage est INCONDITIONNEL, le quota interne d'oto ne compte que notre clé.
"""
import asyncio
from unittest.mock import MagicMock

import pytest


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import serper

    m = FastMCP("t")
    serper.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def serper(monkeypatch):
    inst = MagicMock()
    mode = {"platform": True}
    traced, used = [], []
    monkeypatch.setattr("oto.tools.serper.SerperClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda p, account=None: ("k", mode["platform"]))
    monkeypatch.setattr("oto_mcp.tools.mail_obfuscation.fetch",
                        lambda url, deadline_s=None: {
                            "ok": True, "status": 200, "verdict": "lu",
                            "html": "<p>rien de caché</p>", "final_url": url})
    monkeypatch.setattr("oto_mcp.session_org.note_call_trace",
                        lambda **kw: traced.append(kw))
    from _releve import capter_units
    capter_units(monkeypatch, "serper", lambda provider, calls: used.append((provider, calls)))
    return inst, traced, used, mode


def test_a_search_traces_the_credits_serper_reported(serper):
    inst, traced, used, _ = serper
    inst.search.return_value = {"organic": [], "credits": 2}
    _tool("serper_search")(query="laverie lyon", num=100)
    assert traced == [{"quantity": 2}]
    assert used == [("serper", 2)]


def test_a_response_without_credits_counts_as_one(serper):
    inst, traced, used, _ = serper
    inst.search.return_value = {"organic": []}
    _tool("serper_search")(query="laverie lyon")
    assert traced == [{"quantity": 1}] and used == [("serper", 1)]


def test_maps_sample_traces_its_credits(serper):
    inst, traced, _, _ = serper
    inst.search_maps.return_value = {"places": [], "credits": 1}
    _tool("serper_maps_sample")(query="laverie", ll="@45.76,4.83,14z")
    assert traced == [{"quantity": 1}]


def test_a_census_traces_the_SUM_of_its_pages_not_one(serper):
    inst, traced, used, _ = serper
    inst.census_maps.return_value = {"query": "laverie", "count": 40, "places": [],
                                     "anchors_used": 9, "pages_fetched": 12,
                                     "credits_used": 24}
    _tool("serper_maps_census")(query="laverie", center="45.76,4.83")
    assert traced == [{"quantity": 24}] and used == [("serper", 24)]


def test_a_census_on_an_older_oto_core_falls_back_to_pages_fetched(serper):
    """Version-skew : tant que le tag oto-core qui rend `credits_used` n'est pas épinglé,
    le backend ne doit ni planter ni retomber à 1 — une page = au moins un crédit."""
    inst, traced, _, _ = serper
    inst.census_maps.return_value = {"query": "laverie", "count": 40, "places": [],
                                     "anchors_used": 9, "pages_fetched": 12}
    _tool("serper_maps_census")(query="laverie", center="45.76,4.83")
    assert traced == [{"quantity": 12}]


def test_all_reviews_trace_the_sum_of_their_pages(serper):
    inst, traced, _, _ = serper
    inst.reviews_all.return_value = {"count": 200, "reviews": [], "pages_fetched": 20,
                                     "credits_used": 20, "truncated": True}
    _tool("serper_reviews")(cid="123")
    assert traced == [{"quantity": 20}]


def test_one_page_of_reviews_traces_its_credits(serper):
    inst, traced, _, _ = serper
    inst.search_reviews.return_value = {"reviews": [], "credits": 1}
    _tool("serper_reviews")(op="page", cid="123")
    assert traced == [{"quantity": 1}]


def test_lens_traces_its_credits(serper):
    inst, traced, _, _ = serper
    inst.search_lens.return_value = {"organic": [], "credits": 3}
    _tool("serper_lens")(url="https://example.com/photo.jpg")
    assert traced == [{"quantity": 3}]


def test_a_scrape_traces_its_credits(serper):
    inst, traced, used, _ = serper
    inst.scrape_page.return_value = {"markdown": "Contact : écrire à l'équipe.",
                                     "text": "Contact", "credits": 2, "metadata": {}}
    _tool("serper_scrape")(url="https://example.com/contact")
    assert traced == [{"quantity": 2}] and used == [("serper", 2)]


def test_on_the_customers_own_key_it_is_metered_but_oto_quota_is_untouched(serper):
    """Le métrage est inconditionnel (la facturation lit `key_mode` à part) ; le quota
    interne d'oto ne compte que NOTRE clé."""
    inst, traced, used, mode = serper
    mode["platform"] = False
    inst.search.return_value = {"organic": [], "credits": 2}
    _tool("serper_search")(query="laverie lyon")
    assert traced == [{"quantity": 2}]
    assert used == []


def test_a_rejected_input_is_neither_metered_nor_counted(serper):
    """Un 400 Serper lève avant tout comptage : rien n'a été servi, rien ne se facture."""
    from oto_mcp.mcp_errors import McpError

    inst, traced, used, _ = serper
    inst.search.side_effect = RuntimeError("Serper search 400: Missing query")
    with pytest.raises(McpError):
        _tool("serper_search")(query="x")
    assert traced == [] and used == []


@pytest.mark.parametrize("raw,expected", [
    ({"credits": 0}, 0), ({"credits": 10}, 10), ({"credits": 2.0}, 2),
    ({"credits": None}, 1), ({"credits": "2"}, 1), ({"credits": True}, 1),
    ({"credits": -3}, 1), ("pas un dict", 1),
])
def test_credits_consumed_reads_only_a_sane_count(raw, expected):
    from oto_mcp.tools.serper import credits_consumed

    assert credits_consumed("search", raw) == expected


@pytest.mark.parametrize("tool", ["serper_maps_census", "serper_reviews"])
def test_la_description_servie_annonce_le_champ_qui_dit_le_cout(tool):
    """Les deux tools qui PAGINENT rendent `credits_used` (oto-core ≥ 1.121.0, le pin),
    et leurs descriptions listaient une forme de sortie qui ne l'avait plus.

    Un agent ne connaît d'un outil que ce que sa description lui dit : un champ omis
    n'existe pas pour lui — et c'est précisément le champ qui dit ce que l'appel a
    coûté, sur les deux seuls tools serper capables de coûter cher."""
    import asyncio as _asyncio

    from fastmcp import FastMCP
    from oto_mcp.tools import serper as S

    m = FastMCP("t")
    S.register(m)
    doc = _asyncio.run(m.get_tool(tool)).description or ""
    assert "credits_used" in doc, (
        f"{tool} annonce une forme de sortie sans le champ de coût qu'il rend")
