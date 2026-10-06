"""WordPress — posts, pages, custom content types, media, taxonomies, via
the core REST API (`wp/v2`). Wraps `oto.tools.wordpress.WordPressClient`.

Multi-field credential (`site_url`, `username`, `application_password`),
resolved per call via `access.resolve_credential_fields("wordpress")`, with
`site_url` passed to `_guard` (egress guard, then HTTPS) BEFORE any client
is built (the site is declared by the user: it is exactly the shape the guard
exists to refuse when it points at the internal network).
Multi-account: one account = one site.

⚠️ **No post or page goes public without `wordpress_publish`.**
`wordpress_content` and `wordpress_article` refuse `status=publish|future`: the
"nothing moves" / "it's live" boundary stays in the tool NAME
(same choice as `webflow_publish`), never one parameter among others.
⚠️ **Media, on the other hand, is public as soon as it is uploaded** (its
`source_url` can be read without an account), including the featured image of
a draft: WordPress has no draft media. The descriptions say so.

`wordpress_article` is the composed path — Markdown converted to native editor
blocks (`wordpress_blocks`), categories/tags given by NAME (created if
missing), featured image imported from an oto source, SEO fields written when
the site allows it. It is the path an agent gets right; the single-purpose
tools remain for everything else.

The "Connect" flow (WordPress's native authorization screen) lives in
`auth/wordpress.py`; it sets the SAME credential as the form.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional, Union

import requests
from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from oto.tools.common.errors import UpstreamHTTPError

from .. import access, egress
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import wordpress_blocks

if TYPE_CHECKING:
    from oto.tools.wordpress import WordPressClient

logger = logging.getLogger(__name__)

# Statuses that make content public — reserved for `wordpress_publish`.
_LIVE_STATUSES = {"publish", "future"}
# Internal editor types: served over REST, never content to write.
_INTERNAL_TYPES = frozenset({
    "attachment", "wp_block", "wp_template", "wp_template_part", "wp_navigation",
    "wp_font_family", "wp_font_face", "wp_global_styles", "nav_menu_item"})
# What a list returns per item: enough to choose from, not the whole content.
_LIST_KEYS = ("id", "status", "date", "modified", "slug", "link", "type", "parent")

# Known SEO meta keys, per plugin. Written through `meta` ONLY if the site
# exposes them over REST (route schema) — otherwise WordPress ignores them SILENTLY.
_SEO_META = {
    "yoast": {"title": "_yoast_wpseo_title", "description": "_yoast_wpseo_metadesc",
              "focus_keyword": "_yoast_wpseo_focuskw"},
    "rankmath": {"title": "rank_math_title", "description": "rank_math_description",
                 "focus_keyword": "rank_math_focus_keyword"},
}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    if value is None or value == "":
        raise _bad(f"op='{op}' requires {name}")
    return value


def _wp_code(e: UpstreamHTTPError) -> str:
    return e.body.get("code", "") if isinstance(e.body, dict) else ""


def _wp_message(e: UpstreamHTTPError) -> str:
    if isinstance(e.body, dict):
        return str(e.body.get("message") or e.body)[:300]
    return str(e.body)[:300]


def _translate(e: Exception) -> McpError:
    """Upstream error → actionable message. The WordPress `code` (`rest_*`) is
    read, never guessed from the text."""
    from oto.tools.wordpress import (WordPressMediaFieldsError, WordPressRateLimited,
                                     WordPressRedirect)

    if isinstance(e, McpError):
        return e
    if isinstance(e, WordPressMediaFieldsError):
        # The media EXISTS (and it is public): sending the caller back to retry would create a duplicate.
        return _bad(f"media {e.media_id} uploaded (already public), but its alt "
                    f"text / caption / title were not set "
                    f"(HTTP {e.status_code}) — complete it with `wordpress_media "
                    f"op=update id={e.media_id}`, do not upload it again.")
    if isinstance(e, WordPressRateLimited):
        wait = (f"retry in {int(e.retry_after)} s" if e.retry_after is not None
                else "retry later")
        return _bad(f"the WordPress site is rate limiting (429) — {wait}.")
    if isinstance(e, (WordPressRedirect, ValueError)):   # EgressRefused ⊂ ValueError
        return _bad(str(e))
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return _bad("WordPress site unreachable (connection or timeout) — check the "
                    "site URL, or retry in a moment.")
    if isinstance(e, UpstreamHTTPError):
        code, msg = _wp_code(e), _wp_message(e)
        if e.status_code == 401 and code == "rest_not_logged_in":
            # Ambiguous by construction: WordPress returns this same code for a
            # wrong username/password AND for an Authorization header stripped
            # along the way (seen on a local WordPress). We name both.
            return _bad(
                "WordPress did not recognize you (401 rest_not_logged_in): either "
                "the username or application password is wrong (or revoked), "
                "or the host / a security plugin strips the Authorization "
                "header. See the connector sheet (note section).")
        if e.status_code == 401:
            return _bad(f"username or application password rejected (401 {code}): "
                        f"{msg}")
        if e.status_code == 403:
            return _bad(f"insufficient WordPress permissions for this account (403 {code}): {msg}")
        if e.status_code == 404 and code == "rest_no_route":
            return _bad("this route does not exist on the site (404 rest_no_route) — content "
                        "type not exposed over REST, or plugin missing.")
        if e.status_code == 404:
            return _bad(f"not found (404 {code}): {msg}")
        if e.status_code >= 500:
            return _bad(f"the WordPress site is erroring (HTTP {e.status_code}) — "
                        f"this is not your input; retry later. {msg}".rstrip())
        return _bad(f"WordPress rejected the request (HTTP {e.status_code} {code}): {msg}")
    raise e


def _run(fn):
    try:
        return fn()
    except McpError:
        raise
    except Exception as e:  # noqa: BLE001 — translated, or re-raised as is by _translate
        raise _translate(e) from e


def _strip(item) -> dict:
    """Removes `_links` (heavy, no value for an agent)."""
    if isinstance(item, dict):
        return {k: v for k, v in item.items() if k != "_links"}
    return item


def _rendered(v):
    if isinstance(v, dict):
        return v.get("raw", v.get("rendered"))
    return v


def _slim(item: dict, keys=_LIST_KEYS) -> dict:
    """What a LIST returns per item: enough to choose from, never the whole content
    (a full post per row would be paid for on every turn). `get` returns the rest."""
    out = {k: item.get(k) for k in keys if k in item}
    for k in ("title", "name"):
        if k in item:
            out[k] = _rendered(item[k])
    return out


def _diff(current: dict, changes: dict) -> dict:
    """`{field: {"from", "to"}}` for the fields that actually CHANGE —
    compared on the raw form (`raw`), never on the rendered HTML."""
    out = {}
    for k, new in changes.items():
        old = _rendered(current.get(k))
        if old != new:
            out[k] = {"from": old, "to": new}
    return out


def _guard(site_url: str) -> bool:
    """Before any client is built: egress guard, then HTTPS
    (`auth.wordpress.http_allowed`). Returns `allow_http` for the client."""
    from ..auth.wordpress import http_allowed

    egress.check_url(site_url, connector="wordpress", field="site_url")
    return http_allowed(site_url)


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — `GET wp/v2/users/me?context=edit`.

    Authenticated, no side effects, free. `context=edit` is only served to an
    identified user: a 401 therefore really means "this password doesn't work"
    (or the header is stripped by the host — same fix: correct it on the
    site side), never a limit of the probe."""
    from oto.tools.wordpress import WordPressClient

    allow_http = _guard(fields.get("site_url") or "")
    try:
        me = WordPressClient(fields["site_url"], fields["username"],
                             fields["application_password"], allow_http=allow_http).me()
    except UpstreamHTTPError as e:
        if e.status_code == 401:
            raise connector_verify.NonAutorise(_translate(e).error.message) from e
        raise
    if not me.get("id"):
        raise RuntimeError(f"unexpected response from users/me: {str(me)[:200]}")


