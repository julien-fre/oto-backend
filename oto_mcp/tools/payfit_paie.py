"""PayFit — la PAIE elle-même : bulletins, comptabilité et virements, état du cycle,
temps de travail réalisé, titres-restaurant.

Module frère de `payfit.py` (même clé, même client, monté par `Connector.modules`).
Découpé par domaine pour tenir sous 500 lignes ; le client, les gardes, le rendu de
fichier et la sonde vivent dans `payfit_garde`.

⚠️ **Ce que cette API ne sert pas, et qu'un agent est tenté d'inventer.** Il n'y a
**aucun endpoint** pour : les LIGNES d'un bulletin (brut, net, cotisation par
cotisation), les cumuls annuels, un « coût employeur » agrégé, les charges en tant
que ressource, la DSN, les plannings et les pointages, les notes de frais, les
avantages en nature en tant qu'objet, les soldes et compteurs de congés. Les seuls
montants structurés de toute l'API sont les **écritures comptables**
(`payfit_payroll(op="accounting")`) : le coût employeur et les charges s'y lisent par
numéro de compte (641x salaires, 645x cotisations, 6417x avantages en nature), et
c'est la seule voie qui existe. Le reste ne se déduit pas — ça se dit absent.

⚠️ **Le mois se dit `AAAAMM`** (janvier = `01`) partout ici, jamais `AAAA-MM` : c'est
la seule forme que PayFit accepte, et son refus ne nomme aucun champ. Le client
refuse la forme à tirets avant le réseau.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import payfit_socle as S
from .payfit_garde import (_client, limit_or_default, need, refuse_ignored,
                           refuse_unknown_op, run, serve_document)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def payfit_payslip(
        op: Literal["list", "download"] = "list",
        collaborator_id: Optional[str] = None,
        contract_id: Optional[str] = None,
        payslip_id: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Payslips of one PayFit collaborator — their metadata, or the PDF itself.

        ⚠️ **PayFit never serves a payslip's LINES.** There is no endpoint for the
        gross, the net, or any individual contribution: only the metadata and the
        PDF. Amounts that a program can read live in
        `payfit_payroll(op="accounting")`. Do not present figures extracted from a
        PDF as if the API had returned them.

        `op`:
        - **"list"** (default): every payslip of `collaborator_id`, all contracts
          together — `{year, month, contractId, payslipId, payslipUrl}`. Not
          paginated and not filterable upstream.
        - **"download"**: the PDF of one payslip. Needs the THREE ids of one entry
          of the list plus the collaborator's. Returns a signed temporary URL (or
          inline text for a small textual body), never the raw bytes.

        ⚠️ DOCUMENTS ARE LOCKED BY DEFAULT. A file cannot be field-redacted, and
        this one carries per-employee data (social security number, bank
        details, names and amounts) that the org's PayFit field policy masks. It
        is served only when an org_admin has lifted every PayFit mask; otherwise
        the call is refused with the reason. Do not try to rebuild the document
        from other calls — the refusal is the org's policy, not a bug.

        Args:
            op: list (default) | download.
            collaborator_id: both ops — from `payfit_collaborator`.
            contract_id: op="download" — the `contractId` OF THAT payslip entry,
                not another contract of the person.
            payslip_id: op="download" — the entry's `payslipId`.
            fields: op="list" — keep only these keys per row (`payslipId` always
                kept); omitted or `["*"]` = the full view.
        """
        # L'op d'abord : un `op` inventé qui se ferait répondre « exige
        # collaborator_id » enverrait chercher un argument au lieu d'une op.
        if op not in ("list", "download"):
            raise refuse_unknown_op(op, "list", "download")
        need(op, collaborator_id=collaborator_id)
        if op == "list":
            refuse_ignored(op, contract_id=contract_id, payslip_id=payslip_id)
            c = _client()
            env = run(lambda: c.list_payslips(collaborator_id))
            return S.rows((env or {}).get("payslips"), "payslips", "payslipId",
                          fields=fields)
        need(op, contract_id=contract_id, payslip_id=payslip_id)
        refuse_ignored(op, fields=fields)
        c = _client()
        return serve_document(lambda: c.get_payslip(
            collaborator_id, contract_id, payslip_id))

    @mcp.tool()
    def payfit_payroll(
        op: Literal["status", "accounting", "accounting_export",
                    "payment_file"] = "status",
        date: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """One month of PayFit payroll for the whole company: whether it is closed,
        its accounting entries, its accounting export, its bank payment file.

        All four ops take the same argument — the month, as **`YYYYMM`**
        (January = `01`).

        `op`:
        - **"status"** (default): `{status, executionEndDate}` — `completed` means
          the month's figures are final. Read this before trusting any of the other
          three: a `not_completed` month is still moving.
        - **"accounting"**: the payroll journal entries, as data — one row per
          accounting line: `operationDate`, `accountId`, `accountName`, `debit`,
          `credit`, `contractId`, `employeeFullName`, analytic codes. **This is
          where the employer cost and the contributions are** (641x wages, 645x
          contributions, 6417x benefits in kind); PayFit has no other structured
          figures and no aggregate of them. Not paginated: the whole month arrives.
        - **"accounting_export"**: the same journal as the FILE an accountant
          imports (CSV).
        - **"payment_file"**: the bank transfer file of the month — it carries every
          employee's bank details.

        ⚠️ DOCUMENTS ARE LOCKED BY DEFAULT. A file cannot be field-redacted, and
        `accounting_export` and `payment_file` carry per-employee data (social
        security number, bank details, names and amounts) that the org's PayFit
        field policy masks. It is served only when an org_admin has lifted every
        PayFit mask; otherwise the call is refused with the reason. Do not try
        to rebuild the document from other calls — the refusal is the org's
        policy, not a bug.
        `status` and `accounting` are data, not files: the org's policy redacts
        them field by field, so they are never locked.

        Args:
            op: status (default) | accounting | accounting_export | payment_file.
            date: the month, `YYYYMM` — required by all four ops.
            fields: op="accounting" — keep only these keys per row (`accountId`
                always kept); omitted or `["*"]` = the full view.
        """
        if op not in ("status", "accounting", "accounting_export", "payment_file"):
            raise refuse_unknown_op(op, "status", "accounting", "accounting_export",
                                    "payment_file")
        need(op, date=date)
        c = _client()
        if op == "status":
            refuse_ignored(op, fields=fields)
            return S.one(run(lambda: c.get_payroll_status(date)), "payroll")
        if op == "accounting":
            return S.rows(run(lambda: c.list_accounting_entries(date)),
                          "entries", "accountId", fields=fields)
        if op == "accounting_export":
            refuse_ignored(op, fields=fields)
            return serve_document(lambda: c.get_accounting_export(date))
        refuse_ignored(op, fields=fields)
        return serve_document(lambda: c.get_payment_file(date))

    @mcp.tool()
    def payfit_worked_time(
        date: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Worked time of one month, per contract (France): `effectiveWorkedTime`,
        `payedWorkedTime` and `workTimeUnit`.

        ⚠️ This is a **monthly aggregate, not a schedule**. PayFit serves no
        planning, no clock-ins and no day-by-day breakdown — there is no endpoint
        for them. A "forfait jours" contract shows up here as its unit; the
        modality itself is a field of `payfit_contract(fr=True)`.

        Paginated: pass back `next_cursor` as `cursor`.

        Args:
            date: the month, `YYYYMM` (January = `01`).
            limit: 1..50 (default 50).
            cursor: `next_cursor` of the previous page.
            fields: keep only these keys per row (`contractId` always kept);
                omitted or `["*"]` = the full view.
        """
        c = _client()
        return S.page(run(lambda: c.list_worked_time(
            date, limit=limit_or_default(limit), cursor=cursor)),
            "contracts", "contractId", fields=fields)

    @mcp.tool()
    def payfit_meal_voucher(
        date: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Meal vouchers (titres-restaurant) of one month, per collaborator
        (France): how many, their face value, the employer's share, the employee's
        share, and whether days off are eligible.

        Paginated: pass back `next_cursor` as `cursor`.

        Args:
            date: the month, `YYYYMM` (January = `01`).
            limit: 1..50 (default 50).
            cursor: `next_cursor` of the previous page.
            fields: keep only these keys per row (`collaboratorId` always kept);
                omitted or `["*"]` = the full view.
        """
        c = _client()
        return S.page(run(lambda: c.list_meal_vouchers(
            date, limit=limit_or_default(limit), cursor=cursor)),
            "mealVouchers", "collaboratorId", fields=fields)
