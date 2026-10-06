"""TheirStack — job postings by employer + technologies used (ERP…).

Wraps `oto.tools.theirstack.client.TheirStackClient` (API v1, Bearer). Keyed
`api_key`, **BYO or platform key**: `auth_modes = {byo_user, byo_org, platform}`
since oto-backend#405 — TheirStack is commodity data, hence resellable,
unlike the CRMs/ATSs which stay byo-only. TheirStack bills per credit,
per record returned.
⚠️ This docstring said "byo-only" until 2026-09-09, several weeks AFTER
#405 opened platform mode. The registry (`providers.REGISTRY`) is the source
of truth, not this text — the contradiction led to the wrong conclusion that the
two TheirStack lines on the website ("Get hiring signals", "tech stack") could not be sold.
The platform tier stays **grant-only** (`default_quota=0,
platform_key_open=False`): an org only gets in through an explicit grant.

Two operations, read-only:
- `theirstack_jobs_search`: the job postings published by one or more companies (or by
  country / title / tech) — the "who is hiring what, where, since when" signal.
- `theirstack_companies_search`: the firmographic record + the detected technologies
  (technographics: ERP, CRM, e-commerce…) of named or filtered companies.

The agent-facing contract is PROJECTED by default (what a sourcing sweep reads:
company, title, date, url, location / name, domain, headcount, industry, technologies);
`full=True` returns the whole TheirStack record (description, salaries, hiring_team,
company_object…). The typed filters cover everyday use; `extra` (merged LAST,
it wins) opens up the whole vendor DSL without breaking the tool's schema.

Billing: the credit is counted per COMPANY record returned — one company credit
"unlocks" all the jobs + technologies + firmographics of that company.
The OpenAPI spec (17/08/2026) prices it in API credits: 1 per job returned on
jobs/search, 3 per company on companies/search — in both cases `limit` bounds
the spend, and `metadata.truncated_*` says what was NOT returned for lack of credits.
TheirStack returns NO counter of credits consumed: every response carries
`credits_estimes`, our estimate from this rate card (oto#174), labelled as such.
Partial coverage on SMEs (≈ 8% of the small French wholesalers seen in the
pilot): `data: []` is a NORMAL result, not an error — do not retry.

Calls to the client are written out in plain sight (`_client().search_jobs(…)`): that is what
makes them verifiable by the version-skew probe (`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection, session_org
from ..connectors import verify as connector_verify

# What a sourcing sweep reads on a job / a company (`full=True` returns everything).
_JOB_FIELDS = ("company", "job_title", "date_posted", "url", "location")
_COMPANY_FIELDS = ("name", "domain", "employee_count", "industry", "technology_names")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"TheirStack rejected the API key (HTTP {status}) — check the key "
                "configured on this connector (TheirStack: Settings → API keys).")
    if status == 402:
        return ("TheirStack: credits exhausted or plan insufficient (402) — top up the "
                "account, or reduce `limit`.")
    if status == 422:
        return (f"TheirStack rejected the filters (422): {e.body} — jobs_search requires at "
                "least one of posted_at_max_age_days / posted_at_gte / posted_at_lte / "
                "company_names (company_name_or) / company_domain_or / company_linkedin_url_or; "
                "the field names in `extra` must be those of the TheirStack DSL.")
    if status == 429:
        return "TheirStack: too many requests (429) — retry in a moment."
    if status in (500, 502, 503, 504):
        return f"TheirStack is temporarily unavailable (HTTP {status}) — retry later."
    return f"TheirStack rejected the request (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> dict:  # noqa: ARG001
    """"Test the connection" probe — covers `auth+quota`: the credit balance is
    the FREE authenticated call (a search, even `limit=1`, would spend
    credits). The balance used to be read then THROWN AWAY: an empty account kept a green probe.

    Documented response (`GET /v0/billing/credit-balance`): `api_credits`,
    `used_api_credits`, `ui_credits`, `used_ui_credits`, `earliest_expiration`.
    `api_credits` is the ALLOCATION, not what remains: an empty account answered
    `api_credits=1700, used_api_credits=1700` and kept a green probe (oto
    signal #1189). So the account is empty when `used_api_credits >= api_credits` — or
    when the allocation is zero, if `used_api_credits` is missing.
    """
    from oto.tools.theirstack.client import TheirStackClient

    solde = TheirStackClient(api_key=fields["key"]).credit_balance()
    api = solde.get("api_credits") if isinstance(solde, dict) else None
    used = solde.get("used_api_credits") if isinstance(solde, dict) else None
    if not isinstance(api, int):
        raise RuntimeError(
            f"TheirStack answered without a readable API credit balance: {str(solde)[:200]}")
    restant = api - used if isinstance(used, int) else api
    if restant <= 0:
        raise connector_verify.QuotaEpuise(
            "The TheirStack key is good, but the account has no API credits left "
            f"({used if isinstance(used, int) else '?'} used out of {api}). "
            "Top up the account at TheirStack — reconnecting would change nothing.")
    return {"quota": {"api_credits": api,
                      "used_api_credits": used,
                      "restant": restant,
                      "unite": "API credits"}}


def _clean_names(names: Optional[list[str]], what: str) -> list[str]:
    if names is None:
        return []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise _bad(f"`{what}` must be a list of strings.")
    return [n.strip() for n in names if n and n.strip()]


def _merge_extra(payload: dict, extra: Optional[dict]) -> dict:
    """`extra` = raw TheirStack DSL keys, merged LAST (they win
    over the typed arguments — it is the escape hatch to the ~110 vendor filters)."""
    if extra is None:
        return payload
    if not isinstance(extra, dict):
        raise _bad("`extra` must be a dict of TheirStack filters (vendor DSL).")
    payload.update(extra)
    return payload


def _project(result: Any, fields: tuple, full: bool) -> Any:
    """`full=True` → payload UNCHANGED; otherwise each item of `data` is narrowed to
    `fields`. The `metadata` envelope (total, truncated_*) is kept in every case."""
    if full:
        return result
    return output_projection.project(result, items_path="data", fields=fields)


#: The published rate card (OpenAPI spec, 17/08/2026), in API credits per record returned.
_CREDITS_PAR_OFFRE = 1
_CREDITS_PAR_ENTREPRISE = 3


def _with_credit_estimate(result: Any, par_record: int, unite: str) -> Any:
    """Add `credits_estimes` to the envelope (oto#174).

    TheirStack returns NO credit counter — neither in `metadata` nor in a
    header; only a separate balance call gives it. Procedures nonetheless said
    "count the credits from `metadata`": impossible, and the caller
    went over budget without knowing it. The backend knows the rate card and the number
    of records returned (that is already the metering, `_trace_quantity`): we say so, labelling
    it ESTIMATED — it is not a statement from the provider."""
    if not (isinstance(result, dict) and isinstance(result.get("data"), list)):
        return result
    out = dict(result)
    out["credits_estimes"] = par_record * len(result["data"])
    out["credits_estimes_source"] = (
        f"Estimated from the published rate card ({par_record} credit(s) per {unite} "
        "returned), not a counter: TheirStack returns none. The real balance "
        "is read on the TheirStack dashboard (API: "
        "GET /v0/billing/credit-balance).")
    return out


def _trace_quantity(result: Any) -> None:
    """Per-unit metering (partner billing, 21/08) — the number of records RETURNED
    in `data`, before projection (`_project` never changes the length of
    the list, only the keys of each item). This is what TheirStack
    actually bills: 1 API credit/job on jobs/search, 3/company on
    companies/search — see the module docstring. Both tools resolve
    to the SAME connector (`namespace_of` = first token, "theirstack" for
    both: no multi-token prefix "theirstack_jobs"/"theirstack_companies"
    is declared in the registry) — so it is the price grid of the partner's
    billing consumer (external repo) that must tell the two
    RATES apart by TOOL name, not by connector."""
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        session_org.note_call_trace(quantity=len(result["data"]))


def _record_platform_usage(result, is_platform: bool) -> None:
    """Debit oto's internal quota when OUR key was the one used.

    Same two operations as `aiark`/`fullenrich`, and the same clean separation:
    - `record_platform_usage` counts ONLY platform mode — it is oto's quota
      on its own key, irrelevant when the customer brings their own;
    - `note_call_trace(quantity=…)` above is UNCONDITIONAL — it is the
      metering, and `tool_calls.key_mode` says separately which key the call
      went through, which the billing consumer reads to bill only
      the partner's key.
    Counted in RECORDS returned, not in calls: TheirStack bills us
    per record (1 credit/job, 3/company), so a call that returns 50
    jobs costs 50, and an empty page costs 0."""
    if not is_platform:
        return
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        access.record_platform_usage("theirstack", len(result["data"]))


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.theirstack.client import TheirStackClient

    connector_verify.register("theirstack", _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client() -> tuple[TheirStackClient, bool]:
        key, is_platform = access.resolve_api_key("theirstack")
        return TheirStackClient(api_key=key), is_platform

    def _run(fn):
        """Translate a TheirStack refusal into an actionable tool error."""
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    @mcp.tool()
    def theirstack_jobs_search(
        company_names: Optional[list[str]] = None,
        posted_at_max_age_days: int = 90,
        job_country_code_or: Optional[list[str]] = None,
        limit: int = 25,
        page: int = 0,
        extra: Optional[dict] = None,
        full: bool = False,
    ) -> dict:
        """Job postings published by given companies (or by country / title / tech),
        from TheirStack — the "who is hiring what, where, since when" signal.

        Billing: credits are billed per company record returned — one company credit
        unlocks ALL the jobs + technologies + firmographics of that company (the API
        spec counts it as 1 API credit per job returned here; `limit` bounds the
        spend either way). Coverage is partial on small companies (~8% of small
        French wholesalers): an EMPTY `data` is a normal result, not an error — do
        not retry, move on.

        Returns `{metadata: {total_results?, truncated_results, truncated_companies,
        total_companies?}, data: [{company, job_title, date_posted, url, location}],
        credits_estimes, credits_estimes_source}` (`full=True` → the raw records
        instead). TheirStack returns NO credit counter (not in `metadata`, not in a
        header): `credits_estimes` is OUR estimate from the published rate (1 per
        job returned) — sum it to track a budget; the real balance is on the
        TheirStack dashboard.

        ⚠️ The company DOMAIN is NULLABLE (raw records, `full=True`): the same
        company can come back without it, from one day to the next. An exclusion or
        a dedup table keyed on the domain then lets that record through SILENTLY —
        key on the company name too, never on the domain alone.

        Args:
            company_names: exact company names, CASE-SENSITIVE (`company_name_or`) —
                pass the name as TheirStack spells it. For looser matching put
                `company_name_case_insensitive_or` / `company_name_partial_match_or`
                / `company_domain_or` in `extra` instead.
            posted_at_max_age_days: only jobs posted in the last N days (default 90;
                0 = today only). TheirStack REQUIRES a date filter or a company
                identifier — this default satisfies it.
            job_country_code_or: ISO2 codes of the job location (e.g. ["FR"]).
            limit: results per page (default 25) — this is the cost bound.
            page: 0-based page number.
            extra: any other TheirStack filter, merged LAST (overrides the typed args):
                `job_title_or` (keywords), `job_title_pattern_or` (regex),
                `job_technology_slug_or`, `company_technology_slug_or`,
                `company_country_code_or`, `min_employee_count`, `industry_or`,
                `job_seniority_or`, `workplace_types_or`, `include_total_results`…
            full: return the raw TheirStack records (description, salary, hiring_team,
                company_object, technology_slugs…). Default: each item is projected to
                {company, job_title, date_posted, url, location}; the `metadata`
                envelope (total_results, truncated_results…) is always kept.
        """
        names = _clean_names(company_names, "company_names")
        if limit is not None and limit <= 0:
            raise _bad("`limit` must be ≥ 1.")
        if page is not None and page < 0:
            raise _bad("`page` is 0-based (≥ 0).")
        payload: dict = {"page": page, "limit": limit}
        if posted_at_max_age_days is not None:
            payload["posted_at_max_age_days"] = posted_at_max_age_days
        if names:
            payload["company_name_or"] = names
        if job_country_code_or:
            payload["job_country_code_or"] = list(job_country_code_or)
        payload = _merge_extra(payload, extra)
        client, is_platform = _client()
        result = _run(lambda: client.search_jobs(payload))
        _record_platform_usage(result, is_platform)
        _trace_quantity(result)
        return _with_credit_estimate(_project(result, _JOB_FIELDS, full),
                                     _CREDITS_PAR_OFFRE, "job")

    @mcp.tool()
    def theirstack_companies_search(
        company_names: Optional[list[str]] = None,
        company_country_code_or: Optional[list[str]] = None,
        limit: int = 25,
        page: int = 0,
        extra: Optional[dict] = None,
        full: bool = False,
    ) -> dict:
        """Company records from TheirStack — firmographics + the technologies detected
        in their job postings (ERP, CRM, e-commerce stack…): the technographic read
        on a named company or a filtered segment.

        Billing: credits are billed per company record returned — one company credit
        unlocks ALL the jobs + technologies + firmographics of that company (the API
        spec counts 3 API credits per company returned here; `limit` bounds the
        spend). Coverage is partial on small companies (~8% of small French
        wholesalers): an EMPTY `data` is a normal result, not an error — do not
        retry, move on. `metadata.truncated_companies` > 0 means the credit balance,
        not the filter, cut the list.

        Returns `{metadata: {…, truncated_companies, total_companies?}, data: [{name,
        domain, employee_count, industry, technology_names}], credits_estimes,
        credits_estimes_source}` (`full=True` → the raw records instead).
        TheirStack returns NO credit counter (not in `metadata`, not in a header):
        `credits_estimes` is OUR estimate from the published rate (3 per company
        returned) — sum it to track a budget; the real balance is on the
        TheirStack dashboard.

        Args:
            company_names: exact company names, CASE-SENSITIVE (`company_name_or`).
                For looser matching put `company_name_case_insensitive_or` /
                `company_name_partial_match_or` / `company_domain_or` in `extra`.
            company_country_code_or: ISO2 codes of the HQ country (e.g. ["FR"]).
            limit: results per page (default 25) — the cost bound.
            page: 0-based page number.
            extra: any other TheirStack filter, merged LAST (overrides the typed args):
                `company_technology_slug_or` (companies using a tech),
                `min_employee_count` / `max_employee_count`, `industry_or`,
                `company_name_partial_match_or`, `job_filters` + `min_num_jobs_found`
                (hiring signals), `expand_technology_slugs`, `include_total_results`…
            full: return the raw records (technology_slugs, jobs_found,
                technologies_found, linkedin_url, revenue, funding…). Default: each
                item is projected to {name, domain, employee_count, industry,
                technology_names}; the `metadata` envelope is always kept.
        """
        names = _clean_names(company_names, "company_names")
        if limit is not None and limit <= 0:
            raise _bad("`limit` must be ≥ 1.")
        if page is not None and page < 0:
            raise _bad("`page` is 0-based (≥ 0).")
        payload: dict = {"page": page, "limit": limit}
        if names:
            payload["company_name_or"] = names
        if company_country_code_or:
            payload["company_country_code_or"] = list(company_country_code_or)
        payload = _merge_extra(payload, extra)
        client, is_platform = _client()
        result = _run(lambda: client.search_companies(payload))
        _record_platform_usage(result, is_platform)
        _trace_quantity(result)
        return _with_credit_estimate(_project(result, _COMPANY_FIELDS, full),
                                     _CREDITS_PAR_ENTREPRISE, "company")
