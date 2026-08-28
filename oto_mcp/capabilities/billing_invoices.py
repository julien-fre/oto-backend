"""Les factures d'une org (#488) — la liste. Le PDF, lui, est une route à part.

REST-only comme toute la famille billing (ADR 0043) : une facture se consulte dans
un tableau de bord, elle ne transite pas dans un contexte LLM. Lecture = tout membre
de l'org active (`billing.payments` a le même régime, et une facture en dit moins
qu'un journal de tentatives).

⚠️ **Le téléchargement du PDF n'est PAS une capacité, et c'est structurel** : un
handler de capacité rend un `dict` que l'adaptateur emballe en `JSONResponse`
(`capabilities/_rest_adapter.py`) — il n'a aucun moyen de servir
`application/pdf`. La route de téléchargement vit donc dans `api/billing.py`,
écrite à la main, exactement comme l'export ZIP d'un projet
(`api/projects.py::me_project_export`). C'est la même exception, pour la même
raison : un octet n'est pas du JSON.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .. import billing
from ._authz import ORG_MEMBER
from ._types import Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

# Chemin REST du PDF — même patron que le binding de la route écrite à la main
# dans `api/billing.py`. Déclaré ICI parce que c'est la liste qui le sert au
# client ; les deux se lisent l'un à côté de l'autre dans le diff qui les change.
PDF_PATH = "/api/me/billing/invoices/{id}/pdf"


class InvoicesInput(BaseModel):
    limit: int = 24

    @field_validator("limit")
    @classmethod
    def _cap(cls, v):
        # Même garde que `PaymentsInput._cap` : la valeur part telle quelle en
        # `LIMIT %s`, un négatif ferait échouer Postgres en 500 opaque.
        return max(1, min(int(v), 100))


class Invoice(BaseModel):
    """Un document COMPTABLE émis pour un encaissement — pas une tentative de
    paiement (ça, c'est `billing.payments`). Son numéro vient de Pennylane, qui
    porte la numérotation continue d'Otomata."""
    id: int = Field(description="Identifiant local du document (séquence), celui "
                                "qu'attend la route de téléchargement du PDF.")
    kind: str = Field(description="'invoice' (facture) | 'credit_note' (AVOIR, émis "
                                  "sur remboursement — ses montants sont NÉGATIFS).")
    status: str = Field(
        description="'issued' = document émis, numéroté, définitif. 'pending' = "
                    "l'émission n'a pas encore abouti — l'encaissement, lui, a bien "
                    "eu lieu et la facture est due ; elle est rejouée "
                    "automatiquement. Un `pending` n'est jamais un paiement perdu.")
    number: Optional[str] = Field(
        default=None,
        description="Numéro de facture, attribué par Pennylane à la finalisation. "
                    "`null` tant que `status='pending'` : un numéro n'existe pas "
                    "avant le document.")
    currency: str = Field(description="Code devise ISO en minuscules ('eur').")
    amount_ht: Optional[int] = Field(default=None,
                                     description="Total hors taxes, en CENTIMES.")
    vat_rate_bps: Optional[int] = Field(
        default=None, description="Taux appliqué, en points de base (2000 = 20,00 %).")
    vat_amount: Optional[int] = Field(default=None, description="TVA, en centimes.")
    amount_ttc: Optional[int] = Field(
        default=None,
        description="Total toutes taxes comprises, en centimes — ce qui a été "
                    "réellement débité. ⚠️ NÉGATIF sur un avoir.")
    vat_scheme: Optional[str] = Field(
        default=None,
        description="'fr_ttc' | 'reverse_charge' (autoliquidation) | 'export'.")
    period_start: Optional[str] = Field(
        default=None, description="Début de la période d'abonnement couverte "
                                  "('YYYY-MM-DD HH:MM:SS' UTC).")
    period_end: Optional[str] = Field(default=None, description="Fin de la période.")
    issued_at: Optional[str] = Field(
        default=None,
        description="Date PORTÉE par le document, c'est-à-dire celle de "
                    "l'encaissement — pas celle de son émission technique.")
    has_pdf: bool = Field(
        description="Le PDF est-il disponible au téléchargement ? `false` avec "
                    "`status='issued'` signale un document bien émis dont le "
                    "fichier n'a pas encore été récupéré : la reprise le fera.")
    pdf_path: Optional[str] = Field(
        default=None,
        description="Chemin REST du PDF, à préfixer de la base d'API (ce n'est pas "
                    "une URL absolue, et surtout pas une URL publique : la route "
                    "exige le même jeton que le reste de `/api/me`). `null` quand "
                    "`has_pdf` est faux.")
    emailed_at: Optional[str] = Field(
        default=None,
        description="Envoi au contact de facturation. `null` = non envoyé (adresse "
                    "absente, ou relais indisponible) — le document reste "
                    "téléchargeable, l'e-mail n'en conditionne rien.")
    created_at: str = Field(description="Création de la ligne de suivi.")


class InvoicesView(BaseModel):
    """Factures ET avoirs de l'org active, plus récents d'abord. Liste vide sur un
    abonnement offert (comp) : rien n'y est encaissé, donc rien n'y est facturé."""
    invoices: list[Invoice]


_FIELDS = ("id", "kind", "status", "number", "currency", "amount_ht", "vat_rate_bps",
           "vat_amount", "amount_ttc", "vat_scheme", "period_start", "period_end",
           "issued_at", "has_pdf", "emailed_at", "created_at")


def _invoices(ctx: ResolvedCtx, inp: InvoicesInput) -> dict:
    from ..db import billing_invoices as db_invoices

    rows = db_invoices.list_billing_invoices(ctx.org_id, inp.limit)
    out = []
    for r in rows:
        item = {k: r.get(k) for k in _FIELDS}
        item["has_pdf"] = bool(r.get("has_pdf"))
        # Le chemin n'est servi QUE s'il y a quelque chose au bout : un lien vers
        # une 404 se subit au clic, il ne se diagnostique pas (même règle que
        # `links._bouton`).
        item["pdf_path"] = (PDF_PATH.format(id=r["id"]) if item["has_pdf"] else None)
        out.append(item)
    return {"invoices": out}


# Feature flag (ADR 0043, dark launch) : même gate que le reste du billing.
CAPABILITIES += [replace(_cap, gate=billing.is_enabled) for _cap in [
    Capability(
        key="me.billing.invoices.list", handler=_invoices, Input=InvoicesInput,
        authz=ORG_MEMBER, Output=InvoicesView,
        description="List the org's invoices and credit notes (most recent first). "
                    "Numbers come from Pennylane, which holds Otomata's continuous "
                    "numbering. The PDF itself is downloaded from `pdf_path`, a "
                    "separate authenticated route that returns application/pdf.",
        rest=RestBinding("GET", "/api/me/billing/invoices"),
    ),
]]
