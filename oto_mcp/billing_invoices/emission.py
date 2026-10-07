"""La TRACE d'un encaissement — plus aucune émission automatique (#488).

## L'émission automatique a été RETIRÉE le 2026-09-09

**Décision d'Alexis, 2026-09-09 : plus aucune pièce n'est créée chez Pennylane sans
geste humain.** Ni facture, ni avoir, ni fiche client. Le chemin qui le faisait —
composition du document, création du brouillon, contrôle de montant, finalisation,
téléchargement du PDF — et le seam fournisseur qui le portait
(`billing_invoices/pennylane.py`) ont été **supprimés, pas débranchés** : un appel
qu'on se contente de ne plus faire revient au premier nettoyage qui « répare » un
appel manquant.

Trois faits l'ont décidée :

- **le doublon.** Une facture d'une org cliente existait DÉJÀ chez Pennylane,
  créée à la main ; l'émission automatique en a produit une seconde ;
- **les données de facturation peuvent être fausses** — identité du client,
  adresse. Personne ne les vérifie avant qu'elles soient gravées ;
- **une facture finalisée n'est pas rattrapable.** C'est une pièce comptable : elle
  ne se supprime pas, elle ne se corrige que par un avoir. Un document faux émis
  tout seul coûte donc deux pièces et une explication au client.

⚠️ **La cible n'est PAS tranchée** (audit en cours au 2026-09-09) : soit un
BROUILLON chez Pennylane validé à la main, soit rien chez Pennylane et une facture
« à valider » côté oto. Rien ici ne présume de l'une ni de l'autre. Ce module ne
fait plus que la trace ; le store (`db/billing_invoices.py`) garde, lui, son
vocabulaire complet — `issued`, numéro, PDF — pour celle qui sera retenue.

## Ce qui continue, exactement comme avant

**L'encaissement est TRACÉ.** Dès qu'une ligne de `billing_payments` passe à
`paid`, une ligne de `billing_invoices` naît et passe aussitôt en **`held`** — le
statut ajouté ce jour-là — avec la cause de son attente
(`manual_issuance_required`) : jamais un paiement muet. Trois chemins y mènent, et
le balayage du runner reste le filet — c'est lui qui rend vraie la phrase « jamais
un paiement sans trace de facture », les appels en ligne ne font que raccourcir le
délai :

| chemin | quand |
| --- | --- |
| `billing.confirm` | retour navigateur, webhook d'un premier paiement, rattrapage |
| `billing.process_webhook` | échéance encaissée, remboursement constaté |
| `billing_runner` (balayage) | **le filet** — tout ce que les deux premiers ont raté |

**`held` est ce qui distingue un ARRÊT d'une PANNE.** Le seul levier qui existait
avant lui était de retirer la clé plateforme : chaque tick horaire aurait alors
journalisé une erreur par paiement, sur une file qui ne se vide jamais — une
coupure qui hurle. Une ligne tenue, elle, quitte la file de reprise
(`pending_billing_invoices` ne retient que `pending`) : elle reste VISIBLE dans le
tableau des factures, et silencieuse.

Le montant remboursé d'un avoir est écrit à la CRÉATION de sa ligne (`amount_ttc`
négatif) : le webhook Mollie qui l'a vu ne repassera pas, et rien d'autre ne le
porte. Sans lui, la pièce à poser à la main aurait perdu son montant.

## Ce qu'on ne trace pas

Un paiement **sans décomposition fiscale** (`amount_ht IS NULL`) : les deux
encaissements du 25/08/2026, antérieurs à la règle de TVA. Ils se régularisent à la
main (`docs/billing.md`), et une ligne d'attente que rien ne résoudra jamais serait
une fausse alerte permanente.

## Rien ne lève ici

`billing.confirm` et `billing.process_webhook` appellent ce module sur un paiement
RÉUSSI : une exception qui remonterait ferait rendre une erreur au payeur — la
faute exacte de #493, qui a fait repayer un client.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..db import billing_invoices as db_invoices

logger = logging.getLogger(__name__)

# La cause portée par une ligne tenue. Ce n'est PAS un échec : aucun appel n'a été
# tenté, et `attempts` reste donc à zéro — l'incrémenter ferait lire un fournisseur
# en panne là où il y a une décision.
EN_ATTENTE_DE_LA_MAIN = "manual_issuance_required"
_DETAIL = ("la plateforme n'émet plus aucune pièce chez Pennylane automatiquement "
           "(décision du 2026-09-09) : l'encaissement est tracé, le document se pose "
           "à la main. Aucun appel fournisseur n'a été tenté.")


def _tenir(row: dict) -> dict:
    """Passe la ligne en `held` avec sa cause, et rend la ligne relue.

    `held` est ce qui fait qu'ARRÊTER ne devient pas BRUITER : une ligne tenue
    quitte la file de reprise (`pending_billing_invoices` filtre `pending`), donc
    le balayage horaire ne la revisite pas et rien ne se plaint toutes les heures
    d'un document qui n'est plus dû tout seul.

    L'écriture est idempotente en base : une ligne déjà tenue n'est pas retouchée,
    une ligne déjà émise n'est jamais dé-facturée."""
    db_invoices.hold_billing_invoice(row["id"], EN_ATTENTE_DE_LA_MAIN, _DETAIL)
    return db_invoices.get_billing_invoice(row["id"])


