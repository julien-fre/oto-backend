"""La déclaration « en lecture » (`tools/lecture.LECTURE`) ne ment pas.

Une recette n'appelle QUE des outils déclarés en lecture : une déclaration posée par
erreur sur un outil qui écrit chez le tiers ouvrirait à une recette l'envoi d'un mail ou
la création d'un contact, sans modèle pour s'arrêter. Cette garde lit le catalogue
RÉELLEMENT monté et refuse toute déclaration portée par un outil qui a la forme d'un
écrivain : un verbe d'écriture dans son nom, une op d'écriture dans son `op`, ou un
connecteur à modèle.
"""
from __future__ import annotations

import re

import pytest

from _mcp_app import static_mcp
from oto_mcp.recipes import contrat
from oto_mcp.tool_visibility import namespace_of
from oto_mcp.tools.lecture import en_lecture

#: Les verbes d'un outil qui change quelque chose chez son fournisseur.
_ECRIT = re.compile(r"(^|_)(send|post|push|compose|create|update|delete|write|set|"
                    r"add|remove|upload|fire|reply|invite|publish|archive|move|"
                    r"cancel|pause|resume|connect|disconnect|reveal)(_|$)")


async def _outils():
    return await static_mcp().list_tools(run_middleware=False)


def _ops(tool) -> list:
    props = (getattr(tool, "parameters", None) or {}).get("properties") or {}
    op = props.get("op") or {}
    enum = op.get("enum") or []
    for alt in op.get("anyOf") or []:
        enum += alt.get("enum") or []
    return [str(o) for o in enum]


@pytest.mark.asyncio
async def test_un_outil_declare_en_lecture_n_a_pas_la_forme_d_un_ecrivain():
    fautifs = []
    for t in await _outils():
        if not en_lecture(t):
            continue
        if namespace_of(t.name) in contrat.NAMESPACES_A_MODELE:
            fautifs.append(f"{t.name} : connecteur à modèle")
        if _ECRIT.search(t.name):
            fautifs.append(f"{t.name} : verbe d'écriture dans le nom")
        fautifs += [f"{t.name} : op d'écriture `{o}`" for o in _ops(t) if _ECRIT.search(o)]
    assert not fautifs, fautifs


@pytest.mark.asyncio
async def test_les_outils_des_recettes_documentees_sont_declares_en_lecture():
    """L'exemple de `docs/recettes.md` doit pouvoir tourner."""
    servis = {t.name: t for t in await _outils()}
    for nom in ("linkedin_aiark_search", "apollo_search_people", "serper_search",
                "fr_search", "fr_stock_search"):
        assert en_lecture(servis[nom]), nom


@pytest.mark.asyncio
async def test_aucun_outil_de_plateforme_ni_ecrivain_connu_n_est_declare():
    servis = {t.name: t for t in await _outils()}
    for nom in ("email_send", "slack_post_message", "gmail_compose", "http_post",
                "lemlist_push_rows", "hubspot_push_rows", "routine_fire", "oto_call",
                "data_write"):
        if nom in servis:
            assert not en_lecture(servis[nom]), nom
