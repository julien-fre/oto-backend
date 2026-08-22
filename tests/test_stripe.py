"""Connecteur Stripe — paiements & facturation (api.stripe.com v1).

Verrouille : l'entrée de registre (credential à 3 champs, byo-only, catégorie
Finance, éditeur Stripe), la doc how-to, la surface MCP (9 tools, chacun avec
une description — régression du piège f-string-docstring), la sonde « tester la
connexion », la jointure tool↔client oto-core (garde version-skew), le dispatch
`op=` (required manquant refusé, arg non pertinent refusé), et surtout les deux
propriétés qui font la sûreté de ce connecteur : **rien ne déplace d'argent**,
et **l'agrégat dit quand il est incomplet**.
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import connector_verify, providers
from oto_mcp.tool_visibility import namespace_of
from oto_mcp.tools import stripe

EXPECTED_TOOLS = {
    "stripe_search", "stripe_customer", "stripe_subscription", "stripe_invoice",
    "stripe_payment", "stripe_balance", "stripe_catalog", "stripe_checkout",
    "stripe_event",
}


@pytest.fixture(scope="module")
def all_tools():
    from fastmcp import FastMCP
    from oto_mcp.tools import register_all

    m = FastMCP("t")
    register_all(m)
    return {t.name: t for t in asyncio.run(m._list_tools())}


@pytest.fixture(autouse=True)
def _fake_credential(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_credential_fields",
        lambda provider, account=None: {"api_key": "rk_test_x", "api_version": "",
                                        "stripe_account": ""})


def _fn_with_mock_client():
    """Enregistre le module avec `StripeClient` mocké, DANS le patch (sinon le
    `from … import StripeClient` de `register()` capture la vraie classe avant
    que le patch ne s'applique)."""
    from fastmcp import FastMCP

    patcher = patch("oto.tools.stripe.client.StripeClient")
    cls = patcher.start()
    m = FastMCP("t")
    stripe.register(m)
    return m, cls, patcher


def _tool(m, name):
    return asyncio.run(m.get_tool(name)).fn


# --- registre -----------------------------------------------------------------

def test_stripe_is_a_byo_only_multi_field_connector():
    c = providers.REGISTRY["stripe"]
    assert c.kind == "tools"
    assert c.secret_kind == "fields"
    assert c.auth_modes == frozenset({"byo_user", "byo_org"})
    assert "platform" not in c.auth_modes, "jamais de clé Stripe partagée entre orgs"
    assert c.default_active is False
    assert c.default_quota == 0
    # secret_kind="fields" ⇒ résolu par resolve_credential_fields, pas resolve_api_key
    assert "stripe" in providers.CREDENTIAL_PROVIDERS
    assert "stripe" not in providers.KEY_PROVIDERS


def test_stripe_credential_fields_separate_the_secret_from_what_it_reads():
    c = providers.REGISTRY["stripe"]
    assert [f.name for f in c.secret_fields] == ["api_key", "api_version", "stripe_account"]
    # api_version et stripe_account sont NON secrets ⇒ ils deviennent des config_fields
    # et voyagent avec la clé gagnante via resolve_credential().config.
    assert [f.name for f in c.config_fields] == ["api_version", "stripe_account"]
    assert c.secret_fields[0].secret is True
    assert all(not f.required for f in c.config_fields), \
        "les deux satellites sont facultatifs"


def test_stripe_connect_account_is_a_credential_field_not_a_tool_parameter(all_tools):
    """Avec Connect, la MÊME question rend le CA d'une autre société selon cet
    en-tête. Le poser au credential le rend impossible à basculer en cours de
    conversation — s'il devenait un paramètre d'outil, cette propriété tombe."""
    assert "stripe_account" in {f.name for f in providers.REGISTRY["stripe"].config_fields}
    for name in EXPECTED_TOOLS:
        params = set(all_tools[name].parameters.get("properties", {}))
        assert "stripe_account" not in params, f"{name} expose stripe_account en paramètre"


def test_stripe_catalogue_overlays_are_curated():
    c = providers.REGISTRY["stripe"]
    assert c.category == "Finance"
    # Sans entrée d'éditeur, le catalogue annoncerait « Otomata » comme auteur de
    # l'API Stripe — chez les clients d'un tenant tiers, c'est notre marque qui
    # revendiquerait le travail d'un autre.
    assert c.publisher_name == "Stripe"
    assert c.label == "Stripe"
    assert providers._LOGO_DOMAIN_BY_CONNECTOR["stripe"] == "stripe.com"
    assert "stripe" not in providers._SANS_LOGO_DE_MARQUE


def test_stripe_has_onboarding_doc():
    kinds = {s.kind for s in providers.REGISTRY["stripe"].doc_sections}
    assert {"prerequisite", "usage", "note"} <= kinds


# --- surface MCP --------------------------------------------------------------

def test_stripe_tools_register_under_namespace(all_tools):
    assert EXPECTED_TOOLS <= set(all_tools)
    assert all(namespace_of(t) == "stripe" for t in all_tools if t.startswith("stripe_"))


def test_stripe_exposes_exactly_the_expected_tools(all_tools):
    assert {t for t in all_tools if t.startswith("stripe_")} == EXPECTED_TOOLS


def test_stripe_tools_all_have_descriptions(all_tools):
    for name in EXPECTED_TOOLS:
        assert all_tools[name].description, f"{name} has no description"


def test_verify_probe_registered():
    # ⚠️ le patch DOIT être arrêté : sans ça la classe cliente reste un
    # MagicMock pour les tests suivants, et `hasattr(mock, "…")` étant
    # toujours vrai, les tests d'absence ne prouveraient plus rien.
    m, cls, patcher = _fn_with_mock_client()
    try:
        assert connector_verify.supports("stripe")
    finally:
        patcher.stop()


# --- jointure tool ↔ client oto-core (garde version-skew) ---------------------

def test_client_exposes_methods_called_by_tools():
    from oto.tools.stripe.client import StripeClient
    for meth in ("search", "balance", "list_balance_transactions",
                 "get_balance_transaction", "list_payouts", "get_payout",
                 "list_customers", "get_customer", "create_customer",
                 "update_customer", "list_customer_payment_methods",
                 "list_subscriptions", "get_subscription", "list_subscription_items",
                 "list_invoices", "get_invoice", "get_invoice_lines",
                 "create_invoice", "update_invoice", "list_invoice_items",
                 "create_invoice_item", "list_payment_intents", "get_payment_intent",
                 "list_charges", "get_charge", "list_refunds", "get_refund",
                 "list_disputes", "get_dispute", "list_products", "get_product",
                 "create_product", "update_product", "list_prices", "get_price",
                 "create_price", "update_price", "list_coupons",
                 "list_promotion_codes", "list_payment_links", "get_payment_link",
                 "get_payment_link_line_items", "create_payment_link",
                 "update_payment_link", "list_checkout_sessions",
                 "get_checkout_session", "get_checkout_session_line_items",
                 "list_events", "get_event", "list_webhook_endpoints"):
        assert callable(getattr(StripeClient, meth, None)), f"StripeClient.{meth} manquant"


def test_no_money_moving_verb_is_reachable_from_this_module():
    """La propriété centrale du connecteur : ce n'est pas une politique de ce
    fichier — les méthodes n'existent pas sur le client, donc les rebrancher
    demande une PR oto-core, pas une ligne ici."""
    from oto.tools.stripe.client import StripeClient
    for absent in ("create_refund", "create_payout", "cancel_payout",
                   "reverse_payout", "close_dispute", "cancel_subscription",
                   "finalize_invoice", "pay_invoice", "void_invoice",
                   "delete_customer", "capture_charge", "create_charge"):
        assert not hasattr(StripeClient, absent), f"StripeClient.{absent} est réapparu"


# --- dispatch op= (comportement) ----------------------------------------------

def test_limit_defaults_to_100_because_stripe_silently_truncates_to_10():
    m, cls, patcher = _fn_with_mock_client()
    try:
        _tool(m, "stripe_customer")(op="list")
        assert cls.return_value.list_customers.call_args.kwargs["limit"] == 100
    finally:
        patcher.stop()


def test_limit_out_of_range_is_refused():
    m, cls, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="entre 1 et 100"):
            _tool(m, "stripe_customer")(op="list", limit=500)
    finally:
        patcher.stop()