# ── facture ──────────────────────────────────────────────────────────────────

def ensure_invoice_for_payment(payment_row: dict) -> Optional[dict]:
    """Le point d'entrée de tous les chemins : la trace d'un encaissement. NE LÈVE PAS.

    Idempotent par construction — la trace est unique en base `(paiement, kind)` —
    et **aucun appel fournisseur n'en part** : la ligne naît `pending`, porte sa
    cause, et attend la pièce posée à la main.

    Une ligne déjà `issued` (les documents d'avant le 2026-09-09) ressort telle
    quelle, sans être retouchée."""
    if str(payment_row.get("status")) != "paid":
        return None
    org_id, row_id = payment_row["org_id"], payment_row["id"]
    if payment_row.get("amount_ht") is None:
        # Règle (c) de #488 : les deux encaissements du 25/08 n'ont pas de TVA
        # calculée et ne s'inventent pas. Aucune trace créée — une ligne en attente
        # que rien ne pourra jamais résoudre serait une fausse alerte permanente.
        logger.info("facturation: paiement %s (org %s) sans décomposition fiscale — "
                    "hors règle, régularisation manuelle (docs/billing.md)",
                    row_id, org_id)
        return None

    ref = payment_row.get("payment_id") or payment_row.get("payment_intent_id")
    row = db_invoices.ensure_billing_invoice(org_id, row_id, kind="invoice",
                                             payment_ref=ref)
    if row["status"] == "issued":
        return row
    logger.info("facturation: org %s, paiement %s — encaissement tracé, facture À "
                "POSER À LA MAIN (aucune émission automatique depuis le 2026-09-09)",
                org_id, row_id)
    return _tenir(row)


# ── avoir ────────────────────────────────────────────────────────────────────

def ensure_credit_note_for_refund(payment_row: dict,
                                  refunded_cents: int) -> Optional[dict]:
    """La trace d'un remboursement constaté chez le PSP. NE LÈVE PAS.

    ⚠️ **Une seule ligne d'avoir par paiement** (clé `(paiement, 'credit_note')`).
    Un second remboursement PARTIEL sur le même paiement ne produira donc pas une
    seconde ligne : le cas est journalisé en `error` et demande un avoir manuel.
    Une clé par remboursement supposerait de suivre les objets `refund` de Mollie,
    que le webhook ne porte pas — il ne donne que l'id du paiement et son
    `amountRefunded` cumulé."""
    if refunded_cents <= 0:
        return None
    org_id, row_id = payment_row["org_id"], payment_row["id"]
    if payment_row.get("amount_ht") is None:
        # Même règle (c) que pour la facture : ces paiements-là n'étant pas
        # facturés, leur remboursement n'a aucune facture à annuler.
        logger.info("facturation: remboursement du paiement %s (org %s) sans "
                    "décomposition fiscale — avoir manuel (docs/billing.md)",
                    row_id, org_id)
        return None

    ref = payment_row.get("payment_id") or payment_row.get("payment_intent_id")
    # `amount_ttc` est écrit ICI, à la création : c'est la seule occasion. Le
    # webhook qui a vu le remboursement ne repassera pas, et la pièce posée à la
    # main a besoin de son montant.
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
        return row
    logger.info("facturation: org %s, paiement %s — remboursement de %s centimes "
                "tracé, avoir À POSER À LA MAIN", org_id, row_id, refunded_cents)
    return _tenir(row)


