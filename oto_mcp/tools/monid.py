"""Monid — the paid gateway to the data endpoints of ~70 providers.

Wraps `oto.tools.monid.MonidClient` (`/v1` API, Bearer, OpenAPI contract 0.1.0). keyed
`api_key`, `apify` regime: BYO by default, platform key on explicit grant — each
call is debited from the prepaid wallet of the Monid workspace whose key is used.

**Four tools, one per object, the verb in `op`, the default is a read** (ADR 0047):
`monid_endpoint` (`discover` → `inspect`), `monid_run` (the only one that spends),
`monid_runs` (`list` / `get` / `stop`), `monid_wallet`. An argument that does not apply
to the chosen `op` is REFUSED, never ignored.

**No dry_run on `monid_run`**, like the other paid data calls
(`apify_run*`, theirstack): the default dry-run stays reserved for actions that leave
the organization. The protection of a paid call is the price read at `inspect` and the
volume parameters set at launch.

⚠️ **The platform key serves a Monid workspace SHARED** by all the orgs that
have a grant. Its list of runs is therefore the others' too (inputs, outputs, and
identifiers that would open `get` and `stop`): `monid_runs(op="list")` is refused under
this key, naming it. `get` and `stop` stay open by run identifier — opaque,
returned to whoever launched it (apify, firecrawl precedent). The balance is not served either
(`monid_wallet` refused under this key, naming it): it is that of the platform's shared
wallet, not that of the org holding a grant — a launch short of
funds says so through its 402.

⚠️ **The launch has a time budget, and it is short on purpose.** An MCP client
hangs up around 60 s, and a runner worker REPLAYS a call that overruns — hence pays twice.
Everything is counted from the tool's entry, key resolution included: the
POST read gets what remains of `_RUN_BUDGET_S` once the connection
(`_CONNECT_S`, set by the client) and the time already spent are removed, at most `_RUN_READ_S`; if
less than `_RUN_READ_MIN_S` remains, the launch is NOT sent (nothing went out, nothing
is billed). Then the wait for an accepted run until the same deadline; a re-read
started just before overruns it by ~10 s at most. Worst case ≈ 55 s **in per-socket timeouts**,
as long as the connection is established on the first try: each unreachable address adds
up to 10 s, and DNS resolution is not bounded. A synchronous provider slower
than the granted read therefore yields an UNKNOWN outcome: the run may exist.

⚠️ **An unknown outcome is never retried here, and the refusal says so.** Monid has no
idempotency key: relaunching a launch lost in flight can pay twice. The client
flags these cases (`may_have_run`); the tool refuses by forbidding it first, then by
naming `monid_runs(op="list")` — or, under the platform key whose list is
closed, an administrator. This refusal goes out as `INTERNAL_ERROR`, not `INVALID_PARAMS` (a
DELIBERATE departure from the named refusal of the other cases): an "invalid argument" tells the agent to fix
and call again, which is the exact path to double payment. Accepted consequence: not being
an "expected" error (`error_taxonomy._is_expected_error`), each unknown outcome
goes up to Sentry — a run possibly paid twice deserves that signal, and a synchronous
provider slower than the granted read will produce some.

**A failing re-read is not a tool failure**: the run was accepted, it is
returned with the next step, and the failure is logged. A re-read that returns something other
than THIS run (empty body, list, other identifier) counts as a failure: it never
replaces the accepted run.

**Metering** (pattern `tools/theirstack.py`): a launch that returns a run counts 1 —
`note_call_trace` always, `record_platform_usage` only under the platform key. A
quota debit that fails never hides an accepted run: it is logged with
the run's identifier (to catch up the count), and the envelope is returned.

Client calls are written in plain sight (`client.run(…)`): that is what makes them
checkable by the version-skew probe (`test_tools_client_methods_exist`). The bounds,
the translation of refusals, a run's envelope and the guards live in `monid_socle.py`.
"""
from __future__ import annotations

import logging
import time
from typing import Literal, Optional

import requests
from fastmcp import FastMCP
from oto.tools.monid.client import (MonidClient, MonidHTTPError, MonidProtocolError,
                                    is_terminal)

from .. import access, session_org
from ..connectors import verify as connector_verify
from .monid_socle import (_CONNECT_S, _DISCOVER_LIMIT_MAX, _DROP_ENDPOINT, _DROP_RUN,
                          _RUN_BUDGET_S, _RUN_READ_MIN_S, _RUN_READ_S, _RUNS_LIMIT_MAX,
                          _WAIT_MAX_S, _appel, _bad, _borne, _cause, _enveloppe,
                          _est_un_run, _garde_liste, _garde_solde, _hors_op, _projeter,
                          _relecture_ratee)

