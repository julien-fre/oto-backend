"""Markdown → WordPress block markup (Gutenberg), deterministic, no network.

Agents write Markdown; WordPress stores `post_content` as HTML annotated with
block comments (`<!-- wp:paragraph -->…`). HTML without those comments still
publishes, but the editor shows it as ONE "Classic" block — the client opens
the post and cannot edit a heading or reorder a paragraph. So the article path
converts to NATIVE core blocks.

The markup below is what each core block's `save()` produces (WordPress ≥ 6.6:
`wp-block-heading` / `wp-block-list` classes, list items as inner blocks).
Deviating from it makes the editor flag "This block contains unexpected or
invalid content" — the post still renders on the site, but it is exactly the
kind of friction this module exists to remove. Anything that has no faithful
core-block equivalent (raw HTML) goes into a `core/html` block, which the
editor accepts verbatim.

Nothing is dropped. A list item holds text and nested lists only (that is all
`core/list-item` accepts); a list whose items carry anything else — code, a
table, a quote, raw HTML — is kept WHOLE as one `core/html` block rendered by
markdown-it, rather than losing the code from a step-by-step guide.

Not a helper for a tool of its own: no `register()` (cf. tests/test_capabilities_drift).
"""
from __future__ import annotations

import html as _html
import json
import re

from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

_IMG_ONLY = re.compile(r'^\s*<img [^>]*>\s*$')


def _open(name: str, attrs: dict | None = None) -> str:
    return f"<!-- wp:{name} {json.dumps(attrs, separators=(',', ':'))} -->" if attrs \
        else f"<!-- wp:{name} -->"


def _block(name: str, inner: str, attrs: dict | None = None) -> str:
    return f"{_open(name, attrs)}\n{inner}\n<!-- /wp:{name} -->"


def _inline(tok) -> str:
    # A soft line break inside a paragraph is a space in Markdown's own
    # semantics; a literal "\n" would survive into the block and differ from
    # what the editor re-serializes.
    children = [c for c in (tok.children or [])]
    for c in children:
        if c.type == "softbreak":
            c.type, c.content = "text", " "
    return _MD.renderer.renderInline(children, _MD.options, {}).strip()


def _find_close(tokens, i: int) -> int:
    """Index of the token closing `tokens[i]` (same nesting level)."""
    depth = 0
    for j in range(i, len(tokens)):
        depth += tokens[j].nesting
        if depth == 0:
            return j
    return len(tokens) - 1


class _NotNative(Exception):
    """A list item carries a block `core/list-item` cannot hold."""


def _list(tokens, i: int, end: int) -> str:
    ordered = tokens[i].type == "ordered_list_open"
    tag = "ol" if ordered else "ul"
    attrs: dict = {}
    if ordered:
        attrs["ordered"] = True
        start = tokens[i].attrGet("start")
        if start and str(start) != "1":
            attrs["start"] = int(start)
    start_attr = f' start="{attrs["start"]}"' if "start" in attrs else ""
    items = []
    j = i + 1
    while j < end:
        if tokens[j].type == "list_item_open":
            close = _find_close(tokens, j)
            text_parts, nested = [], []
            k = j + 1
            while k < close:
                t = tokens[k]
                if t.type == "paragraph_open":
                    text_parts.append(_inline(tokens[k + 1]))
                    k = _find_close(tokens, k) + 1
                elif t.type in ("bullet_list_open", "ordered_list_open"):
                    sub_close = _find_close(tokens, k)
                    nested.append(_list(tokens, k, sub_close))
                    k = sub_close + 1
                else:
                    raise _NotNative(t.type)
            li = "<li>" + " ".join(p for p in text_parts if p) + "".join(nested) + "</li>"
            items.append(_block("list-item", li))
            j = close + 1
        else:
            j += 1
    inner = f'<{tag} class="wp-block-list"{start_attr}>' + "".join(items) + f"</{tag}>"
    return _block("list", inner, attrs or None)


def _table(tokens, i: int, end: int) -> str:
    # Rendered by markdown-it (thead/tbody, alignment as inline style), wrapped
    # the way core/table saves.
    table_html = _MD.renderer.render(tokens[i:end + 1], _MD.options, {}).strip()
    table_html = table_html.replace("<table>", '<table class="has-fixed-layout">', 1)
    table_html = re.sub(r">\s+<", "><", table_html)
    table_html = re.sub(r' style="text-align:\w+"', "", table_html)
    return _block("table", f'<figure class="wp-block-table">{table_html}</figure>')


def _blocks(tokens, start: int, stop: int) -> list[str]:
    out: list[str] = []
    i = start
    while i < stop:
        t = tokens[i]
        if t.type == "paragraph_open":
            close = _find_close(tokens, i)
            inner = _inline(tokens[i + 1])
            if _IMG_ONLY.match(inner):
                out.append(_block("image", f'<figure class="wp-block-image">{inner}</figure>'))
            elif inner:
                out.append(_block("paragraph", f"<p>{inner}</p>"))
            i = close + 1
        elif t.type == "heading_open":
            close = _find_close(tokens, i)
            level = int(t.tag[1])
            inner = _inline(tokens[i + 1])
            out.append(_block(
                "heading", f'<h{level} class="wp-block-heading">{inner}</h{level}>',
                {"level": level} if level != 2 else None))
            i = close + 1
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            close = _find_close(tokens, i)
            try:
                out.append(_list(tokens, i, close))
            except _NotNative:
                rendered = _MD.renderer.render(tokens[i:close + 1], _MD.options, {})
                out.append(_block("html", rendered.strip()))
            i = close + 1
        elif t.type == "blockquote_open":
            close = _find_close(tokens, i)
            inner = "".join(_blocks(tokens, i + 1, close))
            out.append(_block("quote", f'<blockquote class="wp-block-quote">{inner}</blockquote>'))
            i = close + 1
        elif t.type in ("fence", "code_block"):
            code = _html.escape(t.content.rstrip("\n"), quote=False)
            out.append(_block("code", f'<pre class="wp-block-code"><code>{code}</code></pre>'))
            i += 1
        elif t.type == "hr":
            out.append(_block(
                "separator", '<hr class="wp-block-separator has-alpha-channel-opacity"/>'))
            i += 1
        elif t.type == "html_block":
            out.append(_block("html", t.content.strip()))
            i += 1
        elif t.type == "table_open":
            close = _find_close(tokens, i)
            out.append(_table(tokens, i, close))
            i = close + 1
        else:
            # Every block token markdown-it produces with this configuration is
            # handled above: a new one must be converted, never skipped.
            raise ValueError(f"bloc Markdown non converti : {t.type}")
    return out


def markdown_to_blocks(markdown: str) -> str:
    """Markdown → block markup ready for `post_content`. Empty input → ""."""
    tokens = _MD.parse(markdown or "")
    return "\n\n".join(_blocks(tokens, 0, len(tokens)))

