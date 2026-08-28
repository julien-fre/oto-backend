"""Facturation des abonnements (#488) — un document Pennylane par encaissement.

Un **package** et non un module : `oto_mcp/billing.py` porte déjà le cycle de
paiement sur 650 lignes, et ce lot ajoute trois responsabilités qui n'ont pas la
même durée de vie ni les mêmes raisons de changer.

    pennylane.py   le seam FOURNISSEUR — la clé de la compta d'Otomata, la
                   traduction d'un échec en exception, les codes de TVA
    emission.py    le CYCLE — trace, facture, avoir, reprise
    mail.py        l'E-MAIL au contact de facturation

Ce qui vit ailleurs, et pourquoi :

- la **table** `billing_invoices` → `db/billing_invoices.py`, comme tout le store ;
- la **surface** (liste + PDF) → `capabilities/billing_invoices.py` et la route de
  téléchargement dans `api/billing.py` (un PDF ne passe pas par la couche
  capacité, qui ne rend que du JSON) ;
- le **client HTTP** Pennylane → oto-core (`oto.tools.pennylane`), source unique
  des connecteurs. Rien de HTTP n'est réécrit ici.

Ce que le reste du backend appelle tient en trois noms — c'est l'intérêt de la
façade : les appelants (`billing.confirm`, `billing.process_webhook`,
`billing_runner.tick`) ne connaissent ni Pennylane ni la table.
"""
from __future__ import annotations

from .emission import (          # noqa: F401 — la façade EST la surface publique
    InvoiceRefused,
    avoir_remboursement,
    billing_contact,
    ensure_credit_note_for_refund,
    ensure_invoice_for_payment,
    facturer_encaissement,
    sweep,
)
from .pennylane import PLATFORM_KEY_ENV, PennylaneUnavailable, is_configured  # noqa: F401

__all__ = [
    "InvoiceRefused",
    "PLATFORM_KEY_ENV",
    "PennylaneUnavailable",
    "avoir_remboursement",
    "billing_contact",
    "ensure_credit_note_for_refund",
    "ensure_invoice_for_payment",
    "facturer_encaissement",
    "is_configured",
    "sweep",
]
