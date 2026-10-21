"""Les lectures dont un process de prospection a besoin dans une recette (21/10/2026).

Une recette n'appelle qu'un outil déclaré en lecture (`tools/lecture.LECTURE`). Ces
outils-ci ne font que lire chez leur fournisseur ; les déclarer permet de garder leurs
réponses — souvent des centaines de milliers de caractères — sur le serveur plutôt que
dans le contexte d'un agent. `lemlist_database` n'est en lecture que depuis que ses ops
de persona sont passées à `lemlist_database_act`. `lemlist_enrich_result` relève un
enrichissement déjà soumis (le `collect` que rend `waterfall_phone`) : il ne fait que lire.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp.tools.lecture import en_lecture

LECTURES = ["lemlist_database", "theirstack_jobs_search", "theirstack_companies_search",
            "dataforseo_technologies", "web_read", "lemlist_enrich_result"]


@pytest.fixture(scope="module")
def outils():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name: t for t in asyncio.run(m._list_tools())}


@pytest.mark.parametrize("nom", LECTURES)
def test_declare_en_lecture(outils, nom):
    assert en_lecture(outils[nom])


def test_l_outil_qui_agit_sur_la_base_lemlist_ne_lit_pas(outils):
    assert not en_lecture(outils["lemlist_database_act"])
