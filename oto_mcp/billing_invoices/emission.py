"""Émettre la facture d'un encaissement, et l'avoir d'un remboursement (#488).

## Ce qui déclenche une facture

**L'ENCAISSEMENT, pas l'abonnement.** Dès qu'une ligne de `billing_payments` passe
à `paid`, un document est dû — que le mandat soit né ou non, que le miroir
d'abonnement soit posé ou non. Faire dépendre la facture de l'ouverture des droits
laisserait sans document exactement le cas qui a coûté cher le 25/08 : de l'argent
pris, un abonnement pas encore ouvert.

Trois chemins écrivent `paid`, et chacun appelle ici :

| chemin | quand |
| --- | --- |
| `billing.confirm` | retour navigateur, webhook d'un premier paiement, rattrapage |
| `billing.process_webhook` | échéance dont Mollie annonce l'encaissement |
| `billing_runner` (balayage) | **le filet** — tout ce que les deux premiers ont raté |

Le balayage n'est pas une redondance de confort : il est ce qui rend vraie la
phrase « jamais un paiement sans trace de facture ». Les appels en ligne ne font
que raccourcir le délai.

## Pourquoi l'émission ne fait jamais échouer un paiement

Un appel Pennylane peut refuser, expirer, ou n'avoir pas de clé. Laisser cette
exception remonter dans `confirm` rendrait une erreur au payeur **sur un paiement
réussi** — la faute exacte de #493, qui a fait repayer un client. L'émission est
donc absorbée, et ce n'est pas un repli silencieux : la tentative est écrite en
base (`billing_invoices`, `status='pending'`), sa cause est nommée (`error_code`),
elle est journalisée en `error`, et la reprise horaire la rejoue jusqu'à ce qu'elle
aboutisse. **Un `pending` qui dure est un incident visible, pas un oubli.**

## Ce qu'on refuse d'émettre

- un paiement **sans décomposition fiscale** (`amount_ht IS NULL`) : ce sont les
  deux encaissements du 25/08/2026, antérieurs à la règle de TVA. Inventer leur
  TVA ferait un document faux ; ils se régularisent à la main (`docs/billing.md`) ;
- un paiement dont l'org n'a **pas d'identité de facturation** exploitable : une
  facture sans raison sociale ni adresse n'est pas une facture ;
- un brouillon dont le **total ne correspond pas** à ce qui a été débité. C'est la
  seule raison d'être du passage par un brouillon : une facture finalisée ne se
  supprime plus, elle ne se corrige que par un avoir.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .. import billing, billing_vat, links, org_store
from ..db import billing as db_billing
from ..db import billing_invoices as db_invoices
from ..db import users as db_users
from . import mail, pennylane
from .pennylane import PennylaneUnavailable

logger = logging.getLogger(__name__)


class InvoiceRefused(RuntimeError):
    """L'émission est refusée par NOS règles (pas par le fournisseur) — la cause
    est un code stable, journalisé sur la ligne et lisible en supervision."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _as_datetime(value) -> Optional[datetime]:
    """Un horodatage du journal (normalisé « YYYY-MM-DD HH:MM:SS », UTC implicite)
    ou un `datetime` → un `datetime` conscient du fuseau. `None` si illisible."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # noqa: SILENT — horodatage illisible : l'appelant retombe sur maintenant
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _paid_at(payment_row: dict) -> datetime:
    """La date que PORTE la facture : celle de l'encaissement.

    Dérivée de la ligne de journal (`updated_at`, l'instant où `paid` y a été
    gravé) plutôt que passée par l'appelant : elle doit être la MÊME à la première
    tentative et à la dixième reprise, sinon une facture rejouée trois jours plus
    tard porterait la date de sa reprise."""
    return (_as_datetime(payment_row.get("updated_at"))
            or _as_datetime(payment_row.get("created_at"))
            or datetime.now(timezone.utc))


def _plan_meta(org_id: int, plan: Optional[str]) -> tuple[Optional[str], str]:
    """`(libellé du palier, intervalle)`. Le palier vient de l'appelant quand il le
    connaît (`confirm` le lit dans la metadata du paiement), sinon du miroir
    d'abonnement.

    Aucun des deux ⟹ libellé `None` : le paiement est réel, la facture est due, et
    seul le NOM du palier manque à sa ligne — la refuser pour ça priverait le
    client d'un document à cause d'un mot."""
    if not plan:
        sub_row = db_billing.get_org_subscription(org_id) or {}
        plan = sub_row.get("plan")
    meta = billing.PLANS.get(plan or "") or {}
    if not meta:
        logger.warning("facturation: org %s — palier inconnu (%r), la ligne de "
                       "facture portera un libellé générique", org_id, plan)
    return meta.get("label"), meta.get("interval") or "month"


def _period(org_id: int, debut: datetime, interval: str) -> tuple[datetime, datetime]:
    """La période couverte. Elle commence à l'encaissement et finit à la borne du
    cycle quand le miroir en porte une postérieure — c'est elle qui fait foi
    (`org_subscriptions.current_period_end`), l'ajout calendaire n'étant qu'un
    repli quand l'abonnement n'existe plus (résilié, jamais ouvert)."""
    fin = _as_datetime((db_billing.get_org_subscription(org_id) or {})
                       .get("current_period_end"))
    if fin is None or fin <= debut:
        fin = billing._add_period(debut, interval)
    return debut, fin


def _identity(org_id: int) -> dict:
    identity = db_billing.get_billing_identity(org_id)
    manques = billing_vat.missing_identity_fields(identity)
    if manques:
        raise InvoiceRefused(
            "billing_identity_required",
            f"org {org_id} : identité de facturation incomplète, champs manquants "
            f"— {', '.join(manques)}. Une facture sans raison sociale ni adresse "
            f"n'est pas opposable.")
    return identity


def _cents(valeur) -> Optional[int]:
    try:
        return round(float(valeur) * 100)
    except (TypeError, ValueError):
        return None


def _verifier_montants(doc: dict, attendu_ttc: int, attendu_ht: int, ref: str) -> None:
    """Le brouillon dit-il ce qui a été débité ?

    Le contrôle attrape ce qu'aucun autre ne peut voir : un code de TVA qui ferait
    calculer 20 % là où le régime est à 0 %. Un champ absent n'est PAS un écart —
    on ne bloque pas une facture sur une réponse plus pauvre qu'attendu, on le dit."""
    ttc, ht = _cents(doc.get("currency_amount")), _cents(doc.get("currency_amount_before_tax"))
    if ttc is None and ht is None:
        logger.warning("facturation: brouillon %s sans total lisible — contrôle de "
                       "montant impossible, finalisation quand même", ref)
        return
    ecarts = []
    if ttc is not None and ttc != attendu_ttc:
        ecarts.append(f"TTC {ttc} ≠ {attendu_ttc} centimes débités")
    if ht is not None and ht != attendu_ht:
        ecarts.append(f"HT {ht} ≠ {attendu_ht} centimes")
    if ecarts:
        raise InvoiceRefused(
            "amount_mismatch",
            f"brouillon {ref} : {' ; '.join(ecarts)}. NON finalisé — une facture "
            f"finalisée ne se supprime plus, elle ne se corrige que par un avoir.")


