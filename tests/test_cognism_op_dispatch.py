"""Dispatch `op=` des tools `cognism_*` (ADR 0047 §Amendement, appliqué au
connecteur cognism : 9 tools → 6).

Ce que ce fichier verrouille, et que `test_cognism.py` ne couvrait PAS : il exerce
le contrat HTTP à travers UN tool par endpoint (une fonction = un endpoint, rien à
router). La consolidation déplace le risque exactement là : une cible mal câblée
appelle silencieusement la mauvaise méthode du client, et rien ne casse au boot.

Enjeu propre à ce connecteur : **`cognism_redeem` COÛTE DES CRÉDITS** (le reveal),
là où `cognism_search` est un preview gratuit. D'où, pour chaque cible payante :
la méthode appelée, ET le mutisme des voisines dangereuses (`assert_not_called`) —
un `op="contact"` qui déclencherait aussi le redeem société facturerait deux fois.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
# Les méthodes du client qui FACTURENT (reveal). Toute op doit les laisser muettes,
# sauf celle qui les demande explicitement.
_BILLED = ("redeem_contacts", "redeem_accounts")


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import cognism as C

    m = FastMCP("t")
    C.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    """Faux `CognismClient` + clé résolue. `register()` importe la classe à
    l'appel : patcher l'attribut du module oto-core avant `_tool()` suffit."""
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.cognism.client.CognismClient",
                        lambda api_key=None: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("k", False))
    monkeypatch.setattr("oto_mcp.access.record_platform_usage", lambda provider: None)
    return inst


def _assert_nothing_billed(client):
    for meth in _BILLED:
        getattr(client, meth).assert_not_called()


# --- surface : les anciens noms ont bien disparu -------------------------------

def test_the_surface_is_the_six_consolidated_tools():
    """Une consolidation qui laisserait traîner un ancien tool doublerait la
    surface au lieu de la réduire (et l'agent choisirait au hasard)."""
    from fastmcp import FastMCP
    from oto_mcp.tools import cognism as C

    m = FastMCP("t")
    C.register(m)
    names = {t.name for t in asyncio.run(m._list_tools())}
    assert names == {
        "cognism_search", "cognism_redeem", "cognism_enrich_contact",
        "cognism_enrich_account", "cognism_entitlement", "cognism_filter_values",
    }


# --- recherche (GRATUIT) --------------------------------------------------------

@pytest.mark.parametrize("op,method,other", [
    ("contact", "search_contacts", "search_accounts"),
    ("account", "search_accounts", "search_contacts"),
])
def test_search_ops_route_to_the_right_client_method(client, op, method, other):
    _tool("cognism_search")(op=op, filters={"x": 1})
    getattr(client, method).assert_called_once()
    getattr(client, other).assert_not_called()
    # Le preview est gratuit : il ne doit JAMAIS toucher un endpoint facturé.
    _assert_nothing_billed(client)


@pytest.mark.parametrize("op,expected", [("contact", 25), ("account", 100)])
def test_search_uses_the_page_size_default_of_its_target(client, op, expected):
    """Les défauts amont diffèrent (25 contacts / 100 sociétés) : les fusionner en
    une valeur unique changerait silencieusement la pagination d'un des deux."""
    _tool("cognism_search")(op=op)
    method = "search_contacts" if op == "contact" else "search_accounts"
    assert getattr(client, method).call_args.kwargs["index_size"] == expected


def test_search_explicit_page_size_wins(client):
    _tool("cognism_search")(op="account", index_size=10)
    assert client.search_accounts.call_args.kwargs["index_size"] == 10


def test_search_passes_filters_and_cursor_through(client):
    _tool("cognism_search")(op="contact", filters={"firstName": "Stjepan"},
                            last_returned_key="cur-1")
    assert client.search_contacts.call_args.args[0] == {"firstName": "Stjepan"}
    assert client.search_contacts.call_args.kwargs["last_returned_key"] == "cur-1"


# --- reveal (CONSOMME DES CRÉDITS) ---------------------------------------------

def test_redeem_contact_bills_only_the_contact_endpoint(client):
    _tool("cognism_redeem")(op="contact", ids=["abc"])
    client.redeem_contacts.assert_called_once()
    client.redeem_accounts.assert_not_called()
    client.search_contacts.assert_not_called()


def test_redeem_account_bills_only_the_account_endpoint(client):
    _tool("cognism_redeem")(op="account", redeem_ids=["r1"])
    client.redeem_accounts.assert_called_once()
    client.redeem_contacts.assert_not_called()
    client.search_accounts.assert_not_called()


@pytest.mark.parametrize("op,method", [("contact", "redeem_contacts"),
                                       ("account", "redeem_accounts")])
def test_redeem_forwards_its_arguments(client, op, method):
    _tool("cognism_redeem")(op=op, ids=["a"], merge_phones_and_locations=True)
    kw = getattr(client, method).call_args.kwargs
    assert kw["ids"] == ["a"] and kw["redeem_ids"] is None
    assert kw["merge_phones_and_locations"] is True


@pytest.mark.parametrize("op", ["contact", "account"])
def test_redeem_without_ids_refuses_and_bills_nothing(client, op):
    """Ni `ids` ni `redeem_ids` = rien à révéler : refus qui NOMME l'op et les deux
    arguments, jamais un appel amont facturé « pour voir »."""
    with pytest.raises(McpError) as exc:
        _tool("cognism_redeem")(op=op)
    assert f"op='{op}'" in str(exc.value)
    assert "ids" in str(exc.value) and "redeem_ids" in str(exc.value)
    _assert_nothing_billed(client)


def test_redeem_has_no_default_op(client):
    """Aucune op payante atteignable par défaut : `op` est obligatoire, un appel
    nu ne peut pas partir chercher un reveal."""
    with pytest.raises(TypeError):
        _tool("cognism_redeem")()
    _assert_nothing_billed(client)


@pytest.mark.parametrize("tool", ["cognism_search", "cognism_entitlement"])
def test_read_tools_have_no_default_op_either(client, tool):
    """Les cibles ne sont pas substituables (racine du `filters` différente) :
    deviner en rendrait une page fausse, pas une erreur."""
    with pytest.raises(TypeError):
        _tool(tool)()


# --- entitlement ----------------------------------------------------------------

@pytest.mark.parametrize("op,method,other", [
    ("contact", "contact_entitlement", "account_entitlement"),
    ("account", "account_entitlement", "contact_entitlement"),
])
def test_entitlement_ops_route_to_the_right_client_method(client, op, method, other):
    _tool("cognism_entitlement")(op=op)
    getattr(client, method).assert_called_once()
    getattr(client, other).assert_not_called()


# --- refus d'une cible inconnue -------------------------------------------------

@pytest.mark.parametrize("tool", ["cognism_search", "cognism_redeem",
                                  "cognism_entitlement"])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool):
    """Une cible inconnue doit lever en nommant les cibles valides — et n'atteindre
    AUCUNE méthode du client (donc aucun crédit consommé par un chemin dérivé)."""
    with pytest.raises(McpError, match="op doit être"):
        _tool(tool)(op="nope")
    assert client.method_calls == []


