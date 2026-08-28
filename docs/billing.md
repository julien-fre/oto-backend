# Facturation par org (ADR 0043) — le modèle, la TVA, le consentement, et le double débit du 25/08

## Ce que Mollie voit, et ce qu'il ne voit pas

**Il n'y a pas d'abonnement Mollie.** Chercher `/v2/customers/<id>/subscriptions`
pour comprendre un abonnement oto ne rend rien : ADR 0043 pose le miroir local
`org_subscriptions` comme source de vérité, PSP-agnostique. Mollie ne connaît que
des **paiements** : un `sequenceType=first` au checkout, puis des `recurring` (MIT)
rejoués par `billing_runner.tick()` sur `customerId` + `mandateId`. Deux tables le
reflètent — `org_subscriptions` (PK `org_id`, donc **un** abonnement par org,
structurellement) et `billing_payments` (journal, `kind` ∈ `initial` | `renewal`).
Une troisième, `billing_identities`, ne reflète rien de Mollie : elle dit **qui
paie et depuis quel pays**, et c'est elle qui décide du montant (voir la TVA,
plus bas).

Trois objets Mollie, trois durées de vie :

| objet | naît | vit |
| --- | --- | --- |
| **customer** (`cst_…`) | à la 1ʳᵉ souscription de l'org | **pour toujours** — un seul par org |
| **paiement** (`tr_…`) | à chaque checkout / échéance | jusqu'à son statut terminal |
| **mandat** (`mdt_…`) | **quelques minutes APRÈS** l'encaissement du 1ᵉʳ paiement | jusqu'à révocation |

La troisième ligne est le piège central, et il a coûté 19 € au premier client.

## On ne vend pas sans consentement (#487)

`legal_docs.py` déclarait depuis toujours un contexte **`purchase`** (CGU + CGV +
DPA) que **personne n'appelait** : `billing.subscribe` ne consultait pas
`legal_acceptances`, et le tunnel n'affichait aucune mention légale. Publier des
CGV ne les rend opposables à personne — il faut une **acceptation horodatée**.

`subscribe` prend donc l'appelant (`sub`, **obligatoire** : accepter est un acte de
personne, pas d'organisation) et refuse **409 `legal_required`** tant que les trois
documents ne sont pas acceptés **à leur version courante**. Un bump de version dans
`legal_docs.CURRENT_DOCS` rouvre le gate ; une acceptation périmée ne vaut pas.

### Deux préalables, un seul aller-retour

