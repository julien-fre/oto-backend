"""Factures d'ACHAT Pennylane — importer, lire, corriger, valider.

Module du connecteur `pennylane` (cf. `Connector.modules` au registre) : même
clé, même client, domaine distinct — comme le grand livre et les devis.

Cycle : `pennylane_upload_file` dépose le PDF, `op="import"` crée la facture
(avec `import_as_incomplete=True`, elle arrive en `accounting_status =
validation_needed`), `op="update"` la corrige (libellé, dates, montants, et le
`vat_rate` de ses LIGNES), `op="validate"` la passe en `complete`.

**La validation est une écriture comptable ENGAGEANTE** : jamais implicite,
jamais dans un import, et l'API n'expose pas le geste inverse.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from fastmcp import FastMCP

from .pennylane_socle import _bad, _client, _ecrit, _need

_CHAMPS_FACTURE = ("label", "date", "deadline", "invoice_number", "supplier_id",
                   "currency", "currency_amount_before_tax", "currency_amount",
                   "currency_tax", "external_reference")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def pennylane_supplier_invoice(
        op: Literal["list", "get", "lines", "import", "update", "validate"] = "list",
        invoice_id: Optional[int] = None,
        max_pages: Optional[int] = None,
        file_attachment_id: Optional[int] = None,
        supplier_id: Optional[int] = None,
        date: Optional[str] = None,
        deadline: Optional[str] = None,
        currency_amount_before_tax: Optional[str] = None,
        currency_amount: Optional[str] = None,
        currency_tax: Optional[str] = None,
        invoice_lines: Optional[Union[list[dict], dict]] = None,
        currency: Optional[str] = None,
        external_reference: Optional[str] = None,
        import_as_incomplete: bool = False,
        invoice_number: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict | list:
        """Factures d'ACHAT (factures fournisseurs reçues) — le côté « dépenses ».

        `op` :
        - "list" : l'inventaire des factures reçues, à confronter aux décaissements
          (rapprochement bancaire) ou pour repérer ce qui reste à payer. ⚠️ Sans
          `max_pages`, TOUT l'historique revient — commencer petit.
        - "get" (`invoice_id`) : la facture, dont `accounting_status` (draft |
          archived | entry | validation_needed | complete).
        - "lines" (`invoice_id`) : ses lignes, avec leur `id` et leur `vat_rate` —
          les `id` à passer à `op="update"`.
        - "import" : crée la facture depuis un PDF déjà posté
          (`pennylane_upload_file` → `file_attachment_id`). Pennylane ne fait PAS
          d'OCR : YOU (ayant lu le PDF) fournis les champs, montants en STRING.
          Le HT (`currency_amount_before_tax`) est exigé au niveau FACTURE et
          REFUSÉ dans une ligne : une ligne ne porte que `currency_amount` (TTC)
          et `currency_tax`, plus son `vat_rate`. `import_as_incomplete=True` la
          laisse en `validation_needed`.
        - "update" (`invoice_id`) : corrige une facture pas encore validée — les
          champs de facture fournis (`label`, `date`, `deadline`,
          `invoice_number`, `supplier_id`, montants, `external_reference`,
          `currency`) et les lignes, via `invoice_lines` = un OBJET
          `{"update": [{"id": …, "vat_rate": …}], "create": [...], "delete":
          [{"id": …}]}` (ici pas une liste). La référence ne documente aucun
          refus sur une facture déjà `complete` : si Pennylane refuse, le refus
          remonte tel quel.
        - "validate" (`invoice_id`) : passe la facture en `complete` — l'écriture
          comptable est posée. ⚠️ Geste ENGAGEANT et sans retour par l'API : ne
          l'appeler QUE sur demande explicite de l'utilisateur, facture par
          facture, jamais dans la foulée d'un import. Refus 422 si les lignes
          d'écriture ne sont pas équilibrées.

        `vat_rate` d'une ligne = code Pennylane : 20 %→"FR_200", 10 %→"FR_100",
        5,5 %→"FR_55", exonéré→"exempt". Codes d'AUTOLIQUIDATION de la référence
        v2 : `intracom_21`, `intracom_55`, `intracom_85`, `intracom_100`,
        `extracom`, `crossborder`, `FR_85_construction`, `FR_100_construction`,
        `FR_200_construction`. La référence ne les définit pas au-delà de leur nom
        et ne connaît AUCUN `intracom_200` : pour une acquisition intra-UE à 20 %,
        demander le bon code à l'utilisateur (question comptable), ne pas deviner.

        Plusieurs instances Pennylane dans une org (perso et société) : sans
        `_instance`, la clé personnelle répond d'abord. Pour les achats de la
        société, passer `_instance="org:<id>:pennylane"`.

        Args:
            op: "list" (défaut) | "get" | "lines" | "import" | "update" | "validate".
            invoice_id: requis pour get / lines / update / validate.
            max_pages: op="list" / "lines" — borne la pagination.
            file_attachment_id: op="import" — id renvoyé par `pennylane_upload_file`.
            supplier_id: op="import" (requis) / "update" — fournisseur existant
                (`pennylane_supplier`).
            date / deadline: op="import" (requis) / "update" — dates ISO (facture /
                échéance).
            currency_amount_before_tax / currency_amount / currency_tax:
                op="import" (requis) / "update" — HT / TTC / TVA de la FACTURE, en
                STRING.
            invoice_lines: op="import" — LISTE de ≥1 ligne (`currency_amount`,
                `currency_tax`, `vat_rate`, et `ledger_account_id` au besoin) ;
                op="update" — OBJET {create|update|delete}.
            currency: défaut EUR à l'import ; à l'update, seulement pour la changer.
            external_reference: clé d'idempotence / de trace.
            import_as_incomplete: op="import" — laisse la facture en
                `validation_needed`.
            invoice_number / label: numéro fournisseur / libellé comptable.
        """
        c = _client()
        if op == "list":
            return c.get_supplier_invoices(max_pages=max_pages)
        if op == "get":
            return c.get_supplier_invoice(_need(invoice_id, "invoice_id", op))
        if op == "lines":
            return c.get_supplier_invoice_lines(_need(invoice_id, "invoice_id", op),
                                                max_pages=max_pages)
        if op == "import":
            lignes = _need(invoice_lines, "invoice_lines", op)
            if not isinstance(lignes, list):
                raise _bad("op='import' : invoice_lines est une LISTE de lignes")
            return _ecrit(lambda: c.import_supplier_invoice(
                file_attachment_id=_need(file_attachment_id, "file_attachment_id", op),
                supplier_id=_need(supplier_id, "supplier_id", op),
                date=_need(date, "date", op), deadline=_need(deadline, "deadline", op),
                currency_amount_before_tax=_need(
                    currency_amount_before_tax, "currency_amount_before_tax", op),
                currency_amount=_need(currency_amount, "currency_amount", op),
                currency_tax=_need(currency_tax, "currency_tax", op),
                invoice_lines=lignes, currency=currency or "EUR",
                external_reference=external_reference,
                import_as_incomplete=import_as_incomplete,
                invoice_number=invoice_number, label=label,
            ), "l'import de facture d'achat")
        if op == "update":
            ident = _need(invoice_id, "invoice_id", op)
            valeurs = {"label": label, "date": date, "deadline": deadline,
                       "invoice_number": invoice_number, "supplier_id": supplier_id,
                       "currency": currency,
                       "currency_amount_before_tax": currency_amount_before_tax,
                       "currency_amount": currency_amount, "currency_tax": currency_tax,
                       "external_reference": external_reference}
            champs = {k: valeurs[k] for k in _CHAMPS_FACTURE if valeurs[k] is not None}
            if invoice_lines is not None and not isinstance(invoice_lines, dict):
                raise _bad("op='update' : invoice_lines est un OBJET "
                           "{create|update|delete: [...]}, pas une liste — pour "
                           "changer le vat_rate d'une ligne : {\"update\": "
                           "[{\"id\": <id de op='lines'>, \"vat_rate\": …}]}")
            try:
                return _ecrit(lambda: c.update_supplier_invoice(
                    ident, fields=champs, invoice_lines=invoice_lines),
                    "la correction de facture d'achat")
            except ValueError as e:
                raise _bad(f"op='update' : {e}") from e
        if op == "validate":
            return _ecrit(lambda: c.validate_supplier_invoice_accounting(
                _need(invoice_id, "invoice_id", op)),
                "la validation comptable de facture d'achat")
        raise _bad("op doit être 'list', 'get', 'lines', 'import', 'update' ou 'validate'")
