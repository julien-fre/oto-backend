"""Devis Pennylane — créer un devis, le lire, le lister, son PDF, le facturer.

Troisième module du connecteur `pennylane` (cf. `Connector.modules` au
registre) : même clé, même client, domaine distinct. Un devis précède la
facture quand le client l'exige (outil achats, bon pour accord) ; une pro forma
n'en tient pas lieu.

**Un devis n'a pas de brouillon.** Pennylane le crée au statut `pending` (en
attente d'acceptation) ; rien ne part chez le client tant qu'on ne le lui
transmet pas. Le statut ne bouge que par `op="set_status"`.

**Plusieurs instances Pennylane dans une même org** (par exemple une instance
personnelle et celle de la société) : sans `_instance`, c'est la cascade qui
choisit — la clé personnelle d'abord. Pour deviser ou facturer au nom de la
société, passer `_instance="org:<id>:pennylane"`.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP

from .pennylane_socle import _bad, _client, _ecrit, _need

_STATUTS = ("pending", "accepted", "denied", "invoiced", "expired")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def pennylane_quote(
        op: Literal["list", "get", "lines", "pdf", "create", "set_status",
                    "to_invoice"] = "list",
        quote_id: Optional[int] = None,
        customer_id: Optional[int] = None,
        date: Optional[str] = None,
        deadline: Optional[str] = None,
        lines: Optional[list] = None,
        free_text: Optional[str] = None,
        external_reference: Optional[str] = None,
        quote_template_id: Optional[int] = None,
        customer_invoice_template_id: Optional[int] = None,
        status: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> dict | list:
        """DEVIS de vente (quotes) — le document qui précède la facture.

        `op` :
        - "list" : les devis, filtrables par `status` et `customer_id` (filtre
          serveur). ⚠️ Sans `max_pages`, TOUT l'historique revient — commencer petit.
        - "get" (`quote_id`) : le devis complet, dont `status`, `quote_number`,
          `public_file_url` (le PDF) et `linked_invoices`.
        - "lines" (`quote_id`) : ses lignes.
        - "pdf" (`quote_id`) : rend `{quote_id, quote_number, public_file_url,
          filename}` — le lien du PDF à joindre à un mail. ⚠️ Le lien EXPIRE
          (30 minutes) : le relire juste avant de s'en servir, jamais le stocker.
        - "create" : crée le devis (`customer_id`, `date`, `deadline` = fin de
          validité, `lines`). Pas de brouillon : il naît `pending`. Le client doit
          exister (`pennylane_customer`). Annonce à l'utilisateur le client, les
          lignes et la validité avant d'appeler.
        - "set_status" (`quote_id`, `status`) : pending | accepted | denied |
          invoiced | expired — par exemple `accepted` quand le client a signé.
        - "to_invoice" (`quote_id`) : crée une facture client qui reprend le devis
          (client, lignes), toujours en **brouillon** ; la finaliser et l'envoyer
          restent des gestes de `pennylane_invoice`, après validation humaine.
          Demande le scope `customer_invoices:all` en plus de `quotes:all`.

        Les lignes ont le schéma STRICT des factures (tout écart → 400 opaque) —
        une ligne = UNE des 2 formes : produit `{product_id: int, quantity:
        number}` (overrides possibles : label, raw_currency_unit_price, unit,
        vat_rate), ou libre `{label: str, quantity: number, unit: str,
        raw_currency_unit_price: str, vat_rate: str}`, tous requis. Une REMISE
        est une ligne libre à prix négatif (`raw_currency_unit_price: "-100.00"`).
        `vat_rate` = code Pennylane : 20 %→"FR_200", 10 %→"FR_100", 5,5 %→"FR_55",
        exonéré→"exempt".

        Plusieurs instances Pennylane dans une org (perso et société) : sans
        `_instance`, la clé personnelle répond d'abord. Pour un devis au nom de la
        société, passer `_instance="org:<id>:pennylane"`.

        Args:
            op: "list" (défaut) | "get" | "lines" | "pdf" | "create" | "set_status"
                | "to_invoice".
            quote_id: requis pour get / lines / pdf / set_status / to_invoice.
            customer_id: op="create" (requis) ; op="list" (filtre).
            date: op="create" — date du devis (YYYY-MM-DD).
            deadline: op="create" — fin de validité (YYYY-MM-DD).
            lines: op="create" — lignes au schéma strict ci-dessus.
            free_text: op="create" — texte libre imprimé sur le PDF (conditions,
                référence de commande du client…).
            external_reference: op="create" — trace de la source ; op="to_invoice" —
                référence de la facture créée.
            quote_template_id: op="create" — modèle de rendu du devis.
            customer_invoice_template_id: op="to_invoice" — modèle de la facture.
            status: op="set_status" (requis) ; op="list" (filtre).
            max_pages: op="list" — borne la pagination.
        """
        if status is not None and status not in _STATUTS:
            raise _bad(f"status inconnu : {status!r} — attendu : {', '.join(_STATUTS)}")
        c = _client()
        if op == "list":
            return c.list_quotes(max_pages=max_pages, status=status,
                                 customer_id=customer_id)
        if op == "get":
            return c.get_quote(_need(quote_id, "quote_id", op))
        if op == "lines":
            return c.get_quote_lines(_need(quote_id, "quote_id", op))
        if op == "pdf":
            devis = c.get_quote(_need(quote_id, "quote_id", op))
            url = (devis or {}).get("public_file_url")
            if not url:
                raise _bad(f"Pennylane n'a rendu aucun lien PDF pour le devis {quote_id}.")
            return {"quote_id": quote_id, "quote_number": devis.get("quote_number"),
                    "public_file_url": url, "filename": devis.get("filename")}
        if op == "create":
            return _ecrit(lambda: c.create_quote(
                customer_id=_need(customer_id, "customer_id", op),
                date=_need(date, "date", op),
                deadline=_need(deadline, "deadline", op),
                lines=_need(lines, "lines", op),
                external_reference=external_reference, pdf_free_text=free_text,
                quote_template_id=quote_template_id), "la création de devis")
        if op == "set_status":
            return _ecrit(lambda: c.update_quote_status(
                _need(quote_id, "quote_id", op), _need(status, "status", op)),
                "le changement de statut du devis")
        if op == "to_invoice":
            return _ecrit(lambda: c.create_invoice_from_quote(
                _need(quote_id, "quote_id", op), draft=True,
                external_reference=external_reference,
                customer_invoice_template_id=customer_invoice_template_id),
                "la facturation du devis")
        raise _bad("op doit être 'list', 'get', 'lines', 'pdf', 'create', "
                   "'set_status' ou 'to_invoice'")