def _est_brouillon(doc: dict) -> bool:
    return bool(doc.get("draft")) or doc.get("status") == "draft"


def _emettre(row: dict, payment_row: dict, *, kind: str, label: str,
             amount_ht: int, amount_ttc: int, vat_amount: Optional[int],
             vat_rate_bps: Optional[int], vat_scheme: Optional[str],
             date: datetime, period: tuple[datetime, datetime],
             credited_invoice_id: Optional[int] = None) -> dict:
    """Le geste fournisseur, commun à la facture et à l'avoir. Lève, ne journalise
    pas : c'est l'appelant qui écrit l'issue sur la ligne."""
    org_id = payment_row["org_id"]
    identity = _identity(org_id)
    customer_id = pennylane.sync_customer(org_id, identity)
    ref = pennylane.invoice_external_reference(row["payment_ref"] or f"row{row['payment_row_id']}",
                                               kind)
    jour = date.date().isoformat()

    doc = pennylane.find_document(ref)
    if doc is None:
        doc = pennylane.create_document(
            kind=kind, customer_id=customer_id, date=jour,
            # Échéance = le jour même : le document constate un encaissement déjà
            # fait, il n'appelle aucun règlement.
            deadline=jour, label=label, amount_ht=abs(amount_ht),
            vat_scheme=vat_scheme, vat_rate_bps=vat_rate_bps,
            external_reference=ref,
            free_text=billing_vat.mention_for(vat_scheme or ""),
            currency=(payment_row.get("currency") or "eur").upper())
    if _est_brouillon(doc):
        _verifier_montants(doc, abs(amount_ttc), abs(amount_ht), ref)
        doc = pennylane.finalize(int(doc["id"]))

    pennylane_id = int(doc["id"]) if doc.get("id") else None
    if pennylane_id is None:
        raise PennylaneUnavailable("pennylane_bad_response",
                                   f"document {ref} sans identifiant")
    if kind == "credit_note" and credited_invoice_id:
        pennylane.link_credit_note(credited_invoice_id, pennylane_id)

    db_invoices.mark_billing_invoice_issued(
        row["id"], pennylane_invoice_id=pennylane_id,
        number=doc.get("invoice_number"), external_reference=ref,
        pennylane_customer_id=customer_id, credited_invoice_id=credited_invoice_id,
        currency=(payment_row.get("currency") or "eur"),
        amount_ht=amount_ht, vat_rate_bps=vat_rate_bps, vat_amount=vat_amount,
        amount_ttc=amount_ttc, vat_scheme=vat_scheme,
        period_start=period[0], period_end=period[1], issued_at=date)

    pdf, url = pennylane.fetch_pdf(doc, ref)
    if pdf:
        nom = f"{(doc.get('invoice_number') or ref)}.pdf".replace("/", "-")
        db_invoices.set_billing_invoice_pdf(row["id"], pdf, filename=nom, url=url)
    else:
        # La facture EXISTE et porte son numéro : l'absence de PDF est un manque de
        # pièce jointe, pas une émission ratée. La reprise le retéléchargera.
        db_invoices.set_billing_invoice_pdf(row["id"], None, url=url)
        logger.warning("facturation: document %s émis sans PDF récupéré", ref)
    return db_invoices.get_billing_invoice(row["id"])