def _client(account: Optional[str] = None) -> WordPressClient:
    from oto.tools.wordpress import WordPressClient

    creds = access.resolve_credential_fields("wordpress", account)
    site = creds.get("site_url") or ""
    try:
        allow_http = _guard(site)
        return WordPressClient(site, creds.get("username") or "",
                               creds.get("application_password") or "",
                               allow_http=allow_http)
    except ValueError as e:   # egress guard, HTTP, URL or credentials missing
        raise _bad(f"unusable WordPress credential: {e}") from e


# --- resolving types / taxonomies --------------------------------------------

def _route_for(entries: dict, key: str, kind: str) -> str:
    """`posts`/`post`/`product`/`book` → `wp/v2/posts`. Accepts the slug OR the
    `rest_base`; a type missing from REST is reported with the available list."""
    key = (key or "").strip()
    for slug, t in entries.items():
        base = t.get("rest_base")
        if not base:
            continue
        if key in (slug, base):
            return f"{t.get('rest_namespace') or 'wp/v2'}/{base}"
    dispo = sorted(t.get("rest_base") for s, t in entries.items()
                   if t.get("rest_base") and s not in _INTERNAL_TYPES
                   and "(" not in t.get("rest_base"))
    raise _bad(f"{kind} \"{key}\" not found in REST on this site. Available: {dispo}.")


