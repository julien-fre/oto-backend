"""Notion — pages, databases, blocks (read + write).

Wrappe `oto.tools.notion.lib.notion_client.NotionClient`. Token d'intégration
résolu par appel via `access.resolve_api_key("notion")` — byo. **Cache disque
désactivé** (`cache_enabled=False`) : le cache fichier n'est pas clefé par token
→ fuite cross-user sur un host multi-utilisateur.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » — otomata-tech/oto#69. Couvre `auth` SEUL.

    `GET /v1/users/me` (« Retrieve your token's bot user »). Ce que la doc
    Notion établit :

    - **authentifié** — Bearer token (jeton d'intégration), comme le reste de
      l'API ;
    - **sans effet de bord** — une lecture du bot user associé au jeton ;
    - **le coût** — aucune mention de coût ni de limite de débit particulière
      pour cet appel. Absence de mention, indice, pas une preuve.

    **Authentifié ≠ utilisable** (classe oto#69) : ne distingue pas de scope
    granulaire ici — Notion n'accorde pas de permissions PAR CAPACITÉ sur un
    jeton d'intégration, seulement un PARTAGE par page/base (côté workspace,
    invisible depuis l'API). `cache_enabled=False`, comme `_client()` : le
    cache disque n'est pas clefé par jeton (cf. docstring du module).
    """
    from oto.tools.notion.lib.notion_client import NotionClient

    infos = NotionClient(token=fields["key"], cache_enabled=False)._request(
        "GET", "users/me", use_cache=False) or {}
    if not infos.get("id"):
        raise RuntimeError(
            "Notion a répondu sans identifier de bot user pour ce jeton — "
            f"réponse inattendue : {str(infos)[:200]}")


def _zero_warning(query: str, filter_type: Optional[str],
                  edited_on: Optional[str] = None) -> str:
    """L'avertissement qu'un `notion_search` VIDE porte (otomata-tech/oto#184).

    Un jeton valide auquel rien n'est partagé répond EXACTEMENT comme un espace qui
    ne contient pas ce qu'on cherche (`results: []`), et la sonde `_verify` reste
    verte (elle ne voit que l'authentification). Le savoir existait deux fois —
    doc d'installation, commentaire de sonde — jamais là où l'agent lit : on le
    porte donc dans la réponse, au moment du zéro.

    ⚠️ Formulé comme une POSSIBILITÉ À VÉRIFIER, pas comme un diagnostic : la
    lecture « requête vide + zéro objet ⟹ rien de partagé » n'a pas été éprouvée
    contre l'API Notion, et un diagnostic faux enverrait réparer un partage sain."""
    geste = ("partager la page ou la base voulue avec l'intégration, côté "
             "workspace Notion (menu `...` → Connexions)")
    if edited_on:
        return (
            f"Zéro objet édité le {edited_on} (jour UTC). Sur Notion, ce zéro ne "
            "distingue PAS « rien n'a bougé ce jour-là » de « rien n'est partagé avec "
            "l'intégration ». Pour trancher : relance `notion_search` avec `query=\"\"`, "
            "sans `filter_type` ni `edited_on` — si elle rend aussi zéro, l'intégration "
            f"ne voit vraisemblablement rien : {geste}.")
    if query or filter_type:
        return (
            "Zéro résultat. Sur Notion, un zéro ne distingue PAS « rien ne "
            "correspond » de « rien n'est partagé avec l'intégration » (le jeton "
            "s'authentifie dans les deux cas, la sonde reste verte). Pour trancher : "
            "relance `notion_search` avec `query=\"\"` et sans `filter_type` — si "
            f"elle rend aussi zéro, l'intégration ne voit vraisemblablement rien : "
            f"{geste}.")
    return (
        "Zéro objet sur une recherche SANS filtre : l'intégration ne voit "
        "vraisemblablement rien — aucune page ni base ne lui est partagée (le jeton "
        "s'authentifie, la sonde reste verte, et Notion ne le dit pas). À vérifier "
        f"avant d'agir, puis {geste}. Ce n'est PAS un credential à reposer.")


