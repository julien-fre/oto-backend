"""Apollo — la ligne FACTURÉE des quatre outils qui consomment un crédit hors lot.

`apollo_bulk_match` traçait déjà ses crédits (`tests/test_apollo_bulk_and_payload.py`,
oto#168). `apollo_match_person`, `apollo_enrich_organization`,
`apollo_bulk_enrich_organizations` et `apollo_search_organizations` (1 crédit par
PAGE) ne traçaient rien : leur `tool_calls.quantity` restait NULL, et un facturier
qui ne compte que ce qui est tracé ne les voyait pas.
Ce fichier fige, dans la MÊME unité (le crédit Apollo) :

- le chiffre de l'amont (`credits_consumed`) gagne dès qu'il est lisible, 0 compris ;
- sans lui, on compte ce que la RÉPONSE a servi — une personne, une organisation, le
  nombre d'organisations rendues, une page non vide — jamais ce qui a été soumis,
  jamais 1 pour l'appel ;
- rien de servi trace 0 (un 0 tracé = rien de facturé), jamais une ligne sans compte ;
- la trace est INCONDITIONNELLE (clé commune comme clé propre) et ne touche pas au
  quota de la clé commune (`units`, 1 par match) ;
- un appel refusé ou en échec ne trace rien ;
- `apollo_search_people` et `apollo_job_postings` ne tracent AUCUNE quantité.

Mock la CLASSE client (jamais `requests`) — cf. `tests/test_apollo_location_filters.py`.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

PERSONNE = {"id": "p1", "last_name": "Lovelace", "email": "ada@acme.test"}
ORG = {"id": "o1", "name": "Acme", "primary_domain": "acme.test"}


@pytest.fixture
def apollo(monkeypatch):
    """`(outil, client, traces, usage, mode)` — `mode["platform"]` choisit la clé qui
    répond ; `traces` reçoit chaque `note_call_trace`, `usage` chaque débit de quota."""
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from oto_mcp import access, session_org
    from oto_mcp.tools import apollo as apollo_tool

    client = MagicMock()
    mode = {"platform": True}
    traces: list[dict] = []
    usage: list[tuple] = []
    monkeypatch.setattr(access, "resolve_api_key",
                        lambda *a, **k: ("k", mode["platform"]))
    monkeypatch.setattr(access, "resolve_credential",
                        lambda *a, **k: MagicMock(key="k-byo"))
    monkeypatch.setattr(access, "platform_quota_hint", lambda p: None)
    monkeypatch.setattr(session_org, "note_call_trace", lambda **kw: traces.append(kw))
    from _releve import capter_units
    capter_units(monkeypatch, "apollo", lambda p, n: usage.append((p, n)))
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)

    m = FastMCP("t")
    apollo_tool.register(m)
    return (lambda name: asyncio.run(m.get_tool(name)).fn), client, traces, usage, mode


# --------------------------------------------------------------------------- #
# apollo_match_person
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reponse,attendu", [
    ({"person": dict(PERSONNE), "request_id": 42}, 1),      # trouvée
    ({"person": {"id": "p9", "_stub": True}}, 1),           # coquille : Apollo la facture
    ({"person": None}, 0),                                  # corps sans personne
    ({}, 0),
    (None, 0),                                              # 404 → le client rend None
])
def test_a_match_traces_one_credit_for_a_person_returned_else_zero(apollo, reponse, attendu):
    outil, client, traces, _, _ = apollo
    client.match_person.return_value = reponse
    outil("apollo_match_person")(person_id="p1")
    assert traces == [{"quantity": attendu}]


@pytest.mark.parametrize("reponse,attendu", [
    ({"person": dict(PERSONNE), "credits_consumed": 3}, 3),     # l'amont dit plus
    ({"person": dict(PERSONNE), "credits_consumed": 0}, 0),     # l'amont dit rien facturé
    ({"person": None, "credits_consumed": 1}, 1),               # facturé SANS personne
])
def test_a_match_traces_the_credits_apollo_reported_when_it_does(apollo, reponse, attendu):
    """Le chiffre de l'amont gagne dans les deux sens : au-dessus de 1 (un reveal),
    à 0 malgré une personne, et à 1 sans personne (le non-match facturé)."""
    outil, client, traces, _, _ = apollo
    client.match_person.return_value = reponse
    outil("apollo_match_person")(person_id="p1")
    assert traces == [{"quantity": attendu}]


@pytest.mark.parametrize("illisible", [-1, "2", 1.0, True, [1]])
def test_an_unreadable_figure_falls_back_on_the_response_AND_SAYS_SO(apollo, caplog, illisible):
    outil, client, traces, _, _ = apollo
    client.match_person.return_value = {"person": dict(PERSONNE),
                                        "credits_consumed": illisible}
    with caplog.at_level(logging.WARNING, logger="oto_mcp.tools.apollo"):
        outil("apollo_match_person")(person_id="p1")
    assert traces == [{"quantity": 1}]
    assert any("credits_consumed" in r.getMessage() for r in caplog.records)


def test_a_missing_figure_is_not_a_warning(apollo, caplog):
    """`people/match` ne déclare pas `credits_consumed` : son absence est le cas
    courant, un avertissement par appel ne dirait rien."""
    outil, client, _, _, _ = apollo
    client.match_person.return_value = {"person": dict(PERSONNE)}
    with caplog.at_level(logging.WARNING, logger="oto_mcp.tools.apollo"):
        outil("apollo_match_person")(person_id="p1")
    assert not caplog.records


@pytest.mark.parametrize("platform", [True, False])
def test_the_match_is_traced_on_any_key_and_the_quota_is_untouched(apollo, platform):
    """Inconditionnelle, comme `apollo_bulk_match` : c'est `key_mode` (posé par le
    résolveur) qui dit s'il y a quelque chose à facturer. Le quota, lui, ne bouge
    pas : 1 par appel sur la clé commune, rien sur une clé propre."""
    outil, client, traces, usage, mode = apollo
    mode["platform"] = platform
    client.match_person.return_value = {"person": dict(PERSONNE), "credits_consumed": 3}
    outil("apollo_match_person")(person_id="p1")
    assert traces == [{"quantity": 3}]
    assert usage == ([("apollo", 1)] if platform else [])


def test_a_personal_email_reveal_is_traced_on_your_own_key(apollo):
    outil, client, traces, usage, _ = apollo
    client.match_person.return_value = {"person": dict(PERSONNE)}
    outil("apollo_match_person")(person_id="p1", reveal_personal_emails=True)
    assert traces == [{"quantity": 1}] and usage == []


def test_the_traced_match_still_serves_its_payload(apollo):
    outil, client, _, _, _ = apollo
    client.match_person.return_value = {"person": dict(PERSONNE), "request_id": 42}
    out = outil("apollo_match_person")(person_id="p1")
    assert out == {"person": PERSONNE, "request_id": "42"}


def test_a_refused_match_traces_nothing(apollo):
    """Identifiant trop faible : le client refuse AVANT l'appel — rien de consommé."""
    from oto_mcp.mcp_errors import McpError

    outil, client, traces, usage, _ = apollo
    client.match_person.side_effect = ValueError("identifier too weak")
    with pytest.raises(McpError):
        outil("apollo_match_person")(first_name="Ada", org_name="Acme")
    assert traces == [] and usage == []