def _type_route(c, type_: str) -> str:
    if type_ in ("posts", "post"):
        return "wp/v2/posts"
    if type_ in ("pages", "page"):
        return "wp/v2/pages"
    return _route_for(_run(c.types), type_, "content type")


def _type_taxonomies(c, type_: str) -> list:
    """The taxonomies THIS type accepts (`category`, `post_tag`, a custom one…),
    read from the site: a page has no categories, a custom type has its
    own."""
    key = (type_ or "").strip()
    for slug, t in _run(c.types).items():
        if key in (slug, t.get("rest_base")):
            return list(t.get("taxonomies") or [])
    raise _bad(f"content type \"{key}\" not found in REST on this site.")


def _tax_route(c, taxonomy: str) -> str:
    if taxonomy in ("categories", "category"):
        return "wp/v2/categories"
    if taxonomy in ("tags", "post_tag"):
        return "wp/v2/tags"
    return _route_for(_run(c.taxonomies), taxonomy, "taxonomy")


def _refuse_live(data: dict, tool: str) -> None:
    if (data or {}).get("status") in _LIVE_STATUSES:
        raise _bad(f"{tool} does not publish (status={data['status']!r}): the content "
                   "stays a draft, `wordpress_publish` puts it live.")


# --- SEO -----------------------------------------------------------------------

def _seo_plan(c, route: str, namespaces: list) -> dict:
    """Where to write SEO on THIS site: `{"plugin", "via", "keys"}` or
    `{"plugin", "via": None, "reason"}`. Reads the route schema (OPTIONS):
    a meta key not declared `show_in_rest` is silently ignored by
    WordPress — so we only write it if the schema carries it."""
    plugin = ("yoast" if "yoast/v1" in namespaces
              else "rankmath" if "rankmath/v1" in namespaces else None)
    if plugin is None:
        return {"plugin": None, "via": None,
                "reason": "no SEO plugin detected (Yoast, Rank Math)."}
    try:
        schema, _ = c.request("OPTIONS", route)
    except (UpstreamHTTPError, requests.ConnectionError, requests.Timeout) as e:
        # The schema could not be READ: we don't write, and we say why — never
        # "fields not exposed", which would send the user to add a snippet for nothing.
        return {"plugin": plugin, "via": None,
                "reason": f"route schema unreadable: {_translate(e).error.message}"}
    meta_props = (((schema or {}).get("schema") or {}).get("properties") or {}) \
        .get("meta", {}).get("properties", {}) or {}
    keys = _SEO_META[plugin]
    if all(k in meta_props for k in keys.values()):
        return {"plugin": plugin, "via": "meta", "keys": keys}
    return {"plugin": plugin, "via": None,
            "reason": (f"{plugin} is installed but its fields are not exposed for "
                       "writing by this site's REST API — the snippet to add is "
                       "on the connector sheet (note section).")}


def _seo_meta(plan: dict, seo: dict) -> dict:
    if plan.get("via") != "meta":
        return {}
    return {plan["keys"][k]: v for k, v in seo.items() if v is not None}


# --- terms by name -------------------------------------------------------------

