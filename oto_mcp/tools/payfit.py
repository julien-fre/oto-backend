"""PayFit — payroll and HR software. **Everything the Partner API documents for
reading**. No write is wired (decision of 24/09/2026).

Wraps `oto.tools.payfit.PayfitClient` (Bearer, "Partner API" v1). keyed
`api_key`, BYO (member or org): a PayFit API key is created by a company admin
and only gives access to that company — there is no platform key. The company id
is never asked for: the client reads it by introspecting the key. Fixed hosts
(`partner-api.payfit.com`, `oauth.payfit.com`): no field designates a destination,
so no egress guard (`oto_mcp/egress.py`) needs to be set here.

## What this connector serves, since 17/09/2026

Everything: the company, the directory, the contracts (including the FR variant: DSN
nature, collective agreement, day-based package, termination reason, executive
officer status), absences, payslips (metadata and PDF), payroll accounting entries and
their export, the payment file, the payroll cycle status, actual worked time,
meal vouchers, health insurance and provident fund, documents. And
**no write**: creating a collaborator, a contract, an absence, cancelling it,
affiliating a contract to a health insurance or requesting a regularization return
the named refusal `payfit_write_not_wired`, which says what the call would have done —
nothing is sent to PayFit, whatever the argument. These ops stay in their enum so
that the agent receives this named refusal and not "unknown op".

There was no choice to make between "serving payroll" and "protecting
people": they are two different mechanisms.

⚠️ **Protection no longer goes through a hard-coded removal.** It goes through the
**per-org field filters** (ADR 0009/0015) and a **protective server default** for
this connector (`field_filter_defaults.SERVER_DEFAULTS["payfit"]`): NIR (and
NTT), IBAN/BIC and `absence_type` are masked until an org_admin lifts the
rule. Everything else comes out: pay carried by the accounting entries,
payslips, employer cost, full contracts, worked time, health insurance,
meal vouchers, contact details, birth, nationality, seniority, manager.

⚠️ **DOCUMENTS (payslip PDF, accounting export, payment file, tax
document) are locked**: a filter does not read inside a file, so they
only come out if the org's effective policy for `payfit` masks nothing —
otherwise a named refusal, and fail-closed if it is unreadable (`payfit_garde.serve_document`).

⚠️ **Only one key is renamed, and it is mechanical**: an absence's type comes out
as `absence_type`, because `FieldFilter` matches by leaf name and a rule on `type`
would also hit `emails[].type` and four other harmless fields.
`absence_category` (`ordinary_leave` | `restricted`) goes with it and stays readable
when the type is masked. The full why: `payfit_socle`.

## Surface (ADR 0047), verb in `op`, default always read

This module:
- `payfit_company` — the key's company; `fr=True` adds SIREN/SIRET.
- `payfit_collaborator` — list | get; create not wired.
- `payfit_contract` — list | get; `fr=True` → FR variant; create not wired.
- `payfit_absence` — list; create and cancel not wired.

Sibling modules (same key, same client, mounted by `Connector.modules`):
`payfit_paie` (payslips, accounting and payments, cycle status, worked
time, meal vouchers), `payfit_social` (health insurance, provident fund, documents).

**No argument is silently dropped** (`is not None`) → `payfit_garde`.
**No write is wired**: `payfit_garde.not_wired`, with neither key nor client.

Derived from the public documentation and OpenAPI spec (read on 2026-09-17).
**No real call**: no key available — neither the exact shape of the responses nor
the side effects of a write (does PayFit notify the employee? is a created
absence immediately in payroll?) have been verified.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import payfit_socle as S
from .payfit_garde import (_client, limit_or_default, need, not_wired,
                           redaction_notice, refuse_ignored, refuse_unknown_op,
                           register_probe, run)


def register(mcp: FastMCP) -> None:
    register_probe()

    @mcp.tool()
    def payfit_company(fr: Optional[bool] = None) -> dict:
        """The PayFit company the API key belongs to — name, country, registration
        number, address, number of active contracts.

        Its `country` tells whether the French variants apply: `payfit_contract(
        fr=True)`, the meal vouchers, the worked time and the insurance tools are
        French-only.

        Args:
            fr: French variant — adds `siren`, `siret`, the legal address and the
                health-insurance proration method. France only.
        """
        c = _client()
        return S.one(run(lambda: c.get_company(fr=bool(fr))), "company")

    @mcp.tool()
    def payfit_collaborator(
        op: Literal["list", "get", "create"] = "list",
        collaborator_id: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        personal_email: Optional[str] = None,
        other_name: Optional[str] = None,
        social_security_number: Optional[str] = None,
        personal_address: Optional[dict] = None,
        birth_information: Optional[dict] = None,
        personal_phone_number: Optional[str] = None,
        number_of_children: Optional[int] = None,
        gender: Optional[str] = None,
        invite_collaborator: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """The PayFit employee directory — read only: this connector does not write
        to PayFit.

        A collaborator carries what the API key's scopes allow: names, matricule,
        professional and personal emails, phones, addresses, manager, team,
        contracts (id, dates, status), birth date and country, nationality, gender,
        termination date, and — masked by the server default — the social security
        number and the bank details.

        `op`:
        - **"list"** (default): the collaborators, optionally the one whose contract
          carries `email`. Paginated: pass back `next_cursor` as `cursor`.
        - **"get"**: one collaborator (`collaborator_id`).
        - **"create"**: NOT WIRED — never writes. It answers the named refusal
          `payfit_write_not_wired`, saying what it would have done; nothing is sent
          to PayFit. Hiring is done in PayFit itself.

        Args:
            op: list (default) | get | create.
            collaborator_id: op="get".
            email: op="list" — exact contract email (not the login email).
            first_name / last_name / personal_email / other_name /
                social_security_number / personal_address / birth_information /
                personal_phone_number / number_of_children / gender /
                invite_collaborator: op="create" only — not wired, nothing is sent.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the full view.
        """
        creation = dict(
            first_name=first_name, last_name=last_name, personal_email=personal_email,
            other_name=other_name, social_security_number=social_security_number,
            personal_address=personal_address, birth_information=birth_information,
            personal_phone_number=personal_phone_number,
            number_of_children=number_of_children, gender=gender,
            invite_collaborator=invite_collaborator)
        if op == "list":
            refuse_ignored(op, collaborator_id=collaborator_id, **creation)
            c = _client()
            return S.page(run(lambda: c.list_collaborators(
                limit=limit_or_default(limit), cursor=cursor, email=email)),
                "collaborators", "id", fields=fields, redaction=redaction_notice())
        if op == "get":
            need(op, collaborator_id=collaborator_id)
            refuse_ignored(op, email=email, limit=limit, cursor=cursor, fields=fields,
                           **creation)
            c = _client()
            return S.one(run(lambda: c.get_collaborator(collaborator_id)),
                         "collaborator", redaction=redaction_notice())
        if op == "create":
            # Neither the NIR nor the address is included: they would end up in the log.
            autres = sorted(k for k, v in creation.items()
                            if v is not None and k not in ("first_name", "last_name"))
            raise not_wired(op, "created the collaborator", first_name=first_name,
                            last_name=last_name, champs_fournis=autres)
        raise refuse_unknown_op(op, "list", "get", "create")

    @mcp.tool()
    def payfit_contract(
        op: Literal["list", "get", "create"] = "list",
        contract_id: Optional[str] = None,
        collaborator_id: Optional[str] = None,
        job_title: Optional[str] = None,
        start_date: Optional[str] = None,
        fr: Optional[bool] = None,
        include_in_progress: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Employment contracts in PayFit, read only: job title, status, start/end
        dates, probation end date, weekly hours, full-time equivalent, collaborator
        id.

        `fr=True` (French companies only) reads a DIFFERENT collection, and it is
        the only one that carries the contract nature (`natureContratDsn`: 01 CDI,
        02 CDD…), the conventional status, the collective agreement (`idcc`), the
        termination reason (`motifRuptureDeContratDsn`), the working time modality
        (`standard`, `forfait_heures`, `forfait_jours`…), the "cadre dirigeant"
        flag, and the linked health-insurance and provident-fund contract ids.
        For a French company, read `fr=True`.

        ⚠️ `op="list"` returns active, pending and LAST YEAR's archived contracts —
        not the company's full history. And there is no amendment history anywhere
        in this API: a contract is served as it stands today.

        `op`:
        - **"list"** (default): paginated; pass back `next_cursor` as `cursor`.
        - **"get"**: one contract (`contract_id`).
        - **"create"**: NOT WIRED — never writes. It answers the named refusal
          `payfit_write_not_wired`, saying what it would have done; nothing is sent
          to PayFit.

        Args:
            op: list (default) | get | create.
            contract_id: op="get".
            collaborator_id / job_title / start_date: op="create" only — not wired,
                nothing is sent.
            fr: op="list"/"get" — French variant (default False).
            include_in_progress: op="list" — also contracts still being created.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`contractId` always
                kept); omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, contract_id=contract_id, collaborator_id=collaborator_id,
                           job_title=job_title, start_date=start_date)
            c = _client()
            return S.page(run(lambda: c.list_contracts(
                limit=limit_or_default(limit), cursor=cursor,
                include_in_progress=include_in_progress, fr=bool(fr))),
                "contracts", "contractId", fields=fields, redaction=redaction_notice())
        if op == "get":
            need(op, contract_id=contract_id)
            refuse_ignored(op, collaborator_id=collaborator_id, job_title=job_title,
                           start_date=start_date, include_in_progress=include_in_progress,
                           limit=limit, cursor=cursor, fields=fields)
            c = _client()
            return S.one(run(lambda: c.get_contract(contract_id, fr=bool(fr))),
                         "contract", redaction=redaction_notice())
        if op == "create":
            raise not_wired(op, "created an employment contract (put on payroll)",
                            collaborator_id=collaborator_id, job_title=job_title,
                            start_date=start_date)
        raise refuse_unknown_op(op, "list", "get", "create")

    @mcp.tool()
    def payfit_absence(
        op: Literal["list", "create", "cancel"] = "list",
        absence_id: Optional[str] = None,
        contract_id: Optional[str] = None,
        absence_type: Optional[str] = None,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_moment: Optional[str] = None,
        end_moment: Optional[str] = None,
        comment: Optional[str] = None,
        status: Optional[list] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Absences in PayFit, read only: contract id, start and end (date + moment of
        day), status, and the type.

        The type is served as **`absence_type`** (not `type`), alongside
        **`absence_category`**: `ordinary_leave` for paid leave, RTT, rest, unpaid
        leave, remote work or school; `restricted` for everything else — sick
        leave, work accident, maternity, bereavement, marriage… The server default
        MASKS `absence_type` (health data, GDPR art. 9) and leaves
        `absence_category` readable, so absence planning works without reading a
        medical reason. An org_admin can lift that mask for the whole connector.
        A `••••` is a masked value, not a missing one — do not infer the reason.

        ⚠️ This API has **no leave balance and no counter**: acquired or remaining
        paid leave, RTT left, seniority-based days are nowhere in it. Do not
        compute a balance from this list and present it as PayFit's — it is not.

        `op`:
        - **"list"** (default): paginated; pass back `next_cursor` as `cursor`.
          There is no single-absence read upstream.
        - **"create"** and **"cancel"**: NOT WIRED — never write. They answer the
          named refusal `payfit_write_not_wired`, saying what they would have done;
          nothing is sent to PayFit.

        Args:
            op: list (default) | create | cancel.
            contract_id: op="list" — filter.
            begin_date / end_date: op="list" — YYYY-MM-DD, absences overlapping the
                window.
            absence_id / absence_type / start_moment / end_moment / comment:
                op="create"/"cancel" only — not wired, nothing is sent.
            status: op="list" — approved (PayFit's default) | pending_approval |
                declined | cancelled | pending_cancellation | all.
            limit: op="list" — 1..50 (default 50).
            cursor: op="list" — `next_cursor` of the previous page.
            fields: op="list" — keep only these keys per row (`id` always kept);
                omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, absence_id=absence_id, absence_type=absence_type,
                           start_moment=start_moment, end_moment=end_moment,
                           comment=comment)
            c = _client()
            return S.page(run(lambda: c.list_absences(
                limit=limit_or_default(limit), cursor=cursor, contract_id=contract_id,
                status=status, begin_date=begin_date, end_date=end_date)),
                "absences", "id", fields=fields, shape=S.absence,
                redaction=redaction_notice())
        if op == "create":
            # The reason (`absence_type`) is not included: health data, masked
            # by default, which would end up in the log.
            raise not_wired(op, "recorded an approved absence (goes into payroll)",
                            contract_id=contract_id, begin_date=begin_date,
                            end_date=end_date, start_moment=start_moment,
                            end_moment=end_moment)
        if op == "cancel":
            raise not_wired(op, "cancelled the absence", absence_id=absence_id)
        raise refuse_unknown_op(op, "list", "create", "cancel")
