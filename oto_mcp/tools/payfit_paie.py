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
Seule exception, et elle se DIT lue et non servie : les heures sup, que
`payfit_payslip(op="overtime")` lit dans le PDF du bulletin (`payfit_bulletin`). Leurs
nombres sont des données (filtrées champ par champ par la politique de l'org) ; la
ligne brute est du texte du bulletin, et suit le verrou des documents.

⚠️ **Le mois se dit `AAAAMM`** (janvier = `01`) partout ici, jamais `AAAA-MM` : c'est
la seule forme que PayFit accepte, et son refus ne nomme aucun champ. Le client
refuse la forme à tirets avant le réseau.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from fastmcp import FastMCP

from .. import file_extract
from . import payfit_bulletin
from . import payfit_socle as S
from .payfit_garde import (OVERTIME_LINE_LOCKED, _bad, _client, documents_unlocked,
                           limit_or_default, need, refuse_ignored, refuse_unknown_op,
                           run, serve_document)

# Au-delà, l'appel porterait trop de téléchargements : on demande un mois.
OVERTIME_MAX_PAYSLIPS = 24


def _overtime(c, collaborator_id: str, date: Optional[str]) -> dict:
    """Les lignes heures sup des bulletins d'un salarié (un mois, ou tous) — le PDF
    est lu CÔTÉ SERVEUR et seul ce qui nomme des heures sup en sort.

    `kind`, `label`, `numbers`, `rates` sont des données : la politique de champs de
    l'org les filtre par nom, comme toute sortie JSON. `line`, elle, est un extrait
    VERBATIM du bulletin, qui répète montants et taux sous un nom que la politique ne
    relie pas à `numbers` ni `rates` : elle suit le verrou des documents et ne sort
    que si l'org a ouvert les documents (`documents: true`, alerte du scanner de
    sécurité)."""
    if date is not None and not re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", date):
        raise _bad(f"PayFit : `date` s'écrit `AAAAMM` (janvier = 01), reçu « {date} ».")
    brute = documents_unlocked()
    env = run(lambda: c.list_payslips(collaborator_id))
    slips = [p for p in ((env or {}).get("payslips") or []) if isinstance(p, dict)]
    if date is not None:
        slips = [p for p in slips
                 if f"{int(p.get('year', 0)):04d}{int(p.get('month', 0)):02d}" == date]
    slips.sort(key=lambda p: (int(p.get("year", 0)), int(p.get("month", 0))),
               reverse=True)
    truncated = len(slips) > OVERTIME_MAX_PAYSLIPS
    rows = []
    for p in slips[:OVERTIME_MAX_PAYSLIPS]:
        blob = run(lambda p=p: c.get_payslip(
            collaborator_id, p.get("contractId"), p.get("payslipId")))
        ex = file_extract.extract((blob or {}).get("data") or b"",
                                  (blob or {}).get("filename") or "bulletin.pdf",
                                  (blob or {}).get("mimetype") or "application/pdf")
        row = {"year": p.get("year"), "month": p.get("month"),
               "contractId": p.get("contractId"), "payslipId": p.get("payslipId")}
        if ex.ok:
            row["lines"] = [l if brute else {k: v for k, v in l.items() if k != "line"}
                            for l in payfit_bulletin.overtime_lines(ex.text)]
        else:
            row["lines"] = []
            row["unreadable"] = f"{ex.status} : {ex.detail}"
        rows.append(row)
    out = {"collaboratorId": collaborator_id, "count": len(rows), "payslips": rows,
           "notice": ("lignes lues dans le texte du PDF, format non contractuel : "
                      "`numbers` est dans l'ordre de la ligne, sans rôle attribué. "
                      "Vérifie la lecture sur un bulletin avant d'en tirer un total.")}
    if not brute:
        out["line_withheld"] = OVERTIME_LINE_LOCKED
    if truncated:
        out["truncated"] = (f"{len(slips)} bulletins, {OVERTIME_MAX_PAYSLIPS} lus (les "
                            "plus récents) : passe `date` pour viser un mois.")
    return out


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def payfit_payslip(
        op: Literal["list", "download", "overtime"] = "list",
        collaborator_id: Optional[str] = None,
        contract_id: Optional[str] = None,
        payslip_id: Optional[str] = None,
        date: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Payslips of one PayFit collaborator — their metadata, the PDF itself, or
        only its overtime lines.

        ⚠️ **PayFit never serves a payslip's LINES.** There is no endpoint for the
        gross, the net, or any individual contribution: only the metadata and the
        PDF. Amounts that a program can read live in
        `payfit_payroll(op="accounting")`. Figures read from a PDF (op="download"
        text, op="overtime") are READ from the document, not returned by the API:
        say so when you use them.

        `op`:
        - **"list"** (default): every payslip of `collaborator_id`, all contracts
          together — `{year, month, contractId, payslipId, payslipUrl}`. Not
          paginated and not filterable upstream.
        - **"download"**: one payslip. Needs the THREE ids of one entry of the list
          plus the collaborator's. Returns the PDF's extracted TEXT inline
          (`{encoding: "text", format: "pdf-text", content, pages}`) plus
          `raw_url`, a short-lived signed URL to the original PDF.
        - **"overtime"**: ONLY the overtime lines (heures supplémentaires /
          complémentaires / majorées) of the collaborator's payslips — one month
          with `date` (`YYYYMM`), else the most recent 24. The PDF is read server
          side and nothing else leaves it: per payslip `{year, month, contractId,
          payslipId, lines: [{kind, label, numbers, rates, line}]}`. `line` (the
          raw text of the line) is part of the document: it is served only when
          the org's PayFit field policy masks nothing, else `line_withheld` says
          why. `kind` =
          `paiement` (the paid hours) or `allegement` (a contribution reduction or
          exemption on them — never add it to the paid amount). `numbers` are in
          the line's order, with no role assigned: the payslip layout is not a
          contract, so check the reading against one PDF before totalling. Loop
          over `payfit_collaborator()` for a whole company, and over `_account`
          for a group.

        ⚠️ op="download" IS LOCKED BY DEFAULT. A file cannot be field-redacted,
        and this one carries per-employee data (social security number, bank
        details, names and amounts) that the org's PayFit field policy masks. It
        is served only when an org_admin has lifted every PayFit mask; otherwise
        the call is refused with the reason. Do not try to rebuild the document
        from other calls — the refusal is the org's policy, not a bug.
        op="overtime" is not locked: the document never leaves the server; only
        its raw `line` follows the lock.

        Args:
            op: list (default) | download | overtime.
            collaborator_id: all ops — from `payfit_collaborator`.
            contract_id: op="download" — the `contractId` OF THAT payslip entry,
                not another contract of the person.
            payslip_id: op="download" — the entry's `payslipId`.
            date: op="overtime" — the month, `YYYYMM` (January = `01`); omitted =
                the most recent 24 payslips.
            fields: op="list" — keep only these keys per row (`payslipId` always
                kept); omitted or `["*"]` = the full view.
        """
        # L'op d'abord : un `op` inventé qui se ferait répondre « exige
        # collaborator_id » enverrait chercher un argument au lieu d'une op.
        if op not in ("list", "download", "overtime"):
            raise refuse_unknown_op(op, "list", "download", "overtime")
        need(op, collaborator_id=collaborator_id)
        if op == "list":
            refuse_ignored(op, contract_id=contract_id, payslip_id=payslip_id, date=date)
            c = _client()
            env = run(lambda: c.list_payslips(collaborator_id))
            return S.rows((env or {}).get("payslips"), "payslips", "payslipId",
                          fields=fields)
        if op == "overtime":
            refuse_ignored(op, contract_id=contract_id, payslip_id=payslip_id,
                           fields=fields)
            return _overtime(_client(), collaborator_id, date)
        need(op, contract_id=contract_id, payslip_id=payslip_id)
        refuse_ignored(op, fields=fields, date=date)
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
