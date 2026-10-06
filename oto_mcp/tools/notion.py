"""Notion — pages, databases, blocks (read + write).

Wraps `oto.tools.notion.lib.notion_client.NotionClient`. Integration token
resolved per call via `access.resolve_api_key("notion")` — byo. **Disk cache
disabled** (`cache_enabled=False`): the file cache is not keyed by token
→ cross-user leak on a multi-user host.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `GET /v1/users/me` ("Retrieve your token's bot user"). What the Notion
    docs establish:

    - **authenticated** — Bearer token (integration token), like the rest of
      the API;
    - **no side effects** — a read of the bot user tied to the token;
    - **the cost** — no mention of any particular cost or rate limit
      for this call. Absence of mention is a hint, not proof.

    **Authenticated ≠ usable** (oto#69 class): does not distinguish any granular
    scope here — Notion grants no PER-CAPABILITY permissions on an integration
    token, only SHARING per page/database (workspace side, invisible from the
    API). `cache_enabled=False`, like `_client()`: the disk cache is not keyed
    by token (see the module docstring).
    """
    from oto.tools.notion.lib.notion_client import NotionClient

    infos = NotionClient(token=fields["key"], cache_enabled=False)._request(
        "GET", "users/me", use_cache=False) or {}
    if not infos.get("id"):
        raise RuntimeError(
            "Notion answered without identifying a bot user for this token — "
            f"unexpected response: {str(infos)[:200]}")


def _zero_warning(query: str, filter_type: Optional[str],
                  edited_on: Optional[str] = None) -> str:
    """The warning that an EMPTY `notion_search` carries (otomata-tech/oto#184).

    A valid token to which nothing is shared answers EXACTLY like a workspace that
    does not contain what is searched (`results: []`), and the `_verify` probe stays
    green (it only sees authentication). The knowledge existed twice —
    install doc, probe comment — never where the agent reads: so we
    carry it in the response, at the moment of the zero.

    ⚠️ Worded as a POSSIBILITY TO CHECK, not as a diagnosis: the
    reading "empty query + zero objects ⟹ nothing shared" has not been tested
    against the Notion API, and a wrong diagnosis would send people to fix a healthy share."""
    geste = ("share the desired page or database with the integration, in the "
             "Notion workspace (`...` menu → Connections)")
    if edited_on:
        return (
            f"Zero objects edited on {edited_on} (UTC day). On Notion, this zero does "
            "NOT distinguish \"nothing changed that day\" from \"nothing is shared with "
            "the integration\". To tell: rerun `notion_search` with `query=\"\"`, "
            "without `filter_type` or `edited_on` — if it also returns zero, the integration "
            f"probably sees nothing: {geste}.")
    if query or filter_type:
        return (
            "Zero results. On Notion, a zero does NOT distinguish \"nothing "
            "matches\" from \"nothing is shared with the integration\" (the token "
            "authenticates in both cases, the probe stays green). To tell: "
            "rerun `notion_search` with `query=\"\"` and without `filter_type` — if "
            f"it also returns zero, the integration probably sees nothing: "
            f"{geste}.")
    return (
        "Zero objects on a search WITHOUT a filter: the integration "
        "probably sees nothing — no page or database is shared with it (the token "
        "authenticates, the probe stays green, and Notion does not say so). To check "
        f"before acting, then {geste}. It is NOT a credential to reset.")


# `notion_get_markdown` answers at most this many characters of Markdown.
_MAX_MARKDOWN = 100_000


def _invalid_params(call):
    """Run `call`; a ValueError from the client becomes INVALID_PARAMS with its message.

    The client raises ValueError only for the caller's arguments: an id that is
    not a Notion id, the wrong kind of object (`NotionIdKindError` — a page given
    as a database, a database with several data sources, listed), a missing or
    conflicting parameter. A non-JSON answer from Notion is a `NotionAPIError`,
    not a ValueError, so it is never presented as a bad argument."""
    try:
        return call()
    except ValueError as e:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e))) from e


