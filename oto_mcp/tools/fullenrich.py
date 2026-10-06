"""FullEnrich — waterfall multi-provider contact enrichment (phones + emails).

~70% phone hit rate. Async bulk API (POST → poll). Pay-per-result.

⚠️ Deliberately async MCP surface (signal #252): the former synchronous tool polled
in-process for 131-147s → every MCP client hangs up (~60s), result lost AND credits
consumed. Now: `fullenrich_enrich_linkedin` SUBMITS the job (~1s, bulk
up to 100 contacts) and `fullenrich_result` reads the status/the result —
polling is the agent's job.

Metering (partner billing): distinct facts, no price. The submission traces the
contacts SUBMITTED. Reading a finished job traces:
- as `quantity`, the credits FullEnrich DEDUCTED (`cost.credits`, returned by
  oto-core as `cost_credits`) — the figure to reconcile with upstream;
- `found_work_emails` / `found_personal_emails` / `found_phones`, the number of
  CONTACTS in the job where at least one value of each kind was found — what a
  per-result price that is not a multiple of the upstream rate card reads.
An unfinished read traces 0 and no counts. The backend carries no rate card and
deduplicates nothing: a job read twice traces its figures twice, and the
consumer counts it once by its `enrichment_id` (the `job_id` of the
`org.usage.calls` lens, which also returns the counts as `found`).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, session_org
from ..connectors import verify as connector_verify

_CREDITS_URL = "https://app.fullenrich.com/api/v1/account/credits"

# Reading a job must never turn into an endless loop on the agent side (signals
# #990, #1027-#1029). A status still in progress says WHEN to come back; a status that
# will never change is a NAMED refusal; past this ceiling since submission, the read
# says to stop (a 100-contact job usually finishes in under 4 minutes).
_REPASSER_S = {"CREATED": 30, "IN_PROGRESS": 30, "RATE_LIMIT": 60}
_PLAFOND_MIN = 20
_TERMINAUX = {
    "CANCELED": ("fullenrich_job_canceled",
                 "the job was canceled at FullEnrich: it will return no result."),
    "NOT_FOUND": ("fullenrich_job_not_found",
                  "FullEnrich does not know (or no longer knows) this job — wrong id or expired job."),
}


def _refus(code: str, message: str, **data) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=f"Refusal `{code}`: {message}",
                              data={"code": code, "retryable": False, **data}))


def _minutes_depuis(submitted_at: Optional[str]) -> Optional[float]:
    if submitted_at is None:
        return None
    try:
        t = _dt.datetime.fromisoformat(submitted_at)
    except ValueError:
        raise McpError(ErrorData(
            code=INVALID_PARAMS,
            message=("`submitted_at` unreadable: pass back the `submitted_at` "
                     "returned by fullenrich_enrich_linkedin as is (ISO 8601 with timezone).")))
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - t).total_seconds() / 60


class FullenrichASec(RuntimeError):
    """FullEnrich account is empty, seen BEFORE submitting. Carries `status_code = 402` ON PURPOSE,
    like `serper.SerperASec`: the taxonomy (`error_taxonomy`, level 0 →
    `quota_exhausted` + marking of the key used) reads it like any 402, with no
    parallel path to maintain."""
    status_code = 402


def _solde(headers: dict) -> int:
    """The account's credit balance: `GET /api/v1/account/credits` (⚠️ v1, NOT the v2
    of the rest of the client — two distinct version prefixes at FullEnrich, verified).
    Read without side effects. Raises if the balance is unreadable: a guessed balance
    would let through a batch that cannot be paid, or refuse a good one."""
    import requests

    r = requests.get(_CREDITS_URL, headers=headers, timeout=15)
    r.raise_for_status()
    infos = r.json() or {}
    restant = infos.get("balance")
    if not isinstance(restant, int) or isinstance(restant, bool):
        raise RuntimeError(
            "FullEnrich answered without a readable credit balance: "
            f"{str(infos)[:200]}")
    return restant


def _verify(fields: dict, config: dict | None = None) -> dict:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth+quota`.

    Bearer token, balance read (`_solde`). No explicit mention of
    "free" in what we found — absence of a mention is a hint, not
    proof, like Folk and Pennylane.

    The balance (`balance`) tells a dead key from an empty account — topping up
    is not reconnecting.
    """
    from oto.tools.fullenrich.client import FullenrichClient

    restant = _solde(FullenrichClient(api_key=fields["key"])._headers())
    if restant <= 0:
        raise connector_verify.QuotaEpuise(
            "The FullEnrich key is good, but the account is empty (0 credits "
            "left). Top up the account at FullEnrich — reconnecting would "
            "change nothing.")
    return {"quota": {"restant": restant, "unite": "credits"}}