def test_a_failed_match_traces_nothing(apollo):
    from oto.tools.apollo.client import ApolloError

    outil, client, traces, _, _ = apollo
    client.match_person.side_effect = ApolloError("Apollo 500", status_code=500)
    with pytest.raises(ApolloError):
        outil("apollo_match_person")(person_id="p1")
    assert traces == []


# --------------------------------------------------------------------------- #
# apollo_enrich_organization
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reponse,attendu", [
    ({"organization": dict(ORG)}, 1),
    ({"organization": None}, 0),
    ({"organization": {}}, 0),
    ({}, 0),
    ({"organization": dict(ORG), "credits_consumed": 2}, 2),
    ({"organization": dict(ORG), "credits_consumed": 0}, 0),
    ({"organization": dict(ORG), "credits_consumed": "x"}, 1),
])
def test_an_enrich_traces_one_credit_for_an_organization_returned(apollo, reponse, attendu):
    outil, client, traces, usage, _ = apollo
    client.enrich_organization.return_value = reponse
    out = outil("apollo_enrich_organization")(domain="acme.test")
    assert traces == [{"quantity": attendu}]
    assert out == reponse, "la réponse est servie telle quelle"
    assert usage == [], "l'enrichissement d'organisation ne débite pas le quota"


@pytest.mark.parametrize("platform", [True, False])
def test_the_enrich_is_traced_on_any_key(apollo, platform):
    outil, client, traces, _, mode = apollo
    mode["platform"] = platform
    client.enrich_organization.return_value = {"organization": dict(ORG)}
    outil("apollo_enrich_organization")(domain="acme.test")
    assert traces == [{"quantity": 1}]


