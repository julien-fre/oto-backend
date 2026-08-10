"""Le domaine LinkedIn a DEUX fournisseurs non substituables, et c'est le nommage
qui doit le dire (ADR 0010 §Amendement 2026-08-10, oto-backend#279) :

- `linkedin_unipile_*` — la **session opérée** (chats, invitations, feed, publication,
  recherche connectée) : rate-limitée par LinkedIn sur le compte de l'utilisateur ;
- `linkedin_aiark_*` — de la **donnée achetée au crédit** (search, export, mobile,
  reverse) : aucun compte connecté, facturée par enregistrement rendu.

Aucun des deux ne prend le nom NU `linkedin_*` : `linkedin_unipile_chat` n'a pas
d'équivalent AI Ark, `linkedin_aiark_person(op="mobile")` pas d'équivalent Unipile —
router silencieusement entre eux produirait des trous que l'agent lirait comme des
absences de résultat.

Ce fichier remplace les tests de l'ex-connecteur `linkedin` (#231, AI Ark en
app-credits), **déposé** le 2026-08-10 : même vendeur, même client `AiArkClient`,
mêmes 5 fonctions qu'`aiark` — il n'en différait que par le mode d'auth, ce qui est
une distinction d'INSTANCE (ADR 0038/0044 §F), pas de connecteur.
"""
import asyncio

import pytest

from oto_mcp import providers
from oto_mcp.tool_visibility import namespace_of


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name for t in asyncio.run(m._list_tools())}


# --- l'ex-connecteur `linkedin` est déposé ------------------------------------

def test_linkedin_connector_is_gone():
    """Il ne différait d'`aiark` que par son mode d'auth, et coûtait de poser DEUX
    FOIS la même clé pour un seul pool de crédits vendeur (ADR 0024 : chaque
    connecteur résout SON nom)."""
    assert "linkedin" not in providers.REGISTRY
    assert "linkedin" not in providers.KEY_PROVIDERS
    assert providers.connector_for_namespace("linkedin") is None


def test_no_tool_keeps_the_bare_linkedin_prefix(all_tools):
    """Le nom nu ne doit désigner NI l'un ni l'autre fournisseur — sinon il laisse
    croire à une surface LinkedIn canonique qui n'existe pas."""
    bare = {t for t in all_tools
            if t.startswith("linkedin_")
            and not t.startswith(("linkedin_unipile_", "linkedin_aiark_"))}
    assert bare == set()


def test_aiark_absorbed_the_platform_mode():
    """Le packaging « offert par oto » survit sans connecteur-doublon : c'est le
    grant plateforme sur `aiark`, mécanisme standard."""
    c = providers.REGISTRY["aiark"]
    assert "platform" in c.auth_modes
    assert providers.is_byo_user("aiark")      # et le BYO reste ouvert


# --- les deux namespaces cohabitent, chacun sous SON connecteur ----------------

@pytest.mark.parametrize("prefix,connector", [
    ("linkedin_unipile_", "unipile"),
    ("linkedin_aiark_", "aiark"),
])
def test_namespace_resolves_to_its_own_connector(all_tools, prefix, connector):
    """Le gate d'un tool suit son NAMESPACE. Sans la résolution au plus long préfixe
    DÉCLARÉ au registre, le 1er token (`linkedin`) mettrait les deux familles sous
    un même connecteur — donc le mauvais credential, la mauvaise activation et la
    mauvaise sélection pour l'une des deux."""
    tools = {t for t in all_tools if t.startswith(prefix)}
    assert tools, f"aucun tool {prefix}* monté"
    for t in tools:
        assert namespace_of(t) == prefix.rstrip("_")
        assert providers.connector_for_namespace(namespace_of(t)).name == connector


def test_the_two_families_are_disjoint(all_tools):
    unipile = {t for t in all_tools if t.startswith("linkedin_unipile_")}
    aiark = {t for t in all_tools if t.startswith("linkedin_aiark_")}
    assert unipile and aiark
    assert unipile.isdisjoint(aiark)
