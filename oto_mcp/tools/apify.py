"""Apify — rent an already-written scraper instead of writing one (apify.com).

Wraps `oto.tools.apify.client.ApifyClient` (API v2). keyed `api_key` (Bearer),
byo-only: each user/org connects THEIR account — an actor is billed by usage.

Apify is not a scraper but a **catalog of scrapers** (the *actors*): Google
Maps, LinkedIn, Instagram, Amazon, Booking, TikTok… Each actor has its own input
JSON, described on its Store page. Hence the path:

1. `apify_store_search("google maps")` → find the actor and its identifier.
2. `apify_actor(id)` → read its card (default options, memory, timeout).
3. `apify_run_sync(id, input)` → launch and fetch the results (≤ 300 s),
   or `apify_run` + `apify_run_status` + `apify_dataset_items` for a long job.

A running actor costs money: setting `max_items` / `timeout_secs` /
`max_total_charge_usd` AT LAUNCH is the only protection — afterwards, it is billed.

Calls to the client are written out in plain (`_client().run(…)`) and not dispatched
by name: that is what makes them verifiable by the version-skew probe.
"""
from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS
from oto.tools.common.errors import UpstreamHTTPError

from .. import access, session_org
from ..connectors import verify as connector_verify

if TYPE_CHECKING:
    from oto.tools.apify.client import ApifyClient

logger = logging.getLogger(__name__)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Apify rejected the token (HTTP {status}) — check the key configured "
                "on this connector (Apify: Settings → API & Integrations).")
    if status == 402:
        return ("Apify: insufficient credits/plan (402) — top up the account, or "
                "narrow the run's scope (max_items).")
    if status == 404:
        return (f"Apify: not found (404) — check the identifier. An actor is written "
                f"`username/actor-name` (or its id), a run/dataset is an opaque id. {e.body}")
    if status == 408:
        return ("Apify: the run exceeded the 300 s of synchronous mode — relaunch with "
                "`apify_run`, then `apify_run_status` and `apify_dataset_items`.")
    if status == 429:
        return "Apify: too many requests (429) — retry in a moment."
    if status in (500, 502, 503, 504):
        return f"Apify is temporarily unavailable (HTTP {status}) — retry later."
    return f"Apify refused the request (HTTP {status}): {e.body}"


# The states a run never leaves. Its cost is not final there yet: see `_cout`.
_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"})

# `apify_run_sync` waits this long in all, by slices of at most `_WAIT_SLICE_S` —
# Apify's own cap on `waitForFinish`, and the 300 s the synchronous endpoint used to hold.
_SYNC_BUDGET_S = 300
_WAIT_SLICE_S = 60


def _maintenant() -> float:
    """The clock of `apify_run_sync`'s budget — a seam, so a test can drive it."""
    return time.monotonic()


def _run_of(out: Any) -> Optional[dict]:
    """The run object of a `run` / `run_status` answer — the `{data: …}` envelope of
    the API v2, or a flat body read as the run itself."""
    run = out.get("data") if isinstance(out, dict) else None
    if not isinstance(run, dict):
        run = out if isinstance(out, dict) else None
    return run


def _microdollars(cout: float) -> int:
    """Run cost in micro-dollars, rounded UP — the unit and the rounding of Jev
    (`tools/jev.py::_microdollars`: the `round` before `ceil` keeps float noise from
    billing one micro-dollar too many)."""
    return math.ceil(round(float(cout) * 1e6, 6))


def _gratuit() -> None:
    """Trace the call as FREE (`quantity=0`). A call never leaves its quantity NULL:
    the ledger reads NULL as 1 (`tool_calls.quantity`), and 1 µ$ per read, mixed into
    the micro-dollars of the finished runs, would be billed."""
    session_org.note_call_trace(quantity=0)


