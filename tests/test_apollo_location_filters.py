"""Un domaine est mondial : la localisation est ce qui le rend exploitable (#354/#356).

Sur une filiale française d'un groupe international, le domaine est partagé par tout
le groupe — franke.com rend 1887 profils, verifone.com 3282, sonova.com 3147 — et rien
ne permettait d'en garder les Français : prospecter revenait à révéler au hasard, un
crédit par tentative, majoritairement hors France. Apollo accepte pourtant
`person_locations` / `organization_locations`.

Ce qui est gardé ici : que les deux paramètres traversent le tool JUSQU'AU client. Un
paramètre avalé en route rendrait un résultat non filtré qui passerait pour filtré —
le mode de panne qu'on ne voit pas (cf. `aiark_company_search(account=)`).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def _call(monkeypatch, **kwargs):
    """Appelle le tool avec un client mocké. `_client` étant une closure de
    `register`, on remplace la CLASSE (importée à l'intérieur) et la résolution
    de clé — pas d'appel réseau, pas de crédit."""
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool

    client = MagicMock()
    client.search_people.return_value = {"people": []}
    monkeypatch.setattr(access, "resolve_api_key", lambda *a, **k: ("k", False))
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)
    m = FastMCP("t")
    apollo_tool.register(m)
    asyncio.run(m.get_tool("apollo_search_people")).fn(**kwargs)
    return client.search_people.call_args.kwargs


def test_person_location_reaches_the_client(monkeypatch):
    sent = _call(monkeypatch, domains=["verifone.com"], person_locations=["France"])
    assert sent["person_locations"] == ["France"]
    assert sent["domains"] == ["verifone.com"]


def test_organization_location_reaches_the_client(monkeypatch):
    sent = _call(monkeypatch, domains=["franke.com"],
                 organization_locations=["Paris, France"])
    assert sent["organization_locations"] == ["Paris, France"]


def test_locations_default_to_none(monkeypatch):
    """Pas de filtre implicite : une recherche sans localisation reste mondiale."""
    sent = _call(monkeypatch, domains=["acme.com"])
    assert sent["person_locations"] is None and sent["organization_locations"] is None


def test_the_contract_warns_that_a_domain_is_worldwide():
    """L'agent doit lire POURQUOI filtrer avant de brûler des crédits à l'aveugle."""
    from fastmcp import FastMCP
    from oto_mcp.tools import apollo as apollo_tool

    m = FastMCP("t")
    apollo_tool.register(m)
    doc = asyncio.run(m.get_tool("apollo_search_people")).description or ""
    assert "DOMAIN IS WORLDWIDE" in doc
    assert "person_locations" in doc


# --- projection de sortie : `fields` sur la recherche d'organisations ---------

_PAGE = {
    "pagination": {"page": 1, "total_entries": 490},
    "organizations": [{"name": "Acme", "primary_domain": "acme.fr",
                       "logo_url": "https://…", "sic_codes": [], "intent_strength": None}],
    # `mixed_companies/search` rend DEUX listes : les sociétés déjà présentes dans
    # le compte Apollo arrivent sous `accounts`, avec la même graisse.
    "accounts": [{"name": "Beta", "primary_domain": "beta.fr",
                  "logo_url": "https://…", "owned_by_organization_id": None}],
}


def _search_orgs(monkeypatch, **kwargs):
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool

    client = MagicMock()
    client.search_organizations.return_value = _PAGE
    monkeypatch.setattr(access, "resolve_api_key", lambda *a, **k: ("k", False))
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)
    m = FastMCP("t")
    apollo_tool.register(m)
    return asyncio.run(m.get_tool("apollo_search_organizations")).fn(**kwargs)


def test_without_fields_the_payload_is_untouched(monkeypatch):
    """`fields` est opt-in STRICT : omis, on ne retire rien. Le défaut n'est pas
    l'objet de ce lot — le choisir demanderait de mesurer ce qui ne sert jamais."""
    assert _search_orgs(monkeypatch, name="acme") == _PAGE


def test_fields_projects_BOTH_lists_and_keeps_the_envelope(monkeypatch):
    """Une page à `per_page=100` pèse ~113 000 c. et dépasse la limite de sortie de
    certains clients MCP : l'appel devient inexploitable directement (#645).

    Le piège gardé ici est `accounts` : projeter la seule liste `organizations`
    laisserait `fields` sans effet visible sur la moitié du payload — un paramètre
    d'économie à moitié inerte est pire qu'absent, parce qu'on le croit appliqué.
    """
    out = _search_orgs(monkeypatch, name="acme", fields=["name", "primary_domain"])
    assert out["organizations"] == [{"name": "Acme", "primary_domain": "acme.fr"}]
    assert out["accounts"] == [{"name": "Beta", "primary_domain": "beta.fr"}]
    # l'enveloppe reste : sans elle, l'agent croit avoir tout vu
    assert out["pagination"] == {"page": 1, "total_entries": 490}
