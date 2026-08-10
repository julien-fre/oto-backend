"""Connecteur LinkedIn (#231) — connecteur DISTINCT d'`aiark`, pas un mode/alias.
Même vendeur (AI Ark) et même client oto-core réutilisé tel quel
(`oto.tools.aiark.client.AiArkClient`), mais surface MCP plus simple (pas de
tool `credits`) et **app credits only** (`auth_modes={"platform"}` seul — pas
de BYO). Verrouille : l'entrée registre, la surface MCP curée, la jointure
tool↔client oto-core (même classe que `aiark`, garde version-skew), et que
`aiark` reste totalement INCHANGÉ (BYO + tool credits toujours là) à côté.
"""
import asyncio

import pytest

from oto_mcp import providers
from oto_mcp.tool_visibility import namespace_of

EXPECTED_TOOLS = {
    "linkedin_company_search",
    "linkedin_people_search",
    "linkedin_export_person",
    "linkedin_reverse_lookup",
    "linkedin_mobile_phone",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    tools = asyncio.run(m._list_tools())
    return {t.name for t in tools}


# --- registre -----------------------------------------------------------------

def test_linkedin_is_classic_keyed_connector():
    c = providers.REGISTRY["linkedin"]
    assert c.kind == "tools"
    assert c.mount_url is None
    assert c.keyed and c.secret_kind == "api_key"
    assert "linkedin" in providers.KEY_PROVIDERS


def test_linkedin_is_app_credits_only():
    # #231 : connecteur neuf, distinct d'`aiark` — seul le grant plateforme résout.
    c = providers.REGISTRY["linkedin"]
    assert c.auth_modes == frozenset({"platform"})
    assert not providers.is_byo_user("linkedin")
    assert not c.org_shareable  # is_org_shareable : pas de secret d'équipe/org non plus


def test_aiark_is_left_completely_untouched():
    # Le connecteur historique garde son BYO (byo_user/byo_org/platform) — le
    # nouveau `linkedin` s'ajoute À CÔTÉ, il ne le remplace ni ne le restreint.
    c = providers.REGISTRY["aiark"]
    assert c.auth_modes == frozenset({"byo_user", "byo_org", "platform"})
    assert providers.is_byo_user("aiark")
    assert c.org_shareable


# --- surface MCP --------------------------------------------------------------

def test_linkedin_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= all_tools
    # ⚠️ `linkedin_` n'est plus un préfixe qui identifie CE connecteur : depuis
    # l'ADR 0010 §Amendement (2026-08-10), le namespace porte la capacité suffixée du
    # fournisseur dès que plusieurs fournisseurs non substituables la rendent — d'où
    # `linkedin_unipile_*` (session opérée) à côté de `linkedin_*` (AI Ark, donnée
    # achetée). Le tri se fait sur le plus long préfixe DÉCLARÉ, pas sur le 1er token.
    assert all(namespace_of(t) == "linkedin"
               for t in all_tools
               if t.startswith("linkedin_") and not t.startswith("linkedin_unipile_"))


def test_linkedin_unipile_namespace_resolves_to_unipile(all_tools):
    """Le gate d'un tool suit son NAMESPACE : `linkedin_unipile_*` doit rester
    gouverné par le connecteur `unipile`, jamais par le connecteur `linkedin`
    (AI Ark). Sans la résolution au plus long préfixe déclaré, le 1er token
    (`linkedin`) les ferait tomber sous le mauvais connecteur — donc le mauvais
    credential, la mauvaise activation et la mauvaise sélection."""
    tools = {t for t in all_tools if t.startswith("linkedin_unipile_")}
    assert tools, "aucun tool linkedin_unipile_* monté"
    for t in tools:
        assert namespace_of(t) == "linkedin_unipile"
        assert providers.connector_for_namespace(namespace_of(t)).name == "unipile"


def test_linkedin_has_no_credits_tool(all_tools):
    # Surface volontairement simplifiée — pas de tool "get credit" côté linkedin
    # (contrairement à `aiark_credits`, qui reste exposé pour le BYO).
    assert "linkedin_credits" not in all_tools
    assert "aiark_credits" in all_tools  # aiark inchangé


def test_linkedin_verify_is_probe_not_tool(all_tools):
    from oto_mcp import connector_verify
    assert "linkedin_verify_key" not in all_tools
    assert connector_verify.supports("linkedin")
    assert connector_verify.supports("aiark")  # les deux sondes coexistent


def test_linkedin_async_bulk_endpoints_not_exposed(all_tools):
    assert not any("bulk" in t or "track" in t for t in all_tools
                   if t.startswith("linkedin_"))


# --- jointure tool ↔ client oto-core (même classe qu'aiark, garde version-skew) --

def test_client_exposes_methods_called_by_tools():
    from oto.tools.aiark.client import AiArkClient
    for meth in ("verify_key", "search_companies", "search_people",
                 "export_person", "reverse_lookup", "mobile_phone"):
        assert callable(getattr(AiArkClient, meth, None)), f"AiArkClient.{meth} manquant"