# ── destinataire ─────────────────────────────────────────────────────────────

def billing_contact(org_id: int, identity: Optional[dict] = None) -> Optional[str]:
    """À qui part la facture : l'adresse déclarée sur l'identité, sinon le premier
    org_admin (par ancienneté). Aucune des deux ⟹ personne, et on le dit — un
    e-mail envoyé « au hasard » d'un membre serait pire qu'un e-mail non envoyé."""
    identity = identity if identity is not None else db_billing.get_billing_identity(org_id)
    if identity and identity.get("billing_email"):
        return identity["billing_email"]
    admins = [m["sub"] for m in org_store.list_org_members(org_id)
              if m.get("org_role") == "org_admin"]
    emails = db_users.emails_by_subs(admins)
    for sub in admins:
        if emails.get(sub):
            return emails[sub]
    return None


def _notifier(row: dict, org_id: int) -> None:
    """Envoie l'e-mail une seule fois par document (`emailed_at`). Best-effort :
    un e-mail non parti ne remet pas en cause la facture."""
    if row.get("emailed_at") or row.get("status") != "issued":
        return
    to = billing_contact(org_id)
    if not to:
        logger.warning("facturation: aucune adresse de facturation pour l'org %s — "
                       "document %s non notifié", org_id, row.get("number") or row["id"])
        return
    vue = dict(row)
    vue["vat_mention"] = billing_vat.mention_for(row.get("vat_scheme") or "")
    lien = links.link_for("billing")
    if mail.send_invoice_email(to, vue, app_url=lien):
        db_invoices.mark_billing_invoice_emailed(row["id"], to)


# ── facture ──────────────────────────────────────────────────────────────────

def ensure_invoice_for_payment(payment_row: dict, *,
                               plan: Optional[str] = None) -> Optional[dict]:
    """Le point d'entrée de tous les chemins : trace, émission, e-mail. NE LÈVE PAS.

    Idempotent par construction — la trace est unique en base `(paiement, kind)`,
    et une ligne déjà `issued` ressort telle quelle sans toucher au fournisseur.
    Un webhook rejoué ne crée donc pas une seconde facture."""
    if str(payment_row.get("status")) != "paid":
        return None
    org_id, row_id = payment_row["org_id"], payment_row["id"]
    if payment_row.get("amount_ht") is None:
        # Règle (c) de #488 : les deux encaissements du 25/08 n'ont pas de TVA
        # calculée et ne s'inventent pas. Aucune trace créée — une ligne `pending`
        # que rien ne pourra jamais résoudre serait une fausse alerte permanente.
        logger.info("facturation: paiement %s (org %s) sans décomposition fiscale — "
                    "hors règle, régularisation manuelle (docs/billing.md)",
                    row_id, org_id)
        return None

    ref = payment_row.get("payment_id") or payment_row.get("payment_intent_id")
    row = db_invoices.ensure_billing_invoice(org_id, row_id, kind="invoice",
                                             payment_ref=ref)
    if row["status"] == "issued":
        _notifier(row, org_id)
        return row

    label_plan, interval = _plan_meta(org_id, plan)
    date = _paid_at(payment_row)
    debut, fin = _period(org_id, date, interval)
    libelle = (f"Abonnement {label_plan}" if label_plan else "Abonnement Otomata")
    libelle += f" — période du {debut.date().isoformat()} au {fin.date().isoformat()}"

    try:
        row = _emettre(
            row, payment_row, kind="invoice", label=libelle,
            amount_ht=int(payment_row["amount_ht"]),
            amount_ttc=int(payment_row["amount"]),
            vat_amount=payment_row.get("vat_amount"),
            vat_rate_bps=payment_row.get("vat_rate_bps"),
            vat_scheme=payment_row.get("vat_scheme"),
            date=date, period=(debut, fin))
    except (PennylaneUnavailable, InvoiceRefused) as e:
        db_invoices.mark_billing_invoice_failed(row["id"], e.code, e.detail)
        logger.error("facturation: org %s, paiement %s — facture NON émise (%s) : %s",
                     org_id, row_id, e.code, e.detail)
        return db_invoices.get_billing_invoice(row["id"])
    logger.info("facturation: org %s — facture %s émise pour le paiement %s",
                org_id, row.get("number"), row_id)
    _notifier(row, org_id)
    return row


