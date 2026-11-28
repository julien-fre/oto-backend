"""OneNote — the notebooks a person reaches on Microsoft 365, via Microsoft Graph
(`OneNoteClient`): her own, a team's (a Microsoft 365 group), a SharePoint site's.

Credential = the PERSON's Microsoft 365 account (carrier `microsoft`, OAuth, delegated
permissions) that has authorized THIS service (scope `NOTES`), acquired and renewed by
`auth/microsoft.py`; several linked accounts are chosen by the generic `_account=` axis.

**Surface** — the pair of every connector (17/10/2026): `onenote` reads (notebooks,
sections, pages, a page's content) — `lecture.LECTURE`; `onenote_act` writes (a page, an
addition to a page, a section, a notebook) — `lecture.ACTE`, the tool the client asks to
confirm. Nothing deletes: removing a page is done in OneNote.

Protocol facts the tools carry (`oto.tools.microsoft.onenote`): every id is valid only
under its root (`group_id` / `site_id` / the person's), so the same root is passed again;
listing pages without a section fails once the person has many sections; a page is read
and written as HTML, changed by commands that target an element id (read with
`format="html"`).
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Literal, Optional

from fastmcp import FastMCP

from .. import output_projection
from ._microsoft_graph import bad, markdown_html, need, raw, refuse_ignored, run, token
from .lecture import ACTE, LECTURE

#: The Microsoft service these tools call: its scopes, its card (`auth/microsoft`).
_SERVICE = "onenote"
_LABEL = "OneNote"

_OPS = ("notebooks", "sections", "pages", "page")
_ACT_OPS = ("create_page", "update_page", "create_section", "create_notebook")


def _ops_error(ops: tuple) -> str:
    return "op must be " + ", ".join(f"'{o}'" for o in ops[:-1]) + f" or '{ops[-1]}'"


def _web(o: dict) -> Optional[str]:
    return ((o.get("links") or {}).get("oneNoteWebUrl") or {}).get("href")


def _parent(o: dict, key: str) -> Optional[dict]:
    p = o.get(key) or None
    return {"id": p.get("id"), "name": p.get("displayName")} if p else None


def _notebook(n: dict) -> dict:
    return {"id": n.get("id"), "name": n.get("displayName"), "isShared": n.get("isShared"),
            "userRole": n.get("userRole"), "lastModified": n.get("lastModifiedDateTime"),
            "webUrl": _web(n)}


def _section(s: dict) -> dict:
    return {"id": s.get("id"), "name": s.get("displayName"),
            "notebook": _parent(s, "parentNotebook"),
            "lastModified": s.get("lastModifiedDateTime")}


def _page(p: dict) -> dict:
    return {"id": p.get("id"), "title": p.get("title"),
            "section": _parent(p, "parentSection"),
            "created": p.get("createdDateTime"), "lastModified": p.get("lastModifiedDateTime"),
            "webUrl": _web(p)}


class _Texte(HTMLParser):
    """A page's HTML as plain text: one line per block, nothing of the markup."""
    _BLOCS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "title"}

    def __init__(self):
        super().__init__()
        self.morceaux: list[str] = []
        self._cache = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._cache += 1
        elif tag in self._BLOCS:
            self.morceaux.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._cache -= 1

    def handle_data(self, data):
        if not self._cache:
            self.morceaux.append(data)


def _texte(html: str) -> str:
    p = _Texte()
    p.feed(html or "")
    lignes = (" ".join(l.split()) for l in "".join(p.morceaux).splitlines())
    return "\n".join(l for l in lignes if l)


def _contenu(body: Optional[str], html: Optional[str], op: str) -> str:
    """Exactly one body: markdown (`body`, rendered like `gmail`) or HTML as is."""
    if (body is None) == (html is None):
        raise bad(f"op='{op}' takes exactly one of `body` (markdown) or `html`.")
    return markdown_html(body) if body is not None else html


