"""Dispatch `op=` des tools `forager_*` (6 tools, ADR 0047 : un objet métier, `op=` en verbe).

Trois familles de garanties, même patron que `test_silae_op_dispatch.py` :
  1. chaque op → la bonne méthode du client `ForagerClient`, avec les bons args ;
  2. les refus : op inconnue, argument obligatoire manquant nommant l'op, argument
     fourni mais non utilisé par l'op (silencieusement ignoré = résultat crédible à
     côté de la demande — la classe de bug que `_refuse_ignored` existe pour barrer) ;
  3. **la surface exacte** = 6 tools, et **aucun tool `api_keys_*`/`forager_api_key`**
     n'apparaît — la gestion de clé API reste dashboard-only par conception (secret
     brut jamais en argument MCP), pas un oubli à combler plus tard.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.forager.ForagerClient", lambda **kw: inst)
    monkeypatch.setattr(
        "oto_mcp.access.resolve_credential_fields",
        lambda provider: {"api_key": "fg-test-key", "account_id": None},
    )
    return inst


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import forager as F

    m = FastMCP("t")
    F.register(m)
    return asyncio.run(m.get_tool(name)).fn


def test_the_surface_is_exactly_the_six_tools(client):
    from fastmcp import FastMCP
    from oto_mcp.tools import forager as F

    m = FastMCP("t")
    F.register(m)
    assert sorted(t.name for t in asyncio.run(m.list_tools())) == [
        "forager_account", "forager_autocomplete", "forager_feedback",
        "forager_job_post", "forager_organization", "forager_person",
    ]


# --- job posts ------------------------------------------------------------

def test_job_post_search_default(client):
    _tool("forager_job_post")(filters={"title": "engineer"})
    client.search_job_posts.assert_called_once_with(title="engineer")


def test_job_post_totals(client):
    _tool("forager_job_post")(op="totals", filters={"is_remote": True})
    client.search_job_posts_totals.assert_called_once_with(is_remote=True)


def test_job_post_no_filters_passes_empty_dict(client):
    _tool("forager_job_post")()
    client.search_job_posts.assert_called_once_with()


def test_job_post_bad_op(client):
    with pytest.raises(McpError, match="op doit être"):
        _tool("forager_job_post")(op="bogus")


# --- organizations (+ website folded in) -----------------------------------

def test_organization_search(client):
    _tool("forager_organization")(filters={"locations": [1, 2]})
    client.search_organizations.assert_called_once_with(locations=[1, 2])


def test_organization_totals(client):
    _tool("forager_organization")(op="totals")
    client.search_organizations_totals.assert_called_once_with()


def test_organization_website_by_domain(client):
    _tool("forager_organization")(op="website", domain="acme.com")
    client.lookup_website.assert_called_once_with(
        domain="acme.com", organization_id=None, organization_linkedin_public_identifier=None,
    )


def test_organization_website_requires_exactly_one_identifier(client):
    with pytest.raises(McpError, match="exactement un"):
        _tool("forager_organization")(op="website")
    with pytest.raises(McpError, match="exactement un"):
        _tool("forager_organization")(op="website", domain="acme.com", organization_id=5)
    client.lookup_website.assert_not_called()


def test_organization_search_refuses_website_params(client):
    with pytest.raises(McpError, match="op='search' n'utilise pas domain"):
        _tool("forager_organization")(domain="acme.com")
    client.search_organizations.assert_not_called()


def test_organization_website_refuses_filters(client):
    with pytest.raises(McpError, match="n'utilise pas filters"):
        _tool("forager_organization")(op="website", domain="acme.com", filters={"page": 2})
    client.lookup_website.assert_not_called()


# --- people ------------------------------------------------------------

@pytest.mark.parametrize("op,method", [
    ("detail", "lookup_person_detail"),
    ("work_emails", "lookup_person_work_emails"),
    ("personal_emails", "lookup_person_personal_emails"),
    ("phone_numbers", "lookup_person_phone_numbers"),
])
def test_person_lookup_ops_route_to_the_right_method(client, op, method):
    _tool("forager_person")(op=op, person_id=99)
    getattr(client, method).assert_called_once()


def test_person_lookup_requires_exactly_one_identifier(client):
    with pytest.raises(McpError, match="exactement un"):
        _tool("forager_person")(op="detail")
    with pytest.raises(McpError, match="exactement un"):
        _tool("forager_person")(op="detail", person_id=1, linkedin_public_identifier="janedoe")


def test_person_reverse_by_email(client):
    _tool("forager_person")(op="reverse_by_email", email="jane@acme.com")
    client.lookup_person_by_email.assert_called_once_with("jane@acme.com")


def test_person_reverse_by_email_requires_email(client):
    with pytest.raises(McpError, match="op='reverse_by_email' requiert email"):
        _tool("forager_person")(op="reverse_by_email")
    client.lookup_person_by_email.assert_not_called()


def test_person_reverse_by_phone(client):
    _tool("forager_person")(op="reverse_by_phone", phone_number="+15551234567")
    client.lookup_person_by_phone_number.assert_called_once_with("+15551234567")


def test_person_do_contacts_enrichment_only_on_work_emails(client):
    with pytest.raises(McpError, match="do_contacts_enrichment ne s'applique qu'à op='work_emails'"):
        _tool("forager_person")(op="detail", person_id=1, do_contacts_enrichment=True)
    client.lookup_person_detail.assert_not_called()


def test_person_role_search(client):
    _tool("forager_person")(op="role_search", filters={"role_is_current": True})
    client.search_person_roles.assert_called_once_with(role_is_current=True)


def test_person_role_search_totals(client):
    _tool("forager_person")(op="role_search_totals")
    client.search_person_roles_totals.assert_called_once_with()


def test_person_role_search_refuses_identifier_params(client):
    """The refusal must name what was actually refused (`person_id`) and a
    hint the caller can act on — not a garbled reference to `filters`, which
    wasn't even passed here."""
    with pytest.raises(McpError, match=r"op='role_search' n'utilise pas person_id — sélectionne par filtres"):
        _tool("forager_person")(op="role_search", person_id=1)
    client.search_person_roles.assert_not_called()


