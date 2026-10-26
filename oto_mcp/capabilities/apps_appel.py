"""Ce qu'une app OUVERTE demande — `POST /api/me/apps/{app_id}/call`, face REST seule.

Une app s'exécute chez son lecteur, dans un cadre isolé sans réseau : tout ce qu'elle
lit, écrit ou lance passe par le front, qui le relaie ICI, au nom du lecteur. C'est
la seule porte : le front n'appelle plus les tableaux ni les agents pour une app.

**La règle : droits d'une app = droits de son AUTEUR ∩ droits de son LECTEUR.**
L'auteur (`apps.updated_by`, le dernier à avoir écrit l'app) choisit ce qu'elle
touche ; le lecteur l'ouvre avec ses propres droits. Sans la règle, quelqu'un qui peut
éditer l'app sans lire un tableau le déclarerait et le ferait lire par la session d'un
collègue. Chaque appel juge donc, sur la ressource DÉCLARÉE au manifeste :
- **l'auteur**, par l'ownership seul (`ownership.can_access` sur le NUMÉRO du tableau,
  `_acces_agent.niveau` sur l'agent) — jamais par sa session : il n'est pas là, et
  une résolution par nom se ferait dans SON org active (ADR 0023) ;
- **le lecteur**, en exécutant pour lui les capacités existantes (`_list_rows`,
  `_aggregate`, `_update_row`, `_append_row`, `run_now`) : leurs règles, pas une copie.
Auteur inconnu ou qui a perdu l'accès : refus nommé, rien n'est servi.

**Ce que l'app voit est borné par son manifeste**, ici et non plus dans le front
seul : champs déclarés (projection, filtres, tri, regroupement), valeurs posables,
colonnes d'ajout, agents lisibles ou lançables, entrée d'agent. Un manifeste stocké
est déjà normalisé (`apps_manifeste.forme`) et ses tableaux sont des numéros.

**Les résultats d'agent** : `agent_runs` et `run_result` rendent le statut d'un run
et ce que l'agent a conclu (`runs.outcome`, `runs.note`) — d'un agent déclaré, et
d'un travail qui est un run de cet agent.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .. import db, ownership
from . import _acces_agent
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

PROJECT_RTYPE = "project"
MAX_ROWS = 200
MAX_INPUT = 300
MAX_NOTE = 4000
MAX_RUNS = 50
# Ce qu'une ligne porte toujours, en plus des champs déclarés : de quoi la désigner,
# la réécrire sans écraser (`_revision`) et la dater.
_META = frozenset({"_id", "_revision", "_created_at", "_updated_at"})
_DATES = frozenset({"_created_at", "_updated_at"})
_AGG_OPS = frozenset({"count_rows", "count", "sum", "avg", "min", "max"})
_JOB_DONE = frozenset({"done", "failed", "expired"})

Action = Literal["read_rows", "read_aggregate", "write_cell", "add_row",
                 "start_agent", "agent_runs", "run_result"]


class AppCallInput(BaseModel):
    app_id: int
    action: Action
    table: Optional[str] = Field(default=None, description="The table's name in the manifest.")
    agent: Optional[str] = Field(default=None, description="The agent's name in the manifest.")
    # read_rows : limit, offset, order_by, order_dir, filter, q — read_aggregate :
    # group_by, metrics, filter — agent_runs : limit.
    options: dict = Field(default_factory=dict)
    row_id: Optional[str] = None
    field: Optional[str] = None
    value: Optional[str] = None
    expected_revision: Optional[str] = None
    values: Optional[dict] = None
    input: Optional[str] = None
    job_id: Optional[int] = None


class AppRun(BaseModel):
    job_id: int
    status: str
    started_at: Optional[Any] = None
    finished_at: Optional[Any] = None
    error: Optional[str] = None
    outcome: Optional[str] = None
    note: Optional[str] = None


class AppCallOut(BaseModel):
    rows: Optional[list[dict]] = None
    total: Optional[int] = None
    groups: Optional[list[dict]] = None
    row: Optional[dict] = None
    job_id: Optional[int] = None
    runs: Optional[list[AppRun]] = None
    run: Optional[AppRun] = None


def _refus(status: int, code: str, msg: str) -> AuthzDenied:
    return AuthzDenied(status, code, msg)


def _app_pour(ctx: ResolvedCtx, app_id: int) -> dict:
    app = db.apps.get(int(app_id))
    if (app is None or app.get("archived_at") is not None
            or not ownership.can_access(ctx.sub, PROJECT_RTYPE, str(app["project_id"]), "read")):
        raise _refus(404, "unknown_app", f"App {app_id} doesn't exist or isn't shared with you.")
    if not app.get("updated_by"):
        raise _refus(403, "app_author_unknown",
                     "Nobody is known to have written this app, so it can't act for anyone.")
    return app


def _table(app: dict, alias: Optional[str], want: str) -> tuple[str, dict]:
    t = (app["manifest"].get("tables") or {}).get(alias or "")
    if not isinstance(t, dict) or not str(t.get("table") or "").isdigit():
        raise _refus(400, "undeclared_table", f"This app doesn't declare a table named “{alias}”.")
    ns = str(t["table"])
    if not ownership.can_access(app["updated_by"], ownership.TYPE_RESSOURCE_DATASTORE, ns, want):
        raise _refus(403, "app_author_lost_access",
                     f"This app's author can no longer {want} table {ns}: the app can't either.")
    return ns, t


def _lisible(t: dict, champ: str) -> bool:
    """Un champ déclaré — ou l'élément d'une liste par sa colonne (`tags[]`,
    `contacts[].role`). Sans liste de champs, tout ce que le lecteur voit."""
    fields = t.get("fields")
    return fields is None or champ.split("[")[0].split(".")[0] in fields


def _filtre(t: dict, raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _refus(400, "invalid_filter", "A filter is an object of field → value.")
    caches = sorted(k for k in raw if k not in _DATES and not _lisible(t, k))
    if caches:
        raise _refus(403, "undeclared_field",
                     f"An app can only filter on the fields it declares (not {', '.join(caches)}).")
    return json.dumps(raw) if raw else None


def _projeter(t: dict, row: dict) -> dict:
    fields = t.get("fields")
    if fields is None:
        return row
    return {k: v for k, v in row.items() if k in fields or k in _META}


def _agent(ctx: ResolvedCtx, app: dict, alias: Optional[str], *, run: bool) -> dict:
    a = (app["manifest"].get("agents") or {}).get(alias or "")
    if not isinstance(a, dict):
        raise _refus(400, "undeclared_agent", f"This app doesn't declare an agent named “{alias}”.")
    if run and not a.get("run"):
        raise _refus(403, "agent_not_runnable", f"This app may read “{alias}” but not start it.")
    trig = db.get_trigger(int(a["id"]), int(ctx.org_id))
    voit = (lambda sub: _acces_agent.niveau(sub, int(ctx.org_id), trig)) if trig else None
    if trig is None or voit(ctx.sub) is None:
        raise _refus(404, "unknown_agent", f"Agent {a['id']} isn't one you can see here.")
    niveau_auteur = voit(app["updated_by"])
    if niveau_auteur is None or (run and niveau_auteur not in _acces_agent.ECRIRE):
        raise _refus(403, "app_author_lost_access",
                     f"This app's author can no longer {'run' if run else 'see'} agent "
                     f"{a['id']}: the app can't either.")
    return {**a, "trigger": trig}


def _run(job: dict, resultats: dict) -> dict:
    r = resultats.get(job.get("run_id") or "") or {}
    note = r.get("note")
    return {"job_id": int(job["id"]), "status": job["status"],
            "started_at": job.get("created_at"), "finished_at": job.get("finished_at"),
            "error": job.get("last_error"), "outcome": r.get("outcome"),
            "note": note[:MAX_NOTE] if isinstance(note, str) else None}


def _call(ctx: ResolvedCtx, inp: AppCallInput) -> dict:
    from .datastore import rows as R

    app = _app_pour(ctx, inp.app_id)
    o = inp.options or {}

    if inp.action == "read_rows":
        ns, t = _table(app, inp.table, "read")
        order_by = o.get("order_by")
        if order_by is not None and (not isinstance(order_by, str)
                                     or (order_by not in _DATES and not _lisible(t, order_by))):
            raise _refus(403, "undeclared_field", "An app can only sort on a field it declares.")
        q = o.get("q")
        if q and t.get("fields") is not None:
            raise _refus(403, "search_needs_all_fields",
                         "Search runs over every field: it needs a table declared without a field list.")
        page = R._list_rows(ctx, R.ListRowsInput(
            datastore=ns, limit=min(max(int(o.get("limit") or 50), 1), MAX_ROWS),
            offset=max(int(o.get("offset") or 0), 0), order_by=order_by,
            order_dir="asc" if o.get("order_dir") == "asc" else "desc",
            q=q if isinstance(q, str) and q else None, filter=_filtre(t, o.get("filter"))))
        return {"rows": [_projeter(t, r) for r in page.get("rows") or []],
                "total": page.get("total")}

    if inp.action == "read_aggregate":
        ns, t = _table(app, inp.table, "read")
        group_by = o.get("group_by")
        if group_by is not None and (not isinstance(group_by, str) or not _lisible(t, group_by)):
            raise _refus(403, "undeclared_field", "An app can only group on a field it declares.")
        metrics = o.get("metrics") or [{"op": "count_rows", "label": "count"}]
        if not isinstance(metrics, list) or len(metrics) > 10:
            raise _refus(400, "invalid_metrics", "Metrics are a list of at most 10.")
        propres = []
        for i, m in enumerate(metrics):
            m = m if isinstance(m, dict) else {}
            if m.get("op") not in _AGG_OPS:
                raise _refus(400, "invalid_metrics", f"Unknown metric “{m.get('op')}”.")
            if m.get("field") is not None and (not isinstance(m["field"], str) or not _lisible(t, m["field"])):
                raise _refus(403, "undeclared_field", "An app can only measure the fields it declares.")
            # Pas de `where` par métrique : il filtrerait sur un champ non déclaré.
            propres.append({"op": m["op"], **({"field": m["field"]} if m.get("field") else {}),
                            "label": m.get("label") if isinstance(m.get("label"), str) else f"m{i}"})
        out = R._aggregate(ctx, R.AggregateInput(
            datastore=ns, group_by=group_by, metrics=json.dumps(propres),
            filter=_filtre(t, o.get("filter"))))
        return {"groups": out.get("groups") or []}

    if inp.action == "write_cell":
        ns, t = _table(app, inp.table, "write")
        allowed = (t.get("set") or {}).get(inp.field or "")
        if not allowed:
            raise _refus(403, "undeclared_write", f"This app may not change “{inp.field}”.")
        if inp.value not in allowed:
            raise _refus(403, "undeclared_value",
                         f"“{inp.value}” isn't a value this app may set {inp.field} to.")
        if not inp.row_id or not inp.expected_revision:
            # Une app écrit une ligne qu'elle a MONTRÉE : sans révision, elle écraserait
            # ce qu'un autre a changé depuis.
            raise _refus(400, "revision_required", "An app's write names the row's `_revision` as it was shown.")
        row = R._update_row(ctx, R.UpdateRowInput(
            datastore=ns, row_id=inp.row_id, patch={inp.field: inp.value},
            expected_revision=inp.expected_revision))
        return {"row": _projeter(t, row)}

    if inp.action == "add_row":
        ns, t = _table(app, inp.table, "write")
        values = inp.values or {}
        cols = t.get("add") or []
        if not cols:
            raise _refus(403, "undeclared_write", "This app may not add rows to this table.")
        extra = sorted(k for k in values if k not in cols)
        if extra:
            raise _refus(403, "undeclared_write", f"This app may not fill: {', '.join(extra)}.")
        if not values:
            raise _refus(400, "empty_row", "The new row is empty.")
        row = R._append_row(ctx, R.AppendRowInput(datastore=ns, row=values))
        return {"row": _projeter(t, row)}

    if inp.action == "start_agent":
        from .runner_triggers import TriggerInput, _triggers

        a = _agent(ctx, app, inp.agent, run=True)
        handoff = None
        if inp.input:
            if not a.get("input"):
                raise _refus(403, "agent_input_not_allowed", f"This app may not pass an input to “{inp.agent}”.")
            if len(inp.input) > MAX_INPUT:
                raise _refus(400, "agent_input_too_long", f"An input is at most {MAX_INPUT} characters.")
            # Marquée comme une DONNÉE venue d'une app : le run la lit après sa consigne.
            handoff = f"Input from the app “{app['title']}”:\n{inp.input}"
        out = _triggers(ctx, TriggerInput(op="run_now", trigger_id=int(a["id"]), handoff=handoff))
        return {"job_id": int(out["job"]["id"])}

    if inp.action == "agent_runs":
        a = _agent(ctx, app, inp.agent, run=False)
        jobs = db.list_jobs(int(ctx.org_id), trigger_id=int(a["id"]),
                            limit=min(max(int(o.get("limit") or 10), 1), MAX_RUNS))
        resultats = db.apps.run_results(j.get("run_id") for j in jobs)
        return {"runs": [_run(j, resultats) for j in jobs]}

    # run_result
    if inp.job_id is None:
        raise _refus(400, "missing_job_id", "`job_id` is required.")
    job = db.apps.job_of_org(int(ctx.org_id), int(inp.job_id))
    declares = {str(a.get("id")): alias for alias, a in (app["manifest"].get("agents") or {}).items()
                if isinstance(a, dict)}
    if job is None or job.get("trigger_id") not in declares:
        raise _refus(404, "unknown_run", "No run of this app's agents has that id.")
    _agent(ctx, app, declares[job["trigger_id"]], run=False)
    resultats = db.apps.run_results([job.get("run_id")]) if job["status"] in _JOB_DONE else {}
    return {"run": _run(job, resultats)}


CAPABILITIES += [
    Capability(
        key="me.app.call", handler=_call, Input=AppCallInput, Output=AppCallOut,
        authz=ORG_MEMBER,
        description=(
            "What an open app asks, relayed by the front on behalf of its viewer: read "
            "rows or aggregates of a declared table, set a declared field to a declared "
            "value (with the row's `_revision`), add a row with declared columns, start "
            "a declared agent, read its runs and what they concluded. Allowed only when "
            "BOTH the app's author and its viewer may do it."
        ),
        mcp=None,
        rest=RestBinding("POST", "/api/me/apps/{app_id}/call"),
    ),
]
