## prerequisite — clé api stripe

crée une **clé restreinte** dans Stripe (Dashboard → Developers → API keys → « Create restricted key » — voir la [doc des clés](https://docs.stripe.com/keys)), puis colle-la dans oto.
- des permissions en **lecture** suffisent pour interroger clients/abonnements/factures/paiements/solde — **rien de tout ça ne déplace d'argent**. écrire dans le catalogue (produits, prix, coupons, codes promo, lignes de facture, liens de paiement) demande en plus les scopes d'**écriture** correspondants sur la clé restreinte (ex. Products/Prices/Coupons/Promotion codes write) — sans eux, Stripe refuse ces écritures avec un 403
- une clé restreinte (`rk_…`) est préférable à une clé secrète (`sk_…`) : elle limite ce que la clé peut atteindre même si elle fuite, et Stripe la recommande explicitement pour les agents IA
- ⚠️ une clé **publiable** (`pk_…`) est refusée : c'est le jeton du navigateur, il ne peut lire ni client ni facture
- le **mode** se lit dans la clé : `rk_test_…` / `sk_test_…` = mode test, `rk_live_…` / `sk_live_…` = mode réel. Les deux mondes sont séparés — un client de test n'existe pas en réel, et inversement
- byo-only : pas de clé oto partagée. ce sont vos livres de comptes

deux champs facultatifs à côté de la clé :
- **version d'API** — laisse vide sauf raison précise : vide = la version par défaut de votre compte, celle que montre votre dashboard
- **compte connecté** (`acct_…`, Stripe Connect) — si vous le renseignez, **toutes** les lectures portent sur ce compte et non sur le vôtre

## usage — clients, abonnements, factures, encaissements, solde

- « combien a-t-on facturé le mois dernier ? » → `stripe_invoice(op="totals", created_after=…, created_before=…)` (somme **par devise**, avec un drapeau `complete`)
- « combien a-t-on réellement encaissé ? » → `stripe_balance(op="transactions", …)` — chaque ligne porte ses frais et son net, ce que les factures ne savent pas
- « combien avons-nous en caisse ? » → `stripe_balance(op="get")`
- « quand arrive le prochain virement ? » → `stripe_balance(op="payouts")`
- « retrouve ce client » → `stripe_search(resource="customers", query='email~"acme.com"')`
- « ses factures » → `stripe_invoice(op="list", customer_id="cus_…")`
- « qui est en train de résilier ? » → `stripe_subscription(op="list", status="all")` puis filtrer sur `cancel_at_period_end`, `past_due`, `unpaid` — Stripe n'a pas d'état « en train de partir »
- « pourquoi ce paiement a échoué ? » → `stripe_payment(op="get_intent", payment_intent_id="pi_…")` et lire `last_payment_error` ; ou `op="get_charge"` et lire `outcome.seller_message`
- « sa carte est-elle expirée ? » → `stripe_customer(op="payment_methods", customer_id="cus_…")`
- « a-t-on des litiges en cours ? » → `stripe_payment(op="list_disputes")` — attention à `evidence_details.due_by`, l'argent est déjà retiré du solde pendant ce temps
- « fais-moi un lien de paiement pour cette offre » → `stripe_catalog(op="list_prices")` puis `stripe_checkout(op="create_link", price_id="price_…")`
- « ajoute 200 € sur sa prochaine facture » → `stripe_invoice(op="add_item", customer_id="cus_…", amount=20000, currency="eur")`
- « crée un code -20% pour le lancement » → `stripe_catalog(op="create_coupon", percent_off=20, duration="once", name="LAUNCH20")` (rend un `coupon_id`) puis `stripe_catalog(op="create_promotion_code", coupon_id="cp_…", code="LAUNCH20")` — le coupon est la RÈGLE de remise, la promotion code est le TEXTE que le client tape
- « désactive ce code promo » → `stripe_catalog(op="update_promotion_code", promotion_code_id="promo_…", active=false)` — les redemptions déjà faites ne sont pas touchées, seuls les usages futurs sont bloqués
- « quels codes promo sont actifs ? » → `stripe_catalog(op="list_promotion_codes", active=true)`

## note — ce que ce connecteur ne fera jamais

rembourser, résilier un abonnement, finaliser/envoyer/encaisser une facture, virer de l'argent, clore un litige, supprimer un client : **aucune de ces opérations n'est atteignable**, et pas seulement par choix de configuration — les méthodes correspondantes n'existent pas dans la librairie sous-jacente. faites-les depuis votre dashboard Stripe, où elles sont tracées et confirmées.

ce qui reste possible en écriture est délibérément sans conséquence financière directe : créer/modifier un client, poser une ligne sur une prochaine facture, créer une facture **au brouillon**, gérer le catalogue produits/prix/coupons/codes promo, créer un lien de paiement (page hébergée par Stripe — aucun numéro de carte ne passe par oto, et personne n'est débité tant qu'un humain n'a pas payé). un coupon/code promo ne fait rien tout seul : il ne s'applique qu'au moment où un client paie via un lien/checkout qui l'accepte, ou qu'on l'attache soi-même à un abonnement depuis le dashboard.

## note — pièges vérifiés en live le 2026-08-22 (facture/lien/prix) et le 2026-08-23 (coupons/codes promo)

- **une facture brouillon n'attrape pas les lignes en attente sans qu'on le demande.** poser une ligne puis créer la facture rendait `total=0` et zéro ligne, le montant restant en suspens — un « facture créée » parfaitement faux. `stripe_invoice(op="create_draft")` passe donc `pending_items="include"` par défaut ; mettez `"exclude"` si vous voulez vraiment un brouillon vide
- **un lien de paiement peut exiger un code de taxe sur le produit** (comptes éligibles aux paiements gérés) : `400 « the product tax code is missing »`. correctif → `stripe_catalog(op="update_product", product_id=…, tax_code="txcd_10000000")` (services génériques), puis recréer le lien
- **le montant d'un prix Stripe est immuable.** « changer le prix » = créer un nouveau prix et désactiver l'ancien (`op="update_price", active=false`) ; `update_price` refuse explicitement un `unit_amount`
- **créer un code promo a changé de forme sur les comptes récents** (vérifié sur un compte réel en version d'API `2026-07-29.dahlia`, la version par défaut d'un compte neuf) : Stripe a retiré le champ à plat `coupon` au profit d'un objet `promotion` imbriqué. C'est géré en interne (`coupon_id` reste le paramètre côté `stripe_catalog`), rien à changer côté usage — mais si vous lisez la réponse brute d'un code promo, le coupon appliqué est sous `promotion.coupon`, plus sous `coupon`
- **`expires_at` d'un code promo ne peut pas dépasser le `redeem_by` de son coupon** — Stripe refuse purement et simplement, en nommant les deux horodatages, si vous posez une expiration de code plus lointaine que la date limite du coupon lui-même

## note — bornes de lecture

- les listes rendent **100 objets au maximum** par page, et Stripe retombe silencieusement à 10 si on ne demande rien — le connecteur pose toujours 100. il n'existe pas de total sur les listes de premier niveau : pour un chiffre, `op="totals"`, pas une addition à la main
- `op="totals"` balaie jusqu'à 2 000 factures et **le dit** (`complete: false`) au-delà, au lieu de rendre une somme partielle qui passerait pour le chiffre d'affaires
- **les montants sont dans la plus petite unité de la devise** (centimes) et ne sont **jamais additionnés entre devises**
- la recherche (`stripe_search`) ne couvre que sept ressources et est en cohérence à terme : un objet créé à l'instant peut n'apparaître qu'au bout d'une minute
- `stripe_event` ne remonte que **30 jours** (rétention Stripe) — pour un trimestre, passer par les factures et les paiements