def test_person_detail_do_contacts_enrichment_false_is_still_refused(client):
    """`False` is a real, meaningful value here (not "unset") — the refusal
    guard must not treat it as absent via a truthiness check."""
    with pytest.raises(McpError, match="do_contacts_enrichment ne s'applique qu'à op='work_emails'"):
        _tool("forager_person")(op="detail", person_id=1, do_contacts_enrichment=False)
    client.lookup_person_detail.assert_not_called()


def test_person_bad_op(client):
    with pytest.raises(McpError, match="op doit être"):
        _tool("forager_person")(op="bogus", person_id=1)


# --- feedback ------------------------------------------------------------

def test_feedback_personal_email(client):
    _tool("forager_feedback")(op="personal_email", contact_status="valid",
                               is_correct_person=True, email="jane@gmail.com")
    client.submit_personal_email_feedback.assert_called_once_with(
        "jane@gmail.com", "valid", True, name=None, person_id=None,
    )


def test_feedback_work_email(client):
    _tool("forager_feedback")(op="work_email", contact_status="invalid",
                               is_correct_person=False, email="jane@acme.com", person_id=5)
    client.submit_work_email_feedback.assert_called_once_with(
        "jane@acme.com", "invalid", False, name=None, person_id=5,
    )


def test_feedback_phone_number(client):
    _tool("forager_feedback")(op="phone_number", contact_status="connected",
                               is_correct_person=True, phone_number="+15551234567")
    client.submit_phone_number_feedback.assert_called_once_with(
        "+15551234567", "connected", True, name=None, person_id=None,
    )


def test_feedback_email_ops_require_email(client):
    with pytest.raises(McpError, match="requiert email"):
        _tool("forager_feedback")(op="personal_email", contact_status="valid", is_correct_person=True)
    client.submit_personal_email_feedback.assert_not_called()


def test_feedback_phone_number_refuses_email_param(client):
    with pytest.raises(McpError, match="n'utilise pas email"):
        _tool("forager_feedback")(
            op="phone_number", contact_status="connected", is_correct_person=True,
            phone_number="+1", email="jane@acme.com",
        )
    client.submit_phone_number_feedback.assert_not_called()