def register(mcp: FastMCP) -> None:
    from oto.tools.fullenrich.client import FullenrichClient

    connector_verify.register("fullenrich", _verify, couvre=connector_verify.AUTH_QUOTA)

    def _client(units: int = 1) -> tuple[FullenrichClient, bool]:
        key, is_platform = access.resolve_api_key("fullenrich", units=units)
        return FullenrichClient(api_key=key), is_platform

    @mcp.tool()
    def fullenrich_enrich_linkedin(
        contacts: list[dict],
        enrich_fields: Optional[list[str]] = None,
    ) -> dict:
        """Submit an ASYNC enrichment job (phones + emails) via FullEnrich (waterfall 20+ providers).

        Returns immediately with an `enrichment_id` — the job runs server-side for
        ~30s to 4min. THEN call `fullenrich_result(enrichment_id)` to collect (first
        poll after ~30s, then every ~20-30s until status FINISHED).

        Args:
            contacts: 1-100 contacts in ONE job (batch friends — one job for a whole
                list beats parallel single calls). Each: {"first_name": str,
                "last_name": str, "linkedin_slug": str (e.g. "alexis-laporte",
                NOT a URL — best matching), "domain": str (company website
                domain), "company_name": str (optional)}. Each contact MUST
                carry linkedin_slug OR domain (FullEnrich rejects the job
                otherwise).
            enrich_fields: subset of ["contact.work_emails", "contact.phones",
                "contact.personal_emails"]. Default: work_emails + phones.
                Only ask what you need — pricing is pay-per-result:
                10 credits/phone, 1/work_email, 3/personal_email.
        """
        client, is_platform = _client(units=len(contacts))
        # The balance BEFORE submitting: FullEnrich accepts a batch it cannot
        # pay for, and the failure only showed up at read time, minutes later (oto signals
        # #1276, #1280, #1340). A free read against a lost job. We only refuse
        # at zero: the cost of a batch is only known at result time (billed per data
        # point found), so a "too low" balance would be a guess.
        if _solde(client._headers()) <= 0:
            raise FullenrichASec(
                "FullEnrich: the account of the key used is empty (0 credits left). "
                "The call was correct: do not fix it and do not retry it.")
        try:
            enrichment_id = client.submit(contacts, enrich_fields=enrich_fields)
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        if is_platform:
            # One job = one contact billed per contact: consumption is the
            # NUMBER of contacts, counted in a single operation (the old loop made
            # one request per contact — up to 100 per job).
            # `len(contacts)` and not the real cost: FullEnrich bills per data point
            # found and says nothing at submission (the cost only exists at the result
            # of the job, `fullenrich_result`, on another call) — we do not guess.
            access.record_platform_usage("fullenrich", len(contacts))
        # Per-unit metering (partner billing, 21/08) — UNCONDITIONAL (platform key
        # OR BYO), unlike `record_platform_usage` above (which only counts
        # oto's internal quota on the platform key): `tool_calls.quantity`
        # serves an EXTERNAL consumer (the partner's) who bills the org whatever the
        # key mode. Counts the contacts SUBMITTED, not those actually
        # enriched/found — that figure only exists after the fact, in
        # `fullenrich_result` (async job), a SEPARATE log line.
        session_org.note_call_trace(quantity=len(contacts))
        return {
            "enrichment_id": enrichment_id,
            "submitted": len(contacts),
            "submitted_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "next_step": ("Job accepted. Call fullenrich_result(enrichment_id, "
                          "submitted_at) in ~30s (typical completion 30s-4min)."),
        }

    @mcp.tool()
    def fullenrich_result(enrichment_id: str, submitted_at: Optional[str] = None) -> dict:
        """Collect the result of a FullEnrich job submitted with fullenrich_enrich_linkedin.

        Single status check, returns immediately. Returns `{done: false, status,
        retry_after_s, next_step}` while the job runs — call again after
        `retry_after_s` seconds (jobs typically finish in 30s-4min). Pass the
        `submitted_at` the submission returned: past 20 minutes the answer adds
        `verdict: "still_running_after_20_min"` and says to STOP polling — do not
        resubmit the same contacts, it would be billed twice. A job that will never
        finish (canceled, unknown or expired id) is a named refusal, not a status to
        poll. When done, returns `{done: true, status: "FINISHED", profiles}` with one
        entry per submitted contact: {found, linkedin_slug, full_name, title,
        company_name, phones[], work_emails[], personal_emails[], location}.
        Reading a result never consumes the platform quota (the submission does).

        Args:
            enrichment_id: the `enrichment_id` returned by fullenrich_enrich_linkedin.
            submitted_at: the `submitted_at` returned by the same call.
        """
        from oto.tools.fullenrich.client import FullenrichClient
        # The platform quota is debited at SUBMISSION: the read consumes nothing
        # and does not check it — otherwise an already-paid job became unreadable the very day
        # the quota ran out (#943).
        rc = access.resolve_credential("fullenrich", check_usage=False)
        try:
            res = FullenrichClient(api_key=rc.key).fetch(enrichment_id)
        except RuntimeError as e:
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=f"Refusal `fullenrich_upstream_error`: {e}",
                data={"code": "fullenrich_upstream_error", "retryable": True}))
        status = res["status"]
        if status != "FINISHED":
            # A status read consumed nothing at FullEnrich: a TRACED zero,
            # not an absence — a metering consumer reads absence as 1.
            session_org.note_call_trace(quantity=0)
            if status not in _REPASSER_S:
                code, pourquoi = _TERMINAUX.get(status, (
                    "fullenrich_job_status_unknown",
                    f"FullEnrich returns the status \"{status}\", which does not lead to a result."))
                raise _refus(code, f"{pourquoi} Stop reading this job; resubmit the "
                                   "contacts only if you still need them (new "
                                   "billing).", status=status, enrichment_id=enrichment_id)
            out = {"done": False, "status": status,
                   "retry_after_s": _REPASSER_S[status],
                   "next_step": (f"Still running — call fullenrich_result again in "
                                 f"~{_REPASSER_S[status]}s.")}
            ecoule = _minutes_depuis(submitted_at)
            if ecoule is not None and ecoule >= _PLAFOND_MIN:
                out["verdict"] = f"still_running_after_{_PLAFOND_MIN}_min"
                out["next_step"] = (
                    f"Still running after {int(ecoule)} minutes: stop polling now. "
                    "Report it as blocked (with the enrichment_id) and read the result "
                    "later with the same id — do not resubmit the same contacts, they "
                    "would be billed twice.")
            return out
        # Metering (partner billing), UNCONDITIONAL (platform key OR BYO), like
        # `fullenrich_enrich_linkedin`: the consumer filters on `key_mode`.
        #   • `quantity` = the credits FULLENRICH deducted for this job —
        #     `cost_credits`, its `cost.credits` re-read by oto-core: the
        #     RECONCILIATION figure. Cost absent (oto-core older than the field, upstream silent) →
        #     NO quantity, never a value guessed from the profiles.
        #   • `found_*` = how many CONTACTS in the job have at least one value of each
        #     kind (a contact with two e-mails counts ONCE, an empty contact nowhere).
        #     Facts read from the profiles — hence traced even without a declared
        #     cost. This is what a per-result price that is not a multiple
        #     of the upstream rate card reads.
        # No rate card here, neither 1/3/10 nor conversion: a rate is a commercial
        # decision. ⚠️ Every FINISHED read of the same job traces the same
        # figures: counting a job once (by its `enrichment_id`, which the billing lens
        # returns as `job_id`) is the consumer's business, not the backend's.
        profiles = res.get("profiles") or []
        trace = {
            "found_work_emails": sum(1 for p in profiles if getattr(p, "work_emails", None)),
            "found_personal_emails": sum(1 for p in profiles
                                         if getattr(p, "personal_emails", None)),
            "found_phones": sum(1 for p in profiles if getattr(p, "phones", None)),
        }
        cost = res.get("cost_credits")
        if isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0:
            trace["quantity"] = cost
        session_org.note_call_trace(**trace)
        return {
            "done": True,
            "status": "FINISHED",
            "profiles": [p.to_dict() for p in res["profiles"]],
        }
