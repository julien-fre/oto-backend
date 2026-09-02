"""Runner d'échéances d'abonnement (ADR 0043) — la « récurrence » maison.

Le miroir local fait foi : cette boucle de fond (lifespan, même famille que
scheduler.py) fait tout le cycle à intervalle horaire :

1. **Échéances dues** (`due_subscriptions`) : rejoue un paiement MIT
   (`sequenceType=recurring`) sur `customerId`+`mandateId`. `Idempotency-Key`
   DÉTERMINISTE `org<id>-<période>-a<tentative>` → un tick concurrent/rejoué
   renvoie le MÊME paiement Mollie (HTTP 200), jamais un double débit.
   Le montant prélevé est le **TTC** (#486), calculé par le MÊME seam que la
   souscription (`billing.tax_for_org`) sur l'identité de facturation de l'org à
   l'instant du prélèvement. Une identité qui ne permet plus de calculer la TVA
   rend `blocked:<code>` : rien n'est prélevé, rien n'est décalé — un montant
   approximatif serait pire qu'un mois non prélevé.

   ⚠️ **Une échéance inprélevable laisse un ÉTAT, pas seulement un log (#829).**
   Trois branches abandonnaient ici sans rien écrire (TVA incalculable, palier
   disparu du catalogue, mandat perdu) : le cycle ne bougeait pas, le droit ne se
   fermait pas, et le seul témoin était une `log.error` dans un journal qui ne
   remonte qu'à ~24 h. Passé ce délai, plus rien ne disait qu'une org consommait
   gratuitement ni depuis quand. Elles écrivent désormais `block_code`/
   `block_since` sur l'abonnement (`_block`), effacés dès qu'une échéance passe.
   La question « qui sert-on sans encaisser, et depuis quand ? » a enfin une
   réponse : `db_billing.blocked_subscriptions()`.
2. **Politique d'impayé** (dunning borné) : échec → retry à J+3 (tentatives
   trackées par le JOURNAL, pas un compteur mutable) ; 3 échecs → `past_due`
   + grace 15 j (Art 9.4 — cf. `_GRACE`).

   ⚠️ **Le PRÉAVIS avant suspension n'existe pas, et c'est un écart connu.** Le
   même article promet un préavis de CINQ JOURS avant toute suspension (#768) :
   aujourd'hui rien ne part, ni à l'échec d'un prélèvement, ni à l'entrée en
   grâce, ni à la fermeture — le client découvre la suspension en la subissant.
   Envoyer l'email serait à portée (`email_templates`, locale du destinataire) ;
   ce qui manque est le reste de l'engagement : **désigner** le destinataire
   (administrateur d'org ? identité de facturation ? le tenant qui répond du
   client final ?) et **tracer** l'envoi pour pouvoir prouver qu'on a informé.
   C'est exactement la primitive de notification absente de toute la plateforme
   (#766) ; la bâtir ici seule en ferait un doublon. Suivi en #768.
3. **Sweeps** : résiliations à période échue + graces consommées → `canceled`
   (c'est la fermeture d'entitlement ; les données ne bougent jamais).
4. **Réconciliation** (`open_billing_payments`) : re-polle les paiements non
   terminaux (checkout fermé post-paiement, prélèvement SEPA qui se dénoue en
   plusieurs jours…) ; un premier paiement jamais encaissé finit `expired`
   (Mollie expire les paiements ouverts ; garde-fou TTL 48 h en secours).
5. **Encaissements en attente de mandat** (#493) : un premier paiement `paid`
   dont l'abonnement n'est pas ouvert. Il est TERMINAL au journal (l'encaissement
   est gravé dès son constat) donc invisible de la file ci-dessus — sans cette
   reprise, un payeur qui ferme son onglet pendant la course au mandat resterait
   débité et sans droits.
6. **Factures** (#488) : tout encaissement sans document, et toute émission restée
   en attente (clé Pennylane absente, fournisseur en panne, identité incomplète).
   Placé en DERNIER pour lire l'état que les cinq étapes précédentes viennent
   d'écrire. C'est LUI la garantie « jamais un paiement sans trace de facture » —
   les appels en ligne de `confirm` et du webhook ne font que raccourcir le délai.

Sans MOLLIE_API_KEY le tick est un no-op silencieux (le serveur vit sans
billing). Un tick raté ne tue jamais la boucle.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import billing, mollie_client
from .db import billing as db_billing

log = logging.getLogger("oto_mcp.billing_runner")

_POLL_INTERVAL_S = 3600
_RETRY_DELAY = timedelta(days=3)
_MAX_ATTEMPTS = 3

# **Cette durée est un ENGAGEMENT CONTRACTUEL, pas un réglage.** L'Art 9.4 du contrat
# de service promet la suspension après QUINZE JOURS d'impayé. C'est le délai qu'on a
# écrit au client : le raccourcir, c'est le suspendre plus tôt que promis. Il a valu
# 14 jours jusqu'au 01/09/2026 — un jour pris au client, et toujours dans ce sens-là.
# L'écart, et la référence de l'article, sont dans oto-backend#768. Un test en fait un
# plancher (`tests/test_billing_b3_runner.py`) : allonger la grâce reste libre,
# descendre sous 15 jours rougit.
#
# ⚠️ Ce que ce compteur mesure vraiment : 15 jours à partir du passage en `past_due`,
# c'est-à-dire du 3ᵉ échec — donc APRÈS les relances (J0, J+3, J+6). Le client dispose
# ainsi de ces 15 jours plus une semaine environ, ce qui rend la lecture prudente quel
# que soit le point de départ que retient l'article. Ce point de départ se tranche sur
# la pièce, pas ici — et il ne peut pas se trancher tant que rien ne part vers le
# client (cf. le PRÉAVIS manquant, en tête de module).
_GRACE = timedelta(days=15)
_INITIAL_INTENT_TTL = timedelta(hours=48)
# Au-delà, un encaissement sans abonnement n'est plus une course mais un incident
# ouvert : `confirm` le refuse (`no_mandate`) et le rejouer chaque heure n'apprend
# plus rien. Même borne que le TTL du premier paiement — un seul horizon de reprise.
_MANDATE_CATCHUP_WINDOW = _INITIAL_INTENT_TTL

# statuts Mollie d'un paiement MIT qui valent encaissement (ou en cours de
# dénouement — `pending` = prélèvement SEPA soumis ; la réconciliation/webhook
# rattrape un éventuel rejet ultérieur).
_PAYMENT_OK = frozenset({"paid", "pending", "authorized"})
_PAYMENT_FAILED = frozenset({"failed", "canceled", "expired"})


def _block(org_id: int, code: str, detail: str, now: datetime) -> str:
    """Une échéance qu'on ne peut PAS tirer — et qui le DIT (#829).

    ⚠️ **C'est le correctif de fond de ce module.** Ces branches se contentaient d'une
    `log.error` et d'un `return` : rien n'était prélevé, mais rien n'avançait non plus
    — ni le cycle, ni l'impayé, ni la fermeture du droit. Le seul témoin était une
    ligne dans un journal qui ne remonte qu'à ~24 h. Passé ce délai, PLUS AUCUNE
    donnée ne disait qu'une org consommait sans payer, ni depuis quand : le service
    continuait gratuitement, indéfiniment, sans que personne — ni le client, ni
    nous — en soit averti.

    Ce que ça reste, volontairement : **on ne prélève pas, et on ne ferme pas non
    plus**. Un montant approximatif serait pire qu'un mois non prélevé, et fermer le
    droit d'un client qui n'a jamais été prévenu serait pire encore (le préavis de
    5 jours promis par l'Art 9.4 n'existe toujours pas — #768). Ce que ça n'est plus :
    muet. L'état est en base (`block_code`/`block_since`), lisible par
    `db_billing.blocked_subscriptions()` et servi au client sur son propre écran de
    facturation.
    """
    db_billing.flag_subscription_block(org_id, code, detail, now=now)
    log.error("billing_runner: org %s NON PRÉLEVABLE (%s) — service rendu sans "
              "encaissement, cycle non avancé : %s", org_id, code, detail)
    return f"blocked:{code}"


def _charge_one(sub_row: dict, now: datetime) -> str:
    """Tire l'échéance d'UN abonnement. Retourne l'issue (log/test) :
    'renewed' | 'retry' | 'past_due' | 'skipped' | 'blocked:<code>'."""
    org_id = sub_row["org_id"]
    if sub_row.get("provider") == "comp":
        # abonnement FORCÉ par un admin (non payé) — jamais de débit. Ceinture
        # + bretelles : due_subscriptions l'exclut déjà (next_billing_at NULL).
        return "skipped"
    plan = billing.PLANS.get(sub_row["plan"])
    if plan is None:
        return _block(org_id, "plan_unknown",
                      f"palier {sub_row['plan']!r} absent du catalogue courant",
                      now)
    if not sub_row.get("customer_id") or not sub_row.get("mandate_id"):
        return _block(org_id, "no_mandate",
                      f"aucun customer/mandat rejouable (method="
                      f"{sub_row.get('method')})", now)

    # MÊME calcul qu'à la souscription, MÊME seam (#486) : l'échéance est prélevée
    # TTC, au taux de l'identité de facturation AU MOMENT DU PRÉLÈVEMENT — une org
    # qui change de pays entre deux mois change de régime pour l'échéance suivante,
    # sans que rien de déjà facturé ne bouge.
    try:
        tax = billing.tax_for_org(org_id, plan["amount"])
    except ValueError as e:
        # Ni fallback ni débit approximatif : sans identité exploitable, il n'y a pas
        # de montant correct à prendre. On ne touche PAS au cycle (next_billing_at
        # reste dû) — le prélèvement repartira dès que l'identité sera réparée.
        return _block(org_id, str(e).split(":", 1)[0].strip() or "tax_blocked",
                      str(e), now)

    period_ref = str(sub_row.get("current_period_end") or "epoch")[:10]
    attempt = db_billing.count_renewal_attempts(
        org_id, sub_row.get("current_period_end") or now) + 1
    idempotency_key = f"org{org_id}-{period_ref}-a{attempt}"

    row_id = db_billing.insert_billing_payment(
        org_id, "renewal", tax["amount_ttc"], currency=plan["currency"],
        status="processing", attempt=attempt, tax=tax)
    try:
        payment = mollie_client.create_recurring_payment(
            tax["amount_ttc"], customer_id=sub_row["customer_id"],
            mandate_id=sub_row["mandate_id"], currency=plan["currency"],
            idempotency_key=idempotency_key, webhook_url=billing.webhook_url(),
            description=f"Abonnement {plan['label']} — échéance {period_ref}")
        pstatus = str(payment.get("status") or "")
        db_billing.update_billing_payment(row_id, status=pstatus or "processing",
                                          payment_id=payment.get("id"))
    except mollie_client.MollieError as e:
        db_billing.update_billing_payment(row_id, status="failed")
        log.warning("billing_runner: org %s échéance refusée (Mollie %s)",
                    org_id, e.status_code)
        pstatus = "failed"

    if pstatus in _PAYMENT_OK:
        # ancrage CALENDAIRE sur la fin de période payée (pas sur la date du
        # tick — les retries J+3 ne décalent pas le cycle) ; si l'échéance a
        # traîné plus d'une période, on avance jusqu'à dépasser maintenant.
        base = sub_row.get("current_period_end")
        nxt = billing._add_period(base if isinstance(base, datetime) else now,
                                  plan["interval"])
        while nxt <= now:
            nxt = billing._add_period(nxt, plan["interval"])
        db_billing.schedule_next_billing(org_id, nxt, nxt)
        log.info("billing_runner: org %s renouvelée (plan %s → %s)",
                 org_id, sub_row["plan"], nxt.date())
        return "renewed"

    # échec — retry borné puis past_due + grace (fermeture au sweep).
    if attempt < _MAX_ATTEMPTS:
        db_billing.retry_billing_at(org_id, now + _RETRY_DELAY)
        log.warning("billing_runner: org %s échéance refusée (tentative %d/%d) — "
                    "retry %s", org_id, attempt, _MAX_ATTEMPTS,
                    (now + _RETRY_DELAY).date())
        return "retry"
    db_billing.set_subscription_status(org_id, "past_due",
                                       grace_until=now + _GRACE)
    log.warning("billing_runner: org %s en impayé (3 échecs) — grace jusqu'au %s",
                org_id, (now + _GRACE).date())
    return "past_due"


def _reconcile_one(row: dict, now: datetime) -> None:
    """Re-polle UN paiement non terminal du journal (initial ou renewal — tous
    des objets `payment` Mollie `tr_`)."""
    ref = row.get("payment_id") or row.get("payment_intent_id")
    if not ref:
        # Ligne non terminale SANS référence PSP : elle sera re-sélectionnée à chaque
        # tick, et aucun polling ne pourra jamais la faire avancer. Un `return` nu en
        # faisait un déchet invisible dans la file de réconciliation.
        log.error("billing_runner: paiement %s (org %s) non terminal SANS référence "
                  "PSP — irréconciliable, bloqué en file", row.get("id"),
                  row.get("org_id"))
        return
    status = str(mollie_client.get_payment(ref).get("status") or "")
    if row.get("kind") == "initial" and status == "paid":
        # encaissé sur la page hébergée sans que confirm ait tourné (onglet
        # fermé) : on termine la pose du miroir nous-mêmes. On DIT lequel — la
        # leçon de #291 vaut ici comme au webhook : « le plus récent » peut être un
        # autre checkout de la même org.
        _catch_up(row["org_id"], ref)
        return
    if status and status != row["status"]:
        db_billing.update_billing_payment(row["id"], status=status)
        return
    # premier paiement resté ouvert trop longtemps → expiré (garde-fou ; Mollie
    # expire de lui-même, ce TTL couvre un statut en vol).
    if row.get("kind") == "initial" and status not in mollie_client.TERMINAL_PAYMENT_STATUSES:
        created = row.get("created_at")
        created_dt = created if isinstance(created, datetime) else None
        if created_dt and now - created_dt > _INITIAL_INTENT_TTL:
            db_billing.update_billing_payment(row["id"], status="expired")


def _catch_up(org_id: int, payment_ref: str) -> None:
    """Termine la pose du miroir pour un encaissement constaté hors `confirm`."""
    try:
        billing.confirm(org_id, payment_ref=payment_ref)
    except Exception as e:
        # ERREUR, pas warning : ici un payeur est DÉBITÉ et sans droits. Le niveau
        # décide de la visibilité au-delà du journal (Sentry ne remonte que les
        # ERROR) — un rattrapage qui échoue à chaque tick doit se voir.
        log.error("billing_runner: confirm de rattrapage org %s (paiement %s) a "
                  "échoué — encaissement sans droits ouverts : %s",
                  org_id, payment_ref, e, exc_info=True)


def tick() -> dict:
    """Un passage complet (sync, appelé en thread). Retourne les compteurs."""
    if not mollie_client.is_configured():
        return {}
    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {}

    for org_id in db_billing.sweep_period_end_cancellations():
        log.info("billing_runner: org %s résiliée (période échue)", org_id)
        counts["closed"] = counts.get("closed", 0) + 1
    for org_id in db_billing.sweep_grace_expired():
        log.warning("billing_runner: org %s fermée (grace consommée)", org_id)
        counts["closed"] = counts.get("closed", 0) + 1

    for sub_row in db_billing.due_subscriptions():
        outcome = _charge_one(sub_row, now)
        counts[outcome] = counts.get(outcome, 0) + 1

    for row in db_billing.open_billing_payments():
        try:
            _reconcile_one(row, now)
            counts["reconciled"] = counts.get("reconciled", 0) + 1
        except mollie_client.MollieError as e:
            log.warning("billing_runner: réconciliation paiement %s : %s",
                        row.get("id"), e)

    # Encaissements dont l'abonnement attend encore son mandat (#493). Ces lignes
    # sont `paid`, donc terminales, donc hors de la file ci-dessus : sans cette
    # reprise, personne ne re-interrogerait le mandat une fois l'onglet fermé.
    for row in db_billing.paid_initials_awaiting_subscription(
            since=now - _MANDATE_CATCHUP_WINDOW):
        ref = row.get("payment_intent_id") or row.get("payment_id")
        if not ref:
            continue
        _catch_up(row["org_id"], ref)
        counts["mandate_catchup"] = counts.get("mandate_catchup", 0) + 1

    # FACTURES (#488), en DERNIER : ce balayage lit l'état laissé par tout ce qui
    # précède — l'échéance qui vient d'être encaissée et le rattrapage de mandat
    # sont donc facturés dans le MÊME tick, pas une heure plus tard. C'est lui qui
    # rend vraie la phrase « jamais un paiement sans trace de facture » : les appels
    # en ligne de `confirm` et du webhook ne font que raccourcir le délai.
    try:
        from . import billing_invoices
        counts.update(billing_invoices.sweep())
    except Exception as e:  # noqa: BLE001 — la facturation ne casse pas le cycle de paiement
        log.error("billing_runner: balayage des factures échoué — %s", e, exc_info=True)
    return counts


async def run_billing_loop(interval: int = _POLL_INTERVAL_S) -> None:
    """Boucle de fond (lifespan) — un tick raté ne tue pas la boucle."""
    log.info("billing runner démarré (intervalle %ss)", interval)
    while True:
        try:
            counts = await asyncio.to_thread(tick)
            if counts:
                log.info("billing_runner tick : %s", counts)
        except asyncio.CancelledError:
            log.info("billing runner arrêté")
            raise
        except Exception as e:  # un tick raté ne tue pas la boucle
            # ERROR et pas WARNING : un tick qui échoue systématiquement arrête TOUT
            # le cycle de facturation (échéances, dunning, réconciliation, factures)
            # sans rien changer d'observable. En warning, il ne franchissait même pas
            # le journal — c'était le plus silencieux des arrêts de ce module.
            log.error("billing_runner tick échoué — aucune échéance n'a été traitée "
                      "ce passage : %s", e, exc_info=True)
        await asyncio.sleep(interval)