def test_a_failed_enrich_traces_nothing(apollo):
    from oto.tools.apollo.client import ApolloError

    outil, client, traces, _, _ = apollo
    client.enrich_organization.side_effect = ApolloError("Apollo 422", status_code=422)
    with pytest.raises(ApolloError):
        outil("apollo_enrich_organization")(domain="acme.test")
    assert traces == []


# --------------------------------------------------------------------------- #
# apollo_bulk_enrich_organizations
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reponse,attendu", [
    ({"organizations": [dict(ORG), dict(ORG), dict(ORG)]}, 3),
    ({"organizations": [dict(ORG), None, {}]}, 1),          # seules les fiches RENDUES
    ({"organizations": []}, 0),
    ({"organizations": [dict(ORG)] * 3, "credits_consumed": 2}, 2),
    ({"organizations": [dict(ORG)] * 3, "credits_consumed": 0}, 0),
    ({"organizations": [], "credits_consumed": 4}, 4),
    ({"organizations": [dict(ORG)] * 3, "credits_consumed": -2}, 3),
])
def test_a_bulk_enrich_traces_the_organizations_RETURNED_not_the_domains_sent(
        apollo, reponse, attendu):
    """Cinq domaines soumis à chaque fois : la quantité ne vient jamais de là."""
    outil, client, traces, usage, _ = apollo
    client.bulk_enrich_organizations.return_value = reponse
    out = outil("apollo_bulk_enrich_organizations")(
        domains=[f"c{i}.test" for i in range(5)])
    assert traces == [{"quantity": attendu}]
    assert out == reponse and usage == []


@pytest.mark.parametrize("reponse", [{}, {"organizations": None}, {"status": "success"}])
def test_a_bulk_enrich_nothing_can_be_counted_in_traces_zero_AND_SAYS_SO(
        apollo, caplog, reponse):
    """Ni chiffre de l'amont ni liste lisible : on ne facture pas à l'aveugle les
    domaines soumis — 0 tracé, et le journal le dit."""
    outil, client, traces, _, _ = apollo
    client.bulk_enrich_organizations.return_value = reponse
    with caplog.at_level(logging.WARNING, logger="oto_mcp.tools.apollo"):
        outil("apollo_bulk_enrich_organizations")(domains=["a.test", "b.test"])
    assert traces == [{"quantity": 0}]
    assert any("nothing countable" in r.getMessage() for r in caplog.records)


def test_a_refused_bulk_enrich_traces_nothing(apollo):
    """Plus de 10 domaines : le client refuse avant l'appel."""
    outil, client, traces, _, _ = apollo
    client.bulk_enrich_organizations.side_effect = ValueError("11 domains")
    with pytest.raises(ValueError):
        outil("apollo_bulk_enrich_organizations")(domains=["a.test"] * 11)
    assert traces == []


