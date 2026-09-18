"""PayFit — protection sociale complémentaire (mutuelle, prévoyance) et documents.

Module frère de `payfit.py` (même clé, même client, monté par `Connector.modules`).
Le client, les gardes, le rendu de fichier et la sonde vivent dans `payfit_garde`.

⚠️ **Deux niveaux qu'il ne faut pas confondre.** Les contrats de MUTUELLE et de
PRÉVOYANCE sont ceux de l'ENTREPRISE (référence, population, option, taux patronal
et salarial) ; l'affiliation est celle d'UN CONTRAT DE TRAVAIL. Lire le premier ne
dit pas qui est affilié : `affiliatedContractIds` le dit dans un sens,
`healthInsuranceContractIds` d'un `payfit_contract(fr=True)` dans l'autre.

⚠️ **Une affiliation REMPLACE, elle n'ajoute pas.** `op="affiliate"` pose la liste
entière : un id omis désaffilie le salarié du contrat correspondant. Lire l'état
courant avant d'écrire n'est pas une précaution, c'est la seule façon d'ajouter.

⚠️ **Une régularisation recalcule des cotisations DÉJÀ PASSÉES EN PAIE** et se
répercute sur un bulletin. C'est l'écriture la plus lourde de ce connecteur.

Les documents fiscaux (`income_tax`) et de retraite automatique (`auto_enrolment`)
sont **britanniques**. Il n'existe **aucun équivalent français** : ni DSN, ni
attestation, ni export fiscal FR dans cette API — un `payfit_document` sur une
entreprise française rendra une liste vide, et c'est la réponse juste.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from . import payfit_socle as S
from .payfit_garde import (_client, is_dry, need, preview, refuse, refuse_ignored,
                           refuse_unknown_op, run, serve_document)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def payfit_insurance(
        op: Literal["list", "affiliate", "regularize"] = "list",
        kind: Literal["health", "provident"] = "health",
        contract_id: Optional[str] = None,
        insurance_contract_ids: Optional[list] = None,
        employee_is_exempted: Optional[bool] = None,
        effective_date: Optional[str] = None,
        dry_run: Optional[bool] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Complementary health insurance (mutuelle) and provident fund
        (prévoyance) of a French PayFit company — the company's contracts, and one
        employee's affiliation to them.

        `kind` picks the family: `health` (mutuelle) or `provident` (prévoyance).
        Both read with the same scope upstream.

        `op`:
        - **"list"** (default): the COMPANY's contracts of that kind — reference,
          population code, option, contribution base and method, employer and
          employee rates, and `affiliatedContractIds` (the employment contracts
          attached to it).
        - **"affiliate"**: sets the employment contract's affiliation. ⚠️ It
          **REPLACES** the whole list: an id you omit unaffiliates the employee
          from it. Read the contract's current ids first
          (`payfit_contract(op="get", fr=True)`) and send the full intended list.
          `employee_is_exempted` only applies to `kind="health"`.
        - **"regularize"**: asks PayFit for a retroactive health-insurance
          regularization from `effective_date`. ⚠️ It recalculates contributions
          that ALREADY went through payroll and lands on a payslip.
          `kind="health"` only.

        ⚠️ `dry_run` DEFAULTS TO TRUE on affiliate and regularize.

        Args:
            op: list (default) | affiliate | regularize.
            kind: health (default) | provident.
            contract_id: op="affiliate"/"regularize" — the EMPLOYMENT contract.
            insurance_contract_ids: op="affiliate"/"regularize" — ids from
                `op="list"`. The complete intended list, not a delta.
            employee_is_exempted: op="affiliate", kind="health" — the employee is
                exempted from joining.
            effective_date: op="regularize" — YYYY-MM-DD.
            dry_run: op="affiliate"/"regularize" — default True.
            fields: op="list" — keep only these keys per row (`idContrat` always
                kept); omitted or `["*"]` = the full view.
        """
        if op == "list":
            refuse_ignored(op, contract_id=contract_id,
                           insurance_contract_ids=insurance_contract_ids,
                           employee_is_exempted=employee_is_exempted,
                           effective_date=effective_date, dry_run=dry_run)
            c = _client()
            env = (run(c.list_health_insurance_contracts) if kind == "health"
                   else run(c.list_provident_fund_contracts))
            return S.rows((env or {}).get("contracts"), "contracts", "idContrat",
                          fields=fields)
        if op not in ("affiliate", "regularize"):
            raise refuse_unknown_op(op, "list", "affiliate", "regularize")
        need(op, contract_id=contract_id,
             insurance_contract_ids=insurance_contract_ids)
        refuse_ignored(op, fields=fields)
        if op == "affiliate":
            refuse_ignored(op, effective_date=effective_date)
            if kind == "provident":
                refuse_ignored(op, employee_is_exempted=employee_is_exempted)
            if is_dry(dry_run):
                return preview(op, kind=kind, contract_id=contract_id,
                               replaces_with=insurance_contract_ids,
                               employee_is_exempted=employee_is_exempted)
            c = _client()
            if kind == "health":
                return S.one(run(lambda: c.set_health_insurance(
                    contract_id, health_insurance_contract_ids=insurance_contract_ids,
                    employee_is_exempted=employee_is_exempted)), "affiliated")
            return S.one(run(lambda: c.set_provident_fund(
                contract_id, provident_fund_contract_ids=insurance_contract_ids)),
                "affiliated")
        if kind != "health":
            raise refuse("PayFit : la régularisation n'existe que pour la mutuelle "
                         "(kind='health') — l'API n'en a pas pour la prévoyance.")
        need(op, effective_date=effective_date)
        refuse_ignored(op, employee_is_exempted=employee_is_exempted)
        if is_dry(dry_run):
            return preview(op, contract_id=contract_id,
                           insurance_contract_ids=insurance_contract_ids,
                           effective_date=effective_date)
        c = _client()
        return S.one(run(lambda: c.request_health_insurance_regularization(
            contract_id, health_insurance_contract_ids=insurance_contract_ids,
            effective_date=effective_date)), "regularization")

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
