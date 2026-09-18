## prerequisite — ta clé api nextmotion

connecte-toi à l'[application web Nextmotion](https://app.nextmotion.net), puis **Settings → API Keys** → génère une clé et copie-la : elle n'est plus affichée ensuite. colle-la dans tes clés de connecteur oto sous `nextmotion`.
- la clé agit **au nom de l'utilisateur qui l'a générée**, sur les cliniques dont il est employé ; elle n'expire pas, se révoque par « Reroll » ou suppression au même endroit
- l'accès API est inclus dans l'offre Scale, ou en option payante à partir de l'offre Growth (grille publique [nextmotion.net/tarifs](https://www.nextmotion.net/tarifs)) ; qui peut générer une clé selon le rôle dans la clinique n'est pas documenté
- BYO seulement : pas de clé oto partagée

## usage — agenda, catalogue, ventes, leads, statistiques et stock

commence par `nextmotion_clinic()` : chaque autre outil demande un `clinic_id`.
- « qui travaille dans la clinique ? » → `nextmotion_practitioner(op="list", clinic_id=…)`
- « l'agenda du jour » → `nextmotion_appointment(op="list", clinic_id=…, date="AAAA-MM-JJ")`
- « quels créneaux libres ? » → `nextmotion_availability(clinic_id=…, start_date=…, end_date=…)`
- « déplace ce rendez-vous » → prends UN créneau de `nextmotion_availability`, puis `nextmotion_appointment(op="reschedule", appointment_id=…, visit_type_opening_hour_id=<id du créneau>, time_slot=<time_slot du créneau>)`
- « qu'est-ce qu'on propose, à quel prix ? » → `nextmotion_catalog(kind="visit_type"|"treatment_type"|"treatment_pricing"|…, clinic_id=…)`
- « les devis / factures » → `nextmotion_quote(op="list", clinic_id=…)`, `nextmotion_invoice(op="list", clinic_id=…)`
- « les factures de janvier » → `nextmotion_invoice(op="list", clinic_id=…, invoiced_from="2026-01-01", invoiced_to="2026-01-31")`
- « quels lots expirent bientôt, qu'est-ce qui est en rupture ? » → `nextmotion_product(op="list", clinic_id=…, expiring_within_days=30)` ou `stock_state="low"|"out"`
- « quelles salles, quels appareils, quelles plages, qui est absent ? » → `nextmotion_calendar(kind="room"|"device"|"opening_hour"|"absence", clinic_id=…)` (`show_all=True` pour toute la clinique, pas seulement l'utilisateur de la clé)
- « les demandes de rendez-vous en ligne à traiter » → `nextmotion_calendar(kind="appointment_request", clinic_id=…, request_status="new")`
- « où en sont les patients du jour ? » → `nextmotion_journey(clinic_id=…, start_date="AAAA-MM-JJ", end_date="AAAA-MM-JJ")`
- « forfaits, répartitions comptables, produits du catalogue » → `nextmotion_catalog(kind="treatment_package"|"accounting_distribution"|"global_product", clinic_id=…)` ; les soins d'un forfait → `op="items"`, la répartition par praticien d'un tarif ou d'un forfait → `op="distributions"`
- « qui a payé quoi, par quel moyen ? » → `nextmotion_payment(op="list", clinic_id=…)` ou `invoice_id=…` pour une facture
- « le chiffre d'affaires par mois, par type de soin » → `nextmotion_statistics(kind="appointment_income"|"treatment_types"|"treatment_types_income", clinic_id=…, period_type="month")`
- « combien ce patient a-t-il facturé, payé ? » → `nextmotion_patient_stats(patient_id=…)` avec l'id servi par un rendez-vous, un devis ou une facture
- « le pipeline des prospects » → `nextmotion_lead(op="list", clinic_id=…)`, et les libellés de source ou de statut → `nextmotion_setting(kind="object_label", clinic_id=…, label_types=["lead_source"])`
- « abonnement Nextmotion, moyens de paiement, gabarits, webhooks » → `nextmotion_setting(kind="feature"|"payment_medium"|"communication_template"|"document_template"|"webhook", clinic_id=…)`

## note — factures par période : un parcours complet, borné

- l'API Nextmotion ne filtre pas les factures par date et ne documente pas leur ordre : l'outil lit **toutes** les pages (100 factures par appel) et garde celles dont `invoiced_time` tombe dans la période, bornes incluses
- le parcours est plafonné par `max_pages` (20 par défaut, 100 au plus) ; la réponse dit `pages_lues`, `factures_parcourues` et `complet`
- `complet: false` = résultat **partiel** : relance avec `offset=<offset_suivant>` et la même période pour lire la suite
- avec une période, `limit` est refusé et `offset` est le point de départ du parcours
- pas de filtre de période sur les devis : un devis n'a pas de date de facturation, et sa date d'émission peut être vide

## note — stock produits : pas de lien avec les factures

- `nextmotion_product` lit le stock de la clinique (un lot par ligne : numéro de lot, péremption, niveaux de stock, prix unitaire, produit et marque), en lecture seule
- l'API Nextmotion n'expose **aucun consommable ni lot par facture ou par soin** : une ligne de facture porte l'acte et ses montants, jamais les lots consommés, et rien ne relie un lot à une facture ou à un patient

## note — données de santé : ce qui n'est pas servi

- **aucun contenu médical** : dossier et liste des patients, antécédents, photos et médias, ordonnances, consentements, soins réalisés, consultations, visites et leurs notes, questionnaires de santé et suivi post-soin restent hors du connecteur, comme le chat avec les patients
- **tout ce qui sort passe par une liste blanche** écrite d'après la spec : un champ que Nextmotion ajouterait demain ne sort pas, et `fields=["*"]` rend la vue par défaut, jamais le brut
- **le patient n'est servi que par son id** dans les rendez-vous, parcours, devis, factures et paiements (aperçu `dry_run` compris) : ni nom, ni prénom, ni email, ni téléphone, et aucun outil ne résout cet id en personne — n'essaie pas de deviner qui c'est. `nextmotion_patient_stats` rend ses totaux financiers et ses dates de visite, rien de plus
- **la personne d'une demande en ligne ou d'un lead n'est pas servie** : ni nom, ni coordonnées, ni date de naissance, ni notes, ni référence externe ; aucune recherche par nom n'est proposée
- retirés aussi : date de naissance, sexe, commentaires du praticien, titres, notes et textes de rappel des évènements d'agenda, titres de devis/facture, détails de ligne, document PDF, lien vers le soin réalisé et tout texte libre, **sans option pour obtenir le brut**
- restent en texte les libellés du catalogue (type de visite, nom d'une ligne ou d'un sous-tarif, détail d'un tarif), les étiquettes (source, statut, soin souhaité et zone d'un lead) et le nom des praticiens ; si un praticien a saisi le nom d'un patient dans un libellé de ligne, il passerait

## note — ventes : lignes complètes, statistiques, réglages

- une ligne de devis ou de facture porte son prix, sa quantité, sa remise, sa marge (`markup`), sa TVA et ses sous-tarifs (`subpricing` : nature, prix, TVA, code comptable, part clinique ou praticien)
- un paiement porte le montant par moyen (carte, espèces, chèque, virement, Stripe, avoir, moyens personnalisés) et sa facture, projetée comme dans `nextmotion_invoice`
- les statistiques rendent des graphiques (`title`, `labels`, `datasets`) ; le bloc `meta` de l'API, non décrit, n'est pas servi
- gabarits de communication et de documents : métadonnées seules (type, nom, activé) ; webhooks : sans leurs en-têtes, qui portent d'ordinaire un secret

## note — ce qui touche un vrai patient

- `op="reschedule"` et `op="delete"` ont `dry_run=True` **par défaut** : l'appel rend le rendez-vous tel qu'il est et ce qui changerait, sans rien écrire. passe `dry_run=False` pour agir
- que Nextmotion prévienne le patient (SMS, email) lors d'un report ou d'une suppression n'est pas documenté
- dérivé de la spec OpenAPI publique, jamais exercé avec une vraie clé
