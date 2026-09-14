"""Datastore — la file de REVUE rendue (`data_review_app`) : une ligne à la fois, deux
boutons, dans la conversation.

Pourquoi ça existe : une procédure s'arrête souvent sur une étape HUMAINE (« une
personne relit les leads en attente et les lance un par un ») et cette étape vivait
hors du chat — dashboard, tableur, outil tiers. La carte l'y ramène : la prochaine
ligne au statut `pending`, deux gestes (`approve` / `reject`), puis la suivante.

**La seule app qui ÉCRIT.** `data_app` et `oto_doc_app` restent en lecture seule ;
l'exception est bornée, et chaque borne est revérifiée côté serveur au clic :
- UNE colonne (le statut), deux valeurs fixées par l'appel du modèle et figées dans
  la carte ; une valeur hors des `options` déclarées est refusée ;
- UNE ligne, désignée par son `_id`, et écrite seulement si elle est TOUJOURS à
  `pending` — une ligne passée ailleurs entre-temps est sautée, jamais écrasée ;
- le droit d'écrire est celui du store (`_resolve(write=True)`), comme `data_write`.
La carte n'envoie et ne lance rien dans aucun autre outil : elle écrit un statut. En fin de
file seulement, un bouton « Continue in chat » POSTE, au clic, un message factuel de
l'utilisateur (« Done reviewing: 2 launched, 1 skipped. ») pour que l'agent reprenne ; le
bilan voyage dans les arguments du bouton — fourni par le client, affiché, jamais une garde.

Le texte VU par l'utilisateur (carte, avis, refus) est en anglais — même règle que les
messages atteignables par un utilisateur extérieur (`docs/conventions.md`).

Mécanique (`FastMCPApp`, extra `fastmcp[apps]`) :
- `data_review_app` = point d'entrée, visible du modèle, spine `data_*`.
- `data_review_decide` = outil APP-ONLY : absent de `tools/list` (zéro coût de
  contexte), appelé par le bouton sous un nom HACHÉ `<hash>_data_review_decide` ;
  son nom nu est introuvable, le modèle ne peut pas l'appeler.
  ⚠️ Ce chemin CONTOURNE la visibilité de session (fastmcp retrouve un outil d'app
  « même masqué par un transform ») ET les axes d'appel : `namespace_of` d'un nom
  haché n'est pas `data`, donc `CallContextMiddleware` n'y lit ni `_project` ni
  `_org`. D'où : le contexte de l'appel d'entrée est FIGÉ dans les arguments du
  bouton au rendu, puis REPOSÉ ici par les gardes des axes eux-mêmes
  (`call_axes.PROJECT` / `call_axes.ORG`, appartenance vérifiée).
  ⚠️ Qu'un outil app-only soit absent de `tools/list` est marqué FIXME côté fastmcp
  (le spec veut qu'il y figure, filtré par le host) : un bump au-delà du pin `<3.5`
  peut le faire réapparaître dans le contexte du modèle — relire ce module ce jour-là.
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

# Une carte inline se lit d'un coup d'œil (guide de design Claude : 4-5 données, 2
# actions). Au-delà, c'est `data_app` qu'il faut ouvrir.
_MAX_CHAMPS = 4

# La carte suit la charte du partenaire (hairlines, pastilles, 13 px) mais PEINT avec les
# variables du host (SEP-1865 `styles.variables`) : les valeurs de repli ne servent qu'à
# un host qui n'en fournit pas, en clair comme en sombre. ⚠️ TOUTE couleur passe par une
# variable du host, accent compris : un host peut servir des fonds sombres sans poser
# la classe `.dark` — une couleur réglée par `.dark` seule restait claire sur fond
# sombre (vu dans un vrai host, pastille ambre illisible). Aucune ressource distante (CSP
# du host) : la police est celle du host. ⚠️ Le CADRE est celui du host (bordure et coins
# de l'iframe) : la page reste transparente et sans marge, et la carte ne redessine ni
# bordure ni arrondi — sinon un second fond apparaît derrière ses coins.
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
    """La colonne de statut : celle NOMMÉE, sinon le champ `role: "status"`, sinon la
    colonne de file (`declaration.status_field`).

    On ne lit PAS le `lifecycle` pour décider des boutons : son interprétation est en
    cours de retrait (#317). Les valeurs viennent de l'appel, et seules les `options`
    déclarées les bornent."""
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
    """Les données montrées : `fields` si l'appel les nomme, sinon les premiers champs
    REMPLIS dans l'ordre du schéma (hors titre, statut, notes, méta et couches). Les
    champs `role: "note"` sortent à part — c'est ce qu'un relecteur lit en premier."""
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
    """Une valeur figée dans un bouton passe par le moteur de gabarits du rendu
    (`CallTool.arguments` interpole `{{ clé }}` côté client) : elle n'arriverait pas
    au serveur telle qu'écrite. On refuse à l'ouverture plutôt que d'écrire autre chose
    que ce que le modèle a demandé."""
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
    """Un compteur du bilan, relu depuis les arguments du bouton : fourni par le
    client, donc rejouable — borné, affiché, jamais une garde."""
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
    # noqa: SILENT — extra `apps` absent ⇒ pas de carte, `data_write` reste la voie
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
            # Le renderer range `structuredContent` dans `$result`, et un Slot ne peint
            # qu'un COMPOSANT (clé `type` au premier niveau). Le gestionnaire rend une
            # enveloppe PrefabApp (`$prefab`/`view`/`css`) : poser `$result` entier
            # laissait la carte d'avant affichée, clic après clic. On pose sa `view`.
            on_success=SetState("carte", RESULT.view),
            on_error=ShowToast(ERROR, variant="error"),
        )

    def _fin(ctx: dict):
        """Fin de file. Rien tranché dans cette carte : rien à résumer. Sinon le bilan,
        et UN bouton qui poste ce bilan comme message de l'utilisateur — factuel, jamais
        une consigne au modèle ; les compteurs viennent du client (affichage seul)."""
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
                    # Le bilan ne bouge que sur une écriture réelle.
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
    ):  # pas d'annotation de retour : même gotcha que data_app (#69).
        """Button handler of `data_review_app` — app-only, never listed to the model."""
        ctx = {"datastore": datastore, "column": column, "pending": pending,
               "approve": approve, "reject": reject, "filter": filter,
               "fields": fields, "approve_label": approve_label,
               "reject_label": reject_label, "project": project, "org": org,
               "approve_count": _compte(approve_count),
               "reject_count": _compte(reject_count)}
        undo: list = []
        try:
            # Le contexte figé au rendu, reposé par les gardes des axes (cf. docstring
            # du module) : le projet co-pose son org ; sinon l'org seule.
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
    ):  # pas d'annotation de retour : même gotcha que data_app (#69).
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
        # Figé dans chaque bouton : le clic arrive sans axes (cf. docstring du module).
        # L'org est celle EFFECTIVE de cet appel (seam `current_org` : jeton, run,
        # maison) — pas seulement le jeton `_org=`, sinon un appel résolu sous l'org
        # d'un run cliquerait sous la maison.
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
