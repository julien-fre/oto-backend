"""Le catalogue d'outils doit se lire et se chercher, pas se deviner (issue #275).

Mode différé — `oto_list_my_tools` → `oto_tool_schema` → `oto_call` — c'est LE chemin
d'accès d'un agent tiers à oto : charger les ~350 schémas coûte 228 k tokens, plus que
la fenêtre d'un mistral-large. Le catalogue ne rendait que des noms :

    {"name": "apollo_bulk_enrich_organizations", "enabled": true}

Run réel mesuré le 10/08/2026 (agent Mistral en connector MCP, enrichissement B2B) :
`fr_siret` choisi sans avoir le SIRET → échec ; `serper_web_search(q=…)` au lieu de
`query=` → échec ; sept appels, ~30 s perdues sur 94. Le modèle s'en sort, en tâtonnant
sur ce qu'une ligne de description réglait.

Ces tests couvrent les deux pièces : le RÉSUMÉ (une ligne utile, bornée) et la
RECHERCHE (retrouver un outil par des mots, sans parcourir 350 noms).
"""
import pytest

from oto_mcp import tool_registry


# ── Le résumé d'une ligne ────────────────────────────────────────────────────

def test_blurb_takes_a_whole_sentence_not_a_wrapped_line():
    """Le piège : une docstring est enveloppée à ~80 colonnes, donc sa 1ʳᵉ LIGNE coupe
    au milieu d'une phrase. C'est ce que rendait l'ancien registre."""
    doc = ("Full company profile by SIREN: identity (siège, directors, NAF,\n"
           "employees) + ratios.\n\nUse this as first call.")
    out = tool_registry.blurb(doc, limit=200)
    assert out.startswith("Full company profile by SIREN: identity (siège, directors, "
                          "NAF, employees)")
    assert "\n" not in out
    # Le 2ᵉ paragraphe (le mode d'emploi) n'a rien à faire dans une ligne de catalogue.
    assert "first call" not in out


def test_blurb_prefers_a_complete_sentence_to_a_cut():
    doc = "Search French companies. Filters by NAF, location, headcount and IDCC."
    assert tool_registry.blurb(doc, limit=40) == "Search French companies."


def test_blurb_falls_back_to_a_word_boundary():
    """Quand la 1ʳᵉ phrase est trop longue pour le budget, on coupe au mot — jamais au
    milieu d'un mot, qui donne un catalogue illisible."""
    out = tool_registry.blurb("alpha beta gamma delta epsilon zeta", limit=20)
    assert out.endswith("…") and " " in out
    assert not out.replace("…", "").endswith(("alph", "bet", "gamm"))
    assert len(out) <= 21


def test_blurb_respects_its_budget():
    """~350 entrées rendues d'un coup : le budget n'est pas cosmétique."""
    assert len(tool_registry.blurb("x" * 500, limit=100)) <= 101


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_blurb_tolerates_a_tool_without_docstring(empty):
    assert tool_registry.blurb(empty) == ""


# ── La recherche ─────────────────────────────────────────────────────────────

CATALOG = [
    {"name": "fr_get", "description": "Full company profile by SIREN.",
     "namespace_help": "INSEE SIRENE : données entreprise FR"},
    {"name": "fr_search", "description": "Search French companies by name and NAF.",
     "namespace_help": "INSEE SIRENE : données entreprise FR"},
    {"name": "serper_web_search", "description": "Google web search.",
     "namespace_help": "Serper : recherche web"},
    {"name": "serper_lens", "description": "Reverse image lookup.",
     "namespace_help": "Serper : recherche web"},
    {"name": "email_send", "description": "Send an email from your organisation.",
     "namespace_help": "Email : envoi transactionnel"},
    {"name": "data_share", "description": "Share a table; notifies people by email.",
     "namespace_help": "Datastore : tableaux"},
]


def test_a_french_query_reaches_english_docstrings():
    """Le cas qui motive l'issue. Les docstrings sont en ANGLAIS (contrat LLM) : sans la
    ligne de catalogue du connecteur, curée en français, « entreprises » ne touche rien."""
    names = [e["name"] for e in tool_registry.match("entreprises françaises", CATALOG)]
    assert names[:2] == ["fr_get", "fr_search"] or set(names[:2]) == {"fr_get", "fr_search"}
    assert "serper_lens" not in names


def test_the_plural_does_not_break_the_match():
    """Le catalogue dit « donnée entreprise », l'agent tape « entreprises »."""
    assert [e["name"] for e in tool_registry.match("entreprise", CATALOG)][:2] == \
           [e["name"] for e in tool_registry.match("entreprises", CATALOG)][:2]


def test_the_name_outranks_a_shared_catalog_line():
    """Tous les `serper_*` héritent de « Serper : recherche web ». Sans départager le
    texte propre de l'outil et celui de son connecteur, la recherche rendait le premier
    dans l'ordre alphabétique."""
    assert tool_registry.match("recherche web", CATALOG)[0]["name"] == "serper_web_search"


def test_the_name_outranks_a_passing_mention():
    """`data_share` « notifie par email », `email_send` s'appelle ainsi. Vécu : le
    premier sortait avant le second (égalité parfaite, départagée alphabétiquement)."""
    assert tool_registry.match("envoyer un email", CATALOG)[0]["name"] == "email_send"


def test_an_exact_tool_name_comes_first():
    """Chercher un outil qu'on nomme déjà doit le rendre en tête, `_` compris."""
    assert tool_registry.match("fr_get", CATALOG)[0]["name"] == "fr_get"


def test_a_two_letter_query_survives():
    """Les mots de 1-2 lettres sont écartés comme du bruit (« un », « de ») — sauf quand
    la requête n'est QUE ça : `fr` est un vrai domaine."""
    assert [e["name"] for e in tool_registry.match("fr", CATALOG)] == ["fr_get", "fr_search"]


def test_a_missing_word_does_not_disqualify():
    """ET strict = zéro résultat sur toute requête en langue naturelle (elle porte des
    mots qu'aucune docstring ne contient). On classe, on n'exclut pas."""
    names = [e["name"] for e in tool_registry.match("trouver des entreprises", CATALOG)]
    assert "fr_get" in names


def test_no_query_returns_everything_untouched():
    assert tool_registry.match("", CATALOG) == CATALOG
    assert tool_registry.match("   ", CATALOG) == CATALOG


def test_a_hopeless_query_returns_nothing_rather_than_noise():
    """Zéro est une réponse honnête — l'appelant (`oto_list_my_tools`) rend alors la carte
    des namespaces, pour que l'agent reformule au lieu de conclure à une lacune."""
    assert tool_registry.match("zzzzz", CATALOG) == []
