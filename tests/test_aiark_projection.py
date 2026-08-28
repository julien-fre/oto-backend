"""AI Ark — la page de recherche resserrée par défaut, le brut sur demande.

Signal #364 : « every call with size=100 returns 2.8-3.2M characters and blows the token
limit, forcing a spill-to-file + jq parse for EVERY page. 11 pages fetched in this run =
11 forced file round-trips. » Les guides de sourcing paginent les grands viviers — le
surcoût est donc PAR PAGE, et un agent sans shell (client MCP nu, n8n) cale tout court.

L'enregistrement ci-dessous est une CAPTURE d'un retour réel du 14/08 (élagué à ses clés,
les valeurs longues remplacées par du remplissage de même ordre de grandeur) : un banc qui
reconstitue une forme qu'on IMAGINE mesure la représentation qu'on s'en fait, pas le
système.
"""
import json

from oto_mcp.tools import aiark

_LONG = "Description de la société, son marché, ses offres. " * 40

# Forme réelle d'un `content[]` de `op=people` (clés de premier niveau exhaustives).
PERSON = {
    "id": "03a4cc1e", "identifier": "laportealexis",
    "profile": {"first_name": "Alexis", "last_name": "Laporte", "full_name": "Alexis Laporte",
                "headline": "AI Engineer & Entrepreneur", "title": "AI Founding Engineer",
                "picture": {"source": "https://images.ai-ark.com/" + "x" * 120},
                "background": {"source": "https://media.licdn.com/" + "y" * 160},
                "summary": "Tech entrepreneur since 2010."},
    "link": {"linkedin": "https://www.linkedin.com/in/laportealexis"},
    "location": {"default": "Greater Marseille Metropolitan Area, France", "country": "France"},
    "languages": {"profile_languages": [{"name": "English"}, {"name": "French"}]},
    "industry": "Computer Software",
    "educations": [{"school": {"name": "ENSEEIHT"}, "degree_name": "Master"}] * 3,
    "awards": [{"title": "Prix INP Innov'"}],
    "position_groups": [{"company": {"name": "Otomata"},
                         "profile_positions": [{"description": _LONG}]}] * 10,
    "volunteer_experiences": [{"role": "Coach", "company": {"name": "French Tech"}}] * 5,
    "skills": ["Anthropic Claude", "Intelligence artificielle"] * 11,
    "member_badges": {"premium": True, "creator": True},
    "statistics": {"network": {"followers_count": 4191}},
    "company": {"id": "7e62bf93",
                "summary": {"name": "Otomata", "description": "Otomata builds AI",
                            "industry": "software development", "staff": {"total": 1}},
                "link": {"linkedin": "https://www.linkedin.com/company/otomata-tech"},
                "location": {"headquarter": {"city": "Marseille", "country": "France"},
                             "locations": [{"city": "Marseille"}] * 4},
                "industries": ["software development"], "languages": ["english", "french"],
                "technologies": ["hubspot", "aws"] * 30, "keywords": ["ai"] * 40,
                "naics": ["541511"], "last_updated": "2026-07-20"},
    "department": {"departments": ["engineering"], "seniority": "senior"},
    "last_updated": "2026-07-20",
}

PAGE = {"content": [PERSON] * 100, "size": 100, "totalElements": 6, "totalPages": 6,
        "pageable": {"pageNumber": 0, "pageSize": 100}, "trackId": "436d00b7",
        "number": 0, "empty": False}


def _size(p) -> int:
    return len(json.dumps(p, ensure_ascii=False))


def test_le_defaut_resserre_une_page_de_100_sous_le_plafond():
    brut = _size(PAGE)
    vue = _size(aiark._shape(PAGE, "people", full=False, fields=None))
    # Le banc reproduit l'ordre de grandeur signalé (millions de caractères).
    assert brut > 1_000_000
    # Et la vue de tri ramène la page dans ce qu'un agent lit en ligne.
    assert vue < brut / 10


def test_ce_que_le_sourcing_LIT_survit_a_la_projection():
    out = aiark._shape(PAGE, "people", full=False, fields=None)
    p = out["content"][0]
    assert p["id"] and p["identifier"]
    assert p["profile"]["full_name"] == "Alexis Laporte"
    assert p["profile"]["title"] and p["profile"]["headline"]
    assert p["link"]["linkedin"] and p["location"]["country"] == "France"
    assert p["department"]["seniority"] == "senior"
    # L'identité de la société reste — c'est elle qu'on qualifie.
    assert p["company"]["summary"]["name"] == "Otomata"
    assert p["company"]["location"]["headquarter"]["city"] == "Marseille"


def test_ce_qui_est_ecarte_est_ce_que_le_sourcing_ne_lit_jamais():
    p = aiark._shape(PAGE, "people", full=False, fields=None)["content"][0]
    for k in ("educations", "skills", "statistics", "member_badges", "languages",
              "position_groups", "volunteer_experiences", "awards"):
        assert k not in p, k
    assert "picture" not in p["profile"] and "background" not in p["profile"]
    # Les blocs répétés à l'identique sur les 100 personnes d'une même société.
    for k in ("technologies", "keywords", "naics"):
        assert k not in p["company"], k
    assert "locations" not in p["company"]["location"]


def test_l_enveloppe_de_pagination_est_INTACTE():
    # Sans elle, l'agent croit avoir tout vu et n'ira pas chercher la page suivante.
    out = aiark._shape(PAGE, "people", full=False, fields=None)
    for k in ("totalElements", "totalPages", "pageable", "trackId", "size", "number"):
        assert out[k] == PAGE[k], k


def test_full_rend_la_page_BRUTE_sans_copie_ni_perte():
    assert aiark._shape(PAGE, "people", full=True, fields=None) is PAGE


def test_fields_projette_les_enregistrements_sans_toucher_a_l_enveloppe():
    out = aiark._shape(PAGE, "people", full=False, fields=["id", "link"])
    assert set(out["content"][0]) == {"id", "link"}
    assert out["totalElements"] == 6 and out["trackId"] == "436d00b7"


def test_une_forme_inattendue_passe_sans_lever():
    # Une API tierce change de forme sans prévenir : une projection qui lève
    # transformerait une réponse utile en panne, et c'est le connecteur qu'on blâmerait.
    assert aiark._shape({"content": "pas une liste"}, "people", False, None) == {"content": "pas une liste"}
    assert aiark._shape([], "people", False, None) == []
    assert aiark._shape({"content": [None, 3]}, "people", False, None)["content"] == [None, 3]


def test_une_societe_est_delestee_de_ses_blocs_repetes():
    page = {"content": [PERSON["company"]], "totalElements": 1}
    c = aiark._shape(page, "companies", full=False, fields=None)["content"][0]
    assert c["summary"]["name"] == "Otomata"          # l'identité reste
    assert "technologies" not in c and "keywords" not in c and "naics" not in c