# ── les deux points d'appel du cycle de paiement ─────────────────────────────
#
# `billing.confirm` et `billing.process_webhook` passent par ICI et non par les
# fonctions ci-dessus, pour deux raisons qui tiennent au chemin de paiement :
# ils n'ont qu'un ID de ligne (la ligne en mémoire porte le statut d'AVANT le
# passage à `paid`), et surtout **aucune exception ne doit remonter chez eux**.

def tracer_encaissement(payment_row_id: int) -> None:
    """Trace un paiement qui vient de passer `paid`. **Ne lève jamais.**

    ⚠️ Ce nom dit ce que la fonction fait depuis le 2026-09-09 : elle TRACE, elle
    ne facture plus. Elle s'appelait `facturer_encaissement` tant qu'une facture
    partait d'ici — laisser le nom aurait fait lire à `billing.confirm` qu'un
    document est émis.

    Absorber reste la règle : une exception qui remonterait ferait rendre une
    erreur au payeur **sur un paiement réussi** (#493)."""
    try:
        row = db_invoices.billing_payment_row(payment_row_id)
        if row:
            ensure_invoice_for_payment(row)
    except Exception as e:  # noqa: BLE001 — jamais une erreur servie sur un paiement réussi
        logger.error("facturation: paiement %s encaissé mais non tracé — %s",
                     payment_row_id, e, exc_info=True)


def tracer_remboursement(payment_row_id: int, refunded_cents: int) -> None:
    """Trace un remboursement constaté chez le PSP. **Ne lève jamais** — même
    raison : le webhook doit répondre 200, sinon Mollie le rejoue en boucle sur un
    état qu'un retry ne réparera pas."""
    try:
        row = db_invoices.billing_payment_row(payment_row_id)
        if row:
            ensure_credit_note_for_refund(row, refunded_cents)
    except Exception as e:  # noqa: BLE001 — un webhook ne rend jamais 500 là-dessus
        logger.error("facturation: remboursement du paiement %s non tracé — %s",
                     payment_row_id, e, exc_info=True)


# ── reprise ──────────────────────────────────────────────────────────────────

def sweep(limit: int = 25) -> dict:
    """Le filet du `billing_runner` : tracer ce qui ne l'a pas été. Compteurs pour
    le journal du tick.

    Il ne rejoue plus aucune émission — il n'y en a plus. Il reste la garantie que
    tout encaissement a sa ligne, y compris quand le process a été tué entre
    l'écriture de `paid` et l'appel en ligne.

    Sa seconde boucle est devenue un chemin de PASSAGE : elle ramasse les lignes
    restées `pending` d'avant la coupure — des tentatives qui ont réellement échoué
    — et les tient à leur tour. Elle converge donc vers le vide en un tick, et
    c'est ce qui fait taire le journal horaire au lieu de le remplir."""
    counts: dict[str, int] = {}
    # Les paiements tracés ci-dessous viennent de l'être : sans cette mémoire, la
    # file d'attente les reprendrait dans le MÊME tick.
    vus: set[int] = set()
    for payment_row in db_invoices.paid_payments_without_invoice(limit):
        ensure_invoice_for_payment(payment_row)
        vus.add(payment_row["id"])
        counts["invoice_new"] = counts.get("invoice_new", 0) + 1
    for row in db_invoices.pending_billing_invoices(limit):
        if row["payment_row_id"] in vus:
            continue
        payment_row = db_invoices.billing_payment_row(row["payment_row_id"])
        if not payment_row:
            continue
        if row["kind"] == "credit_note":
            # Le montant remboursé a été écrit à la création de la ligne : le
            # webhook qui l'a vu ne repassera pas, et rien d'autre ne le porte.
            rembourse = abs(int(row.get("amount_ttc") or 0))
            if not rembourse:
                logger.error("facturation: avoir %s en attente sans montant "
                             "remboursé — avoir manuel", row["id"])
                continue
            ensure_credit_note_for_refund(payment_row, rembourse)
        else:
            ensure_invoice_for_payment(payment_row)
        counts["invoice_waiting"] = counts.get("invoice_waiting", 0) + 1
    return counts
