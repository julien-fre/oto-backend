"""PayFit — complementary social protection (health insurance, provident fund) and documents.

Sibling module of `payfit.py` (same key, same client, mounted by `Connector.modules`).
The client, the guards, the file rendering and the probe live in `payfit_garde`.

⚠️ **Two levels that must not be confused.** HEALTH INSURANCE and
PROVIDENT FUND contracts belong to the COMPANY (reference, population, option, employer
and employee rate); affiliation belongs to ONE EMPLOYMENT CONTRACT. Reading the former does not
say who is affiliated: `affiliatedContractIds` says it in one direction,
`healthInsuranceContractIds` of a `payfit_contract(fr=True)` in the other.

**No write is wired** (24/09/2026): `op="affiliate"` and
`op="regularize"` return the named refusal `payfit_write_not_wired`
(`payfit_garde.not_wired`), with no key and no call to PayFit.

The tax documents (`income_tax`) and automatic pension enrolment documents (`auto_enrolment`)
are **British**. There is **no French equivalent**: no DSN, no
certificate, no FR tax export in this API — a `payfit_document` on a
French company will return an empty list, and that is the right answer.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import payfit_socle as S
from .payfit_garde import (_client, need, not_wired, refuse_ignored, refuse_unknown_op,
                           run, serve_document)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def payfit_insurance(
        op: Literal["list", "affiliate", "regularize"] = "list",
        kind: Literal["health", "provident"] = "health",
        contract_id: Optional[str] = None,
        insurance_contract_ids: Optional[list] = None,
        employee_is_exempted: Optional[bool] = None,
        effective_date: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Complementary health insurance and provident fund
        of a French PayFit company — the company's contracts, read only.

        `kind` picks the family: `health` (health insurance) or `provident` (provident fund).
        Both read with the same scope upstream.

        `op`:
        - **"list"** (default): the COMPANY's contracts of that kind — reference,
          population code, option, contribution base and method, employer and
          employee rates, and `affiliatedContractIds` (the employment contracts
          attached to it).
        - **"affiliate"** and **"regularize"**: NOT WIRED — never write. They
          answer the named refusal `payfit_write_not_wired`, saying what they would
          have done; nothing is sent to PayFit. One employee's affiliation reads
          from `payfit_contract(op="get", fr=True)`.

        Args:
            op: list (default) | affiliate | regularize.
            kind: health (default) | provident.
            contract_id / insurance_contract_ids / employee_is_exempted /
                effective_date: op="affiliate"/"regularize" only — not wired,
                nothing is sent.
            fields: op="list" — keep only these keys per row (`idContrat` always
                kept); omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, contract_id=contract_id,
                           insurance_contract_ids=insurance_contract_ids,
                           employee_is_exempted=employee_is_exempted,
                           effective_date=effective_date)
            c = _client()
            env = (run(c.list_health_insurance_contracts) if kind == "health"
                   else run(c.list_provident_fund_contracts))
            return S.rows((env or {}).get("contracts"), "contracts", "idContrat",
                          fields=fields)
        if op == "affiliate":
            famille = "health insurance" if kind == "health" else "provident fund"
            raise not_wired(op, f"replaced the contract's {famille} affiliation",
                            contract_id=contract_id,
                            insurance_contract_ids=insurance_contract_ids)
        if op == "regularize":
            raise not_wired(op, "requested a health insurance regularization",
                            contract_id=contract_id,
                            insurance_contract_ids=insurance_contract_ids,
                            effective_date=effective_date)
        raise refuse_unknown_op(op, "list", "affiliate", "regularize")

    @mcp.tool()
    def payfit_document(
        op: Literal["income_tax", "auto_enrolment", "download"] = "income_tax",
        document_id: Optional[str] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Company documents held by PayFit — UK tax and pension documents, and the
        PDF of any of them.

        ⚠️ **United Kingdom only.** There is no French equivalent anywhere in this
        API: no DSN, no fiscal statement, no French export. On a French company
        these lists are empty, and that is the correct answer — not a sign that
        something is missing.

        `op`:
        - **"income_tax"** (default): UK income tax documents (P45, P60…) —
          `{documentId, type, year, month, contractId, documentUrl, createdAt}`.
        - **"auto_enrolment"**: UK pension auto-enrolment documents.
        - **"download"**: the PDF of one `document_id`, as a signed temporary URL.

        ⚠️ DOCUMENTS ARE LOCKED BY DEFAULT. A file cannot be field-redacted, and
        this one carries per-employee data (social security number, bank
        details, tax figures) that the org's PayFit field policy masks. It is
        served only when an org_admin has lifted every PayFit mask; otherwise
        the call is refused with the reason. Do not try to rebuild the document
        from other calls — the refusal is the org's policy, not a bug.

        Args:
            op: income_tax (default) | auto_enrolment | download.
            document_id: op="download" — a `documentId` from either list.
            fields: the two list ops — keep only these keys per row (`documentId`
                always kept); omitted or `["*"]` = the full view.
        """
        c = _client()
        if op in ("income_tax", "auto_enrolment"):
            refuse_ignored(op, document_id=document_id)
            env = (run(c.list_income_tax_documents) if op == "income_tax"
                   else run(c.list_auto_enrolment_documents))
            return S.rows((env or {}).get("documents"), "documents", "documentId",
                          fields=fields)
        if op == "download":
            need(op, document_id=document_id)
            refuse_ignored(op, fields=fields)
            return serve_document(lambda: c.get_document(document_id))
        raise refuse_unknown_op(op, "income_tax", "auto_enrolment", "download")
