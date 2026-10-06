"""Datastore — the rendered REVIEW queue (`data_review_app`): one row at a time, two
buttons, in the conversation.

Why it exists: a procedure often stops at a HUMAN step ("a person reviews the pending
leads and launches them one by one") and that step lived outside the chat —
dashboard, spreadsheet, third-party tool. The card brings it back in: the next row
at status `pending`, two gestures (`approve` / `reject`), then the next one.

**The only app that WRITES.** `data_app` and `oto_doc_app` stay read-only;
the exception is bounded, and each bound is rechecked server-side at click time:
- ONE column (the status), two values fixed by the model's call and frozen in
  the card; a value outside the declared `options` is refused;
- ONE row, designated by its `_id`, and written only if it is STILL at
  `pending` — a row moved elsewhere in the meantime is skipped, never overwritten;
- the right to write is the store's (`_resolve(write=True)`), like `data_write`.
The card sends and launches nothing in any other tool: it writes a status. Only at the end of the
queue, a "Continue in chat" button POSTS, on click, a factual message from the
user ("Done reviewing: 2 launched, 1 skipped.") so the agent resumes; the
tally travels in the button's arguments — supplied by the client, displayed, never a guard.

The text SEEN by the user (card, notices, refusals) is in English — same rule as the
messages reachable by an outside user (`docs/conventions.md`).

Mechanics (`FastMCPApp`, extra `fastmcp[apps]`):
- `data_review_app` = entry point, visible to the model, `data_*` spine.
- `data_review_decide` = APP-ONLY tool: absent from `tools/list` (zero context
  cost), called by the button under a HASHED name `<hash>_data_review_decide`;
  its bare name cannot be found, the model cannot call it.
  ⚠️ This path BYPASSES session visibility (fastmcp finds an app tool
  "even hidden by a transform") AND the call axes: `namespace_of` of a hashed
  name is not `data`, so `CallContextMiddleware` reads neither `_project` nor
  `_org` there. Hence: the entry call's context is FROZEN in the button's
  arguments at render time, then RESET here by the axes' own guards
  (`call_axes.PROJECT` / `call_axes.ORG`, membership verified).
  ⚠️ An app-only tool being absent from `tools/list` is marked FIXME on the fastmcp side
  (the spec wants it listed, filtered by the host): a bump beyond the `<3.5` pin
  may make it reappear in the model's context — reread this module that day.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.types import INVALID_PARAMS, ErrorData
from starlette.concurrency import run_in_threadpool

from .. import access, call_axes
from ..datastore import declaration
from ..datastore.core import (
    DatastoreNotFound,
    DatastoreReadOnly,
    RowLocked,
    RowNotFound,
    make_store,
)
from ..datastore.identite import AdresseJson as Adresse
from ..mcp_errors import McpError

APP_NAME = "oto-data-review"
DECIDE = "data_review_decide"

# An inline card is read at a glance (Claude design guide: 4-5 data points, 2
# actions). Beyond that, `data_app` is what should be opened.
_MAX_CHAMPS = 4

# The card follows the partner's style guide (hairlines, pills, 13 px) but PAINTS with the
# host's variables (SEP-1865 `styles.variables`): the fallback values only serve a host
# that provides none, in light and dark alike. ⚠️ EVERY color goes through a host
# variable, accent included: a host may serve dark backgrounds without setting
# the `.dark` class — a color set by `.dark` alone stayed light on a dark
# background (seen in a real host, unreadable amber pill). No remote resource (host
# CSP): the font is the host's. ⚠️ The FRAME is the host's (border and corners
# of the iframe): the page stays transparent and margin-free, and the card redraws neither
# border nor rounding — otherwise a second background appears behind its corners.
_CSS = """
html,body{margin:0;padding:0;background:transparent!important}
.pf-app-root{padding:0;background:transparent;
--rc-bg:var(--color-background-primary,#fff);--rc-bg-2:var(--color-background-secondary,#f9f9fb);
--rc-fg:var(--color-text-primary,#1c2024);--rc-muted:var(--color-text-secondary,#60646c);
--rc-line:var(--color-border-primary,#d9d9e0);--rc-soft:var(--color-border-tertiary,#e8e8ec);
--rc-inv-bg:var(--color-background-inverse,#1c2024);--rc-inv-fg:var(--color-text-inverse,#fff);
--rc-amber:var(--color-text-warning,#ab6400);--rc-amber-bg:var(--color-background-warning,rgba(255,197,61,.18));
--rc-accent:var(--color-border-info,#2d69d1)}
.dark .pf-app-root{
--rc-bg:var(--color-background-primary,#111113);--rc-bg-2:var(--color-background-secondary,#18191b);
--rc-fg:var(--color-text-primary,#edeef0);--rc-muted:var(--color-text-secondary,#b0b4ba);
--rc-line:var(--color-border-primary,#363a3f);--rc-soft:var(--color-border-tertiary,#272a2d);
--rc-inv-bg:var(--color-background-inverse,#edeef0);--rc-inv-fg:var(--color-text-inverse,#111113);
--rc-amber:var(--color-text-warning,#ffca16);--rc-amber-bg:var(--color-background-warning,rgba(255,197,61,.14));
--rc-accent:var(--color-border-info,#70b8ff)}
.rc{box-sizing:border-box;width:100%;display:flex;flex-direction:column;gap:12px;padding:16px;
background:var(--rc-bg);
color:var(--rc-fg);font-family:var(--font-sans,ui-sans-serif,system-ui,sans-serif);
font-size:13px;line-height:18px;letter-spacing:-.002em}
.rc-t{margin:0;font-size:inherit;line-height:inherit;color:inherit;font-weight:400}
.rc-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.rc-stack{display:flex;flex-direction:column;gap:2px;min-width:0}
.rc .rc-title{font-size:14px;line-height:20px;font-weight:500;letter-spacing:-.006em}
.rc .rc-caption{font-size:12px;line-height:16px;color:var(--rc-muted)}
.rc-pill{flex-shrink:0;border-radius:9999px;padding:2px 8px;font-size:12px;line-height:16px;
background:var(--rc-amber-bg);color:var(--rc-amber)}
.rc-fields{display:flex;flex-direction:column}
.rc-field{display:grid;grid-template-columns:minmax(6rem,34%) 1fr;gap:12px;padding:6px 0;
border-top:1px solid var(--rc-soft)}
.rc-field:first-child{border-top:0;padding-top:0}
.rc-value{overflow-wrap:anywhere}
.rc-note{display:flex;flex-direction:column;gap:2px;padding:8px 10px;
border-radius:var(--border-radius-md,6px);background:var(--rc-amber-bg)}
.rc .rc-note .rc-caption{color:var(--rc-amber);font-weight:500}
.rc-info{display:flex;align-items:center;gap:6px}
.rc-dot{width:6px;height:6px;flex-shrink:0;border-radius:9999px;background:var(--rc-muted)}
.rc .rc-dot-ok{background:var(--color-text-success,#30a46c)}
.rc-actions{display:flex;justify-content:flex-end;gap:8px;padding-top:12px;
border-top:1px solid var(--rc-soft)}
.rc .rc-btn{height:32px;padding:0 12px;border-radius:9999px;font-size:13px;line-height:19px;
font-weight:400;box-shadow:none;cursor:pointer;transition:background-color .1s ease-out,opacity .1s ease-out}
.rc .rc-btn:focus-visible{outline:2px solid var(--rc-accent);outline-offset:2px}
.rc .rc-btn-primary{background:var(--rc-inv-bg);color:var(--rc-inv-fg);border:1px solid transparent}
.rc .rc-btn-primary:hover{background:var(--rc-inv-bg);opacity:.9}
.rc .rc-btn-secondary{background:transparent;color:var(--rc-fg);border:1px solid var(--rc-line)}
.rc .rc-btn-secondary:hover{background:var(--rc-bg-2)}
@media (pointer:coarse){.rc .rc-btn{height:44px;padding:0 16px}}
@media (prefers-reduced-motion:reduce){.rc .rc-btn{transition:none}}
"""


def _fields(schema: Optional[dict]) -> list[dict]:
    return [f for f in (schema or {}).get("fields") or []
            if isinstance(f, dict) and f.get("key")]


def _label(value: object) -> str:
    return str(value).replace("_", " ").strip().capitalize()


def _status_def(schema: Optional[dict], column: Optional[str]) -> Optional[dict]:
    """The status column: the NAMED one, otherwise the `role: "status"` field, otherwise the
    queue column (`declaration.status_field`).

    We do NOT read the `lifecycle` to decide the buttons: its interpretation is being
    removed (#317). The values come from the call, and only the declared `options`
    bound them."""
    by_key = {f["key"]: f for f in _fields(schema)}
    if column:
        return by_key.get(column) or {"key": column}
    for f in _fields(schema):
        if f.get("role") == "status":
            return f
    return declaration.status_field(schema)


def _refus_valeurs(fdef: dict, pending: str, approve: str, reject: str) -> Optional[str]:
    if approve == reject:
        return "`approve` and `reject` must be two different values."
    if pending in (approve, reject):
        return "`pending` cannot also be an outcome (`approve`/`reject`)."
    options = fdef.get("options")
    if isinstance(options, list) and options:
        hors = [v for v in (pending, approve, reject) if v not in options]
        if hors:
            return (f"value(s) not in the options of `{fdef['key']}`: "
                    f"{', '.join(hors)} — declared options: {', '.join(map(str, options))}.")
    return None


def _titre(row: dict, schema: Optional[dict]) -> str:
    for k in ((declaration.title_field(schema) or {}).get("key"),
              (schema or {}).get("key"), "_id"):
        if k and row.get(k) not in (None, ""):
            return str(row[k])
    return "Row"


def _champs(row: dict, schema: Optional[dict], column: str,
            fields: Optional[list]) -> tuple[list[tuple[str, str]], list[str]]:
    """The data shown: `fields` if the call names them, otherwise the first FILLED
    fields in schema order (excluding title, status, notes, meta and layers). The
    `role: "note"` fields come out separately — it is what a reviewer reads first."""
    decl = _fields(schema)
    labels = {f["key"]: f.get("label") or _label(f["key"]) for f in decl}
    title_key = (declaration.title_field(schema) or {}).get("key")
    note_keys = [f["key"] for f in decl if f.get("role") == "note"]
    if fields:
        keys = [str(k) for k in fields]
    else:
        ordre = [f["key"] for f in decl] or list(row)
        keys = [k for k in ordre
                if k not in (column, title_key) and k not in note_keys
                and not k.startswith("_") and "." not in k]
    champs: list[tuple[str, str]] = []
    for k in keys:
        v = row.get(k)
        if v in (None, "") or isinstance(v, (dict, list)):
            continue
        champs.append((labels.get(k, _label(k)), str(v)))
        if not fields and len(champs) >= _MAX_CHAMPS:
            break
    notes = [str(row[k]) for k in note_keys if row.get(k) not in (None, "")]
    return champs, notes


def _porte_un_gabarit(*valeurs: object) -> bool:
    """A value frozen in a button goes through the renderer's template engine
    (`CallTool.arguments` interpolates `{{ key }}` client-side): it would not reach
    the server as written. We refuse at opening rather than write something other
    than what the model asked for."""
    def _walk(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from _walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from _walk(x)
    return any("{{" in s for v in valeurs for s in _walk(v))


def _suivante(store, datastore: str, column: str, pending: str,
              filter: Optional[dict]) -> tuple[Optional[dict], int]:
    filtre = {**(filter or {}), column: pending}
    restantes = store.count_rows(datastore, filter=filtre)
    rows = store.list_rows(datastore, filter=filtre, limit=1)
    return (rows[0] if rows else None), restantes


def _refus(message: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=message))


def _compte(valeur: object) -> int:
    """A tally counter, reread from the button's arguments: supplied by the
    client, hence replayable — bounded, displayed, never a guard."""
    try:
        return min(max(int(valeur), 0), 100_000)
    except (TypeError, ValueError):
        return 0


def register(mcp: FastMCP) -> None:
    try:
        from fastmcp import FastMCPApp
        from prefab_ui.actions import SetState, ShowToast
        from prefab_ui.actions.mcp import CallTool, SendMessage
        from prefab_ui.app import PrefabApp
        from prefab_ui.components import (  # type: ignore
            H4, Button, Column, Div, Slot, Span, Text,
        )
        from prefab_ui.rx import ERROR, RESULT
    # noqa: SILENT — extra `apps` absent ⇒ no card, `data_write` remains the way
    except Exception:  # pragma: no cover - extra `apps` absent
        return

    app = FastMCPApp(APP_NAME)

    def _app(view, **kw):
        return PrefabApp(view=view, css=[_CSS], **kw)

    def _texte(content: str, cls: str = ""):
        return Text(content, css_class=f"rc-t {cls}".strip())

    def _message(titre: str, texte: str):
        with Div(css_class="rc") as card:
            H4(titre, css_class="rc-t rc-title")
            _texte(texte, "rc-caption")
        return _app(card)

    def _clic(ctx: dict, row_id: str, value: str):
        return CallTool(
            data_review_decide,
            arguments={**ctx, "id": row_id, "value": value},
            # The renderer puts `structuredContent` in `$result`, and a Slot only paints
            # a COMPONENT (`type` key at the top level). The handler returns a
            # PrefabApp envelope (`$prefab`/`view`/`css`): setting the whole `$result`
            # left the previous card displayed, click after click. We set its `view`.
            on_success=SetState("carte", RESULT.view),
            on_error=ShowToast(ERROR, variant="error"),
        )

    def _fin(ctx: dict):
        """End of queue. Nothing decided in this card: nothing to summarize. Otherwise the tally,
        and ONE button that posts this tally as the user's message — factual, never
        an instruction to the model; the counters come from the client (display only)."""
        oui, non = ctx.get("approve_count") or 0, ctx.get("reject_count") or 0
        if not oui and not non:
            with Div(css_class="rc-stack"):
                H4("Nothing to review", css_class="rc-t rc-title")
                _texte(f"Nothing is waiting at “{_label(ctx['pending'])}” right now.",
                       "rc-caption")
            return
        bilan = (f"{oui} {_label(ctx['approve']).lower()}, "
                 f"{non} {_label(ctx['reject']).lower()}")
        with Div(css_class="rc-stack"):
            H4("Review complete", css_class="rc-t rc-title")
            _texte(bilan.replace(", ", " · "))
        _texte("Statuses are saved. Continue in the chat for the next step.",
               "rc-caption")
        message = f"Done reviewing: {bilan}."
        if not _porte_un_gabarit(message):
            with Div(css_class="rc-actions"):
                Button("Continue in chat", css_class="rc-btn rc-btn-primary",
                       on_click=SendMessage(message))

    def _carte(store, ctx: dict, info: Optional[str] = None, ton: str = ""):
        ds, col, pending = ctx["datastore"], ctx["column"], ctx["pending"]
        schema = store.get_schema(ds)
        row, restantes = _suivante(store, ds, col, pending, ctx.get("filter"))
        with Div(css_class="rc") as card:
            if info:
                with Div(css_class="rc-info"):
                    Div(css_class=f"rc-dot {ton}".strip())
                    _texte(info, "rc-caption")
            if row is None:
                _fin(ctx)
            else:
                with Div(css_class="rc-head"):
                    with Div(css_class="rc-stack"):
                        H4(_titre(row, schema), css_class="rc-t rc-title")
                        _texte(f"{restantes} pending review", "rc-caption")
                    Span(_label(pending), css_class="rc-pill")
                champs, notes = _champs(row, schema, col, ctx.get("fields"))
                if champs:
                    with Div(css_class="rc-fields"):
                        for label, value in champs:
                            with Div(css_class="rc-field"):
                                _texte(label, "rc-caption")
                                _texte(value, "rc-value")
                for note in notes:
                    with Div(css_class="rc-note"):
                        _texte("To check", "rc-caption")
                        _texte(note)
                with Div(css_class="rc-actions"):
                    Button(ctx.get("reject_label") or _label(ctx["reject"]),
                           variant="outline", css_class="rc-btn rc-btn-secondary",
                           on_click=_clic(ctx, row["_id"], ctx["reject"]))
                    Button(ctx.get("approve_label") or _label(ctx["approve"]),
                           css_class="rc-btn rc-btn-primary",
                           on_click=_clic(ctx, row["_id"], ctx["approve"]))
        return card

    def _decider(ctx: dict, row_id: str, value: str):
        sub = access.current_user_sub_or_raise()
        store = make_store(sub)
        ds, col, pending = ctx["datastore"], ctx["column"], ctx["pending"]
        if value not in (ctx["approve"], ctx["reject"]):
            raise _refus(f"“{value}” is not an outcome of this card "
                         f"({ctx['approve']} / {ctx['reject']}).")
        try:
            fdef = _status_def(store.get_schema(ds), col) or {"key": col}
            refus = _refus_valeurs(fdef, pending, ctx["approve"], ctx["reject"])
            if refus:
                raise _refus(refus)
            ton = ""
            try:
                actuelle = store.get_row(ds, row_id)
            except RowNotFound:
                info = "This row was removed in the meantime — nothing changed."
            else:
                titre = _titre(actuelle, store.get_schema(ds))
                if str(actuelle.get(col)) != str(pending):
                    info = (f"{titre} was already {_label(actuelle.get(col))} — "
                            "nothing changed.")
                else:
                    store.update_row(ds, row_id, {col: value})
                    info = f"{titre} · {_label(value)}"
                    # The tally only moves on a real write.
                    cle = "approve_count" if value == ctx["approve"] else "reject_count"
                    ctx = {**ctx, cle: (ctx.get(cle) or 0) + 1}
                    ton = "rc-dot-ok" if value == ctx["approve"] else ""
            return _app(_carte(store, ctx, info, ton))
        except DatastoreNotFound:
            raise _refus(f"Table {ds} not found in this context.")
        except DatastoreReadOnly:
            raise _refus(f"Table {ds} is read-only for you.")
        except RowLocked as e:
            raise _refus(str(e))
        except ValueError as e:
            raise _refus(str(e))

    @app.tool()
    async def data_review_decide(
        datastore: str,
        id: str,
        value: str,
        column: str,
        pending: str,
        approve: str,
        reject: str,
        filter: Optional[dict] = None,
        fields: Optional[list] = None,
        approve_label: Optional[str] = None,
        reject_label: Optional[str] = None,
        project: Optional[int] = None,
        org: Optional[int] = None,
        approve_count: int = 0,
        reject_count: int = 0,
    ):  # no return annotation: same gotcha as data_app (#69).
        """Button handler of `data_review_app` — app-only, never listed to the model."""
        ctx = {"datastore": datastore, "column": column, "pending": pending,
               "approve": approve, "reject": reject, "filter": filter,
               "fields": fields, "approve_label": approve_label,
               "reject_label": reject_label, "project": project, "org": org,
               "approve_count": _compte(approve_count),
               "reject_count": _compte(reject_count)}
        undo: list = []
        try:
            # The context frozen at render time, reset by the axes' guards (see module
            # docstring): the project co-sets its org; otherwise the org alone.
            if project is not None:
                undo.extend(await call_axes.PROJECT.pin_for(project, DECIDE))
            elif org is not None:
                undo.extend(await call_axes.ORG.pin_for(org, DECIDE))
            return await run_in_threadpool(_decider, ctx, id, value)
        finally:
            for reset, token in reversed(undo):
                reset(token)

    @app.ui()
    def data_review_app(
        datastore: Adresse,
        pending: str,
        approve: str,
        reject: str,
        column: Optional[str] = None,
        filter: Optional[dict] = None,
        fields: Optional[list[str]] = None,
        approve_label: Optional[str] = None,
        reject_label: Optional[str] = None,
    ):  # no return annotation: same gotcha as data_app (#69).
        """Review queue card (MCP App) — ONE row at a time, two buttons, in the chat.

        For a procedure's HUMAN step ("a person reviews the pending leads and
        launches them"): shows the next row whose status `column` equals `pending`
        (title, a few fields, the note), the USER clicks `approve` or `reject`, the
        status is written and the card moves to the next row. The user decides —
        never call this to decide on their behalf; to set statuses yourself, use
        `data_write`.

        The only rendered app that writes, and only this: one status column, set to
        `approve` or `reject`, on a row still at `pending` at click time (a row
        changed meanwhile is skipped, never overwritten). When the queue is done, the
        card offers one "Continue in chat" button: the user's click posts a factual
        summary as their message ("Done reviewing: 2 launched, 1 skipped.") — the
        signal to resume the procedure. It still sends or launches nothing in any
        other tool.

        Args:
            datastore: the table's NUMBER (`ns_id`), or `slot:<name>`.
            pending: status value awaiting review, e.g. "to_review".
            approve: value written by the primary button, e.g. "launched".
            reject: value written by the secondary button, e.g. "skipped".
            column: the status column ; omit = the field with `role: "status"`.
            filter: extra exact-match `{column: value}` narrowing the queue.
            fields: columns to show ; omit = the first 4 filled ones, schema order.
            approve_label: primary button text ; omit = the `approve` value.
            reject_label: secondary button text ; omit = the `reject` value.
        """
        sub = access.current_user_sub_or_raise()
        store = make_store(sub)
        try:
            ref = access.resolve_datastore_ref(str(datastore))
            ds = str(store.resolve_ns_id(ref))
            schema = store.get_schema(ds)
        except DatastoreNotFound:
            return _message("Table not found",
                            f"No table “{datastore}” in this context.")
        fdef = _status_def(schema, column)
        if fdef is None:
            return _message("No status column",
                            "This table declares no `role: \"status\"` field — pass "
                            "`column=` to name the column to move forward.")
        refus = _refus_valeurs(fdef, pending, approve, reject)
        if refus:
            return _message("Values refused", refus)
        if _porte_un_gabarit(pending, approve, reject, filter, fields,
                             approve_label, reject_label):
            return _message("Values refused",
                            "A value contains `{{`, which the card renderer would "
                            "read as a template: it would not reach the click as "
                            "written.")
        # Frozen in each button: the click arrives without axes (see module docstring).
        # The org is the EFFECTIVE one of this call (`current_org` seam: token, run,
        # home) — not just the `_org=` token, otherwise a call resolved under a run's
        # org would click under the home org.
        ctx = {"datastore": ds, "column": fdef["key"], "pending": pending,
               "approve": approve, "reject": reject, "filter": filter or None,
               "fields": fields or None, "approve_label": approve_label,
               "reject_label": reject_label, "project": access.current_project(),
               "org": access.current_org(sub), "approve_count": 0, "reject_count": 0}
        with Column() as view:
            with Slot("carte"):
                _carte(store, ctx)
        return _app(view, state={"carte": None})

    mcp.add_provider(app)
