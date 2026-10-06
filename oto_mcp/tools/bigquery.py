"""Google BigQuery — oto-core surface (BigQueryClient) exposed per user, multi-account.

Seventh service of the Google account (2026-10-02): scope `bigquery`, granted from its
card. Queries run under the person's identity — their IAM rights, not
those of a shared key — and are billed to the project they designate (`project`).

**Four tools, read-only**:
- `bigquery_catalog` — projects → datasets → tables, one level per call;
- `bigquery_table` — a table's schema (+ FREE preview via `tabledata.list`);
- `bigquery_query` — standard SQL, SELECT only;
- `bigquery_results` — resume a query that didn't finish in time, or fetch the next page.

**The scope allows writing** (`bigquery.readonly` doesn't allow querying, see
`auth/google.SERVICE_SCOPES`): read-only is therefore enforced HERE, not by Google.
Every query first goes through a dry run (`jobs.insert`), whose `statementType`
must be `SELECT` — DML, DDL, scripts and procedures are refused before any
launch. Never inferred from the SQL text: a `WITH … DELETE` or a leading comment
would fool a string reading.

**Cost is bounded on every query**: the dry run estimates the bytes read; above
the cap (`max_gb_billed`, 10 GB by default, 1 TB at most), refusal BEFORE launching,
with the estimate attached. The query then goes out with `maximumBytesBilled` = that cap
— the guard is at Google too, not only on our side.

**Time is bounded**: a tool's REST invocation cuts off at 45 s. The query waits
at most 20 s; not finished → `status="running"` + `job_id`, to resume with
`bigquery_results`. Returned rows are capped (`max_rows` ≤ 1,000, and
~200 KB per page); the rest is read via `page_token`, and the best approach is still to aggregate in
SQL.

**Shared account**: a Google account shared by the org or team is accepted,
as for Gmail (decision of 04/10/2026) — the query then runs with the IAM rights
and billing of whoever connected it. Remote functions and `ML.*`
are billed outside the byte cap.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..auth import google as google_oauth

_GB = 1024 ** 3
_DEFAULT_MAX_GB = 10.0
# HARD cap of a query (≈ $6 at on-demand pricing): the argument cannot exceed it.
_HARD_MAX_GB = 1024.0
_DEFAULT_ROWS = 100
_MAX_ROWS = 1000
_MAX_PREVIEW_ROWS = 100
# Wait on the BigQuery side, under the 45 s of the REST invocation (dry run + token included).
_QUERY_WAIT_MS = 20_000
# Resume: client construction (up to 20 s) + this wait stay under 45 s.
_RESULTS_WAIT_MS = 15_000
# Rendered weight of a page: 1,000 rows of a wide table would weigh megabytes.
# Beyond that, rows are truncated and the rest is read via `page_token`.
_MAX_OUT_BYTES = 200_000
# Identifiers passed to the API (project, dataset, table, job, location): never a
# `/`, a `?` or a `..` — they don't go into a URL path.
_IDENT = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*(:[A-Za-z0-9_\-]+)?$")
_SEGMENT = re.compile(r"^[A-Za-z0-9_\-$]+$")
# A table name allows more (Unicode letters, spaces): we only refuse what
# would change the path or the HTTP request.
_TABLE = re.compile(r"^[^/?#%\\\x00-\x1f]+$")
_LABELS = {"source": "oto"}
_SELECT = "SELECT"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _ident(value: Optional[str], name: str, *, segment: bool = False,
           table: bool = False) -> Optional[str]:
    """A BigQuery identifier safe to pass to the API, or a named refusal.

    `segment`: a single segment (dataset, job, location) — no dot;
    `table`: a table name (more permissive, never `/`, `?`, `#`, `%`, `..`)."""
    if value is None:
        return None
    v = str(value).strip()
    motif = _TABLE if table else _SEGMENT if segment else _IDENT
    if not v or ".." in v or not motif.fullmatch(v):
        raise _bad(f"`{name}`: invalid BigQuery identifier ({v!r}) — letters, digits, "
                   "`_`, `-` only.")
    return v


def _http_error(e, project: Optional[str] = None) -> McpError:
    """`HttpError` → what to do next, read from the BigQuery REASON (never the text)."""
    from oto.tools.google.bigquery.lib.bigquery_client import parse_http_error
    err = parse_http_error(e)
    status, reason, detail = err["status"], err["reason"], err["message"].strip()
    where = f" in project {project}" if project else ""
    if reason == "invalidQuery":
        msg = (f"BigQuery refused the query: {detail} — check the column names "
               "with `bigquery_table` (standard SQL, tables as `project.dataset.table`).")
    elif reason == "bytesBilledLimitExceeded":
        msg = (f"BigQuery stopped the query at the billed-bytes cap: {detail} — "
               "filter more (partition column, fewer columns) or raise "
               "`max_gb_billed`.")
    elif reason == "accessNotConfigured" or "has not been used in project" in detail:
        msg = ("The BigQuery API is not enabled in the Google Cloud project of the OAuth "
               "client that issued the connection — a configuration of that client, to be done by "
               "its administrator (Google Cloud console → APIs & Services → BigQuery "
               f"API); reconnecting the account changes nothing. Google detail: {detail}")
    elif reason == "accessDenied" or status == 403:
        if "jobs.create" in detail:
            msg = (f"Your Google account cannot run queries{where} (permission "
                   "`bigquery.jobs.create`, role \"BigQuery Job User\") — pick another "
                   "billing project (`bigquery_catalog` lists yours) or ask an "
                   f"administrator for this role. Google detail: {detail}")
        else:
            msg = (f"Your Google account has no access to this BigQuery resource: {detail} "
                   "— it needs the \"BigQuery Data Viewer\" role on the dataset.")
    elif reason == "notFound" or status == 404:
        msg = (f"BigQuery cannot find the resource: {detail} — check the name, and "
               "`location` for a dataset outside the US/EU multi-regions.")
    elif reason in ("quotaExceeded", "rateLimitExceeded") or status == 429:
        msg = f"BigQuery: quota or rate limit reached — try again later. Detail: {detail}"
    elif status and status >= 500:
        msg = f"BigQuery is temporarily unavailable (HTTP {status}) — try again later."
    else:
        msg = f"BigQuery refused the request (HTTP {status}, {reason or '?'}): {detail}"
    return _bad(msg)


async def _call(fn, *args, _project: Optional[str] = None, **kwargs):
    """Client call off the event loop; any `HttpError` becomes a named error.

    `_project` is ONLY used for the refusal message (it is not passed to `fn`)."""
    from googleapiclient.errors import HttpError
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except HttpError as e:
        raise _http_error(e, _project)
    except TypeError as e:
        # googleapiclient refuses an identifier outside the pattern (`^[^/]+$`) with a
        # raw TypeError: it's an invalid argument, not an outage.
        raise _bad(f"BigQuery: argument refused ({e}).") from None


def _client_for_user(account: Optional[str] = None):
    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account, service="bigquery")
    except RuntimeError as e:
        raise _bad(str(e))
    from oto.tools.google.bigquery.lib.bigquery_client import BigQueryClient
    return BigQueryClient(credentials=creds)


_GOOGLE_CLIENT_TIMEOUT_S = 20
# oto-backend#867 lot 2 — see gmail.py::_client_for_user_async for the
# rationale (same token-refresh mechanism, same method).
async def _client_for_user_async(account: Optional[str] = None):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_client_for_user, account),
                                      timeout=_GOOGLE_CLIENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise _bad(f"Google did not respond within {_GOOGLE_CLIENT_TIMEOUT_S}s "
                   "(token refresh) — try again.")


async def _billing_project(client, project: Optional[str]) -> str:
    """The project that runs (and pays for) the query: explicit, otherwise the ONLY visible
    project. Several → a refusal that names them, never a random pick (it's an
    invoice)."""
    if project:
        return project
    listed = await _call(client.list_projects, 50)
    ids = [p["id"] for p in listed["projects"] if p.get("id")]
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise _bad("Your Google account sees no BigQuery project: you need one to "
                   "run (and bill) queries.")
    shown = ", ".join(ids[:15]) + (" …" if len(ids) > 15 else "")
    raise _bad(f"Specify `project`, the project that runs and pays for the query: {shown}.")


def _cap_gb(max_gb_billed: Optional[float]) -> float:
    if max_gb_billed is None:
        return _DEFAULT_MAX_GB
    if max_gb_billed <= 0:
        raise _bad("max_gb_billed must be > 0.")
    if max_gb_billed > _HARD_MAX_GB:
        raise _bad(f"max_gb_billed is capped at {_HARD_MAX_GB:g} GB per query.")
    return float(max_gb_billed)


def _gb(n: Any) -> Optional[float]:
    if n in (None, ""):
        return None
    return round(int(n) / _GB, 3)


def _rows_cap(max_rows: int, hard: int = _MAX_ROWS) -> int:
    if max_rows < 1:
        raise _bad("max_rows must be ≥ 1.")
    return min(max_rows, hard)


def _table_view(schema: Optional[dict], raw_rows: Optional[list]) -> dict:
    """Compact view: `columns` (name, type) + `rows` as lists, in schema order."""
    from oto.tools.google.bigquery.lib.bigquery_client import rows_to_records
    fields = (schema or {}).get("fields") or []
    names = [f["name"] for f in fields]
    records = rows_to_records(schema, raw_rows)
    return {
        "columns": [{"name": f["name"], "type": f.get("type"),
                     **({"mode": f["mode"]} if f.get("mode") == "REPEATED" else {})}
                    for f in fields],
        "rows": [[r.get(n) for n in names] for r in records],
    }


def _result(resp: dict, project: str) -> dict:
    """`jobs.query` / `getQueryResults` response → result returned to the agent."""
    ref = resp.get("jobReference") or {}
    job = {"job_id": ref.get("jobId"), "location": ref.get("location") or resp.get("location"),
           "project": ref.get("projectId") or project}
    if not resp.get("jobComplete"):
        return {"status": "running", **job,
                "hint": "query still running — resume with `bigquery_results(job_id, "
                        "project, location)`."}
    out = {"status": "done", **_table_view(resp.get("schema"), resp.get("rows"))}
    poids_tronque = _borner_poids(out)
    out["row_count"] = len(out["rows"])
    out["total_rows"] = int(resp["totalRows"]) if resp.get("totalRows") is not None else None
    for key, src in (("gb_processed", "totalBytesProcessed"), ("gb_billed", "totalBytesBilled")):
        if resp.get(src) is not None:
            out[key] = _gb(resp[src])
    if "cacheHit" in resp:
        out["cache_hit"] = bool(resp["cacheHit"])
    out.update(job)
    if resp.get("pageToken"):
        out["page_token"] = resp["pageToken"]
        out["hint"] = ("rows truncated — next page via `bigquery_results(job_id, "
                       "project, location, page_token)`, or better: aggregate in SQL.")
    if poids_tronque:
        out["truncated_bytes"] = True
        out["hint"] = (f"page truncated to ~{_MAX_OUT_BYTES // 1000} KB ({out['row_count']} "
                       "rows returned) — select fewer columns, reduce `max_rows`, "
                       "or aggregate in SQL.")
    return out


def _borner_poids(out: dict) -> bool:
    """Truncate `out["rows"]` to stay under `_MAX_OUT_BYTES`; true if truncated."""
    total = 0
    for i, row in enumerate(out["rows"]):
        total += len(json.dumps(row, default=str, ensure_ascii=False)) + 1
        if total > _MAX_OUT_BYTES:
            out["rows"] = out["rows"][:i]
            return True
    return False


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def bigquery_catalog(
        project: Optional[str] = None,
        dataset: Optional[str] = None,
        page_token: Optional[str] = None,
        account: Optional[str] = None,
    ) -> dict:
        """Browse BigQuery one level at a time: projects → datasets → tables.

        - no `project`: the projects your Google account can see (pick one as the
          billing `project` of `bigquery_query`);
        - `project`: its datasets (with their location);
        - `project` + `dataset`: its tables and views (type, partition column).

        Data often lives in another project than the one you bill to (e.g.
        `bigquery-public-data`): browse it by name, query it from your own project.

        Args:
            project: project id (e.g. "my-company-dwh").
            dataset: dataset id inside `project`.
            page_token: `next_page_token` of a previous call.
            account: email of the Google account to use (default if omitted).
        """
        if dataset and not project:
            raise _bad("`dataset` requires `project`.")
        project = _ident(project, "project")
        dataset = _ident(dataset, "dataset", segment=True)
        client = await _client_for_user_async(account)
        if not project:
            return {"level": "projects", **await _call(client.list_projects, 100, page_token)}
        if not dataset:
            return {"level": "datasets", "project": project,
                    **await _call(client.list_datasets, project, 200, page_token,
                                  _project=project)}
        return {"level": "tables", "project": project, "dataset": dataset,
                **await _call(client.list_tables, project, dataset, 200, page_token,
                              _project=project)}

    @mcp.tool()
    async def bigquery_table(
        table: str,
        preview_rows: int = 0,
        project: Optional[str] = None,
        account: Optional[str] = None,
    ) -> dict:
        """A BigQuery table's schema, size and partitioning — plus an optional preview.

        The preview reads stored rows directly (`tabledata.list`): free, no query job,
        nothing billed. It doesn't work on views — query those instead.

        Returns {table, type, columns: [{name, type, mode, description?}] (nested
        fields as `a.b`), num_rows, gb, partitioning, clustering, view_sql?, preview?}.

        Args:
            table: `project.dataset.table` (or `dataset.table` with `project`).
            preview_rows: rows to preview (0 = none, max 100).
            project: default project when `table` is `dataset.table`.
            account: email of the Google account to use (default if omitted).
        """
        from oto.tools.google.bigquery.lib.bigquery_client import (
            flatten_schema, split_table_ref)
        try:
            p, d, t = split_table_ref(table, project)
        except ValueError as e:
            raise _bad(str(e))
        p, d, t = (_ident(p, "project"), _ident(d, "dataset", segment=True),
                   _ident(t, "table", table=True))
        if not 0 <= preview_rows <= _MAX_PREVIEW_ROWS:
            raise _bad(f"preview_rows must be between 0 and {_MAX_PREVIEW_ROWS}.")
        client = await _client_for_user_async(account)
        meta = await _call(client.get_table, p, d, t, _project=p)
        part = meta.get("timePartitioning") or meta.get("rangePartitioning")
        out: dict = {
            "table": f"{p}.{d}.{t}", "type": meta.get("type"),
            "location": meta.get("location"),
            "description": meta.get("description"),
            "columns": flatten_schema(meta.get("schema")),
            "num_rows": int(meta["numRows"]) if meta.get("numRows") is not None else None,
            "gb": _gb(meta.get("numBytes")),
            "partitioning": ({"type": part.get("type"), "field": part.get("field")
                              or ("_PARTITIONTIME" if part.get("type") else None),
                              "require_filter": meta.get("requirePartitionFilter")}
                             if part else None),
            "clustering": (meta.get("clustering") or {}).get("fields"),
            "view_sql": (meta.get("view") or {}).get("query"),
        }
        if preview_rows and meta.get("type") == "TABLE":
            page = await _call(client.list_rows, p, d, t, preview_rows, _project=p)
            out["preview"] = _table_view(meta.get("schema"), page.get("rows"))["rows"]
        elif preview_rows:
            out["preview_note"] = (f"preview impossible on a {(meta.get('type') or 'resource').lower()} "
                                   "(only a stored table can be read without a query) — use `bigquery_query`.")
        return {k: v for k, v in out.items() if v is not None}

    @mcp.tool()
    async def bigquery_query(
        sql: str,
        project: Optional[str] = None,
        params: Optional[dict] = None,
        max_rows: int = _DEFAULT_ROWS,
        max_gb_billed: Optional[float] = None,
        dry_run: bool = False,
        location: Optional[str] = None,
        account: Optional[str] = None,
    ) -> dict:
        """Run a read-only SQL query (GoogleSQL / standard SQL) on BigQuery.

        Account: your own Google account, or an account your org or team shared —
        a shared account runs with ITS IAM rights and bills ITS projects (the
        admin's who connected it).

        SELECT only: every query is dry-run first and anything else (INSERT, UPDATE,
        DELETE, MERGE, CREATE, scripts, CALL) is refused before it runs. Name tables
        fully: `project.dataset.table`.

        Cost guard: the dry run estimates the bytes read; above `max_gb_billed`
        (default 10 GB, max 1024) the query is refused before running, and BigQuery
        also enforces the cap (`maximumBytesBilled`). On-demand pricing bills bytes
        SCANNED, not rows returned — `LIMIT` doesn't reduce it; selecting fewer
        columns and filtering on the partition column does. The cap counts bytes
        scanned only: remote functions and `ML.*` (e.g. `ML.GENERATE_TEXT`) are
        billed outside it.

        `dry_run=True` only validates and estimates: {statement_type, gb_processed,
        within_cap, columns, referenced_tables}.

        Returns {status: "done", columns, rows (lists, in column order), row_count,
        total_rows, gb_processed, gb_billed, cache_hit, job_id, location, project,
        page_token?} — or {status: "running", job_id, …} if it takes longer than
        ~20 s: resume with `bigquery_results`. Aggregate in SQL rather than paging
        through raw rows.

        Args:
            sql: the query. Use `@name` placeholders with `params` instead of
                pasting values into the SQL.
            project: billing project that runs the query (your own, even to read
                public or other-project data). Omitted: your only visible project.
            params: named parameters, e.g. {"since": "2026-01-01", "ids": [1, 2]}
                (bool / int / float / string, or a list of one type; CAST in SQL
                for DATE/TIMESTAMP).
            max_rows: rows returned in this call (default 100, max 1000).
            max_gb_billed: cost cap in GB for this query (default 10).
            dry_run: validate and estimate only, run nothing.
            location: job location for datasets outside the US/EU multi-regions
                (e.g. "europe-west1").
            account: email of the Google account to use (default if omitted).
        """
        if not sql or not sql.strip():
            raise _bad("sql is empty.")
        project = _ident(project, "project")
        location = _ident(location, "location", segment=True)
        cap = _cap_gb(max_gb_billed)
        rows = _rows_cap(max_rows)
        from oto.tools.google.bigquery.lib.bigquery_client import query_parameters
        try:
            query_parameters(params)
        except ValueError as e:
            raise _bad(str(e))
        client = await _client_for_user_async(account)
        billing = await _billing_project(client, project)

        plan = await _call(client.dry_run, sql, billing, location=location, params=params,
                           _project=billing)
        stype = plan.get("statement_type")
        if stype != _SELECT:
            raise _bad(f"Read-only: only SELECT queries are accepted (this one "
                       f"is {stype or 'of unknown type'}). Nothing was executed.")
        estimate_gb = round(plan["bytes_processed"] / _GB, 3)
        within = plan["bytes_processed"] <= cap * _GB
        if dry_run:
            return {"dry_run": True, "statement_type": stype, "gb_processed": estimate_gb,
                    "max_gb_billed": cap, "within_cap": within,
                    "columns": [{"name": f["name"], "type": f.get("type")}
                                for f in (plan.get("schema") or {}).get("fields") or []],
                    "referenced_tables": plan["referenced_tables"], "project": billing}
        if not within:
            raise _bad(f"Query refused before execution: it would read ~{estimate_gb:g} GB, "
                       f"above the cap of {cap:g} GB. Reduce the columns, filter on the "
                       "partition column, or raise `max_gb_billed` if intended.")

        resp = await _call(client.query, sql, billing, location=location, params=params,
                           max_results=rows, timeout_ms=_QUERY_WAIT_MS,
                           maximum_bytes_billed=int(cap * _GB), labels=_LABELS,
                           _project=billing)
        return _result(resp, billing)

    @mcp.tool()
    async def bigquery_results(
        job_id: str,
        project: str,
        location: Optional[str] = None,
        page_token: Optional[str] = None,
        max_rows: int = _DEFAULT_ROWS,
        account: Optional[str] = None,
    ) -> dict:
        """Results of a BigQuery query job: resume one still running, or read the
        next page (`page_token`). Same shape as `bigquery_query`.

        Args:
            job_id: `job_id` returned by `bigquery_query`.
            project: `project` returned by `bigquery_query`.
            location: `location` returned by `bigquery_query` (required outside US/EU).
            page_token: `page_token` of the previous page.
            max_rows: rows in this page (default 100, max 1000).
            account: email of the Google account to use (default if omitted).
        """
        rows = _rows_cap(max_rows)
        job_id = _ident(job_id, "job_id", segment=True)
        project = _ident(project, "project")
        location = _ident(location, "location", segment=True)
        client = await _client_for_user_async(account)
        resp = await _call(client.get_query_results, project, job_id, location=location,
                           page_token=page_token, max_results=rows,
                           timeout_ms=_RESULTS_WAIT_MS, _project=project)
        return _result(resp, project)
