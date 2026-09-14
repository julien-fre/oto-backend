"""Un refus attendu d'outil tient sur UNE ligne de journal (refus_journal).

Mesuré le 14/09/2026 : sur une heure de production, 258 « Error calling tool » dont 230
pour `serper_scrape`, chacun suivi d'un cadre de trace complet, alors que c'étaient des
refus levés exprès. Ces bancs fixent la frontière : un refus explicite perd sa trace et
garde son message ; une vraie panne garde tout. Et ils l'éprouvent sur un serveur
fastmcp réellement monté, là où le journal s'écrit.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, ErrorData

from oto_mcp import refus_journal
from oto_mcp.mcp_errors import McpError


class _Collecte(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture()
def journal():
    """Le journal de fastmcp, filtre posé, avec un collecteur et rien d'autre."""
    lg = logging.getLogger(refus_journal.JOURNAL_FASTMCP)
    filtres, niveau = list(lg.filters), lg.level
    collecte = _Collecte()
    lg.addHandler(collecte)
    lg.setLevel(logging.DEBUG)
    refus_journal.installer()
    try:
        yield lg, collecte
    finally:
        lg.removeHandler(collecte)
        lg.filters[:] = filtres
        lg.setLevel(niveau)


def _journaliser(lg, exc):
    try:
        raise exc
    except Exception:
        lg.exception("Error calling tool 'serper_scrape'")


def test_un_refus_explicite_perd_sa_trace_et_garde_son_message(journal):
    lg, collecte = journal
    refus = McpError(ErrorData(code=INVALID_REQUEST,
                               message="Scrape impossible : cette page n'existe pas"))

    _journaliser(lg, refus)

    (r,) = collecte.records
    assert r.exc_info is None and r.levelno == logging.INFO
    assert "Scrape impossible : cette page n'existe pas" in r.getMessage()


def test_une_panne_habillee_en_refus_garde_son_nom_sur_la_ligne(journal):
    """~180 sites lèvent un refus dans un `except` : l'origine vit en `__context__`."""
    lg, collecte = journal
    try:
        try:
            {}["siren"]
        except KeyError:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="SIREN introuvable"))
    except McpError:
        lg.exception("Error calling tool 'fr_get'")

    (r,) = collecte.records
    assert r.exc_info is None and r.levelno == logging.INFO
    assert "refus : SIREN introuvable — cause : KeyError('siren')" in r.getMessage()


def test_un_refus_sans_origine_ne_nomme_aucune_cause(journal):
    lg, collecte = journal

    _journaliser(lg, McpError(ErrorData(code=INVALID_REQUEST, message="page absente")))

    (r,) = collecte.records
    assert "cause" not in r.getMessage()


@pytest.mark.parametrize("panne", [
    RuntimeError("colonne manquante"),
    McpError(ErrorData(code=INTERNAL_ERROR, message="erreur interne")),
])
def test_une_vraie_panne_garde_sa_trace_et_son_niveau(journal, panne):
    lg, collecte = journal

    _journaliser(lg, panne)

    (r,) = collecte.records
    assert r.exc_info is not None and r.levelno == logging.ERROR


def test_poser_le_filtre_deux_fois_n_en_pose_qu_un(journal):
    lg, _ = journal
    refus_journal.installer()
    assert sum(isinstance(f, refus_journal.RefusSurUneLigne) for f in lg.filters) == 1


def test_sur_un_serveur_monte_le_refus_s_ecrit_sur_une_ligne_et_arrive_au_client(journal):
    """Le chemin réel : fastmcp journalise l'échec de l'outil, le client reçoit le refus."""
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError

    _, collecte = journal
    serveur = FastMCP("t-refus")

    @serveur.tool()
    def lire(url: str) -> dict:
        raise McpError(ErrorData(code=INVALID_REQUEST,
                                 message=f"Scrape impossible pour {url} : 404"))

    async def appel():
        async with Client(serveur) as client:
            with pytest.raises(ToolError) as e:
                await client.call_tool("lire", {"url": "https://acme.test/x"})
            return str(e.value)

    recu = asyncio.run(appel())

    assert "Scrape impossible pour https://acme.test/x : 404" in recu
    echecs = [r for r in collecte.records if "Error calling tool" in r.getMessage()]
    assert echecs, "fastmcp doit journaliser l'échec de l'outil"
    assert all(r.exc_info is None and r.levelno == logging.INFO for r in echecs)
