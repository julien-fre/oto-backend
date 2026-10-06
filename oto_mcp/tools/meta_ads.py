"""Meta Ads — three READ tools, as thin envelopes.

The client lives in oto-core (`oto.tools.meta_ads`); the token and the translation
of refusals in `meta_ads_session.py`. Here: a schema, a call, Meta's JSON returned
as is (metric names are the API's).

The only behaviour of its own is the ASYNCHRONOUS insight: a large report does not fit
within the delay of a tool call (45 s on the REST side). We wait a bounded time,
then return the `report_run_id` so the caller can resume — never a call that hangs.

⚠️ Read-only, without exception: no creation, no pause, no budget.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Annotated, Any, Literal, Optional, Union

from fastmcp import FastMCP
from pydantic import Field

from . import meta_ads_session as session
from .meta_ads_session import _bad, _client, appeler

#: Maximum wait for an asynchronous report within ONE call — under the REST delay of
#: 45 s (`api_routes`), with the margin of the surrounding calls.
_ATTENTE_RAPPORT_S = 25.0
_PAS_SONDAGE_S = 2.0
#: TOTAL budget of an insights call (start + polling + reading), under the REST
#: 45 s: beyond that, we return what is needed to resume rather than a cut-off call.
_BUDGET_APPEL_S = 40.0
#: Margin kept to read the rows of a finished report.
_MARGE_LECTURE_S = 8.0

#: What an identifier may be before going into a Graph path (same rule as
#: the core, kept here too: `act_` + digits for an account or an account object,
#: digits for an object or a report). A `/`, a `?` or a `..` would target
#: another node — with `business_management`, a write on the portfolio.
_ID_COMPTE = re.compile(r"^(act_)?\d+$")
_ID_NUMERIQUE = re.compile(r"^\d+$")

_TERMINE = "Job Completed"
_ECHECS = ("Job Failed", "Job Skipped")


def _id(valeur: Optional[str], nom: str, motif: re.Pattern, forme: str) -> Optional[str]:
    if valeur is None:
        return None
    v = str(valeur).strip()
    if not motif.fullmatch(v):
        raise _bad(f"{nom} must be {forme}; got {v!r}.")
    return v


async def _dans_le_budget(fin: float, geste: str, coro):
    """A core call bounded by the time remaining in the tool call."""
    reste = fin - time.monotonic()
    try:
        return await asyncio.wait_for(coro, timeout=max(reste, 0.1))
    except asyncio.TimeoutError:
        raise _bad(f"Meta did not answer {geste} within this call's budget "
                   f"({_BUDGET_APPEL_S:.0f} s) — retry, or use mode=\"async\".") from None


async def _suivre_rapport(ads, run_id: str, limit: int,
                          cursor: Optional[str], fin: float) -> dict:
    """Polls until completion or until the budget runs out; returns the rows or the status.

    `fin` bounds the WHOLE tool call: the wait stops early enough to read the
    rows, otherwise we return a `report_run_id` to resume — never a cut-off call."""
    fin_sondage = min(time.monotonic() + _ATTENTE_RAPPORT_S, fin - _MARGE_LECTURE_S)
    while True:
        etat = await _dans_le_budget(
            fin, "the report status",
            appeler("the report status", ads.get_report_status, run_id))
        statut = etat.get("async_status")
        if statut == _TERMINE:
            if fin - time.monotonic() < _MARGE_LECTURE_S / 2:
                return {"report_run_id": run_id, "status": statut,
                        "hint": "The report is ready. Call meta_ads_insights again "
                                "with this report_run_id to fetch the rows."}
            res = await _dans_le_budget(
                fin, "the report results",
                appeler("the report results", ads.get_report_insights,
                        run_id, limit, cursor))
            return {"report_run_id": run_id, "status": statut, **res}
        if statut in _ECHECS:
            raise _bad(f"Meta report {run_id} ended with status « {statut} ». "
                       "Narrow the request and start a new one.")
        if time.monotonic() >= fin_sondage:
            return {"report_run_id": run_id, "status": statut,
                    "percent_complete": etat.get("async_percent_completion"),
                    "hint": "Still running. Call meta_ads_insights again with this "
                            "report_run_id to fetch the results."}
        await asyncio.sleep(_PAS_SONDAGE_S)


def register(mcp: FastMCP) -> None:
    # NO import from the core here: the connector stays mounted (and refuses,
    # saying so) when oto-core is too old or the application is not configured.
    session.avertir_au_demarrage()

    @mcp.tool()
    async def meta_ads_accounts(
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        cursor: Annotated[Optional[str], Field(
            description="next_cursor from a previous page")] = None,
        fields: Annotated[Optional[str], Field(
            description="Comma-separated Graph fields; replaces the defaults")] = None,
    ) -> dict:
        """List the ad accounts this connection can read: id (act_…), name,
        account_status, currency, timezone_name, amount_spent, business.

        Start here — every other meta_ads tool needs an ad account or object id.
        Returns {data, next_cursor}.
        """
        ads = await _client()
        return await appeler("the ad accounts", ads.list_ad_accounts, limit,
                             cursor, fields)

    @mcp.tool()
    async def meta_ads_objects(
        level: Annotated[Literal["campaign", "adset", "ad"], Field(
            description="Which level of the ad tree to list")] = "campaign",
        ad_account_id: Annotated[Optional[str], Field(
            description="Ad account to list under (act_… or bare id)")] = None,
        object_id: Annotated[Optional[str], Field(
            description="Fetch ONE object by id instead of listing")] = None,
        fields: Annotated[Optional[str], Field(
            description="Comma-separated Graph fields; replaces the defaults")] = None,
        effective_status: Annotated[Optional[list[str]], Field(
            description="e.g. [\"ACTIVE\"], [\"PAUSED\"], [\"ARCHIVED\"]")] = None,
        filtering: Annotated[Optional[list[dict]], Field(
            description="Graph filtering, e.g. [{\"field\": \"campaign.id\", "
                        "\"operator\": \"EQUAL\", \"value\": \"…\"}]")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        cursor: Optional[str] = None,
    ) -> dict:
        """Browse campaigns, ad sets or ads of an ad account — or fetch one object.

        Pass ad_account_id to list (returns {data, next_cursor}), or object_id to
        read one object. Default fields: id, name, status, effective_status,
        budgets, dates (+ objective for campaigns, parent ids for ad sets/ads).
        """
        if (ad_account_id is None) == (object_id is None):
            raise _bad("Pass exactly one of ad_account_id (list) or object_id (get).")
        ad_account_id = _id(ad_account_id, "ad_account_id", _ID_COMPTE,
                            "digits, optionally prefixed by act_")
        object_id = _id(object_id, "object_id", _ID_COMPTE,
                        "digits (a campaign, ad set or ad id) or act_<digits>")
        ads = await _client()
        if object_id:
            return await appeler("the object", ads.get_object, object_id, fields)
        return await appeler(
            f"the {level} list", ads.list_objects, ad_account_id, level,
            fields=fields, effective_status=effective_status, filtering=filtering,
            limit=limit, after=cursor)

    @mcp.tool()
    async def meta_ads_insights(
        object_id: Annotated[Optional[str], Field(
            description="Ad account (act_…), campaign, ad set or ad id")] = None,
        level: Annotated[Optional[Literal["account", "campaign", "adset", "ad"]],
                         Field(description="Aggregation level of the rows")] = None,
        fields: Annotated[Optional[Union[list[str], str]], Field(
            description="Metrics, list or comma-separated, e.g. [\"spend\", "
                        "\"impressions\", \"clicks\", "
                        "\"actions\"]. Default: spend, impressions, reach, "
                        "frequency, clicks, cpc, cpm, ctr, actions, "
                        "cost_per_action_type")] = None,
        date_preset: Annotated[Optional[str], Field(
            description="e.g. today, yesterday, last_7d, last_30d, this_month, "
                        "last_month, maximum")] = None,
        since: Annotated[Optional[str], Field(description="YYYY-MM-DD")] = None,
        until: Annotated[Optional[str], Field(description="YYYY-MM-DD")] = None,
        time_increment: Annotated[
            Optional[Union[Annotated[int, Field(ge=1, le=90)],
                           Literal["monthly", "all_days"]]],
            Field(description="1 = daily rows, 7 = weekly (any 1-90), monthly, "
                              "all_days")] = None,
        breakdowns: Annotated[Optional[Union[list[str], str]], Field(
            description="e.g. [\"age\", \"gender\"], [\"country\"], "
                        "[\"publisher_platform\", \"platform_position\"]")] = None,
        action_attribution_windows: Annotated[Optional[list[str]], Field(
            description="e.g. [\"7d_click\", \"1d_view\"]")] = None,
        filtering: Optional[list[dict]] = None,
        sort: Annotated[Optional[list[str]], Field(
            description="e.g. [\"spend_descending\"]")] = None,
        mode: Annotated[Literal["sync", "async"], Field(
            description="async = background report for large requests")] = "sync",
        report_run_id: Annotated[Optional[str], Field(
            description="Resume an async report started earlier")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """Performance insights (spend, reach, clicks, conversions…) for an ad
        account, campaign, ad set or ad. Returns {data, next_cursor}.

        Use mode="async" for big requests (long ranges, many breakdowns, ad level
        over a whole account): it waits ~25 s, then returns report_run_id if still
        running — call again with report_run_id to get the rows.
        Valid metric/breakdown combinations: oto_guide op=read slug="meta-ads-insights".
        """
        fin = time.monotonic() + _BUDGET_APPEL_S
        report_run_id = _id(report_run_id, "report_run_id", _ID_NUMERIQUE, "digits")
        object_id = _id(object_id, "object_id", _ID_COMPTE,
                        "an ad account (act_<digits>) or a campaign, ad set or ad id")
        ads = await _client()
        if report_run_id:
            return await _suivre_rapport(ads, report_run_id, limit, cursor, fin)
        if not object_id:
            raise _bad("object_id is required (or report_run_id to resume a report).")
        if (since is None) != (until is None):
            raise _bad("Pass both since and until, or neither.")
        kwargs: dict[str, Any] = dict(
            level=level, fields=fields, date_preset=date_preset,
            time_range={"since": since, "until": until} if since else None,
            time_increment=time_increment, breakdowns=breakdowns,
            action_attribution_windows=action_attribution_windows,
            filtering=filtering, sort=sort)
        if mode == "async":
            run_id = await _dans_le_budget(
                fin, "starting the report",
                appeler("starting the report", ads.start_insights_report,
                        object_id, **kwargs))
            return await _suivre_rapport(ads, run_id, limit, cursor, fin)
        return await _dans_le_budget(
            fin, "the insights",
            appeler("the insights", ads.get_insights, object_id,
                    **kwargs, limit=limit, after=cursor))
