"""Store des factures et avoirs d'abonnement (#488) — la TRACE, pas le document.

Le document vit chez **Pennylane** : c'est lui qui porte la numérotation continue,
le PDF et la valeur probante. Cette table dit, pour chaque paiement encaissé, ce
qui a été émis en face — ou ce qui n'a PAS pu l'être, et pourquoi.

Trois propriétés portent tout le reste :

1. **La ligne naît AVANT l'appel au fournisseur**, en `pending`. Un encaissement
   sans facture reste alors visible et reprenable ; sans elle, une clé plateforme
   absente ou un Pennylane en panne ne laisserait aucune trace — un paiement muet.
2. **L'idempotence est en base** : `UNIQUE (payment_row_id, kind)`. Un webhook
   rejoué retombe sur la ligne existante et ne crée ni seconde facture ni second
   avoir. La garde est la contrainte, pas une lecture préalable (deux webhooks
   simultanés passeraient toute lecture).
3. **Le PDF ne sort jamais d'une liste.** `pdf` est un `BYTEA` et aucune lecture
   d'ici ne fait `SELECT *` : le row factory ne normalise que les dates, et des
   octets remontés dans un dict servi en JSON feraient une 500 à la sérialisation
   — sur le chemin le moins emprunté de la surface. Le PDF a son getter dédié.
"""
from __future__ import annotations

from typing import Any, Optional

from ._conn import _connect

# Ce qu'une lecture rend — TOUT sauf `pdf`. La colonne d'octets est nommée par son
# absence ici, et `has_pdf` dit ce qu'un client a besoin de savoir : y a-t-il un
# document à télécharger.
_INVOICE_COLS = (
    "id, org_id, payment_row_id, payment_ref, kind, status, external_reference, "
    "pennylane_customer_id, pennylane_invoice_id, credited_invoice_id, number, "
    "currency, amount_ht, vat_rate_bps, vat_amount, amount_ttc, vat_scheme, "
    "period_start, period_end, issued_at, pdf_filename, pdf_url, emailed_at, "
    "email_to, attempts, error_code, error_detail, last_attempt_at, created_at, "
    "updated_at, (pdf IS NOT NULL) AS has_pdf"
)

INVOICE_KINDS = ("invoice", "credit_note")


def ensure_billing_invoice(org_id: int, payment_row_id: int, *, kind: str = "invoice",
                           payment_ref: Optional[str] = None,
                           amount_ttc: Optional[int] = None) -> dict:
    """La ligne de trace d'une émission — créée si elle n'existe pas, rendue sinon.

    C'est le PREMIER geste d'une émission, avant tout appel réseau : ce qui suit
    peut échouer, la trace, elle, est déjà écrite. `ON CONFLICT DO NOTHING` plutôt
    qu'un `SELECT` préalable — deux webhooks concurrents sur le même paiement
    passeraient toute lecture, seule la contrainte les départage.

    `amount_ttc` sert l'AVOIR : le montant remboursé n'est connu que du webhook qui
    l'a vu passer, et une reprise horaire n'a plus aucun moyen de le retrouver (le
    webhook Mollie ne porte que l'id du paiement). Écrit à la CRÉATION, il est
    l'intention ; la finalisation le réécrira avec ce que le fournisseur a émis.
    """
    if kind not in INVOICE_KINDS:
        raise ValueError(f"kind de facture inconnu : {kind!r} "
                         f"({' | '.join(INVOICE_KINDS)})")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO billing_invoices "
            "  (org_id, payment_row_id, kind, payment_ref, amount_ttc) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (payment_row_id, kind) DO NOTHING",
            (org_id, payment_row_id, kind, payment_ref, amount_ttc))
        return conn.execute(
            f"SELECT {_INVOICE_COLS} FROM billing_invoices "
            "WHERE payment_row_id = %s AND kind = %s",
            (payment_row_id, kind)).fetchone()


def get_billing_invoice(invoice_id: int) -> Optional[dict]:
    with _connect() as conn:
        return conn.execute(
            f"SELECT {_INVOICE_COLS} FROM billing_invoices WHERE id = %s",
            (invoice_id,)).fetchone()


def get_billing_invoice_for_payment(payment_row_id: int,
                                    kind: str = "invoice") -> Optional[dict]:
    with _connect() as conn:
        return conn.execute(
            f"SELECT {_INVOICE_COLS} FROM billing_invoices "
            "WHERE payment_row_id = %s AND kind = %s", (payment_row_id, kind)).fetchone()


def list_billing_invoices(org_id: int, limit: int = 24) -> list[dict]:
    """Les documents d'une org, plus récents d'abord — factures ET avoirs."""
    with _connect() as conn:
        return list(conn.execute(
            f"SELECT {_INVOICE_COLS} FROM billing_invoices WHERE org_id = %s "
            "ORDER BY created_at DESC LIMIT %s", (org_id, limit)))


