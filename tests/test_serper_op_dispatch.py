"""Dispatch de la surface serper consolidée (15 tools → 6, ADR 0047 §Amendement).

Ce que ce fichier verrouille, et qui n'était couvert par RIEN avant : serper n'avait
aucun test. Or son dispatch est dynamique (`getattr(client, method)`) — donc même le
garde-fou anti-version-skew (`test_tools_client_methods_exist`) ne le voit pas, et le
module est déclaré à découvert dans les conventions du repo. Une verticale mal câblée
appellerait silencieusement la mauvaise méthode Serper : la réponse aurait la bonne
forme, avec les mauvais résultats.

Deux invariants de CORRECTION y sont figés en plus du routage, parce qu'ils sont la
raison d'être de la consolidation, pas un effet de bord :
- `serper_reviews` répond TOUT par défaut (une page seule sous-représente un lieu) ;
- les params réservés à une verticale ne fuient pas vers les autres (Serper les
  rejetterait en 400, ou pire les ignorerait).
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import serper

    m = FastMCP("t")
    serper.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    """Client Serper mocké + clé résolue. `_run` fait `getattr(client, method)(**kw)`,
    donc le MagicMock enregistre la méthode appelée ET ses arguments."""
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.serper.SerperClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p, account=None: ("k", False))
    return inst


# --- les 9 verticales de serper_search ----------------------------------------

@pytest.mark.parametrize("kind,method", [
    ("web", "search"),
    ("news", "search_news"),
    ("images", "search_images"),
    ("videos", "search_videos"),
    ("places", "search_places"),
    ("shopping", "search_shopping"),
    ("scholar", "search_scholar"),
    ("patents", "search_patents"),
    ("autocomplete", "autocomplete"),
])
def test_each_kind_calls_its_own_serper_method(client, kind, method):
    getattr(client, method).return_value = {}
    _tool("serper_search")(query="x", kind=kind)
    getattr(client, method).assert_called_once()


def test_web_is_the_default_kind(client):
    client.search.return_value = {}
    _tool("serper_search")(query="x")
    client.search.assert_called_once()


def test_unknown_kind_is_refused_and_names_the_valid_ones(client):
    with pytest.raises(McpError, match="kind"):
        _tool("serper_search")(query="x", kind="nope")
    assert not client.method_calls


@pytest.mark.parametrize("kind,forbidden", [
    ("scholar", ("tbs", "location", "site_filter", "autocorrect")),
    ("patents", ("tbs", "location", "site_filter", "autocorrect")),
    ("images", ("location", "site_filter", "autocorrect")),
    ("places", ("tbs", "site_filter", "autocorrect")),
    ("news", ("location", "site_filter", "autocorrect")),
])
def test_vertical_specific_params_do_not_leak(client, kind, forbidden):
    """Le socle commun est envoyé à toutes les verticales ; les optionnels d'une
    verticale ne doivent PAS partir chez les autres — Serper répondrait 400, ou
    (pire) les ignorerait en silence."""
    _tool("serper_search")(query="x", kind=kind, tbs="qdr:d", location="Paris",
                           site_filter="a.com", autocorrect=True)
    sent = set(client.method_calls[0][2])
    assert sent & set(forbidden) == set(), f"{kind} a reçu {sent & set(forbidden)}"
    assert {"query", "num", "page", "country", "language"} <= sent


def test_web_forwards_all_its_optionals(client):
    client.search.return_value = {}
    _tool("serper_search")(query="x", tbs="qdr:w", location="Lyon",
                           site_filter="linkedin.com/in", autocorrect=False)
    kw = client.search.call_args.kwargs
    assert kw["tbs"] == "qdr:w" and kw["location"] == "Lyon"
    assert kw["site_filter"] == "linkedin.com/in" and kw["autocorrect"] is False


def test_autocomplete_ignores_pagination(client):
    """Il ne pagine pas : lui passer num/page serait un 400 amont."""
    _tool("serper_search")(query="x", kind="autocomplete", num=50, page=3)
    kw = client.autocomplete.call_args.kwargs
    assert "num" not in kw and "page" not in kw
    assert kw["query"] == "x"


# --- avis : le défaut est le chemin COMPLET -----------------------------------

def test_reviews_defaults_to_all(client):
    """La raison d'être de la fusion : une page seule (~10 avis sur des milliers)
    sous-représente silencieusement. Le chemin paresseux doit être le chemin juste."""
    _tool("serper_reviews")(cid="c1")
    client.reviews_all.assert_called_once()
    client.search_reviews.assert_not_called()


def test_reviews_page_is_explicit(client):
    _tool("serper_reviews")(op="page", cid="c1", next_page_token="tok")
    client.search_reviews.assert_called_once()
    assert client.search_reviews.call_args.kwargs["next_page_token"] == "tok"
    client.reviews_all.assert_not_called()


def test_reviews_max_reviews_only_on_all(client):
    _tool("serper_reviews")(cid="c1", max_reviews=50)
    assert client.reviews_all.call_args.kwargs["max_reviews"] == 50
    _tool("serper_reviews")(op="page", cid="c1")
    assert "max_reviews" not in client.search_reviews.call_args.kwargs


def test_reviews_unknown_op_is_refused(client):
    with pytest.raises(McpError, match="op"):
        _tool("serper_reviews")(op="nope", cid="c1")


# --- les tools laissés seuls ---------------------------------------------------

def test_maps_sample_and_census_stay_distinct(client):
    """Params disjoints (`ll`/`place_id`/`cid` contre `center`/`grid`/`zoom`/…) : les
    fusionner ferait un oneOf qui pèse ce que pesaient les deux. Le NOM porte
    l'avertissement à la place du défaut."""
    _tool("serper_maps_sample")(query="laverie", ll="@45.7,4.8,12z")
    client.search_maps.assert_called_once()
    _tool("serper_maps_census")(query="laverie", center="45.7,4.8")
    client.census_maps.assert_called_once()


def test_scrape_rejects_an_unknown_format(client):
    with pytest.raises(McpError, match="format"):
        _tool("serper_scrape")(url="https://x.test", format="pdf")
    client.scrape_page.assert_not_called()


def test_scrape_drops_the_duplicated_text_rendition(client):
    """Serper rend `text` ET `markdown` — 97 % de mots communs pour 37 % du payload."""
    client.scrape_page.return_value = {"markdown": "# T", "text": "T", "metadata": {}}
    out = _tool("serper_scrape")(url="https://x.test")
    assert out["markdown"] == "# T" and "text" not in out


def test_lens_takes_an_image_url(client):
    _tool("serper_lens")(url="https://x.test/i.png")
    assert client.search_lens.call_args.kwargs["url"] == "https://x.test/i.png"