def test_created_window_becomes_stripes_operator_dict():
    """La fenêtre de Stripe est `created[gte]`/`created[lt]`, pas deux champs."""
    m, cls, patcher = _fn_with_mock_client()
    try:
        _tool(m, "stripe_invoice")(op="list", created_after=1704067200,
                                   created_before=1706745600)
        assert cls.return_value.list_invoices.call_args.kwargs["created"] == {
            "gte": 1704067200, "lt": 1706745600}
    finally:
        patcher.stop()


def test_customer_get_requires_id_and_refuses_list_filters():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = _tool(m, "stripe_customer")
        with pytest.raises(McpError, match="requiert .customer_id."):
            fn(op="get")
        with pytest.raises(McpError, match="op='get' n'utilise pas"):
            fn(op="get", customer_id="cus_1", created_after=1)
        cls.return_value.get_customer.assert_not_called()

        cls.return_value.get_customer.return_value = {"id": "cus_1"}
        assert fn(op="get", customer_id="cus_1") == {"id": "cus_1"}
    finally:
        patcher.stop()


def test_customer_create_requires_something_findable():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = _tool(m, "stripe_customer")
        with pytest.raises(McpError, match="au moins `email` ou `name`"):
            fn(op="create")
        fn(op="create", email="a@b.co")
        cls.return_value.create_customer.assert_called_once()
    finally:
        patcher.stop()