def mark_billing_invoice_issued(
    invoice_id: int, *,
    pennylane_invoice_id: Optional[int],
    number: Optional[str],
    external_reference: Optional[str] = None,
    pennylane_customer_id: Optional[int] = None,
    credited_invoice_id: Optional[int] = None,
    currency: str = "eur",
    amount_ht: Optional[int] = None,
    vat_rate_bps: Optional[int] = None,
    vat_amount: Optional[int] = None,
    amount_ttc: Optional[int] = None,
    vat_scheme: Optional[str] = None,
    period_start: Any = None,
    period_end: Any = None,
    issued_at: Any = None,
) -> None:
    """Le document est émis et FINALISÉ chez Pennylane : on grave ce qu'il porte.

    `error_code`/`error_detail` sont remis à NULL — une émission réussie efface la
    cause d'un échec précédent, sinon la ligne dirait à la fois « émise » et
    « voici pourquoi elle ne l'est pas »."""
    with _connect() as conn:
        conn.execute(
            "UPDATE billing_invoices SET status='issued', "
            "pennylane_invoice_id=%s, number=%s, "
            "external_reference=COALESCE(%s, external_reference), "
            "pennylane_customer_id=COALESCE(%s, pennylane_customer_id), "
            "credited_invoice_id=COALESCE(%s, credited_invoice_id), "
            "currency=%s, amount_ht=%s, vat_rate_bps=%s, vat_amount=%s, "
            "amount_ttc=%s, vat_scheme=%s, period_start=%s, period_end=%s, "
            "issued_at=%s, error_code=NULL, error_detail=NULL, updated_at=NOW() "
            "WHERE id=%s",
            (pennylane_invoice_id, number, external_reference, pennylane_customer_id,
             credited_invoice_id, currency, amount_ht, vat_rate_bps, vat_amount,
             amount_ttc, vat_scheme, period_start, period_end, issued_at, invoice_id))


def mark_billing_invoice_failed(invoice_id: int, code: str, detail: str = "") -> None:
    """L'émission n'a pas abouti : la ligne RESTE `pending` et dit pourquoi.

    Pas d'état terminal d'échec — un encaissement doit finir facturé. Le compteur
    de tentatives sert au diagnostic, jamais à abandonner : une reprise qui
    renoncerait laisserait un paiement sans document, et c'est précisément ce
    qu'on interdit."""
    with _connect() as conn:
        conn.execute(
            "UPDATE billing_invoices SET attempts = attempts + 1, error_code=%s, "
            "error_detail=%s, last_attempt_at=NOW(), updated_at=NOW() WHERE id=%s",
            (code, (detail or "")[:500], invoice_id))


def set_billing_invoice_pdf(invoice_id: int, pdf: Optional[bytes], *,
                            filename: Optional[str] = None,
                            url: Optional[str] = None) -> None:
    """Range le document téléchargé. L'URL Pennylane EXPIRE (30 min) : elle est
    conservée comme trace de provenance, jamais servie comme lien."""
    with _connect() as conn:
        conn.execute(
            "UPDATE billing_invoices SET pdf=%s, pdf_filename=%s, "
            "pdf_url=COALESCE(%s, pdf_url), updated_at=NOW() WHERE id=%s",
            (pdf, filename, url, invoice_id))


def get_billing_invoice_pdf(invoice_id: int) -> Optional[dict]:
    """Le SEUL chemin qui lit les octets — org comprise, pour que l'appelant puisse
    vérifier l'appartenance sans une seconde requête."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, org_id, kind, number, pdf, pdf_filename "
            "FROM billing_invoices WHERE id = %s", (invoice_id,)).fetchone()
    if row and isinstance(row.get("pdf"), memoryview):
        row["pdf"] = bytes(row["pdf"])
    return row


def mark_billing_invoice_emailed(invoice_id: int, to: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE billing_invoices SET emailed_at=NOW(), email_to=%s, "
            "updated_at=NOW() WHERE id=%s", (to, invoice_id))


def pending_billing_invoices(limit: int = 50) -> list[dict]:
    """Les émissions à reprendre — la file du `billing_runner`.

    Plus ANCIENNES d'abord : une facture qui n'est pas partie depuis trois jours
    passe avant celle d'il y a une heure, dont l'échec est peut-être transitoire."""
    with _connect() as conn:
        return list(conn.execute(
            f"SELECT {_INVOICE_COLS} FROM billing_invoices WHERE status = 'pending' "
            "ORDER BY created_at ASC LIMIT %s", (limit,)))


def paid_payments_without_invoice(limit: int = 50, *, since=None) -> list[dict]:
    """Encaissements qui n'ont même pas de ligne de trace (`billing_invoices`).

    C'est le filet du filet : si un appel inline n'a jamais eu lieu — process tué
    entre l'écriture de `paid` et l'émission, chemin de code futur qui grave un
    encaissement sans le dire ici — la facture se rattrape quand même. Sans lui,
    la garantie « jamais un paiement sans trace de facture » ne tiendrait qu'à la
    discipline des appelants.

    ⚠️ **`amount_ht IS NULL` est EXCLU, et c'est la règle (c) de #488.** Ce sont les
    deux encaissements du 25/08/2026, débités du HT sans TVA avant que la règle
    n'existe : sans décomposition fiscale, aucune facture conforme n'est
    calculable — en fabriquer une reviendrait à inventer une TVA qui n'a pas été
    collectée. Ils se régularisent à la main, et `docs/billing.md` dit comment."""
    clause = "AND p.created_at > %s " if since is not None else ""
    args: list = [] if since is None else [since]
    with _connect() as conn:
        return list(conn.execute(
            "SELECT p.* FROM billing_payments p "
            "WHERE p.status = 'paid' AND p.amount_ht IS NOT NULL " + clause +
            "  AND NOT EXISTS (SELECT 1 FROM billing_invoices i "
            "                  WHERE i.payment_row_id = p.id AND i.kind = 'invoice') "
            "ORDER BY p.created_at ASC LIMIT %s", (*args, limit)))


def billing_payment_row(payment_row_id: int) -> Optional[dict]:
    """La ligne de journal derrière une facture — ce que la reprise relit.

    Vit ici plutôt que dans `db/billing.py` parce que c'est la reprise de
    facturation qui la demande : le cycle de paiement, lui, a toujours sa ligne
    en main quand il en a besoin."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM billing_payments WHERE id = %s", (payment_row_id,)).fetchone()