logger = logging.getLogger(__name__)


# --- the probe ----------------------------------------------------------------

def _verify(fields: dict, config: dict | None = None) -> Optional[dict]:  # noqa: ARG001
    """"Test the connection" probe: `GET /v1/auth/whoami`, free, no side effect
    — never a run, never the wallet. Returns WHO the key authenticates (workspace,
    user) when Monid names them; the key prefix is not checked here (the
    contract and the CLI do not agree on it)."""
    try:
        moi = MonidClient(api_key=fields["key"]).whoami()
    except MonidHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(
                "Monid rejects this key (401): missing, malformed or revoked — create one "
                "in the Monid dashboard (API keys)." if e.status_code == 401 else
                "Monid recognizes the key but does not tie it to any workspace (403).") from None
        raise
    moi = moi if isinstance(moi, dict) else {}
    ws = moi.get("workspace") if isinstance(moi.get("workspace"), dict) else {}
    user = moi.get("user") if isinstance(moi.get("user"), dict) else {}
    identite = {k: v for k, v in (("workspace_id", ws.get("workspaceId")),
                                  ("workspace", ws.get("name") or ws.get("slug")),
                                  ("user_id", user.get("userId"))) if v}
    return {"identity": identite} if identite else None


def register(mcp: FastMCP) -> None:
    connector_verify.register("monid", _verify)

    def _client() -> tuple[MonidClient, bool]:
        key, is_platform = access.resolve_api_key("monid")
        return MonidClient(api_key=key), is_platform

    @mcp.tool()
    def monid_endpoint(
        op: Literal["discover", "inspect"] = "discover",
        q: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 10,
        min_score: Optional[float] = None,
        provider: Optional[str] = None,
        endpoint: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """Find and read Monid endpoints — Monid is a paid gateway to ~2,000 data endpoints
        from ~70 providers (web search, scraping, contact enrichment, social media…)
        behind one key.

        - **"discover"** (default): describe the need in plain words (`q`). Returns —
          `{items: [{provider, providerDisplayName, endpoint, displayName,
          displayDescription, price, tags, categories}], total, projection}`: `total`
          counts matches before `limit`, there is no cursor; `providerDisplayDescription`
          and `supportedX402Networks` are dropped and named in `projection` (`full=True`
          keeps them).
        - **"inspect"**: one endpoint by `provider` + `endpoint`, exactly as discover
          returned them. Returns — Monid's card untouched: `input` (JSON Schemas for
          `pathParams`, `queryParams`, `body`, plus `bodyType`), `price` (may be absent),
          `notes`, `metrics`, `docUrl`.

        ALWAYS inspect before `monid_run`: the input schema and the live price are only
        there. Price `type` is open (PER_CALL, PER_RESULT with `flatFee`, PER_UNIT,
        TIERED, PER_UNIT_MATRIX…). For TIERED and PER_UNIT_MATRIX, `price.amount` is only
        a base and can be 0 on a paid endpoint: the actual rate is in `default`, `tiers`
        and `variants` — a tier or variant matched on your input applies, and a tier keyed
        on `output` is known only after the run, so read the cost as a floor. Volume
        inputs (`maxItems`, `limit`) often apply PER QUERY: 3 search terms × 10 items can
        bill 30 results.

        Args:
            op: "discover" | "inspect".
            q: op="discover" — the need in plain words, 1-1000 characters.
            category: op="discover" — a category id (lowercase slug, e.g. `lead-generation`).
            limit: op="discover" — cards returned, 1-40.
            min_score: op="discover" — relevance floor, 0-2.
            provider: op="inspect" — provider slug as discover returned it (may contain dots).
            endpoint: op="inspect" — endpoint path as discover returned it (starts with `/`).
            full: op="discover" — whole cards instead of projected ones.
        """
        if op == "inspect":
            _hors_op("inspect", q=q, category=category, min_score=min_score,
                     limit=limit != 10, full=full)
            if not provider or not endpoint:
                raise _bad("op='inspect': `provider` and `endpoint` required, as "
                           "discover returned them.")
            client, is_platform = _client()
            return _appel(lambda: client.inspect(provider, endpoint), is_platform=is_platform)
        if op != "discover":
            raise _bad(f"Invalid `op`: {op!r} (expected: discover, inspect).")
        _hors_op("discover", provider=provider, endpoint=endpoint)
        if not q:
            raise _bad("op='discover': `q` required — describe the need in plain words.")
        _borne("limit", limit, 1, _DISCOVER_LIMIT_MAX)
        client, is_platform = _client()
        page = _appel(lambda: client.discover(q, limit=limit, category=category,
                                              min_score=min_score), is_platform=is_platform)
        return _projeter(page, _DROP_ENDPOINT, full)

    @mcp.tool()
    def monid_run(
        provider: str,
        endpoint: str,
        body: Optional[dict] = None,
        query_params: Optional[dict] = None,
        path_params: Optional[dict] = None,
        wait_seconds: int = 20,
    ) -> dict:
        """Run a Monid endpoint — SPENDS MONEY: the call is debited from the connected
        Monid wallet. Run only an endpoint you inspected (`monid_endpoint(op="inspect")`);
        on your own Monid key, check `monid_wallet` first when the run may be costly.

        Put each input where inspect's schema puts it — `body`, `query_params` or
        `path_params`, never flat — and keep volume inputs (`maxItems`, `limit`) small.

        Waits up to `wait_seconds` for an accepted run to finish. A provider that holds
        the launch longer than ~35 s yields an UNKNOWN-outcome refusal: the run may exist
        and be billed. NEVER retry a run whose outcome is unknown — retrying can pay
        twice; the refusal says where to look for it (`monid_runs(op="list")` on your own
        Monid key, an admin under the platform key).

        Returns — `{run, done, provider_ok, cost_usd, next_step}`: `run` = Monid's run as
        returned (`runId`, `status`, `output`, `providerResponse`…); `done` = terminal
        status; `provider_ok` = COMPLETED with a 2xx provider answer; null while running,
        or when Monid reports no provider HTTP status (then read `next_step`);
        `cost_usd` = the cost Monid reports (null until settled); `next_step` = what to do,
        null when the result is ready. Run status is not provider status: COMPLETED with
        a provider 404 means "not found", and is not billed.

        Args:
            provider: provider slug, exactly as discover/inspect returned it.
            endpoint: endpoint path, exactly as discover/inspect returned it.
            body: JSON body fields, per inspect's `input.body` schema.
            query_params: query-string fields, per inspect's `input.queryParams` schema.
            path_params: path fields, per inspect's `input.pathParams` schema.
            wait_seconds: 0-40 — seconds to poll an accepted run after the launch returns; 0 = no polling (a sync provider still holds the launch up to ~35 s).
        """
        _borne("wait_seconds", wait_seconds, 0, _WAIT_MAX_S)
        debut = time.monotonic()
        client, is_platform = _client()
        # The read gets what REMAINS of the budget: key resolution may have eaten some.
        # Too little → nothing is sent (so nothing is billed), rather than a launch
        # doomed to end in an unknown outcome or to overrun the client's hang-up.
        lecture = min(_RUN_READ_S, _RUN_BUDGET_S - _CONNECT_S - (time.monotonic() - debut))
        if lecture < _RUN_READ_MIN_S:
            raise _bad("Launch not sent: key resolution consumed the call's time "
                       "budget. Nothing went out or was billed, try again.")
        run = _appel(lambda: client.run(provider, endpoint, body=body,
                                        query_params=query_params, path_params=path_params,
                                        timeout=lecture), is_platform=is_platform)
        # A run came back: it counts, whatever becomes of it next.
        session_org.note_call_trace(quantity=1)
        if is_platform:
            try:
                access.record_platform_usage("monid", 1)
            except Exception:
                # The run is launched and billed: hiding its identifier over a counter
                # would push the agent to relaunch, hence to pay twice.
                logger.warning("monid_run: platform quota debit FAILED for run %s "
                               "(run launched and returned, the quota did NOT move)",
                               run.get("runId"), exc_info=True)
        rid = run["runId"]
        if is_terminal(run) or wait_seconds == 0:
            return _enveloppe(run, run_id=rid)
        reste = min(wait_seconds, _RUN_BUDGET_S - (time.monotonic() - debut))
        if reste <= 0:
            return _enveloppe(run, run_id=rid)
        try:
            relu = client.wait_for_run(rid, max_wait_s=reste)
        except (MonidHTTPError, MonidProtocolError, requests.exceptions.RequestException,
                ValueError) as e:
            logger.warning("monid_run: re-read of run %s failed (%s)", rid, _cause(e))
            return _enveloppe(run, run_id=rid, relecture=_relecture_ratee(run, _cause(e)))
        if not _est_un_run(relu, rid):
            logger.warning("monid_run: re-read of run %s unreadable (%s)", rid,
                           type(relu).__name__)
            return _enveloppe(run, run_id=rid,
                              relecture=_relecture_ratee(run, "unreadable response"))
        return _enveloppe(relu, run_id=rid)

    @mcp.tool()
    def monid_runs(
        op: Literal["list", "get", "stop"] = "list",
        run_id: Optional[str] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
        wait_seconds: int = 0,
        full: bool = False,
    ) -> dict:
        """Monid runs of the connected workspace — list them, read one, or stop one.

        - **"list"** (default): newest first. Returns — `{items, cursor, projection}`:
          pass `cursor` back for the next page, null on the last one. Items carry `runId`,
          `provider`, `endpoint`, `status`, `cost`, `createdAt`… but not `input`/`output`
          (read one with "get"); `caller` is dropped and named in `projection`
          (`full=True` keeps it). Look here for a run whose outcome was unknown. Refused
          under the platform key: that shared workspace holds other organizations' runs.
        - **"get"**: one run by `run_id`, with its `output`; `wait_seconds` waits for it
          to finish. Returns — the `{run, done, provider_ok, cost_usd, next_step}`
          envelope of `monid_run`.
        - **"stop"**: stop a running run, and its spending. Asynchronous. Returns —
          `{run_id, status, message, next_step}`; read it again with "get": a metered
          run settles COMPLETED and bills what it used.

        Args:
            op: "list" | "get" | "stop".
            run_id: op="get"/"stop" — the Monid run id (`runId`), not the `_run_id` token from `run_start`.
            limit: op="list" — runs per page, 1-100.
            cursor: op="list" — the previous page's `cursor`.
            status: op="list" — READY, RUNNING, STOPPING, COMPLETED, FAILED, BLOCKED, STOPPED or TIMED_OUT.
            wait_seconds: op="get" — 0-40 s to wait for the run to finish.
            full: op="list" — whole items instead of projected ones.
        """
        if op not in ("list", "get", "stop"):
            raise _bad(f"Invalid `op`: {op!r} (expected: list, get, stop).")
        if op == "list":
            _hors_op("list", run_id=run_id, wait_seconds=wait_seconds != 0)
            _borne("limit", limit, 1, _RUNS_LIMIT_MAX)
            client, is_platform = _client()
            _garde_liste(is_platform)
            page = _appel(lambda: client.list_runs(limit=limit, cursor=cursor, status=status),
                          is_platform=is_platform)
            if isinstance(page, dict):
                page = {**page, "cursor": page.get("cursor") or None}
            return _projeter(page, _DROP_RUN, full)
        _hors_op(op, limit=limit != 20, cursor=cursor, status=status, full=full,
                 wait_seconds=op == "stop" and wait_seconds != 0)
        if not run_id:
            raise _bad(f"op='{op}': `run_id` required (the `runId` of the Monid run).")
        _borne("wait_seconds", wait_seconds, 0, _WAIT_MAX_S)
        client, is_platform = _client()
        if op == "stop":
            out = _appel(lambda: client.stop_run(run_id), is_platform=is_platform)
            out = out if isinstance(out, dict) else {}
            rid = out.get("runId") or run_id
            return {"run_id": rid, "status": out.get("status"), "message": out.get("message"),
                    "next_step": (f"The stop is asynchronous: re-read the run with monid_runs("
                                  f"op=\"get\", run_id=\"{rid}\") — it ends STOPPED, or "
                                  "COMPLETED if it is billed by usage (it then settles what "
                                  "it consumed).")}
        if wait_seconds:
            run = _appel(lambda: client.wait_for_run(run_id, max_wait_s=wait_seconds),
                         is_platform=is_platform)
        else:
            run = _appel(lambda: client.get_run(run_id), is_platform=is_platform)
        return _enveloppe(run, run_id=run_id)

    @mcp.tool()
    def monid_wallet() -> dict:
        """The connected Monid wallet: spendable `balance` (can be negative) and `held`
        (reserved for runs in flight), in USD. Check it before a costly run. Refused
        under the platform key: that wallet is shared, and its balance is not served.

        Returns — `{balance: {value, currency}, held: {value, currency}}`, as Monid
        returns it.
        """
        client, is_platform = _client()
        _garde_solde(is_platform)
        return _appel(lambda: client.wallet_balance(), is_platform=is_platform)
