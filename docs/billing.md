# Facturation par org (ADR 0043) — le modèle, et le double débit du 25/08

## Ce que Mollie voit, et ce qu'il ne voit pas

**Il n'y a pas d'abonnement Mollie.** Chercher `/v2/customers/<id>/subscriptions`
pour comprendre un abonnement oto ne rend rien : ADR 0043 pose le miroir local
`org_subscriptions` comme source de vérité, PSP-agnostique. Mollie ne connaît que
des **paiements** : un `sequenceType=first` au checkout, puis des `recurring` (MIT)
rejoués par `billing_runner.tick()` sur `customerId` + `mandateId`. Deux tables et
c'est tout — `org_subscriptions` (PK `org_id`, donc **un** abonnement par org,
structurellement) et `billing_payments` (journal, `kind` ∈ `initial` | `renewal`).

Trois objets Mollie, trois durées de vie :

| objet | naît | vit |
| --- | --- | --- |
| **customer** (`cst_…`) | à la 1ʳᵉ souscription de l'org | **pour toujours** — un seul par org |
| **paiement** (`tr_…`) | à chaque checkout / échéance | jusqu'à son statut terminal |
| **mandat** (`mdt_…`) | **quelques minutes APRÈS** l'encaissement du 1ᵉʳ paiement | jusqu'à révocation |

La troisième ligne est le piège central, et il a coûté 19 € au premier client.

## Le mandat est une COURSE, pas un état

Le mandat réutilisable ne naît pas avec l'encaissement : chez Mollie il apparaît
une à cinq minutes plus tard. `confirm` le constatait absent 1,4 s après le paiement
et rendait un **409 définitif** (`no_mandate`) — un échec annoncé sur un paiement
réussi.

Depuis #493, la fenêtre `billing.PENDING_WINDOW` (30 min, mesurée depuis le `paidAt`
du PSP, pas depuis l'ouverture du checkout) sépare les deux lectures :

- **dans la fenêtre** → `{"status": "pending_mandate", "payment_status": "paid",
  "retry_after": …}` en **200**. L'argent est pris, l'accès s'ouvrira seul ; le
  client re-sonde et ne repropose surtout pas de payer.
- **au-delà** → `no_mandate` (409), le code historique, dont c'est le seul sens
  vrai : encaissé, récurrence impossible, reprise manuelle. `logger.error` posé.

⚠️ **Un paiement RÉUSSI ne produit jamais de code d'erreur sur `confirm`.** Les
branches d'avancement sont toutes des 200 discriminées par `status` ; `confirm` ne
refuse que lorsque l'APPEL est fautif (`unknown_payment`, `no_pending_subscription`).

## Trois invariants que le code tient maintenant

1. **L'encaissement se grave avant tout le reste.** `status='paid'` est écrit dès
   que le PSP le dit — avant le mandat, avant le plan, avant le miroir. Le journal
   doit dire ce que le PSP a fait, pas ce que nous avons su en faire. Il restait
   `open` sur un paiement réellement débité, ce qui a rendu l'enquête du 25/08
   trompeuse : **ne pas lire le statut du journal comme l'état réel chez le PSP**
   pour les lignes antérieures au correctif.
2. **Une seule souscription en vol à la fois.** `subscribe` refuse (`payment_pending`,
   409) tant qu'un `initial` de moins de 30 min n'a pas *définitivement* échoué —
   `open` comme `paid`. Corollaire assumé : résilier puis re-souscrire dans la
   demi-heure est refusé le temps que la fenêtre s'écoule, le refus nommant le
   paiement qui occupe la place.
3. **Un seul customer Mollie par org.** Il se lit sur le miroir quand il existe,
   **sinon sur le journal** (`billing_payments.customer_id`) : le miroir n'est posé
   qu'à `confirm`, donc au deuxième clic il n'y a encore rien à relire. C'est là
   qu'un second customer naissait, avec son propre mandat — celui que le rejeu MIT
   ne tirerait jamais.

## Qui confirme, et comment il sait QUEL paiement

Quatre appelants, un seul verbe :

| appelant | connaît le `payment_ref` ? |
| --- | --- |
| **webhook** Mollie | oui, c'est celui qu'il vient de recevoir |
| **retour navigateur** | oui depuis #493 — `?payment_ref=tr_…` est posé sur l'URL de retour |
| **polling** du dashboard | non → le plus récent non conclu (correct pour lui) |
| **`billing_runner`** | oui, il l'a lu dans le journal |

Mollie n'ajoute rien à `redirectUrl`, et cette URL se fixe à la **création** du
paiement — où l'id n'existe pas encore. D'où la ré-écriture juste après
(`mollie_client.update_payment`, paiement encore `open`). Un refus de Mollie n'est
pas fatal : on retombe sur « le plus récent », avec un `logger.warning`.

## Les deux files de reprise du runner

Un encaissement journalisé `paid` est **terminal** : il quitte
`open_billing_payments`. Le `billing_runner` a donc **deux** files, et pas une :

- `open_billing_payments()` — les paiements en vol (checkout fermé post-paiement,
  prélèvement SEPA qui met des jours, TTL 48 h du premier paiement) ;
- `paid_initials_awaiting_subscription()` — les encaissements dont l'abonnement
  n'est **pas** ouvert. Sans elle, un payeur qui ferme son onglet pendant la course
  au mandat resterait débité et sans droits, personne ne re-interrogeant le mandat.

## L'incident du 2026-08-25 (org 219, 38 € pour un abonnement à 19 €)

Premier et seul encaissement réel de la plateforme à cette date. Chronologie
vérifiée en base, rejouée par `tests/test_billing_double_debit_493.py` :

| heure (UTC) | fait |
| --- | --- |
| 10:29:44 | l'org ouvre un checkout |
| 10:31:0x | elle paie ; Mollie encaisse |
| 10:31:05 | retour navigateur **1,4 s** plus tard : `valid_mandate()` vide → 409, et `status='paid'` jamais écrit |
| 10:31:44 | le payeur, qui a vu un échec, reclique → **second checkout ET second customer** |
| 10:36 | le mandat apparaît ; le 2ᵉ paiement est encaissé lui aussi |

L'enchaînement n'est pas exotique — payer, voir un échec, recliquer : c'est le
chemin nominal. **Restent hors code, décision du responsable** : le remboursement
du 2ᵉ paiement et la révocation du mandat orphelin né du second customer.

## Où c'est écrit

`billing.py` (le cycle), `db/billing.py` (les deux tables + les files),
`mollie_client.py` (la surface PSP), `capabilities/billing.py` (les six capacités
REST-only — payer est un acte humain, pas d'URL de paiement dans un contexte LLM),
`billing_runner.py` (échéances, dunning, sweeps, reprises). La surface entière est
gatée par `OTO_BILLING_ENABLED=1` (dark launch ADR 0043) et la boucle de fond par
`OTO_BILLING_RUNNER_ENABLED` (défaut : allumée dès que le billing l'est). La clé
`MOLLIE_API_KEY` vient de l'**env du process** (Scaleway Secret Manager au boot),
jamais de SOPS.