def _resolve_terms(c, route: str, values: list, *,
                   create: bool) -> tuple[list, list, list]:
    """Names or ids → `(ids, to create, created)`. An integer is an id, a string is
    ALWAYS a name ("2026" is a tag, not term no. 2026). A missing name
    is created (`create=True`); in dry_run it is only announced. `term_exists`
    (race, case) returns the existing id."""
    ids, to_create, created = [], [], []
    for v in values or []:
        if isinstance(v, int) and not isinstance(v, bool):
            ids.append(v)
            continue
        name = str(v).strip()
        if not name:
            continue
        found = _run(lambda: c.list(route, search=name, per_page=100))["items"]
        match = next((t for t in found
                      if str(t.get("name", "")).strip().lower() == name.lower()), None)
        if match:
            ids.append(match["id"])
            continue
        if not create:
            to_create.append(name)
            continue
        try:
            new_id = c.create(route, {"name": name})["id"]
            ids.append(new_id)
            created.append({"name": name, "id": new_id})
        except UpstreamHTTPError as e:
            existing = (e.body.get("data") or {}).get("term_id") if isinstance(e.body, dict) else None
            if _wp_code(e) == "term_exists" and existing:
                ids.append(int(existing))
            else:
                raise _translate(e) from e
    return ids, to_create, created


def _upload(c, source: Union[str, dict], *, filename: Optional[str] = None,
            **fields) -> dict:
    from .. import file_source

    src = {"kind": "url", "url": source} if isinstance(source, str) else source
    try:
        rf = file_source.resolve(src)
    except file_source.FileSourceError as e:
        raise _bad(f"unreadable file source: {e}") from e
    return _run(lambda: c.upload_media(rf.data, filename or rf.filename, rf.mime, **fields))