def register(mcp: FastMCP) -> None:
    from oto.tools.microsoft import OneNoteClient

    from ..auth import microsoft as ms_auth

    def _client(group_id: Optional[str], site_id: Optional[str]) -> OneNoteClient:
        """The Graph client of THIS caller, with their current token, on ONE root."""
        if group_id and site_id:
            raise bad("Name `group_id` or `site_id`, not both: a notebook has one place.")
        c = OneNoteClient(token(_SERVICE), group_id=group_id, site_id=site_id)
        # oto-core's `OneNoteClient` does not take `session=` (its own `__init__` stops
        # at the token): the guarded session replaces the one it opened, before any
        # request — every request goes through `self.session`.
        c.session = ms_auth.graph_session(_SERVICE)
        return c

    def _run(fn):
        return run(fn, _LABEL)

    @mcp.tool(name="onenote", annotations=LECTURE)
    def onenote(
        op: Literal["notebooks", "sections", "pages", "page"] = "notebooks",
        notebook_id: Optional[str] = None,
        section_id: Optional[str] = None,
        page_id: Optional[str] = None,
        title: Optional[str] = None,
        format: Literal["text", "html"] = "text",
        group_id: Optional[str] = None,
        site_id: Optional[str] = None,
        limit: int = 50,
        full: bool = False,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """READ the person's OneNote notebooks — her own, a team's (`group_id`) or a
        SharePoint site's (`site_id`). Nothing here writes: creating or adding to a page
        is `onenote_act`. Every id is valid only in the place it came from: pass the same
        `group_id` / `site_id` again with it.

        `op`:
        - **"notebooks"** (default): the notebooks, most recently modified first —
          {notebooks: [{id, name, isShared, userRole, lastModified, webUrl}], count}.
        - **"sections"**: the top-level sections of `notebook_id`, or every section of
          the place (those in section groups too) when omitted — {sections: [{id, name,
          notebook, lastModified}], count}.
        - **"pages"**: the pages of `section_id`, most recently modified first, without
          their content; `title` keeps those whose title contains it. Without
          `section_id` every page of the place is listed — Microsoft refuses that once
          there are many sections: list a section's. {pages: [{id, title, section,
          created, lastModified, webUrl}], count}.
        - **"page"**: one page by `page_id`, with its content — as plain text
          (`format="text"`, default) or as OneNote's HTML with its element ids
          (`format="html"`), which `onenote_act(op="update_page", target=…)` targets.

        Args:
            op: notebooks (default) | sections | pages | page.
            notebook_id: op="sections" — the notebook.
            section_id: op="pages" — the section.
            page_id: op="page" — the page.
            title: op="pages" — keep pages whose title contains it (case-insensitive;
                the page text is not searched).
            format: op="page" — "text" (default) or "html" (with element ids).
            group_id: the notebooks of a team (Microsoft 365 group) — a team's `id` from
                `teams_spaces` is its group id. Omitted with `site_id`: the person's
                notebooks.
            site_id: the notebooks of a SharePoint site (`id` from `sharepoint_site`).
            limit: op="notebooks"/"sections"/"pages" — max items (default 50).
            full: return Graph's raw objects instead of the trimmed view.
            fields: op="notebooks"/"sections"/"pages" — keep only these keys of each item.
        """
        # Refusal BEFORE any credential resolution: an unknown op never reaches the client.
        if op not in _OPS:
            raise bad(_ops_error(_OPS))
        if fields is not None and op == "page":
            raise bad("`fields` only applies to the listing ops, not op='page'.")
        if format != "text" and op != "page":
            raise bad(f"`format` only applies to op='page' (got op='{op}').")

        def _liste(cle: str, items: list, vue) -> dict:
            out = {cle: [raw(i) if full else vue(i) for i in items], "count": len(items)}
            return output_projection.project(out, items_path=cle, fields=fields)

        if op == "notebooks":
            refuse_ignored(op, notebook_id=notebook_id, section_id=section_id,
                           page_id=page_id, title=title)
            c = _client(group_id, site_id)
            return _liste("notebooks", _run(lambda: c.list_notebooks(limit=limit)), _notebook)

        if op == "sections":
            refuse_ignored(op, section_id=section_id, page_id=page_id, title=title)
            c = _client(group_id, site_id)
            return _liste("sections", _run(
                lambda: c.list_sections(notebook_id=notebook_id, limit=limit)), _section)

        if op == "pages":
            refuse_ignored(op, notebook_id=notebook_id, page_id=page_id)
            c = _client(group_id, site_id)
            return _liste("pages", _run(lambda: c.list_pages(
                section_id=section_id, title_contains=title, limit=limit)), _page)

        refuse_ignored(op, notebook_id=notebook_id, section_id=section_id, title=title)
        pid = need(page_id, "page_id", op)
        c = _client(group_id, site_id)
        meta = _run(lambda: c.get_page(pid))
        contenu = _run(lambda: c.get_page_content(pid, include_ids=format == "html"))
        vue = raw(meta) if full else _page(meta)
        if format == "html":
            return {**vue, "html": contenu}
        return {**vue, "text": _texte(contenu)}

    @mcp.tool(name="onenote_act", annotations=ACTE)
    def onenote_act(
        op: Literal["create_page", "update_page", "create_section", "create_notebook"],
        section_id: Optional[str] = None,
        page_id: Optional[str] = None,
        notebook_id: Optional[str] = None,
        title: Optional[str] = None,
        name: Optional[str] = None,
        body: Optional[str] = None,
        html: Optional[str] = None,
        target: str = "body",
        action: Literal["append", "prepend", "insert", "replace"] = "append",
        position: Optional[Literal["before", "after"]] = None,
        group_id: Optional[str] = None,
        site_id: Optional[str] = None,
    ) -> dict:
        """WRITE in the person's OneNote notebooks — her own, a team's (`group_id`) or a
        SharePoint site's (`site_id`): ⚠️ every op takes effect, and what is written in a
        shared notebook is seen by everyone who shares it. Reading is `onenote`. Nothing
        here deletes: a page is removed in OneNote.

        `op` (required):
        - **"create_page"**: a new page in `section_id`, titled `title`, its content
          `body` (markdown) or `html` (sent as is). Returns the page {id, title, section,
          webUrl…}.
        - **"update_page"**: changes the page `page_id` with `body` or `html`: by
          default ADDS it at the end of the page (`target="body"`, `action="append"`).
          `target` can name an element by the `id` or `data-id` read with
          `onenote(op="page", format="html")` — `#<id>` or `data-id`; `action` "prepend"
          puts it at the start of the target, "insert" next to it (`position` "after"
          by default, or "before"), "replace" replaces the element. Returns {id,
          updated: true}.
        - **"create_section"**: a new section `name` in `notebook_id`.
        - **"create_notebook"**: a new notebook `name` in the place.

        Args:
            op: create_page | update_page | create_section | create_notebook.
            section_id: op="create_page" — the section to write the page in.
            page_id: op="update_page" — the page to change.
            notebook_id: op="create_section" — the notebook.
            title: op="create_page" — the page title (plain text).
            name: op="create_section"/"create_notebook" — its name.
            body: op="create_page"/"update_page" — the content in markdown.
            html: op="create_page"/"update_page" — the content as HTML, instead of `body`.
            target: op="update_page" — "body" (default) or an element (`#<id>`,
                `data-id`).
            action: op="update_page" — append (default) | prepend | insert | replace.
            position: op="update_page" with action="insert" — after (default) | before.
            group_id: the notebooks of a team (Microsoft 365 group) — a team's `id` from
                `teams_spaces` is its group id. Omitted with `site_id`: the person's
                notebooks.
            site_id: the notebooks of a SharePoint site (`id` from `sharepoint_site`).
        """
        if op not in _ACT_OPS:
            raise bad(_ops_error(_ACT_OPS))
        if op != "update_page" and (target != "body" or action != "append"
                                    or position is not None):
            raise bad(f"`target`/`action`/`position` only apply to op='update_page' "
                      f"(got op='{op}'). Nothing was written.")

        if op == "create_page":
            refuse_ignored(op, page_id=page_id, notebook_id=notebook_id, name=name)
            sid, titre = need(section_id, "section_id", op), need(title, "title", op)
            contenu = _contenu(body, html, op)
            c = _client(group_id, site_id)
            return _page(_run(lambda: c.create_page(sid, title=titre, body_html=contenu)))

        if op == "update_page":
            refuse_ignored(op, section_id=section_id, notebook_id=notebook_id,
                           title=title, name=name)
            pid = need(page_id, "page_id", op)
            if position is not None and action != "insert":
                raise bad("`position` goes with action='insert'. Nothing was written.")
            commande = {"target": need(target, "target", op), "action": action,
                        "content": _contenu(body, html, op)}
            if action == "insert":
                commande["position"] = position or "after"
            c = _client(group_id, site_id)
            _run(lambda: c.update_page(pid, [commande]))
            return {"id": pid, "updated": True}

        nom = need(name, "name", op)
        if op == "create_section":
            refuse_ignored(op, section_id=section_id, page_id=page_id, title=title,
                           body=body, html=html)
            nid = need(notebook_id, "notebook_id", op)
            c = _client(group_id, site_id)
            return _section(_run(lambda: c.create_section(nid, nom)))
        refuse_ignored(op, section_id=section_id, page_id=page_id, notebook_id=notebook_id,
                       title=title, body=body, html=html)
        c = _client(group_id, site_id)
        return _notebook(_run(lambda: c.create_notebook(nom)))