class _Client:
    """The Notion client with every call passed through `_invalid_params`: no
    tool can forget it, and an argument error never surfaces as an internal one."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            return _invalid_params(lambda: attr(*args, **kwargs))
        return call


def _bounded_markdown(result: dict) -> dict:
    """At most `_MAX_MARKDOWN` characters of `markdown`, saying so when cut."""
    text = result.get("markdown") if isinstance(result, dict) else None
    if isinstance(text, str) and len(text) > _MAX_MARKDOWN:
        return {**result, "markdown": text[:_MAX_MARKDOWN], "oto_truncated": True,
                "markdown_chars": len(text)}
    return result


def register(mcp: FastMCP) -> None:
    from oto.tools.notion.lib.notion_client import NotionClient

    connector_verify.register("notion", _verify)

    def _client() -> NotionClient:
        # Declared as `NotionClient` (same methods, what the version-skew probe reads),
        # returned wrapped: every call goes through `_invalid_params`.
        key, _ = access.resolve_api_key("notion")
        return _Client(NotionClient(token=key, cache_enabled=False))

    @mcp.tool()
    def notion_search(
        query: str,
        filter_type: Optional[str] = None,
        sort: str = "relevance",
        edited_on: Optional[str] = None,
        cursor: Optional[str] = None,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Search the workspace (pages + databases shared with the integration).

        The integration sees ONLY what was shared with it in Notion: an empty
        `results` may mean "nothing shared", not "nothing matches". An empty
        answer carries a `warning` saying how to tell the two apart.

        Returns ONE page (at most 100 objects). `has_more: true` means there is
        more: pass the answer's `next_cursor` back as `cursor`, same other args.

        `edited_on` answers "what changed that day": EVERY object whose
        `last_edited_time` falls on that UTC calendar day, most recent first,
        in one answer (walked server-side until the day is passed — cost tracks
        what changed, not workspace size). `sort` and `cursor` do not apply then.

        `fields` keeps only these keys in each object; the envelope
        (`has_more`, `next_cursor`) always stays.

        Args:
            query: text to match; "" lists everything the integration can see.
            filter_type: "page" or "database" to restrict object type
                (databases come back as `data_source` objects).
            sort: "relevance" (default) or "last_edited_time".
            edited_on: "YYYY-MM-DD" (UTC day) — only objects last edited that day.
            cursor: `next_cursor` of the previous answer, to read the next page.
            fields: keys kept in each object (e.g. ["id", "url", "last_edited_time"]).
        """
        client = _client()
        if edited_on and cursor:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=(
                "`edited_on` already returns the whole day in one answer: it is not "
                "paginated, remove `cursor`.")))
        if edited_on:
            try:
                objets = client.search_edited_on(
                    edited_on, filter_type=filter_type, query=query)
            except ValueError as e:  # malformed date — the method's only ValueError
                raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e))) from e
            result = {"results": objets, "edited_on": edited_on}
        else:
            result = client.search(query, filter_type=filter_type, sort=sort,
                                   start_cursor=cursor)
        if not result.get("results"):
            result = {**result,
                      "warning": _zero_warning(query, filter_type, edited_on)}
        return output_projection.project(result, items_path="results", fields=fields)

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
        """Get a database's schema: its columns (`properties`) and data sources.

        `database_id` may be a database id or a data source id (what search returns).
        """
        return _client().get_database(database_id)

    @mcp.tool()
    def notion_query_database(
        database_id: str,
        filter_obj: Optional[dict] = None,
        sorts: Optional[list] = None,
        page_size: int = 100,
        cursor: Optional[str] = None,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Query a database's rows — one page (`has_more` + `next_cursor`).

        `database_id` may be a database id or a data source id.

        Args:
            filter_obj: Notion filter object (e.g. {"property": "Status",
                "select": {"equals": "Done"}}).
            sorts: Notion sorts array.
            cursor: `next_cursor` of the previous answer, to read the next page.
            fields: keys kept in each row (e.g. ["id", "url", "properties"]).
        """
        result = _client().query_database(
            database_id, filter_obj=filter_obj, sorts=sorts, page_size=page_size,
            start_cursor=cursor)
        return output_projection.project(result, items_path="results", fields=fields)

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
            parent_type: "page" or "database". For "database", `parent_id`
                may be a database id or a data source id — never a linked
                view (use the source database).
            title: goes in the database's title column, whatever its name.
            properties: extra Notion property values (db rows: keyed by column).
            content: optional array of Notion block objects for the body
                (any length).
        """
        return _client().create_page(
            parent_id, parent_type, title, properties=properties, content=content)

    @mcp.tool()
    def notion_update_page(
        page_id: str,
        properties: Optional[dict] = None,
        in_trash: Optional[bool] = None,
        icon: Optional[dict] = None,
        cover: Optional[dict] = None,
        archived: Optional[bool] = None,
    ) -> dict:
        """Update a page's properties, icon or cover, or trash/restore it.

        To move a page elsewhere, use notion_move_page.

        Args:
            in_trash: true moves the page to the trash, false restores it.
            icon: e.g. {"type": "emoji", "emoji": "📝"}.
            archived: old name of `in_trash`.
        """
        return _client().update_page(page_id, properties=properties, archived=archived,
                                     in_trash=in_trash, icon=icon, cover=cover)

    @mcp.tool()
    def notion_move_page(page_id: str, parent_id: str, parent_type: str = "page") -> dict:
        """Move a page under another page, or into a database.

        Args:
            parent_type: "page", or "database" (a database or data source id).
        """
        return _client().move_page(page_id, parent_id, parent_type)

    @mcp.tool()
    def notion_append_blocks(
        page_id: str, blocks: list, position: Optional[str] = None,
    ) -> dict:
        """Add block objects to a page/block — any number, sent in batches.

        Args:
            position: "end" (default), "start" (top of the page), or the id of
                an existing child block to insert right after it.
        """
        return _client().append_blocks(page_id, blocks, position=position)

    @mcp.tool()
    def notion_update_block(block_id: str, block: dict) -> dict:
        """Edit one block in place, e.g. {"paragraph": {"rich_text": [...]}} or
        {"to_do": {"checked": true}}. Its children cannot be edited this way."""
        return _client().update_block(block_id, block)

    @mcp.tool()
    def notion_delete_block(block_id: str) -> dict:
        """Move one block to the trash (restorable from Notion)."""
        return _client().delete_block(block_id)

    @mcp.tool()
    def notion_get_markdown(page_id: str) -> dict:
        """Get a page's whole content as Markdown — lighter than notion_get_blocks.

        `truncated: true`: some parts were not loaded; their ids are in
        `unknown_block_ids` and each can be read the same way. Over 100 000
        characters the Markdown is cut: `oto_truncated: true` and
        `markdown_chars` (read the page by blocks then).
        """
        return _bounded_markdown(_client().get_markdown(page_id))

    @mcp.tool()
    def notion_edit_markdown(
        page_id: str,
        replacements: Optional[list[dict]] = None,
        new_content: Optional[str] = None,
        insert: Optional[str] = None,
        position: str = "end",
        allow_deleting_content: bool = False,
    ) -> dict:
        """Edit a page's content as Markdown — give exactly ONE of the three modes.

        Args:
            replacements: targeted edits, [{"old_str": ..., "new_str": ...,
                "replace_all_matches": false}] (max 100). Each `old_str` must
                match exactly once unless `replace_all_matches`.
            new_content: replaces the WHOLE page content.
            insert: Markdown added at `position` ("start" or "end").
            allow_deleting_content: required if the edit removes child pages
                or databases (refused otherwise).
        """
        return _client().edit_markdown(
            page_id, replacements=replacements, new_content=new_content,
            insert=insert, position=position,
            allow_deleting_content=allow_deleting_content)

    @mcp.tool()
    def notion_get_comments(
        page_id: str, cursor: Optional[str] = None, fields: Optional[list[str]] = None,
    ) -> dict:
        """List the open comments on a page or block (`page_id` takes a block id too).

        Each comment carries its `discussion_id` — pass it to notion_add_comment
        to reply in the thread. The integration needs the "read comments"
        capability (off by default in Notion).
        """
        result = _client().list_comments(page_id, start_cursor=cursor)
        return output_projection.project(result, items_path="results", fields=fields)

    @mcp.tool()
    def notion_add_comment(
        text: str,
        page_id: Optional[str] = None,
        block_id: Optional[str] = None,
        discussion_id: Optional[str] = None,
    ) -> dict:
        """Comment on a page or a block, or reply in an existing discussion.

        Give exactly one target. `text` is Markdown (inline formatting only).
        The integration needs the "insert comments" capability (off by default).
        """
        return _client().add_comment(
            text, page_id=page_id, block_id=block_id, discussion_id=discussion_id)

    @mcp.tool()
    def notion_edit_comment(
        comment_id: str, text: Optional[str] = None, delete: bool = False,
    ) -> dict:
        """Rewrite (`text`) or delete (`delete=true`) a comment the integration wrote."""
        client = _client()
        if delete:
            return client.delete_comment(comment_id)
        if not text:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=(
                "Give `text` to rewrite the comment, or `delete=true`.")))
        return client.update_comment(comment_id, text)

    @mcp.tool()
    def notion_create_database(
        parent_page_id: str,
        title: Optional[str] = None,
        properties: Optional[dict] = None,
        is_inline: bool = False,
        database_type: Optional[str] = None,
    ) -> dict:
        """Create a database inside a page.

        Args:
            properties: column schema, e.g. {"Name": {"title": {}},
                "Status": {"select": {"options": [{"name": "Todo"}]}},
                "Due": {"date": {}}}. A "Name" title column is added if none.
            is_inline: show it inside the page rather than as a sub-page.
            database_type: "tasks", "projects" or "skills" for Notion's own
                schema (instead of `properties`).
        """
        return _client().create_database(
            parent_page_id, title=title, properties=properties, is_inline=is_inline,
            database_type=database_type)

    @mcp.tool()
    def notion_update_database(
        database_id: str,
        properties: Optional[dict] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        in_trash: Optional[bool] = None,
    ) -> dict:
        """Change a database's columns, title or description, or trash it.

        `database_id` may be a database id or a data source id. Read the
        current columns first with notion_get_database.

        Args:
            properties: column changes — add {"Due": {"date": {}}}, rename
                {"Due": {"name": "Deadline"}}, remove {"Due": null}, change
                type {"Estimate": {"number": {}}}. Select/status options: give
                the FULL list to keep ({"id": ...} or {"name": ..., "color": ...}).
        """
        return _client().update_database(
            database_id, properties=properties, title=title,
            description=description, in_trash=in_trash)

    @mcp.tool()
    def notion_view(
        op: str,
        database_id: Optional[str] = None,
        view_id: Optional[str] = None,
        name: Optional[str] = None,
        view_type: str = "table",
        filter_obj: Optional[dict] = None,
        sorts: Optional[list] = None,
        configuration: Optional[dict] = None,
        clear: Optional[list[str]] = None,
        cursor: Optional[str] = None,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """Database views (tabs): list, read, create, change, delete.

        op=list (`database_id`) / get (`view_id`) / create (`database_id`,
        `name`, `view_type`, optional `filter_obj`, `sorts`, `configuration`) /
        update (`view_id` + any of `name`, `filter_obj`, `sorts`,
        `configuration`; `clear=["filter"|"sorts"]` resets them) /
        delete (`view_id`; a database's last view cannot be deleted).

        Args:
            view_type: table, board, list, calendar, timeline, gallery, form,
                chart, map or dashboard.
            filter_obj / sorts: same format as notion_query_database.
            configuration: type-specific settings (shown columns, group by…)
                as in the `configuration` of a view read with op=get.
        """
        client = _client()

        def need(value, label):
            if not value:
                raise McpError(ErrorData(code=INVALID_PARAMS,
                                         message=f"op={op} needs `{label}`."))
            return value

        if op == "list":
            result = client.list_views(need(database_id, "database_id"), start_cursor=cursor)
            return output_projection.project(result, items_path="results", fields=fields)
        if op == "get":
            return client.get_view(need(view_id, "view_id"))
        if op == "create":
            return client.create_view(
                need(database_id, "database_id"), need(name, "name"), view_type,
                filter_obj=filter_obj, sorts=sorts, configuration=configuration)
        if op == "update":
            return client.update_view(
                need(view_id, "view_id"), name=name, filter_obj=filter_obj,
                sorts=sorts, configuration=configuration, clear=clear)
        if op == "delete":
            return client.delete_view(need(view_id, "view_id"))
        raise McpError(ErrorData(code=INVALID_PARAMS, message=(
            f"op {op!r}: expected list, get, create, update or delete.")))

    @mcp.tool()
    def notion_list_users(
        cursor: Optional[str] = None, fields: Optional[list[str]] = None,
    ) -> dict:
        """Workspace members, guests and bots — ids for people properties and mentions."""
        result = _client().list_users(start_cursor=cursor)
        return output_projection.project(result, items_path="results", fields=fields)