def register(mcp: FastMCP) -> None:
    connector_verify.register("wordpress", _verify)

    @mcp.tool()
    def wordpress_site() -> dict:
        """What this WordPress site is and what this connection can do there —
        call it first. Returns the site (name, url, description), the connected
        user and their role/capabilities (an Author can only publish their own
        posts; `unfiltered_html` false means scripts/iframes are stripped), the
        content types and taxonomies exposed in REST (with the `type`/`taxonomy`
        value the other wordpress_* tools accept — custom post types included),
        and the detected plugins (SEO: Yoast/Rank Math and whether SEO fields are
        writable here; WooCommerce; ACF)."""
        c = _client()
        index = _run(c.index)
        me = _run(c.me)
        types = _run(c.types)
        taxes = _run(c.taxonomies)
        ns = index.get("namespaces") or []
        caps = me.get("capabilities") or {}
        return {
            "site": {k: index.get(k) for k in ("name", "description", "url", "home",
                                                "gmt_offset", "timezone_string")},
            "user": {"id": me.get("id"), "name": me.get("name"), "roles": me.get("roles"),
                     "can": {k: bool(caps.get(k)) for k in (
                         "publish_posts", "edit_others_posts", "publish_pages",
                         "upload_files", "manage_categories", "unfiltered_html")}},
            "types": [{"type": t.get("rest_base"), "slug": s, "name": t.get("name"),
                       "hierarchical": t.get("hierarchical"),
                       "taxonomies": t.get("taxonomies")}
                      for s, t in types.items()
                      if t.get("rest_base") and s not in _INTERNAL_TYPES],
            "taxonomies": [{"taxonomy": t.get("rest_base"), "slug": s, "name": t.get("name"),
                            "hierarchical": t.get("hierarchical"), "types": t.get("types")}
                           for s, t in taxes.items()
                           if t.get("rest_base") and s not in ("nav_menu", "wp_pattern_category")],
            "plugins": {
                "seo": _seo_plan(c, "wp/v2/posts", ns),
                "woocommerce": "wc/v3" in ns,
                "acf": any(n.startswith("acf/") for n in ns),
            },
        }

    @mcp.tool()
    def wordpress_content(
        op: Literal["list", "get", "create", "update", "delete"],
        type: str = "posts",
        id: Optional[int] = None,
        data: Optional[dict] = None,
        content_format: Literal["raw", "markdown"] = "raw",
        search: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        orderby: Optional[str] = None,
        order: Optional[Literal["asc", "desc"]] = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """WordPress posts, pages and custom post types — list, read, create,
        edit, trash. For a blog article prefer `wordpress_article` (Markdown,
        categories by name, featured image, SEO in one call). No item goes live
        here: `status` publish/future is refused, use `wordpress_publish` —
        but editing an item that is ALREADY published changes it live (check
        with `dry_run` first). Raw HTML in the content (`<script>`, `<iframe>`,
        a `wp:html` block) is kept as written unless the account lacks
        `unfiltered_html` — never paste HTML from an untrusted page.
        `get` returns the editable form (`content.raw`, block markup). `delete`
        moves to the trash; `force=true` deletes permanently. `dry_run` on
        update/delete fetches the item and returns the real diff / what would be
        deleted, without writing.

        Args:
            op: list | get | create | update | delete.
            type: `posts`, `pages`, or a custom type from `wordpress_site`.
            id: the item — get/update/delete.
            data: create/update fields as WordPress names them (title, content,
                excerpt, slug, status draft|pending|private, categories, tags,
                featured_media, parent, meta, acf…). update sends only these keys.
            content_format: `markdown` converts `data.content` to native blocks
                (raw HTML inside it is kept as an HTML block).
            search: list — full-text filter.
            status: list — e.g. `draft`, `publish`, `draft,pending`, `any`.
            page: list — 1-based page.
            per_page: list — up to 100.
            orderby: list — date, modified, title, id…
            order: list — asc | desc.
            force: delete — permanent instead of trash.
            dry_run: validate and preview, no write.
        """
        c = _client()
        route = _type_route(c, type)

        if op == "list":
            out = _run(lambda: c.list(route, page=page, per_page=per_page, search=search,
                                      status=status, orderby=orderby, order=order,
                                      context="edit"))
            out["items"] = [_slim(i) for i in out["items"]]
            return out

        if op == "get":
            _need(id, "id", op)
            return _strip(_run(lambda: c.get(route, id)))

        if op in ("create", "update"):
            body = dict(_need(data, "data", op))
            _refuse_live(body, "wordpress_content")
            if content_format == "markdown" and isinstance(body.get("content"), str):
                body["content"] = _run(
                    lambda: wordpress_blocks.markdown_to_blocks(body["content"]))
            if op == "create":
                body.setdefault("status", "draft")
                if dry_run:
                    return {"dry_run": True, "would_create": body, "route": route}
                return _strip(_run(lambda: c.create(route, body)))
            _need(id, "id", op)
            current = _run(lambda: c.get(route, id))
            if dry_run:
                return {"dry_run": True, "id": id, "status": current.get("status"),
                        "changes": _diff(current, body)}
            return _strip(_run(lambda: c.update(route, id, body)))

        if op == "delete":
            _need(id, "id", op)
            if dry_run:
                current = _run(lambda: c.get(route, id))
                return {"dry_run": True, "id": id,
                        "would": "delete permanently" if force else "move to trash",
                        "item": _slim(current)}
            return _strip(_run(lambda: c.delete(route, id, force=force)))

        raise _bad(f"unknown op: {op}")

    @mcp.tool()
    def wordpress_publish(
        id: int,
        type: str = "posts",
        op: Literal["publish", "unpublish"] = "publish",
        at: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Put a WordPress post/page LIVE, schedule it, or take it back to
        draft — the only wordpress_* tool that changes what visitors see.
        `op=publish` without `at` publishes now; with `at` (ISO datetime in the
        site's timezone, e.g. `2026-10-01T09:00:00`) it schedules (status
        `future`). `op=unpublish` sets it back to draft. `dry_run` returns the
        current status and what would change.

        Args:
            id: the post/page id.
            type: `posts`, `pages`, or a custom type from `wordpress_site`.
            op: publish | unpublish.
            at: optional schedule datetime (site timezone) — publish only.
            dry_run: preview, no write.
        """
        if op == "unpublish" and at:
            raise _bad("op='unpublish' does not take `at`: unpublishing is immediate "
                       "(WordPress cannot schedule a return to draft).")
        c = _client()
        route = _type_route(c, type)
        current = _run(lambda: c.get(route, id))
        if op == "unpublish":
            body = {"status": "draft"}
        elif at:
            body = {"status": "future", "date": at}
        elif current.get("status") == "future":
            # Scheduled: WordPress keeps its future date and leaves it `future`
            # until it is brought back to now (seen locally).
            body = {"status": "publish",
                    "date_gmt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
        else:
            body = {"status": "publish"}
        if dry_run:
            return {"dry_run": True, "id": id, "title": _rendered(current.get("title")),
                    "status": current.get("status"), "would_set": body}
        out = _run(lambda: c.update(route, id, body))
        return {"id": out.get("id"), "status": out.get("status"), "date": out.get("date"),
                "link": out.get("link"), "title": _rendered(out.get("title"))}

    @mcp.tool()
    def wordpress_terms(
        op: Literal["list", "get", "create", "update", "delete"],
        taxonomy: str = "categories",
        id: Optional[int] = None,
        name: Optional[str] = None,
        slug: Optional[str] = None,
        parent: Optional[int] = None,
        description: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
        dry_run: bool = False,
    ) -> dict:
        """WordPress categories, tags and custom taxonomies. Use it to map
        category names to ids, or to manage the terms themselves. Deleting a
        term is permanent (WordPress has no trash for terms) — posts lose that
        term; `dry_run` shows what would go.

        Args:
            op: list | get | create | update | delete.
            taxonomy: `categories`, `tags`, or a custom taxonomy from `wordpress_site`.
            id: the term — get/update/delete.
            name: create/update — display name.
            slug: create/update — URL slug.
            parent: create/update — parent term id (hierarchical taxonomies).
            description: create/update.
            search: list — name filter.
            page: list — 1-based page.
            per_page: list — up to 100.
            dry_run: preview, no write.
        """
        c = _client()
        route = _tax_route(c, taxonomy)
        if op == "list":
            out = _run(lambda: c.list(route, page=page, per_page=per_page, search=search,
                                      hide_empty="false"))
            out["items"] = [_slim(t, ("id", "name", "slug", "parent", "count"))
                            for t in out["items"]]
            return out
        if op == "get":
            return _strip(_run(lambda: c.get(route, _need(id, "id", op), context="view")))
        body = {k: v for k, v in {"name": name, "slug": slug, "parent": parent,
                                   "description": description}.items() if v is not None}
        if op == "create":
            _need(name, "name", op)
            if dry_run:
                return {"dry_run": True, "would_create": body, "route": route}
            return _strip(_run(lambda: c.create(route, body)))
        _need(id, "id", op)
        if op == "update":
            if dry_run:
                current = _run(lambda: c.get(route, id, context="view"))
                return {"dry_run": True, "id": id, "changes": _diff(current, body)}
            return _strip(_run(lambda: c.update(route, id, body)))
        if op == "delete":
            if dry_run:
                current = _run(lambda: c.get(route, id, context="view"))
                return {"dry_run": True, "id": id, "would": "delete permanently",
                        "term": {k: current.get(k) for k in ("id", "name", "slug", "count")}}
            return _strip(_run(lambda: c.delete(route, id, force=True)))
        raise _bad(f"unknown op: {op}")

    @mcp.tool()
    def wordpress_media(
        op: Literal["list", "get", "upload", "update", "delete"],
        id: Optional[int] = None,
        source: Optional[Union[str, dict]] = None,
        filename: Optional[str] = None,
        alt_text: Optional[str] = None,
        caption: Optional[str] = None,
        title: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        dry_run: bool = False,
    ) -> dict:
        """WordPress media library — list, read, upload, edit alt text/caption,
        delete. `upload` takes a public URL or an oto file reference
        (`{"kind": "drive"|"gmail"|"url"|"project_file", …}`) and returns the
        media `id` — use it as a post's `featured_media`. ⚠️ An uploaded file is
        PUBLIC at once at its `source_url` (WordPress has no draft media), even
        if the post using it is a draft. Delete is permanent.

        Args:
            op: list | get | upload | update | delete.
            id: the media item — get/update/delete.
            source: upload — URL string, or oto file reference dict.
            filename: upload — override the stored file name.
            alt_text: upload/update — accessibility text (set it for images).
            caption: upload/update.
            title: upload/update.
            search: list — filter.
            page: list — 1-based page.
            per_page: list — up to 100.
            dry_run: preview, no write.
        """
        c = _client()
        route = "wp/v2/media"
        fields = {k: v for k, v in {"alt_text": alt_text, "caption": caption,
                                     "title": title}.items() if v is not None}
        if op == "list":
            out = _run(lambda: c.list(route, page=page, per_page=per_page, search=search))
            out["items"] = [_slim(m, ("id", "source_url", "mime_type", "alt_text", "date"))
                            for m in out["items"]]
            return out
        if op == "get":
            return _strip(_run(lambda: c.get(route, _need(id, "id", op))))
        if op == "upload":
            _need(source, "source", op)
            if dry_run:
                return {"dry_run": True, "would_upload": source, "fields": fields}
            m = _upload(c, source, filename=filename, **fields)
            return {"id": m.get("id"), "source_url": m.get("source_url"),
                    "mime_type": m.get("mime_type"), "alt_text": m.get("alt_text")}
        _need(id, "id", op)
        if op == "update":
            if dry_run:
                return {"dry_run": True, "id": id,
                        "changes": _diff(_run(lambda: c.get(route, id)), fields)}
            return _strip(_run(lambda: c.update(route, id, fields)))
        if op == "delete":
            if dry_run:
                m = _run(lambda: c.get(route, id))
                return {"dry_run": True, "id": id, "would": "delete permanently",
                        "media": {"title": _rendered(m.get("title")),
                                  "source_url": m.get("source_url")}}
            return _strip(_run(lambda: c.delete(route, id, force=True)))
        raise _bad(f"unknown op: {op}")

    @mcp.tool()
    def wordpress_article(
        title: str,
        markdown: str,
        id: Optional[int] = None,
        type: str = "posts",
        status: Optional[Literal["draft", "pending"]] = None,
        categories: Optional[list[Union[str, int]]] = None,
        tags: Optional[list[Union[str, int]]] = None,
        featured_image: Optional[Union[str, dict]] = None,
        featured_image_alt: Optional[str] = None,
        excerpt: Optional[str] = None,
        slug: Optional[str] = None,
        seo_title: Optional[str] = None,
        seo_description: Optional[str] = None,
        focus_keyword: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """Write a WordPress article in ONE call — the path to use for blog
        posts. Markdown becomes native editor blocks (headings, lists, quotes,
        code, tables, images stay editable in WordPress); raw HTML in it
        (`<script>`, `<iframe>`…) is kept as an HTML block unless the account
        lacks `unfiltered_html` — never paste HTML from an untrusted page.
        Categories and tags are given by NAME (a string, created if missing) or
        id (an integer), and must be taxonomies of `type`. `featured_image` is a
        URL or an oto file reference, uploaded and attached — the image is
        public at once, even on a draft. SEO title/description/
        keyword are written when Yoast or Rank Math expose them (else `seo`
        says why). A new article is saved as draft (or pending review) — then
        `wordpress_publish` to go live or schedule. Pass `id` to rewrite an
        existing article: its status is kept, so rewriting a PUBLISHED article
        changes it live (use `dry_run`, or `wordpress_publish op=unpublish`
        first). `dry_run` resolves everything, reports the terms it would
        create, and writes nothing.

        Args:
            title: article title.
            markdown: body in Markdown.
            id: existing post to update instead of creating.
            type: `posts` or a custom type from `wordpress_site`.
            status: draft | pending (review) — default draft on create, unchanged on update.
            categories: names (strings) or ids (integers).
            tags: names (strings) or ids (integers).
            featured_image: URL or oto file reference dict.
            featured_image_alt: alt text for the featured image.
            excerpt: summary shown in listings.
            slug: URL slug.
            seo_title: SEO title (Yoast / Rank Math).
            seo_description: meta description (Yoast / Rank Math).
            focus_keyword: focus keyphrase (Yoast / Rank Math).
            dry_run: resolve and preview, no write.
        """
        c = _client()
        route = _type_route(c, type)
        body: dict = {"title": title,
                      "content": _run(lambda: wordpress_blocks.markdown_to_blocks(markdown))}
        # Create: draft by default. Update: the status is NOT sent unless
        # asked — sending it unconditionally would unpublish a live post.
        if status is not None or id is None:
            body["status"] = status or "draft"
        if excerpt is not None:
            body["excerpt"] = excerpt
        if slug:
            body["slug"] = slug
        if categories or tags:
            # A type's terms are THE ONES the site declares for it: a page has no
            # categories, a custom type may have its own.
            accepted = _type_taxonomies(c, type)
            for given, tax, label in ((categories, "category", "categories"),
                                      (tags, "post_tag", "tags")):
                if given and tax not in accepted:
                    raise _bad(f"type \"{type}\" has no {label} on this site "
                               f"(taxonomies: {accepted}) — use `wordpress_terms` and "
                               "`wordpress_content data=` for a taxonomy specific to the type.")
        cat_ids, cat_new, cat_made = _resolve_terms(
            c, _tax_route(c, "category"), categories or [], create=not dry_run)
        tag_ids, tag_new, tag_made = _resolve_terms(
            c, _tax_route(c, "post_tag"), tags or [], create=not dry_run)
        if categories:
            body["categories"] = cat_ids
        if tags:
            body["tags"] = tag_ids

        seo_in = {"title": seo_title, "description": seo_description,
                  "focus_keyword": focus_keyword}
        seo_wanted = any(v is not None for v in seo_in.values())
        plan = _seo_plan(c, route, (_run(c.index).get("namespaces") or [])) if seo_wanted else None
        if plan:
            meta = _seo_meta(plan, seo_in)
            if meta:
                body["meta"] = meta

        if dry_run:
            return {"dry_run": True, "would": "update" if id else "create", "id": id,
                    "route": route, "body": body,
                    "terms_to_create": {"categories": cat_new, "tags": tag_new},
                    "featured_image": featured_image, "seo": plan}

        media = None
        try:
            if featured_image:
                media = _upload(c, featured_image, alt_text=featured_image_alt)
                body["featured_media"] = media.get("id")
            post = _run(lambda: c.update(route, id, body) if id else c.create(route, body))
        except McpError as e:
            # Terms and image are created BEFORE the post: a failure here leaves them
            # in place. We name them — never orphaned objects nobody knows about.
            left = []
            if cat_made or tag_made:
                left.append("terms created: " + ", ".join(
                    f"{t['name']} (id {t['id']})" for t in cat_made + tag_made))
            if media:
                left.append(f"image uploaded: media id {media.get('id')} "
                            f"({media.get('source_url')}, already public)")
            if not left:
                raise
            raise _bad(f"{e.error.message} — the post was not written. Left on the "
                       f"site: {' ; '.join(left)}. A new attempt finds the terms "
                       "by name; the media can be attached (`wordpress_content op=update "
                       "data={featured_media: id}`) or deleted (`wordpress_media "
                       "op=delete`).") from e
        pid = post.get("id")
        seo_out = None
        if seo_wanted:
            seo_out = {"plugin": plan.get("plugin"), "written": plan.get("via") == "meta"}
            if plan.get("via") != "meta":
                seo_out["reason"] = plan.get("reason")
        site = c.site_url
        return {
            "id": pid, "status": post.get("status"), "link": post.get("link"),
            "edit_link": f"{site}/wp-admin/post.php?post={pid}&action=edit",
            "categories": post.get("categories"), "tags": post.get("tags"),
            "featured_media": post.get("featured_media"), "seo": seo_out,
        }


# --- "Connect" flow (the site's native authorization screen) ------------------

def _start_flow(ctx, values: dict):
    """Entry point of the `connector_flow` seam — turns refusals into named
    responses (400 / 403) instead of a 500."""
    from ..auth import wordpress as wp_auth
    from ..capabilities._types import AuthzDenied

    try:
        return wp_auth.start(ctx, values)
    except PermissionError as e:
        raise AuthzDenied(403, "org_admin_required", str(e))
    except ValueError as e:   # ConnectRefused, EgressRefused, URL invalide
        raise AuthzDenied(400, "connect_refused", str(e))


def _declare_flow() -> None:
    from ..auth import wordpress as wp_auth
    from ..connectors import flow as connector_flow

    # `site_url` is a FREE field: `declare` refuses a required parameter with no
    # options or default (it can only render a select) — so it is declared
    # optional and `wp_auth.start` itself refuses an empty value, by name.
    connector_flow.declare(
        "wordpress",
        start=_start_flow,
        params=(connector_flow.FlowParam(
            "site_url", "Site URL", required=False,
            help="e.g. blog.example.com — you will approve access in your wp-admin"),),
        label="Connect my WordPress site",
        callback_path=wp_auth.CALLBACK_PATH,
    )


_declare_flow()
