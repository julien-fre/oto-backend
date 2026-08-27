"""La FENÊTRE de lecture de `linear_issue op=list`, au niveau du tool.

Deux signaux d'usage, tous deux d'une procédure quotidienne qui lit Linear :
- **#561** — aucune borne de date : « the caller cannot bound the walk safely
  because the sort key is undocumented ». Toute lecture d'un jour donné devait
  paginer tout le workspace et jeter côté client.
- **#568** — et l'ordre n'est annoncé nulle part. Relevé le 24/08/2026 contre
  l'org 196 : la liste revient par identifiant décroissant, pas par `updatedAt`,
  donc une issue modifiée la veille peut se trouver sur n'importe quelle page —
  un run ne peut pas s'arrêter à la première page hors fenêtre.

Contrairement à Attio, l'amont sait faire : `IssueFilter` porte déjà `updatedAt`
et `createdAt` en `DateComparator` (SDL `@linear/sdk` 92.0.0), et
`Query.issues(orderBy: PaginationOrderBy)` accepte `createdAt` | `updatedAt`.
Il n'y avait rien à refuser ici, seulement à exposer. Le détail de la requête
GraphQL produite est verrouillé dans oto-core
(`tests/test_linear_issue_window.py`) ; ce test-ci verrouille le contrat du
TOOL : les bornes atteignent le client, et elles sont refusées sur les ops qui
ne listent pas — ce module n'ignore jamais un argument mal placé.
"""
import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setattr(
        "oto_mcp.access.resolve_api_key", lambda provider, account=None: ("k", False))


def _fn_with_mock_client():
    """Même montage que tests/test_linear.py : `register()` fait son
    `from ... import LinearClient` à l'appel, donc le patch doit être actif
    pendant l'enregistrement."""
    from fastmcp import FastMCP
    from oto_mcp.tools import linear

    patcher = patch("oto.tools.linear.client.LinearClient")
    cls = patcher.start()
    m = FastMCP("t")
    linear.register(m)
    fn = asyncio.run(m.get_tool("linear_issue")).fn
    return fn, cls.return_value, patcher


def test_les_quatre_bornes_atteignent_le_client():
    fn, client, patcher = _fn_with_mock_client()
    try:
        fn(op="list", updated_after="2026-08-24T00:00:00Z",
           updated_before="2026-08-25T00:00:00Z",
           created_after="2026-08-01T00:00:00Z",
           created_before="2026-08-27T00:00:00Z")
        kwargs = client.list_issues.call_args.kwargs
        assert kwargs["updated_after"] == "2026-08-24T00:00:00Z"
        assert kwargs["updated_before"] == "2026-08-25T00:00:00Z"
        assert kwargs["created_after"] == "2026-08-01T00:00:00Z"
        assert kwargs["created_before"] == "2026-08-27T00:00:00Z"
    finally:
        patcher.stop()


def test_order_by_atteint_le_client():
    fn, client, patcher = _fn_with_mock_client()
    try:
        fn(op="list", order_by="updatedAt")
        assert client.list_issues.call_args.kwargs["order_by"] == "updatedAt"
    finally:
        patcher.stop()


@pytest.mark.parametrize("arg", [
    "updated_after", "updated_before", "created_after", "created_before", "order_by"])
def test_une_borne_sur_un_op_qui_ne_liste_pas_est_refusee_en_la_nommant(arg):
    """`_only` est le contrat du module : un argument hors de l'allow-list de CET
    op lève en le nommant, au lieu d'être silencieusement ignoré."""
    fn, _client, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match=arg):
            fn(op="get", issue_id="OTO-43", **{arg: "2026-08-24T00:00:00Z"})
    finally:
        patcher.stop()


def test_la_recherche_plein_texte_refuse_les_bornes():
    """`search` passe par `issues(filter: {searchableContent:…})`, un chemin qui
    ne construit pas les bornes : les accepter serait les ignorer."""
    fn, _client, patcher = _fn_with_mock_client()
    try:
        with pytest.raises(McpError, match="updated_after"):
            fn(op="search", query="bug", updated_after="2026-08-24T00:00:00Z")
    finally:
        patcher.stop()


def test_sans_borne_le_chemin_nu_ne_change_pas():
    fn, client, patcher = _fn_with_mock_client()
    try:
        fn(op="list")
        kwargs = client.list_issues.call_args.kwargs
        assert all(kwargs[k] is None for k in
                   ("updated_after", "updated_before", "created_after",
                    "created_before", "order_by"))
    finally:
        patcher.stop()