# ── avoir ────────────────────────────────────────────────────────────────────

def ensure_credit_note_for_refund(payment_row: dict,
                                  refunded_cents: int) -> Optional[dict]:
    """L'avoir d'un remboursement constaté chez le PSP. NE LÈVE PAS.

    ⚠️ **Un seul avoir par paiement** (clé `(paiement, 'credit_note')`). Un second
    remboursement PARTIEL sur le même paiement ne produira donc pas un second
    document : le cas est journalisé en `error` et demande un avoir manuel. Une clé
    par remboursement supposerait de suivre les objets `refund` de Mollie, que le
    webhook ne porte pas — il ne donne que l'id du paiement et son
    `amountRefunded` cumulé."""
    if refunded_cents <= 0:
        return None
    org_id, row_id = payment_row["org_id"], payment_row["id"]
    if payment_row.get("amount_ht") is None:
        # Même règle (c) que pour la facture : ces paiements-là ne sont pas facturés
        # automatiquement, donc leur remboursement ne s'avoire pas automatiquement
        # non plus. Une ligne `pending` sans facture à annuler ne se résoudrait
        # jamais — elle sonnerait pour toujours.
        logger.info("facturation: remboursement du paiement %s (org %s) sans "
                    "décomposition fiscale — avoir manuel (docs/billing.md)",
                    row_id, org_id)
        return None
    facture = db_invoices.get_billing_invoice_for_payment(row_id, "invoice")
    ref = payment_row.get("payment_id") or payment_row.get("payment_intent_id")
    row = db_invoices.ensure_billing_invoice(org_id, row_id, kind="credit_note",
                                             payment_ref=ref,
                                             amount_ttc=-refunded_cents)
    if row["status"] == "issued":
        deja = abs(int(row.get("amount_ttc") or 0))
        if refunded_cents > deja:
            logger.error("facturation: org %s, paiement %s — remboursement cumulé de "
                         "%s centimes alors que l'avoir %s n'en couvre que %s : "
                         "avoir complémentaire à émettre À LA MAIN",
                         org_id, row_id, refunded_cents, row.get("number"), deja)
        _notifier(row, org_id)
        return row

    if not facture or facture.get("status") != "issued":
        # Un avoir annule une facture : sans facture émise, il n'a rien à annuler.
        # La ligne reste `pending` et la reprise réessaiera — l'ordre se rétablit
        # tout seul dès que la facture part.
        db_invoices.mark_billing_invoice_failed(
            row["id"], "invoice_not_issued",
            f"paiement {row_id} : la facture n'est pas encore émise, l'avoir attend")
        return db_invoices.get_billing_invoice(row["id"])

    montant = int(payment_row["amount"])
    ht_total = int(payment_row["amount_ht"] or 0)
    if refunded_cents >= montant:
        ht, tva = ht_total, int(payment_row.get("vat_amount") or 0)
        ttc = montant
    else:
        # Remboursement PARTIEL : la ventilation suit la proportion remboursée, la
        # TVA étant le reste — jamais recalculée au taux, sinon la somme des deux
        # ne retomberait pas sur ce qui a été rendu au client.
        ttc = refunded_cents
        ht = round(refunded_cents * ht_total / montant) if montant else refunded_cents
        tva = ttc - ht

    date = _paid_at(payment_row)
    libelle = f"Avoir sur facture {facture.get('number') or ''}".strip()
    try:
        row = _emettre(
            row, payment_row, kind="credit_note", label=libelle,
            amount_ht=-ht, amount_ttc=-ttc, vat_amount=-tva,
            vat_rate_bps=payment_row.get("vat_rate_bps"),
            vat_scheme=payment_row.get("vat_scheme"),
            date=date, period=(_as_datetime(facture.get("period_start")) or date,
                               _as_datetime(facture.get("period_end")) or date),
            credited_invoice_id=facture.get("pennylane_invoice_id"))
    except (PennylaneUnavailable, InvoiceRefused) as e:
        db_invoices.mark_billing_invoice_failed(row["id"], e.code, e.detail)
        logger.error("facturation: org %s, paiement %s — avoir NON émis (%s) : %s",
                     org_id, row_id, e.code, e.detail)
        return db_invoices.get_billing_invoice(row["id"])
    logger.info("facturation: org %s — avoir %s émis (%s centimes) sur la facture %s",
                org_id, row.get("number"), ttc, facture.get("number"))
    _notifier(row, org_id)
    return row


