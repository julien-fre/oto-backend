"""serper_scrape : le délai d'un site scrapé est un REFUS attendu, pas une panne (14/09/2026).

Mesuré le 14/09/2026 : 32 délais de lecture en 40 minutes, chacun journalisé en erreur avec
une trace de trois cadres, remonté à Sentry, et servi à l'agent en « Délai d'attente
dépassé » avec l'indice « réessaie dans un instant ». Or oto-core documente ce délai comme
un échec normal (`SerperClient.scrape_page`), et #662 a mesuré qu'attendre coûte plus cher
que la page. Ces bancs fixent le refus : nommé, qui dit le délai attendu, qui ne pousse pas
au réessai, sans repli, et qui tient sur une ligne de journal.
"""
from __future__ import annotations

import logging
import sys

import pytest
import requests
from mcp.types import INVALID_REQUEST

from oto_mcp import error_taxonomy, refus_journal
from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import mail_obfuscation as M


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
def scrape(monkeypatch):
    """`serper_scrape` monté sur un scraper piloté ; `etat["fetchs"]` compte les lectures
    directes — un repli sur une expiration en ferait partir une."""
    etat = {"scrape": None, "fetchs": []}

    class _Client:
        def __init__(self, *a, **k):
            ...

        @classmethod
        def _refuses_scraping(cls, url):
            return None

        def scrape_page(self, url, include_markdown=True, timeout_s=None):
            if isinstance(etat["scrape"], BaseException):
                raise etat["scrape"]
            return dict(etat["scrape"])

    def _fetch(url, deadline_s=None):
        etat["fetchs"].append(url)
        return {"ok": False, "verdict": "non lu"}

    monkeypatch.setattr("oto.tools.serper.SerperClient", _Client)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key", lambda p: ("k", False))
    monkeypatch.setattr(M, "fetch", _fetch)
    from oto_mcp.tools import serper
    reg = _Reg()
    serper.register(reg)
    return reg.tools["serper_scrape"], etat


def _refus(fn, etat, delai, **kw) -> McpError:
    etat["scrape"] = delai
    with pytest.raises(McpError) as e:
        fn("https://acme.test/contact", **kw)
    return e.value


@pytest.mark.parametrize("delai", [requests.ReadTimeout("Read timed out. (read timeout=15)"),
                                   requests.ConnectTimeout("connect timeout=5")])
def test_un_delai_du_site_est_un_refus_qui_le_dit_sans_repli(scrape, delai):
    fn, etat = scrape
    refus = _refus(fn, etat, delai)
    assert refus.error.code == INVALID_REQUEST
    assert "le site n'a pas répondu en 15 s" in refus.error.message
    assert "ne réessaie pas cette adresse" in refus.error.message
    assert etat["fetchs"] == [], "aucun repli sur une expiration : #662"


@pytest.mark.parametrize("timeout_s, dit", [(5, "en 5 s"), (500, "en 60 s"), (0, "en 1 s")])
def test_le_delai_dit_est_celui_que_le_scraper_a_attendu(scrape, timeout_s, dit):
    """`timeout_s` hors bornes est ramené dedans par le client : le refus dit la valeur
    réellement attendue, pas celle demandée."""
    fn, etat = scrape
    assert dit in _refus(fn, etat, requests.ReadTimeout("x"), timeout_s=timeout_s).error.message


def test_le_refus_de_delai_est_attendu_hors_sentry_et_ne_pousse_pas_au_reessai(scrape):
    fn, etat = scrape
    refus = _refus(fn, etat, requests.ReadTimeout("Read timed out."))
    assert error_taxonomy._is_expected_error(refus), "hors Sentry"
    info = error_taxonomy.classify(refus)
    assert info.retryable is False
    assert "réessaie dans un instant" not in f"{info.message} {info.hint or ''}"


def test_le_refus_de_delai_tient_sur_une_ligne_de_journal_avec_sa_cause(scrape):
    """Le chemin du journal : fastmcp écrit « Error calling tool » avec l'exception ; le
    filtre de refus la rend en une ligne INFO qui nomme le délai d'origine."""
    fn, etat = scrape
    etat["scrape"] = requests.ReadTimeout("Read timed out.")
    try:
        fn("https://acme.test/contact")
    except McpError:
        record = logging.LogRecord("fastmcp.server.server", logging.ERROR, __file__, 1,
                                   "Error calling tool 'serper_scrape'", None, sys.exc_info())
    assert refus_journal.RefusSurUneLigne().filter(record)
    assert record.exc_info is None and record.levelno == logging.INFO
    assert "— cause : ReadTimeout('Read timed out.')" in record.getMessage()