def test_subscription_list_refuses_a_subscription_id():
    m, cls, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="op='list' n'utilise pas"):
            _tool(m, "stripe_subscription")(op="list", subscription_id="sub_1")
        cls.return_value.list_subscriptions.assert_not_called()
    finally:
        patcher.stop()


def test_draft_invoice_pulls_pending_items_by_default():
    """Vérifié en live : sans « include », la facture revient à total=0 avec zéro
    ligne alors que des lignes viennent d'être posées — un « facture créée »
    faux, et le montant reste en suspens."""
    m, cls, patcher = _fn_with_mock_client()
    try:
        _tool(m, "stripe_invoice")(op="create_draft", customer_id="cus_1")
        kwargs = cls.return_value.create_invoice.call_args.kwargs
        assert kwargs["pending_invoice_items_behavior"] == "include"
        assert kwargs["auto_advance"] is False, "un brouillon ne se finalise pas tout seul"
    finally:
        patcher.stop()


def test_draft_invoice_can_still_be_left_empty_on_request():
    m, cls, patcher = _fn_with_mock_client()
    try:
        _tool(m, "stripe_invoice")(op="create_draft", customer_id="cus_1",
                                   pending_items="exclude")
        assert cls.return_value.create_invoice.call_args.kwargs[
            "pending_invoice_items_behavior"] == "exclude"
    finally:
        patcher.stop()


def test_add_item_requires_amount_and_currency():
    m, cls, patcher = _fn_with_mock_client()
    try:
        fn = _tool(m, "stripe_invoice")
        with pytest.raises(McpError, match="requiert `customer_id`"):
            fn(op="add_item", amount=1000, currency="eur")
        with pytest.raises(McpError, match="`amount`.*`currency`"):
            fn(op="add_item", customer_id="cus_1")
        fn(op="add_item", customer_id="cus_1", amount=-2000, currency="eur")
        assert cls.return_value.create_invoice_item.call_args.kwargs["amount"] == -2000
    finally:
        patcher.stop()


# --- l'agrégat : la seule réponse honnête à « combien a-t-on facturé » ---------

def _invoice(amount, currency="eur", status="paid", id_="in_x"):
    return {"id": id_, "currency": currency, "total": amount,
            "amount_paid": amount, "amount_due": 0, "status": status}


def test_totals_sums_per_currency_and_never_across_them():
    m, cls, patcher = _fn_with_mock_client()
    try:
        cls.return_value.list_invoices.return_value = {
            "data": [_invoice(1000, "eur", id_="in_1"),
                     _invoice(2000, "eur", id_="in_2"),
                     _invoice(500, "usd", id_="in_3")],
            "has_more": False}
        out = _tool(m, "stripe_invoice")(op="totals")
        assert out["count"] == 3
        assert out["complete"] is True
        assert out["by_currency"]["eur"]["total"] == 3000
        assert out["by_currency"]["usd"]["total"] == 500
        assert "total" not in out, "aucune somme toutes devises confondues"
        assert out["count_by_status"] == {"paid": 3}
    finally:
        patcher.stop()