def test_feedback_bad_op(client):
    with pytest.raises(McpError, match="op doit être"):
        _tool("forager_feedback")(op="bogus", contact_status="valid", is_correct_person=True)


# --- autocomplete ------------------------------------------------------------

@pytest.mark.parametrize("op", [
    "industries", "organizations", "organization_keywords",
    "locations", "person_skills", "web_technologies",
])
def test_autocomplete_ops_route_to_the_right_method(client, op):
    getattr(client, f"autocomplete_{op}").return_value = {"results": []}
    _tool("forager_autocomplete")(op=op, q="Paris")
    getattr(client, f"autocomplete_{op}").assert_called_once_with("Paris", page=None)


def test_autocomplete_passes_page(client):
    client.autocomplete_locations.return_value = {"results": []}
    _tool("forager_autocomplete")(op="locations", q="Paris", page=2)
    client.autocomplete_locations.assert_called_once_with("Paris", page=2)


# --- account ------------------------------------------------------------

def test_account_me(client):
    _tool("forager_account")()
    client.get_current_user.assert_called_once()


def test_account_me_refuses_date_range(client):
    with pytest.raises(McpError, match="op='me' n'a pas de plage de dates"):
        _tool("forager_account")(date_created_start="2026-01-01")
    client.get_current_user.assert_not_called()


def test_account_balance_log(client):
    _tool("forager_account")(op="balance_log", date_created_start="2026-01-01", page=2)
    client.list_balance_change_logs.assert_called_once_with(
        date_created_start="2026-01-01", date_created_end=None, page=2,
    )


def test_account_balance_totals(client):
    _tool("forager_account")(op="balance_totals", date_created_start="2026-01-01")
    client.get_balance_change_totals.assert_called_once_with(
        date_created_start="2026-01-01", date_created_end=None,
    )


def test_account_balance_totals_refuses_page(client):
    with pytest.raises(McpError, match="page ne s'applique pas"):
        _tool("forager_account")(op="balance_totals", page=1)
    client.get_balance_change_totals.assert_not_called()


def test_account_bad_op(client):
    with pytest.raises(McpError, match="op doit être"):
        _tool("forager_account")(op="bogus")


# --- multi-account refusal surfaces as a handled McpError, not a raw crash --

def test_multi_account_refusal_surfaces_as_invalid_params(monkeypatch):
    """`ForagerClient.resolve_account_id()` raises a plain `ValueError` when
    the key has access to several accounts and none was pinned. That's a
    caller-fixable input problem — it must reach the agent as `McpError`
    (`INVALID_PARAMS`), not propagate as an unhandled exception (which would
    land in Sentry as a backend defect, per this repo's own guide)."""
    inst = MagicMock()
    inst.search_job_posts.side_effect = ValueError(
        "forager: this API key has access to multiple accounts (1 (A), 2 (B)) — "
        "pass account_id explicitly, refusing to guess which one to bill"
    )
    monkeypatch.setattr("oto.tools.forager.ForagerClient", lambda **kw: inst)
    monkeypatch.setattr(
        "oto_mcp.access.resolve_credential_fields",
        lambda provider: {"api_key": "fg-test-key", "account_id": None},
    )
    with pytest.raises(McpError, match="multiple accounts"):
        _tool("forager_job_post")()


# --- API-key management is deliberately absent ------------------------------

def test_no_api_key_management_tool_exists(client):
    """`GET/POST /api/api_keys/` + `GET/DELETE /api/api_keys/{prefix}/` are real
    spec endpoints — deliberately unwrapped (dashboard-only, secret brut jamais
    en argument MCP). This is the tripwire: a future contributor adding a
    convenience wrapper for them should see this test and reconsider."""
    from fastmcp import FastMCP
    from oto_mcp.tools import forager as F

    m = FastMCP("t")
    F.register(m)
    names = {t.name for t in asyncio.run(m.list_tools())}
    assert not any("api_key" in n for n in names)