def _usd(v: Any) -> Optional[float]:
    """A dollar amount Apify served, or None when it is not a finite, positive number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v) or v < 0:
        return None
    return float(v)


def _cout(run: dict, run_id: str, outil: str) -> Optional[float]:
    """What a FINISHED run costs: `usageTotalUsd` plus the events Apify has counted but
    not yet booked.

    ⚠️ A pay-per-event actor (Google Maps, most of the Store) reaches SUCCEEDED before
    Apify books its events: measured on 14/10/2026, run Dlvmeb19jTs5K0k1a read $0.0002
    at SUCCEEDED (the actor start alone) and $0.0122 nine seconds later, once
    `accountedChargedEventCounts` caught up with `chargedEventCounts`. The biller keeps
    the first figure of a job, so the gap is priced here from the actor's own event
    prices — once booked, the gap is 0 and the figure is the same. None when
    `usageTotalUsd` cannot be read.

    Nothing is guessed: without the booked counts, the gap cannot be told from what
    `usageTotalUsd` already holds — adding every counted event could bill it twice —
    so the figure is `usageTotalUsd` alone, and the log SAYS the gap was not priced.
    An event whose price or counts cannot be read is left out the same way, said."""
    total = _usd(run.get("usageTotalUsd"))
    if total is None:
        return None
    comptes = run.get("chargedEventCounts")
    if not isinstance(comptes, dict) or not comptes:
        return total
    reserves = run.get("accountedChargedEventCounts")
    prix = (((run.get("pricingInfo") or {}).get("pricingPerEvent") or {})
            .get("actorChargeEvents"))
    if not isinstance(reserves, dict) or not isinstance(prix, dict):
        logger.warning("%s %s: counted events without readable booked counts or prices "
                       "(%s, %s), the not-yet-booked gap is not priced",
                       outil, run_id, type(reserves).__name__, type(prix).__name__)
        return total
    for evenement, n in comptes.items():
        unitaire = _usd((prix.get(evenement) or {}).get("eventPriceUsd"))
        deja = reserves.get(evenement, 0)
        if unitaire is None or isinstance(n, bool) or not isinstance(n, int) \
                or isinstance(deja, bool) or not isinstance(deja, int):
            logger.warning("%s %s: event %r has no readable price or counts, its "
                           "not-yet-booked part is not priced", outil, run_id, evenement)
            continue
        total += max(0, n - deja) * unitaire
    return total


def _releve(out: Any, run_id: str, outil: str = "apify_run_status") -> None:
    """Record a FINISHED run's real cost on the current call's trace: `quantity` in
    micro-dollars, and the run as the JOB of the line (`apify_run_id`, served as
    `job_id` by the billing lens) — a finished run polled N times traces N times the
    same cost, and the biller counts a job once.

    ⚠️ While the run is not in a terminal state, `usageTotalUsd` is "what it cost SO
    FAR": the poll traces 0 WITHOUT the job — the poll itself is free, and a biller
    that keeps the first figure it sees for a job would keep a partial one. A response
    whose state or cost cannot be read traces 0 without the job too, and SAYS so in
    the log (run id and type only): an absent figure is not a zero, a guessed one would
    be billed, and a silent one would be lost. A terminal run at 0 traces 0 with its
    job — measured, nothing to bill."""
    run = _run_of(out)
    if run is None:
        logger.warning("%s %s: unreadable response (%s), cost not recorded",
                       outil, run_id, type(out).__name__)
        _gratuit()
        return
    status = run.get("status")
    if not isinstance(status, str):
        logger.warning("%s %s: unreadable status (%s), cost not recorded",
                       outil, run_id, type(status).__name__)
        _gratuit()
        return
    if status not in _TERMINAL:
        _gratuit()
        return
    cout = _cout(run, run_id, outil)
    if cout is None:
        logger.warning("%s %s: finished (%s) without a readable cost (%s), cost not "
                       "recorded", outil, run_id, status,
                       type(run.get("usageTotalUsd")).__name__)
        _gratuit()
        return
    rid = run.get("id")
    session_org.note_call_trace(
        quantity=_microdollars(cout),
        apify_run_id=rid if isinstance(rid, str) and rid else run_id)


_LIMITS_URL = "https://api.apify.com/v2/users/me/limits"


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001
    """"Test the connection" probe — covers `auth+quota`.

    `GET /v2/users/me/limits`: authenticated, free, and the only Apify call that says
    how much of the month is left — `limits.maxMonthlyUsageUsd` against
    `current.monthlyUsageUsd`. Listing actors (the former probe) authenticated just
    as well on an account that had hit its cap, and every run then failed upstream.

    The balance is in US DOLLARS of platform usage, not in credits: Apify bills
    compute and proxy, not results.
    """
    import requests

    r = requests.get(_LIMITS_URL, headers={"Authorization": f"Bearer {fields['key']}"},
                     timeout=15)
    if r.status_code in (401, 403):
        raise connector_verify.NonAutorise(
            f"Apify refused the token (HTTP {r.status_code}).")
    r.raise_for_status()
    data = (r.json() or {}).get("data") or {}
    plafond = connector_verify.nombre_lu((data.get("limits") or {}).get("maxMonthlyUsageUsd"))
    consomme = connector_verify.nombre_lu((data.get("current") or {}).get("monthlyUsageUsd"))
    if plafond is None or consomme is None:
        raise RuntimeError(
            f"Apify answered without a readable monthly usage: {str(data)[:200]}")
    restant = round(plafond - consomme, 2)
    if restant <= 0:
        raise connector_verify.QuotaEpuise(
            f"The Apify token is good, but the monthly usage cap is reached "
            f"(${consomme:.2f} of ${plafond:.2f}). Raise the cap or wait for the next "
            "cycle — reconnecting would change nothing.")
    return {"quota": {"restant": restant, "unite": "usd", "limite": plafond}}


def _client() -> ApifyClient:
    from oto.tools.apify.client import ApifyClient

    key, _ = access.resolve_api_key("apify")
    return ApifyClient(api_key=key)


@contextmanager
def _upstream():
    """Turn an Apify refusal into an actionable tool error."""
    try:
        yield
    except ValueError as e:
        raise _bad(str(e))
    except UpstreamHTTPError as e:
        raise _bad(_upstream_message(e))


@contextmanager
def _upstream_du_run(run_id: str):
    """`_upstream` for a run that already exists: the refusal names the run, and
    says how to pick it up — never a bare error that leads to a second launch."""
    reprise = (f" Apify run {run_id} was started: pick it up with "
               f"`apify_run_status(run_id=\"{run_id}\")`, then `apify_dataset_items`.")
    try:
        yield
    except ValueError as e:
        raise _bad(str(e) + reprise)
    except UpstreamHTTPError as e:
        raise _bad(_upstream_message(e) + reprise)


def lancer_acteur(
    actor_id: str,
    run_input: Optional[dict],
    *,
    outil: str,
    max_items: Optional[int] = None,
    limit: Optional[int] = None,
    fields: Optional[list[str]] = None,
    timeout_secs: Optional[int] = None,
    memory_mbytes: Optional[int] = None,
    max_total_charge_usd: Optional[float] = None,
) -> Any:
    """Run `actor_id`, wait for it up to 300 s, bill it once finished, return its items.

    The body of `apify_run_sync`, shared with the dedicated actor tools
    (`tools/apify_actors.py`): `outil` is the tool that answers, named in the cost
    warnings. Returns the LIST of items — or `{error, run_id, status, items}` for a
    run that ended other than SUCCEEDED, or whose output could not be read.
    """
    # Not the synchronous endpoint: it answers with the items only — no run id,
    # no cost — so a run through it could never be billed. Start the run, wait
    # for it by slices, then read its dataset: the finished run carries its cost.
    debut = _maintenant()
    client = _client()
    with _upstream():
        out = client.run(
            actor_id, run_input=run_input, max_items=max_items,
            timeout_secs=timeout_secs, memory_mbytes=memory_mbytes,
            max_total_charge_usd=max_total_charge_usd,
            wait_for_finish=_WAIT_SLICE_S)
    run = _run_of(out)
    run_id = run.get("id") if run else None
    if not isinstance(run_id, str) or not run_id:
        raise _bad(f"Apify started {actor_id} without a readable run id — "
                   "find the run with `apify_actors` / the Apify console.")
    # From here the run EXISTS: every refusal names it, so the agent can pick it
    # up with `apify_run_status` instead of launching (and paying for) it again.
    with _upstream_du_run(run_id):
        while run.get("status") not in _TERMINAL:
            reste = _SYNC_BUDGET_S - (_maintenant() - debut)
            if reste < 1:
                # Never aborted behind the agent's back, never billed here: the
                # run is still going, `apify_run_status` records its cost once
                # it has finished.
                _gratuit()
                raise _bad(
                    f"Apify run {run_id} ({actor_id}) is still {run.get('status')} "
                    f"after {_SYNC_BUDGET_S} s. It keeps running: poll it with "
                    f"`apify_run_status(run_id=\"{run_id}\")`, then read its output "
                    "with `apify_dataset_items`.")
            out = client.run_status(
                run_id, wait_for_finish=min(_WAIT_SLICE_S, int(reste)))
            run = _run_of(out) or {}
    # Finished: billed NOW, before reading the output — a dataset that cannot be
    # read must not leave a finished run unbilled. Only an ANSWERED call is billed
    # (`tool_calls.ok`), so from here nothing is raised: a failed run or an
    # unreadable dataset is answered, with the run, never thrown.
    _releve(out, run_id, outil=outil)
    status = run.get("status")
    dataset_id = run.get("defaultDatasetId")
    try:
        items = client.dataset_items(dataset_id, limit=limit, fields=fields)
    except (ValueError, UpstreamHTTPError) as e:
        detail = (_upstream_message(e) if isinstance(e, UpstreamHTTPError)
                  else str(e))
        return {"error": f"Apify run {run_id} ({actor_id}) ended {status}, but its "
                         f"output could not be read: {detail} Read it with "
                         f"`apify_dataset_items(dataset_id=\"{dataset_id}\")` — the "
                         "run is billed, do not launch it again.",
                "run_id": run_id, "status": status, "dataset_id": dataset_id,
                "items": None}
    if status != "SUCCEEDED":
        return {"error": f"Apify run {run_id} ({actor_id}) ended {status}. Its log: "
                         f"`apify_run_status(run_id=\"{run_id}\")`.",
                "run_id": run_id, "status": status, "items": items}
    return items


def register(mcp: FastMCP) -> None:
    connector_verify.register("apify", _verify, couvre=connector_verify.AUTH_QUOTA)

    # --- find the actor -----------------------------------------------------

    @mcp.tool()
    def apify_store_search(
        search: Optional[str] = None,
        limit: int = 20,
        category: Optional[str] = None,
        sort_by: Optional[str] = None,
    ) -> dict:
        """Search Apify's public Store for an actor that already scrapes your target.

        Start here: naming a target ("google maps reviews", "linkedin company",
        "amazon products") is how you find the actor to run, along with its pricing
        and its identifier.

        Returns — `{data: {items: [{id, username, name, title, description, stats,
            pricingInfos, …}], total}}`. Run it with `username/name`.

        Args:
            search: free text describing the site or data you want.
            category: Store category (e.g. `"SOCIAL_MEDIA"`, `"ECOMMERCE"`).
            sort_by: `"relevance"` | `"popularity"` | `"newest"` | `"lastUpdate"`.
        """
        with _upstream():
            out = _client().store_search(search=search, limit=limit,
                                         category=category, sort_by=sort_by)
        _gratuit()
        return out

    @mcp.tool()
    def apify_actors(limit: int = 50, offset: Optional[int] = None) -> dict:
        """List the actors of THIS account (not the public Store)."""
        with _upstream():
            out = _client().actors(limit=limit, offset=offset)
        _gratuit()
        return out

    @mcp.tool()
    def apify_actor(actor_id: str) -> dict:
        """Fetch one actor's card: builds, versions and `defaultRunOptions`
        (default memory and timeout).

        Read it before a first run to size `memory_mbytes`/`timeout_secs`. The
        actor's INPUT fields are documented on its Store page, not here.

        Args:
            actor_id: `username/actor-name` (as shown in the Store) or its id.
        """
        with _upstream():
            out = _client().actor(actor_id)
        _gratuit()
        return out

    # --- launch -------------------------------------------------------------

    @mcp.tool()
    def apify_run_sync(
        actor_id: str,
        run_input: Optional[dict] = None,
        max_items: Optional[int] = None,
        limit: Optional[int] = None,
        fields: Optional[list[str]] = None,
        timeout_secs: Optional[int] = None,
        memory_mbytes: Optional[int] = None,
        max_total_charge_usd: Optional[float] = None,
    ) -> Any:
        """Run an actor and return its results in one call. Waits up to 300 s.

        The normal path for a bounded scrape. A run still going after 300 s is NOT
        stopped: the error names its run id — continue with `apify_run_status` then
        `apify_dataset_items`. For a scrape you know is long, use `apify_run`.

        Returns: the LIST of dataset items — or, for a run that ended FAILED, TIMED-OUT
        or ABORTED, `{error, run_id, status, items}` (that run is billed too).

        Args:
            actor_id: `username/actor-name` or id.
            run_input: the actor's own input JSON — its fields are specific to each
                actor (e.g. `{"searchStringsArray": ["bakery Marseille"],
                "maxCrawledPlaces": 20}` for the Google Maps scraper). See the
                actor's Store page.
            max_items: cap on BILLED items (pay-per-result actors) — set it.
            limit / fields: paginate and project the returned items (some actors
                return very wide objects).
            timeout_secs / memory_mbytes: run budget on Apify's side.
            max_total_charge_usd: hard cost ceiling for this run.
        """
        return lancer_acteur(
            actor_id, run_input, outil="apify_run_sync", max_items=max_items,
            limit=limit, fields=fields, timeout_secs=timeout_secs,
            memory_mbytes=memory_mbytes, max_total_charge_usd=max_total_charge_usd)

    @mcp.tool()
    def apify_run(
        actor_id: str,
        run_input: Optional[dict] = None,
        max_items: Optional[int] = None,
        timeout_secs: Optional[int] = None,
        memory_mbytes: Optional[int] = None,
        max_total_charge_usd: Optional[float] = None,
        wait_for_finish: Optional[int] = None,
    ) -> dict:
        """Start an actor WITHOUT waiting for it — for scrapes longer than 300 s.

        Returns — `{data: {id, status, defaultDatasetId, …}}` — keep `id` for
            `apify_run_status` and `defaultDatasetId` for `apify_dataset_items`.

        Args:
            wait_for_finish: seconds to wait before returning (max 60) — enough to
                catch a short run without polling.
        """
        with _upstream():
            out = _client().run(
                actor_id, run_input=run_input, max_items=max_items,
                timeout_secs=timeout_secs, memory_mbytes=memory_mbytes,
                max_total_charge_usd=max_total_charge_usd,
                wait_for_finish=wait_for_finish)
        # Launching is free; the run's cost is recorded by `apify_run_status`.
        _gratuit()
        return out

    @mcp.tool()
    def apify_run_status(run_id: str, wait_for_finish: Optional[int] = None) -> dict:
        """Check a run: `status` (READY, RUNNING, SUCCEEDED, FAILED, TIMED-OUT,
        ABORTED), `defaultDatasetId` (where the output is) and `usageTotalUsd`
        (what it cost so far)."""
        with _upstream():
            out = _client().run_status(run_id, wait_for_finish=wait_for_finish)
        _releve(out, run_id)
        return out

    @mcp.tool()
    def apify_abort_run(run_id: str, gracefully: Optional[bool] = None) -> dict:
        """Abort a running actor — stops the billing.

        Args:
            gracefully: let the actor finish its current item and flush its results
                (a hard abort may lose what wasn't pushed yet).
        """
        with _upstream():
            out = _client().abort_run(run_id, gracefully=gracefully)
        # Aborting is free; the run's final cost is recorded by `apify_run_status`.
        _gratuit()
        return out

    # --- read the output ----------------------------------------------------

    @mcp.tool()
    def apify_dataset_items(
        dataset_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        fields: Optional[list[str]] = None,
        omit: Optional[list[str]] = None,
        clean: Optional[bool] = None,
    ) -> Any:
        """Read the results a run produced.

        Returns: the LIST of items.

        Args:
            dataset_id: the run's `defaultDatasetId`.
            fields / omit: keep or drop keys — worth using, several actors return
                objects with dozens of fields per item.
            clean: skip empty/hidden items.
        """
        with _upstream():
            out = _client().dataset_items(
                dataset_id, limit=limit, offset=offset, fields=fields,
                omit=omit, clean=clean)
        _gratuit()
        return out
