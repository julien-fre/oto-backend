"""Dispatch `op=` des tools `lucca_*` (ADR 0047 §Amendement, même patron que silae).

Ce que ce fichier verrouille : la SURFACE (6 tools consolidés par objet métier) et le
routage de chaque op vers la bonne méthode du client, avec les arguments requis
nommés dans le refus quand ils manquent, et un argument non pertinent pour l'op
refusé plutôt qu'ignoré en silence (le mode de panne propre à `op=` : un résultat
crédible à côté de la demande).
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError


@pytest.fixture
def client(monkeypatch):
    """Faux `LuccaClient` + credential résolu.

    `register()` fait son `from oto.tools.lucca import LuccaClient` À L'INTÉRIEUR de
    la fonction : patcher l'attribut du package AVANT `_tool()` suffit."""
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.lucca.LuccaClient", lambda **kw: inst)
    monkeypatch.setattr(
        "oto_mcp.access.resolve_credential_fields",
        lambda provider: {"api_key": "key", "domain": "acme"},
    )
    return inst


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import lucca as L

    m = FastMCP("t")
    L.register(m)
    return asyncio.run(m.get_tool(name)).fn


def test_the_surface_is_exactly_the_six_consolidated_tools(client):
    from fastmcp import FastMCP
    from oto_mcp.tools import lucca as L

    m = FastMCP("t")
    L.register(m)
    assert sorted(t.name for t in asyncio.run(m.list_tools())) == [
        "lucca_absence", "lucca_department", "lucca_employee",
        "lucca_establishment", "lucca_expense_claim", "lucca_leave_request",
    ]


# --- annuaire -------------------------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_users"),
    ("get", {"user_id": "42"}, "get_user"),
])
def test_employee_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("lucca_employee")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_employee_defaults_to_the_reachable_list(client):
    _tool("lucca_employee")()
    client.list_users.assert_called_once()


def test_employee_get_requires_user_id(client):
    with pytest.raises(McpError, match="op='get' requiert user_id"):
        _tool("lucca_employee")(op="get")
    client.get_user.assert_not_called()


def test_employee_get_refuses_list_only_arguments(client):
    with pytest.raises(McpError, match="op='get' n'utilise pas mail"):
        _tool("lucca_employee")(op="get", user_id="42", mail="jean@exemple.fr")
    client.get_user.assert_not_called()


# --- absences ---------------------------------------------------------------------

def test_absence_list_requires_date(client):
    """Lucca lui-même exige `date` — pas de « toutes les absences » non filtrées."""
    with pytest.raises(McpError, match="op='list' requiert date"):
        _tool("lucca_absence")()
    client.list_leaves.assert_not_called()


def test_absence_list_routes_with_date(client):
    _tool("lucca_absence")(date="2026-09-01")
    client.list_leaves.assert_called_once()
    assert client.list_leaves.call_args.args == ("2026-09-01",)


def test_absence_list_drops_comment_by_default(client):
    """`comment` (texte libre Lucca, sans limite documentée) est le seul champ
    verbeux confirmé sur cette ressource (vérifié contre l'OpenAPI Lucca,
    2026-09-15) — coupé par défaut, `comment_length` dit combien."""
    client.list_leaves.return_value = [
        {"id": "1", "comment": "x" * 500, "date": "2026-09-01"}]
    out = _tool("lucca_absence")(date="2026-09-01")
    row = out["leaves"][0]
    assert "comment" not in row and row["comment_length"] == 500
    assert out["projection"]["omitted"] == ["comment"]


def test_absence_list_fields_star_restores_the_raw_record(client):
    client.list_leaves.return_value = [
        {"id": "1", "comment": "x" * 500, "date": "2026-09-01"}]
    out = _tool("lucca_absence")(date="2026-09-01", fields=["*"])
    assert out["leaves"][0]["comment"] == "x" * 500
    assert "projection" not in out


def test_absence_get_routes_and_refuses_list_only_arguments(client):
    with pytest.raises(McpError, match="op='get' n'utilise pas date"):
        _tool("lucca_absence")(op="get", leave_id="1", date="2026-09-01")
    client.get_leave.assert_not_called()

    _tool("lucca_absence")(op="get", leave_id="1")
    client.get_leave.assert_called_once_with("1")


# --- demandes de congé -------------------------------------------------------------