def test_totals_follows_the_cursor_across_pages():
    m, cls, patcher = _fn_with_mock_client()
    try:
        pages = [
            {"data": [_invoice(100, id_="in_1")], "has_more": True},
            {"data": [_invoice(200, id_="in_2")], "has_more": False},
        ]
        cls.return_value.list_invoices.side_effect = pages
        out = _tool(m, "stripe_invoice")(op="totals")
        assert out["count"] == 2
        assert out["by_currency"]["eur"]["total"] == 300
        # la 2e page part APRÈS le dernier id de la 1re
        assert cls.return_value.list_invoices.call_args_list[1].kwargs[
            "starting_after"] == "in_1"
    finally:
        patcher.stop()


def test_totals_declares_itself_incomplete_rather_than_lying():
    """Une somme partielle présentée comme le chiffre d'affaires est le pire
    résultat possible : le plafond doit être VISIBLE dans la réponse."""
    m, cls, patcher = _fn_with_mock_client()
    try:
        cls.return_value.list_invoices.return_value = {
            "data": [_invoice(100, id_="in_1")], "has_more": True}
        out = _tool(m, "stripe_invoice")(op="totals")
        assert out["complete"] is False
        assert "INCOMPLET" in out["note"]
        assert cls.return_value.list_invoices.call_count == stripe._AGGREGATE_MAX_PAGES
    finally:
        patcher.stop()


def test_totals_refuses_write_and_targeting_fields():
    m, cls, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="op='totals' n'utilise pas"):
            _tool(m, "stripe_invoice")(op="totals", invoice_id="in_1")
    finally:
        patcher.stop()


# --- recherche ----------------------------------------------------------------

def test_search_passes_the_query_through():
    m, cls, patcher = _fn_with_mock_client()
    try:
        _tool(m, "stripe_search")(resource="customers", query='email~"acme.com"')
        cls.return_value.search.assert_called_once_with(
            "customers", 'email~"acme.com"', limit=100, page=None)
    finally:
        patcher.stop()


# --- catalogue : l'immuabilité du montant d'un prix ----------------------------

def test_update_price_refuses_a_new_amount_and_says_what_to_do():
    m, cls, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="immuable"):
            _tool(m, "stripe_catalog")(op="update_price", price_id="price_1",
                                       unit_amount=2500)
        cls.return_value.update_price.assert_not_called()
    finally:
        patcher.stop()


def test_create_price_requires_the_three_fields_stripe_needs():
    m, cls, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="requiert `product_id`"):
            _tool(m, "stripe_catalog")(op="create_price", unit_amount=1900)
        _tool(m, "stripe_catalog")(op="create_price", product_id="prod_1",
                                   unit_amount=1900, currency="eur",
                                   recurring_interval="month")
        assert cls.return_value.create_price.call_args.kwargs["recurring"] == {
            "interval": "month"}
    finally:
        patcher.stop()


def test_checkout_create_link_requires_a_price():
    m, cls, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="requiert `price_id`"):
            _tool(m, "stripe_checkout")(op="create_link")
        _tool(m, "stripe_checkout")(op="create_link", price_id="price_1", quantity=3)
        cls.return_value.create_payment_link.assert_called_once()
        items = cls.return_value.create_payment_link.call_args[0][0]
        assert items == [{"price": "price_1", "quantity": 3}]
    finally:
        patcher.stop()


# --- messages d'erreur : actionnables, pas génériques --------------------------

def test_missing_tax_code_error_tells_the_agent_how_to_fix_it():
    """Vécu en live : `create_link` rend un 400 « the product tax code is
    missing » sur un compte éligible aux paiements gérés. Le message doit porter
    le correctif, sinon l'agent abandonne."""
    from oto.tools.common.errors import UpstreamHTTPError
    msg = stripe._upstream_message(UpstreamHTTPError(400, {
        "error": {"message": "Invalid line_items[0]: the product tax code is missing.",
                  "code": None}}, service="stripe"))
    assert "tax_code" in msg and "update_product" in msg


def test_auth_error_mentions_restricted_key_permissions():
    from oto.tools.common.errors import UpstreamHTTPError
    msg = stripe._upstream_message(UpstreamHTTPError(403, {
        "error": {"message": "no perms"}, "request_id": "req_1"}, service="stripe"))
    assert "RESTREINTE" in msg
    assert "req_1" in msg, "le request_id est ce que le support Stripe demande en premier"


def test_not_found_error_mentions_the_test_live_split():
    from oto.tools.common.errors import UpstreamHTTPError
    msg = stripe._upstream_message(UpstreamHTTPError(404, {
        "error": {"message": "No such customer"}}, service="stripe"))
    assert "MODE" in msg