# --------------------------------------------------------------------------- #
# apollo_search_organizations — 1 crédit par PAGE servie
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reponse,attendu", [
    ({"organizations": [dict(ORG)], "accounts": []}, 1),
    ({"organizations": [dict(ORG)] * 100, "accounts": []}, 1),   # la PAGE, pas ses lignes
    ({"organizations": [], "accounts": [dict(ORG)]}, 1),         # sociétés déjà au compte
    ({"organizations": [dict(ORG)]}, 1),
    ({"organizations": [], "accounts": []}, 0),                  # page vide
    ({"organizations": None}, 0),
    ({}, 0),
    ({"organizations": [dict(ORG)], "credits_consumed": 0}, 0),  # l'amont gagne, 0 compris
    ({"organizations": [dict(ORG)], "credits_consumed": 2}, 2),
    ({"organizations": [], "accounts": [], "credits_consumed": 1}, 1),   # vide MAIS facturée
])
def test_an_organization_search_traces_one_credit_per_page_served(apollo, reponse, attendu):
    """Une page qui rend au moins une société (`organizations` ou `accounts`) compte 1,
    quelle que soit sa taille ; une page vide compte 0 — sauf si l'amont dit autre chose."""
    outil, client, traces, usage, _ = apollo
    client.search_organizations.return_value = reponse
    out = outil("apollo_search_organizations")(name="Acme", per_page=100)
    assert traces == [{"quantity": attendu}]
    assert out == reponse, "sans `fields`, la page est servie telle quelle"
    assert usage == [], "une recherche ne débite pas le quota de la clé commune"


@pytest.mark.parametrize("illisible", [-1, "1", 1.0, True])
def test_an_unreadable_search_figure_falls_back_on_the_page_AND_SAYS_SO(
        apollo, caplog, illisible):
    outil, client, traces, _, _ = apollo
    client.search_organizations.return_value = {"organizations": [dict(ORG)],
                                                "credits_consumed": illisible}
    with caplog.at_level(logging.WARNING, logger="oto_mcp.tools.apollo"):
        outil("apollo_search_organizations")(name="Acme")
    assert traces == [{"quantity": 1}]
    assert any("credits_consumed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("platform", [True, False])
def test_the_search_is_traced_on_any_key(apollo, platform):
    outil, client, traces, usage, mode = apollo
    mode["platform"] = platform
    client.search_organizations.return_value = {"organizations": [dict(ORG)]}
    outil("apollo_search_organizations")(name="Acme")
    assert traces == [{"quantity": 1}] and usage == []


def test_a_projected_search_is_traced_once_on_the_page_apollo_served(apollo):
    """`fields` rétrécit ce qui est SERVI, pas ce qu'Apollo a facturé : une seule
    trace, comptée sur la page de l'amont."""
    outil, client, traces, _, _ = apollo
    client.search_organizations.return_value = {"organizations": [dict(ORG)],
                                                "accounts": []}
    out = outil("apollo_search_organizations")(name="Acme", fields=["name"])
    assert traces == [{"quantity": 1}]
    assert out["organizations"] == [{"name": "Acme"}]


def test_a_failed_search_traces_nothing(apollo):
    from oto.tools.apollo.client import ApolloError

    outil, client, traces, _, _ = apollo
    client.search_organizations.side_effect = ApolloError("Apollo 429", status_code=429)
    with pytest.raises(ApolloError):
        outil("apollo_search_organizations")(name="Acme")
    assert traces == []


# --------------------------------------------------------------------------- #
# Ce qui ne consomme aucun crédit ne trace AUCUNE quantité
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("nom,methode,kwargs", [
    ("apollo_search_people", "search_people", {"domains": ["acme.test"]}),
    ("apollo_job_postings", "get_job_postings", {"org_id": "o1"}),
])
def test_a_people_search_or_job_postings_traces_no_quantity(apollo, nom, methode, kwargs):
    """Même quand l'amont glisserait un `credits_consumed` : ces outils restent hors
    du relevé facturé — aucune ligne, pas même un 0."""
    outil, client, traces, usage, _ = apollo
    getattr(client, methode).return_value = {"organizations": [dict(ORG)], "people": [],
                                             "credits_consumed": 7}
    outil(nom)(**kwargs)
    assert not [t for t in traces if "quantity" in t]
    assert usage == []