def register(mcp: FastMCP) -> None:
    from oto.tools.notion.lib.notion_client import NotionClient

    connector_verify.register("notion", _verify)

    def _client() -> NotionClient:
        key, _ = access.resolve_api_key("notion")
        return NotionClient(token=key, cache_enabled=False)

    @mcp.tool()
    def notion_search(
        query: str,
        filter_type: Optional[str] = None,
        sort: str = "relevance",
        edited_on: Optional[str] = None,
    ) -> dict:
        """Search the workspace (pages + databases shared with the integration).

        The integration sees ONLY what was shared with it in Notion: an empty
        `results` may mean "nothing shared", not "nothing matches". An empty
        answer carries a `warning` saying how to tell the two apart.

        `edited_on` answers "what changed that day": EVERY object whose
        `last_edited_time` falls on that UTC calendar day, most recent first,
        in one answer (walked server-side until the day is passed — cost tracks
        what changed, not workspace size). `sort` does not apply then.

        Args:
            query: text to match; "" lists everything the integration can see.
            filter_type: "page" or "database" to restrict object type.
            sort: "relevance" (default) or "last_edited_time".
            edited_on: "YYYY-MM-DD" (UTC day) — only objects last edited that day.
        """
        client = _client()
        if edited_on:
            try:
                objets = client.search_edited_on(
                    edited_on, filter_type=filter_type, query=query)
            except ValueError as e:  # date mal formée — le seul ValueError de la méthode
                raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e))) from e
            result = {"results": objets, "edited_on": edited_on}
        else:
            result = client.search(query, filter_type=filter_type, sort=sort)
        if not result.get("results"):
            result = {**result,
                      "warning": _zero_warning(query, filter_type, edited_on)}
        return result

    @mcp.tool()
    def notion_get_page(page_id: str) -> dict:
        """Get a page's metadata/properties (not its block content)."""
        return _client().get_page(page_id)

    @mcp.tool()
    def notion_get_blocks(page_id: str, recursive: bool = False) -> dict:
        """Get a page's block content. `recursive` fetches nested children too."""
        return _client().get_page_blocks(page_id, recursive=recursive)

    @mcp.tool()
    def notion_get_database(database_id: str) -> dict:
        """Get a database's schema (properties + data sources)."""
        return _client().get_database(database_id)

    @mcp.tool()
    def notion_query_database(
        database_id: str,
        filter_obj: Optional[dict] = None,
        sorts: Optional[list] = None,
        page_size: int = 100,
    ) -> dict:
        """Query a database's rows.

        Args:
            filter_obj: Notion filter object (e.g. {"property": "Status",
                "select": {"equals": "Done"}}).
            sorts: Notion sorts array.
        """
        return _client().query_database(
            database_id, filter_obj=filter_obj, sorts=sorts, page_size=page_size)

    @mcp.tool()
    def notion_create_page(
        parent_id: str,
        parent_type: str,
        title: str,
        properties: Optional[dict] = None,
        content: Optional[list] = None,
    ) -> dict:
        """Create a page under a parent.

        Args:
            parent_type: "page" or "database".
            properties: extra Notion property values (db rows: keyed by column).
            content: optional array of Notion block objects for the body.
        """
        return _client().create_page(
            parent_id, parent_type, title, properties=properties, content=content)

    @mcp.tool()
    def notion_update_page(
        page_id: str,
        properties: Optional[dict] = None,
        archived: Optional[bool] = None,
    ) -> dict:
        """Update a page's properties, or archive/unarchive it (`archived`)."""
        return _client().update_page(page_id, properties=properties, archived=archived)

    @mcp.tool()
    def notion_append_blocks(page_id: str, blocks: list) -> dict:
        """Append block objects to a page/block's children."""
        return _client().append_blocks(page_id, blocks)