# ── les deux points d'appel du cycle de paiement ─────────────────────────────
#
# `billing.confirm` et `billing.process_webhook` passent par ICI et non par les
# fonctions ci-dessus, pour deux raisons qui tiennent au chemin de paiement :
# ils n'ont qu'un ID de ligne (la ligne en mémoire porte le statut d'AVANT le
# passage à `paid`), et surtout **aucune exception ne doit remonter chez eux**.

def facturer_encaissement(payment_row_id: int, *, plan: Optional[str] = None) -> None:
    """Émet la facture d'un paiement qui vient de passer `paid`. **Ne lève jamais.**

    Absorber est la règle, pas une facilité : une exception qui remonterait ferait
    rendre une erreur au payeur **sur un paiement réussi** — l'enchaînement exact
    qui a fait payer deux fois le 25/08 (#493). L'échec n'est pas perdu : il est
    journalisé sur `billing_invoices` avec sa cause, en `pending`, et le
    `billing_runner` le rejoue à chaque tick.

    La ligne est RELUE en base plutôt que reprise en mémoire : la date de la
    facture est celle de l'encaissement, et elle doit être la même à la première
    tentative qu'à la dixième reprise."""
    try:
        row = db_invoices.billing_payment_row(payment_row_id)
        if row:
            ensure_invoice_for_payment(row, plan=plan)
    except Exception as e:  # noqa: BLE001 — jamais une erreur servie sur un paiement réussi
        logger.error("facturation: paiement %s encaissé mais facture non émise — %s",
                     payment_row_id, e, exc_info=True)


def avoir_remboursement(payment_row_id: int, refunded_cents: int) -> None:
    """Émet l'avoir d'un remboursement constaté chez le PSP. **Ne lève jamais** —
    même raison : le webhook doit répondre 200, sinon Mollie le rejoue en boucle
    sur un état qu'un retry ne réparera pas."""
    try:
        row = db_invoices.billing_payment_row(payment_row_id)
        if row:
            ensure_credit_note_for_refund(row, refunded_cents)
    except Exception as e:  # noqa: BLE001 — un webhook ne rend jamais 500 là-dessus
        logger.error("facturation: remboursement du paiement %s — avoir non émis : %s",
                     payment_row_id, e, exc_info=True)


# ── reprise ──────────────────────────────────────────────────────────────────

def sweep(limit: int = 25) -> dict:
    """Le filet du `billing_runner` : facturer ce qui ne l'a pas été, réessayer ce
    qui a échoué. Compteurs pour le journal du tick."""
    counts: dict[str, int] = {}
    for payment_row in db_invoices.paid_payments_without_invoice(limit):
        ensure_invoice_for_payment(payment_row)
        counts["invoice_new"] = counts.get("invoice_new", 0) + 1
    for row in db_invoices.pending_billing_invoices(limit):
        payment_row = db_invoices.billing_payment_row(row["payment_row_id"])
        if not payment_row:
            continue
        if row["kind"] == "credit_note":
            # Le montant remboursé a été écrit à la création de la ligne : le
            # webhook qui l'a vu ne repassera pas, et rien d'autre ne le porte.
            rembourse = abs(int(row.get("amount_ttc") or 0))
            if not rembourse:
                logger.error("facturation: avoir %s en attente sans montant "
                             "remboursé — reprise impossible, avoir manuel", row["id"])
                continue
            ensure_credit_note_for_refund(payment_row, rembourse)
        else:
            ensure_invoice_for_payment(payment_row)
        counts["invoice_retry"] = counts.get("invoice_retry", 0) + 1
    return counts
