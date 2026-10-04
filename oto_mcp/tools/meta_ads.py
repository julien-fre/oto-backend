"""Meta Ads — trois outils de LECTURE, en enveloppes minces.

Le client vit dans oto-core (`oto.tools.meta_ads`) ; le jeton et la traduction des
refus dans `meta_ads_session.py`. Ici : un schéma, un appel, le JSON de Meta rendu
tel quel (noms de métriques ceux de l'API).

Le seul comportement propre est l'insight ASYNCHRONE : un gros rapport ne tient
pas dans le délai d'un appel d'outil (45 s côté REST). On attend un temps borné,
puis on rend le `report_run_id` pour reprendre — jamais un appel qui pend.

⚠️ Lecture seule, sans exception : ni création, ni pause, ni budget.
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

#: Attente maximale d'un rapport asynchrone dans UN appel — sous le délai REST de
#: 45 s (`api_routes`), avec la marge des appels qui l'entourent.
_ATTENTE_RAPPORT_S = 25.0
_PAS_SONDAGE_S = 2.0
#: Budget TOTAL d'un appel d'insights (démarrage + sondage + lecture), sous les 45 s
#: du REST : au-delà, on rend de quoi reprendre plutôt qu'un appel coupé.
_BUDGET_APPEL_S = 40.0
#: Marge gardée pour lire les lignes d'un rapport terminé.
_MARGE_LECTURE_S = 8.0

#: Ce qu'un identifiant peut être avant d'aller dans un chemin Graph (même règle que
#: le cœur, tenue ici aussi : `act_` + chiffres pour un compte ou un objet-compte,
#: des chiffres pour un objet ou un rapport). Un `/`, un `?` ou un `..` viserait un
#: autre nœud — avec `business_management`, une écriture sur le portefeuille.
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
    """Un appel du cœur borné par le temps restant de l'appel d'outil."""
    reste = fin - time.monotonic()
    try:
        return await asyncio.wait_for(coro, timeout=max(reste, 0.1))
    except asyncio.TimeoutError:
        raise _bad(f"Meta did not answer {geste} within this call's budget "
                   f"({_BUDGET_APPEL_S:.0f} s) — retry, or use mode=\"async\".") from None


async def _suivre_rapport(ads, run_id: str, limit: int,
                          cursor: Optional[str], fin: float) -> dict:
    """Sonde jusqu'à la fin ou jusqu'au budget ; rend les lignes ou l'état.

    `fin` borne TOUT l'appel d'outil : l'attente s'arrête assez tôt pour lire les
    lignes, sinon on rend `report_run_id` à reprendre — jamais un appel coupé."""
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
    # AUCUN import du cœur ici : le connecteur reste monté (et refuse en le
    # disant) quand oto-core est trop ancien ou l'application non configurée.
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