def test_leave_request_list_takes_no_argument(client):
    """Lucca ne documente AUCUN paramètre sur cet endpoint — refuser un id fourni
    par erreur sur `op='list'` plutôt que de le laisser tomber en silence."""
    with pytest.raises(McpError, match="op='list' n'utilise pas leave_request_id"):
        _tool("lucca_leave_request")(leave_request_id="1")
    client.list_leave_requests.assert_not_called()

    _tool("lucca_leave_request")()
    client.list_leave_requests.assert_called_once_with()


def test_leave_request_get_requires_id(client):
    with pytest.raises(McpError, match="op='get' requiert leave_request_id"):
        _tool("lucca_leave_request")(op="get")
    client.get_leave_request.assert_not_called()

    _tool("lucca_leave_request")(op="get", leave_request_id="7")
    client.get_leave_request.assert_called_once_with("7")


# --- notes de frais : liste seule, pas d'op ----------------------------------------

def test_expense_claim_has_no_op_parameter(client):
    """Pas de `get` : Lucca n'expose aucun détail par id sur cette ressource — un
    paramètre `op` promettrait un endpoint qui n'existe pas."""
    import inspect

    fn = _tool("lucca_expense_claim")
    assert "op" not in inspect.signature(fn).parameters
    fn()
    client.list_expense_claims.assert_called_once()


def test_expense_claim_forwards_filters(client):
    _tool("lucca_expense_claim")(status_id="Approved", owner_id=[1, 2])
    _, kwargs = client.list_expense_claims.call_args
    assert kwargs["status_id"] == "Approved"
    assert kwargs["owner_id"] == [1, 2]


# --- organisation : départements ---------------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("list", {}, "list_departments"),
    ("get", {"department_id": "5"}, "get_department"),
])
def test_department_ops_route_to_the_right_client_method(client, op, kwargs, method):
    _tool("lucca_department")(op=op, **kwargs)
    getattr(client, method).assert_called_once()


def test_department_get_requires_id(client):
    with pytest.raises(McpError, match="op='get' requiert department_id"):
        _tool("lucca_department")(op="get")
    client.get_department.assert_not_called()


def test_department_list_refuses_get_only_arguments(client):
    with pytest.raises(McpError, match="op='list' n'utilise pas department_id"):
        _tool("lucca_department")(department_id="5")
    client.list_departments.assert_not_called()


def test_department_list_drops_rosters_by_default(client):
    """`users`/`currentUsers` (chaque salarié du département, imbriqué en entier —
    vérifié contre l'OpenAPI Lucca, 2026-09-15) sont coupés par défaut."""
    client.list_departments.return_value = [
        {"id": "5", "name": "RH", "users": [{"id": "1"}, {"id": "2"}],
         "currentUsers": [{"id": "1"}]}]
    out = _tool("lucca_department")()
    row = out["departments"][0]
    assert "users" not in row and "currentUsers" not in row
    assert row["users_length"] == 2 and row["currentUsers_length"] == 1
    assert set(out["projection"]["omitted"]) == {"users", "currentUsers"}


# --- organisation : établissements, liste seule ------------------------------------

def test_establishment_has_no_op_parameter(client):
    """Pas de `get` : Lucca n'expose aucun détail par id sur cette ressource."""
    import inspect

    fn = _tool("lucca_establishment")
    assert "op" not in inspect.signature(fn).parameters
    fn()
    client.list_establishments.assert_called_once()


def test_establishment_paginates_by_page_not_offset(client):
    """⚠️ Seul tool du module à paginer par PAGE (1-indexed) — les cinq autres
    paginent par offset. Une confusion enverrait `offset=` là où Lucca attend `page=`."""
    _tool("lucca_establishment")(page=2, limit=25)
    _, kwargs = client.list_establishments.call_args
    assert kwargs["page"] == 2 and kwargs["limit"] == 25


# --- refus génériques ---------------------------------------------------------------

@pytest.mark.parametrize("tool,kwargs", [
    ("lucca_employee", {}),
    ("lucca_absence", {"date": "2026-09-01"}),
    ("lucca_leave_request", {}),
    ("lucca_department", {}),
])
def test_unknown_op_is_refused_with_the_allowed_list(client, tool, kwargs):
    with pytest.raises(McpError, match="op doit être 'list' ou 'get'"):
        _tool(tool)(op="nope", **kwargs)