**L'ordre est celui du tunnel : identité de facturation, puis consentement.** Le
payeur accepte des CGV *pour un montant*, et le montant n'existe qu'une fois le
pays connu (c'est lui qui décide de la TVA, §#486). Faire consentir d'abord et
chiffrer ensuite ferait accepter un prix qui n'a pas encore été annoncé — le
consentement est le **dernier geste avant la page de paiement**.

Mais ordonner n'est pas refuser un à la fois. Les deux manques sont évalués
ensemble et rendus ensemble :

```json
{ "error": "billing_identity_required",
  "detail": "billing_identity_required: … legal_required: …",
  "details": { "blockers": [
    { "code": "billing_identity_required", "message": "… champs à renseigner : …" },
    { "code": "legal_required", "context": "purchase",
      "message": "… CGU 3.0 (https://oto.cx/terms), CGV 2.0 (…), DPA 2.0 (…) …",
      "documents": [ { "slug": "terms", "label": "CGU", "version": "3.0",
                       "url": "https://oto.cx/terms", "accepted_version": null } ] } ] } }
```

- Le **code de tête** est celui du **premier** manque — les codes historiques
  (`billing_identity_required`, `vat_consumer_unsupported`) sont donc inchangés
  quand ils sont seuls. ⚠️ **Avec deux manques, il n'en nomme qu'un : c'est
  `details.blockers` qu'un client doit lire.**
- `accepted_version` distingue « jamais accepté » (`null`) de « accepté à une
  version périmée » — sans lui, le payeur est renvoyé chercher une case cochée.
- **Rien ne part chez le PSP** tant qu'un préalable manque : un refus après
  création laisserait un customer et une page payable derrière lui.

Le tunnel répare avec `POST /api/me/billing/identity` puis
`POST /api/me/legal/accept {"context": "purchase"}`, et relance `subscribe`.

### Ce qui ne demande PAS de consentement

Un **abonnement offert** (`admin_set_plan`, `comp`) : rien n'y est vendu ni débité.
Une **échéance** : le consentement a été donné à la souscription, `billing_runner`
ne le rejoue pas — `_charge_one` ne prend d'ailleurs pas de `sub`, et un test le
fige.

### La trace est un HISTORIQUE, et elle situe l'acte

`legal_acceptances` portait une PK `(sub, doc_slug)` et l'écriture était un upsert :
accepter les CGV 2.0 **effaçait** la trace de l'acceptation des CGV 1.0. Une
acceptation prouvée par une ligne mutable n'est pas une preuve — c'est le dernier
état d'une preuve.

Depuis le 28/08/2026, **une ligne par acceptation** (id surrogate en PK), avec
`context`, `org_id` (l'org de session = le **payeur**, ADR 0043), `ip` et
`user_agent`. La lecture du gate prend la ligne **la plus récente** de chaque
document (`DISTINCT ON`, départagée par `id` : `accepted_at` vaut `NOW()`, donc
l'horloge de la *transaction* — les trois documents d'un achat portent la même).

L'IP et le user-agent viennent de la requête via `client_trace`, posé par
l'adaptateur REST autour du handler (un handler ne voit pas la requête, ADR 0004) ;
l'IP réelle se lit `CF-Connecting-IP` > **premier** hop de `X-Forwarded-For` >
socket. Hors requête REST, les deux valent `NULL` — une trace absente reste absente.

⚠️ **Les lignes antérieures au 28/08/2026 ont leurs quatre satellites à `NULL`** :
`context IS NULL` veut dire « contexte non tracé », surtout pas « access ». Leur
inventer un contexte ferait mentir la trace là où elle sert de preuve.

⚠️ **Le seul DDL destructif du dépôt sur une table vivante.** Un historique et une
unicité `(sub, doc_slug)` ne peuvent pas coexister : la migration retire la PK, ce
qui casse l'arbitre `ON CONFLICT (sub, doc_slug)` du code **prod** tant qu'il n'a
pas été promu (`POST /api/me/legal/accept` y répondrait 500). Il n'y a pas de
découpe en lots possible (`docs/live-migrations.md`) — la fenêtre se ferme par le
**séquencement** : ce lot part en preprod et en prod d'un seul mouvement.

## Le montant débité est un TTC, et le pays le décide (#486)

**Le prix d'un palier est un HORS TAXES.** Jusqu'au 28/08/2026 c'était ce HT qui
partait au PSP : un client « à 19 € » était débité de 19,00 € alors que la TVA
française de 20 % est due par Otomata quoi qu'il arrive. Sur l'encaissement réel,
aucune facture correcte n'était émettable.

Le taux dépend du **pays du payeur** — donc il faut le connaître **avant** de
débiter. D'où l'ordre imposé : identité de facturation d'abord, paiement ensuite.

### La règle (cadre du 28/08/2026)

| client | régime (`vat_scheme`) | taux | mention portée sur la facture |
| --- | --- | --- | --- |
| **France** | `fr_ttc` | 20 % | — |
| **UE hors FR, n° de TVA** | `reverse_charge` | 0 % | autoliquidation, art. 196 dir. 2006/112/CE |
| **UE hors FR, SANS numéro** | *refus* `vat_consumer_unsupported` | — | guichet OSS non en place |
| **hors UE** | `export` | 0 % | hors champ, art. 259-1 du CGI |

Le refus du particulier européen hors France est un **choix**, pas un trou : le
guichet OSS impose de collecter la TVA du pays du client, de la déclarer et de la
reverser. Tant qu'il n'existe pas, encaisser serait une TVA due et non collectée —
on refuse de souscrire plutôt que de facturer faux.

⚠️ **La forme d'un numéro de TVA n'est pas sa validité.** `billing_vat` contrôle le
préfixe du pays et la grammaire nationale ; il ne dit pas que le numéro existe.
**La vérification VIES est un TODO nommé sur #486** — c'est un appel réseau tiers,
hors du lot. D'ici là, un numéro bien formé mais inexistant fait passer un client en
autoliquidation à tort, et la régularisation est manuelle.

⚠️ **La Grèce est `GR` en ISO-3166-1 et `EL` en TVA intracommunautaire.** C'est la
seule divergence des 27, et un contrôle naïf « le numéro commence par le code pays »
refuserait tout numéro grec valide.

⚠️ **Un code pays inconnu est REFUSÉ, jamais traité en export.** « FR » mal tapé
sortirait de l'Union et passerait un client français à 0 % — un manque à gagner
fiscal parfaitement silencieux. D'où la liste ISO-3166-1 en dur.

### Un seul calcul, deux chemins de débit

`billing.tax_for_org` est le **seam unique** : la souscription
(`billing.subscribe`) et l'échéance (`billing_runner._charge_one`) l'appellent tous
les deux. Deux calculs auraient divergé au premier changement de règle, et la
divergence se serait vue sur une facture, pas dans un test — un client ne peut pas
payer 22,80 € le premier mois et 19,00 € les suivants.

Une identité devenue incalculable au moment d'une échéance ne fait **pas** retomber
le runner sur le HT : il rend `tax_blocked`, ne prélève rien, ne décale pas le cycle
(l'échéance reste due) et le journalise en `error`. Un montant approximatif serait
pire qu'un mois non prélevé.

### Ce qui est journalisé, et ce qui ne l'est PAS

`billing_payments.amount` porte ce qui a **réellement** été passé au PSP, donc le
TTC ; `amount_ht`, `vat_rate_bps`, `vat_amount`, `country_code` et `vat_scheme`
figent la décomposition **à l'instant du débit** — elle ne suit pas un déménagement
ultérieur de l'org.

⚠️ **Les deux encaissements du 25/08 ne sont pas réécrits.** Ils ont réellement été
débités de 19,00 € sans TVA, et `amount_ht IS NULL` est ce qui les distingue d'une
ligne calculée. **Un `null` ici veut dire « ligne d'avant la règle », jamais
« zéro »** — un zéro affirmerait une exonération qui n'a pas eu lieu.

Le taux est en **points de base** (`vat_rate_bps`, 2000 = 20 %) et jamais en
flottant : il sert à calculer des centimes, et une colonne `NUMERIC` ressortirait en
`Decimal`, que le sérialiseur JSON des réponses refuse — 500 à la lecture.

### Ce que voient les surfaces

`subscribe` rend la décomposition **avant** d'envoyer sur la page hébergée (sinon le
payeur découvre le TTC chez Mollie) ; `confirm` la relit du journal ; `status`
annonce le TTC de la **prochaine** échéance, dérivé de l'identité courante, et pose
`vat_blocked` quand il ne peut pas le calculer — un abonnement `active` avec un
`vat_blocked` posé signale une échéance que le runner ne pourra pas prélever.
`me.billing.identity` (GET/PUT `/api/me/billing/identity`) lit et pose la fiche, et
rend toujours `missing` : la même liste que celle nommée par le refus
`billing_identity_required`.

⚠️ **Sur un abonnement OFFERT (`comp`), les champs de TVA de `status` valent tous
`null`, `vat_blocked` compris** : rien n'y sera jamais prélevé, donc il n'y a ni
TTC à annoncer ni alerte à lever — et poser `vat_blocked` sur une org offerte
sans identité serait une fausse alerte sur l'écran dont c'est justement le rôle
de signaler les échéances en danger.

⚠️ **Enregistrer une identité et pouvoir souscrire sont deux choses.** L'identité
d'une société allemande est parfaitement valide et s'enregistre (`missing` vide) ;
c'est le DÉBIT qui est refusé sans numéro de TVA. `vat_blocked` prévient donc
l'écran avant le tunnel, plutôt que de faire remplir un formulaire pour refuser
au paiement.

⚠️ **Point de droit resté ouvert** (conseil, pas code) : le « hors UE = 0 % » du
cadre ne distingue pas le professionnel du particulier, alors que les services
électroniques rendus à un particulier peuvent relever du pays de consommation. La
règle appliquée est celle du cadre.

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

`billing.py` (le cycle), `billing_vat.py` (la règle de TVA, **pure** : ni base,
ni réseau, ni horloge), `db/billing.py` (les trois tables + les files),
`mollie_client.py` (la surface PSP), `capabilities/billing.py` (les six capacités
REST-only — payer est un acte humain, pas d'URL de paiement dans un contexte LLM),
`capabilities/billing_identity.py` (l'identité de facturation, même régime),
`billing_consent.py` + `legal_docs.py` (le consentement d'achat et la source de
vérité des documents), `capabilities/me_legal.py` (l'acceptation, REST-only),
`billing_runner.py` (échéances, dunning, sweeps, reprises). La surface entière est
gatée par `OTO_BILLING_ENABLED=1` (dark launch ADR 0043) et la boucle de fond par
`OTO_BILLING_RUNNER_ENABLED` (défaut : allumée dès que le billing l'est). La clé
`MOLLIE_API_KEY` vient de l'**env du process** (Scaleway Secret Manager au boot),
jamais de SOPS.