# --- tools laissés seuls (variantes disjointes / vocabulaire propre) ------------

def test_enrich_contact_forwards_its_identity_fields(client):
    _tool("cognism_enrich_contact")(email="a@b.com", min_match_score=45)
    kw = client.enrich_contact.call_args.kwargs
    assert kw["email"] == "a@b.com" and kw["min_match_score"] == 45
    client.enrich_account.assert_not_called()
    _assert_nothing_billed(client)


def test_enrich_account_forwards_its_identity_fields(client):
    _tool("cognism_enrich_account")(domain="cognism.com", city="London")
    kw = client.enrich_account.call_args.kwargs
    assert kw["domain"] == "cognism.com" and kw["city"] == "London"
    client.enrich_contact.assert_not_called()


def test_filter_values_keeps_its_own_kind_vocabulary(client):
    """`kind` (technologies/regions/…) n'est PAS la cible contact/account : il
    reste un paramètre distinct, passé tel quel au client."""
    _tool("cognism_filter_values")(kind="technologies", search="salesforce")
    assert client.filter_values.call_args.args[0] == "technologies"
    assert client.filter_values.call_args.kwargs["search"] == "salesforce"


# --- traduction d'erreur (le seam `_run` couvre toutes les ops) -----------------

def test_client_value_error_becomes_an_actionable_mcp_error(client):
    """Filtre invalide / cible manquante détectés côté client (avant réseau) :
    l'agent doit lire le message, pas un 500."""
    client.search_contacts.side_effect = ValueError("seniority: 'Founder' invalide")
    with pytest.raises(McpError, match="seniority"):
        _tool("cognism_search")(op="contact", filters={"seniority": ["Founder"]})
