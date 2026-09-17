## prerequisite — ta clé api nextmotion

connecte-toi à l'[application web Nextmotion](https://app.nextmotion.net), puis **Settings → API Keys** → génère une clé et copie-la : elle n'est plus affichée ensuite. colle-la dans tes clés de connecteur oto sous `nextmotion`.
- la clé agit **au nom de l'utilisateur qui l'a générée**, sur les cliniques dont il est employé ; elle n'expire pas, se révoque par « Reroll » ou suppression au même endroit
- l'accès API est inclus dans l'offre Scale, ou en option payante à partir de l'offre Growth (grille publique [nextmotion.net/tarifs](https://www.nextmotion.net/tarifs)) ; qui peut générer une clé selon le rôle dans la clinique n'est pas documenté
- BYO seulement : pas de clé oto partagée

## usage — agenda, catalogue, devis, factures et stock

commence par `nextmotion_clinic()` : chaque autre outil demande un `clinic_id`.
- « qui travaille dans la clinique ? » → `nextmotion_practitioner(op="list", clinic_id=…)`
- « l'agenda du jour » → `nextmotion_appointment(op="list", clinic_id=…, date="AAAA-MM-JJ")`
- « quels créneaux libres ? » → `nextmotion_availability(clinic_id=…, start_date=…, end_date=…)`
- « déplace ce rendez-vous » → prends UN créneau de `nextmotion_availability`, puis `nextmotion_appointment(op="reschedule", appointment_id=…, visit_type_opening_hour_id=<id du créneau>, time_slot=<time_slot du créneau>)`
- « qu'est-ce qu'on propose, à quel prix ? » → `nextmotion_catalog(kind="visit_type"|"treatment_type"|"treatment_pricing"|…, clinic_id=…)`
- « les devis / factures » → `nextmotion_quote(op="list", clinic_id=…)`, `nextmotion_invoice(op="list", clinic_id=…)`
- « les factures de janvier » → `nextmotion_invoice(op="list", clinic_id=…, invoiced_from="2026-01-01", invoiced_to="2026-01-31")`
- « quels lots expirent bientôt, qu'est-ce qui est en rupture ? » → `nextmotion_product(op="list", clinic_id=…, expiring_within_days=30)` ou `stock_state="low"|"out"`

## note — factures par période : un parcours complet, borné

- l'API Nextmotion ne filtre pas les factures par date et ne documente pas leur ordre : l'outil lit **toutes** les pages (100 factures par appel) et garde celles dont `invoiced_time` tombe dans la période, bornes incluses
- le parcours est plafonné par `max_pages` (20 par défaut, 100 au plus) ; la réponse dit `pages_lues`, `factures_parcourues` et `complet`
- `complet: false` = résultat **partiel** : relance avec `offset=<offset_suivant>` et la même période pour lire la suite
- avec une période, `limit` est refusé et `offset` est le point de départ du parcours
- pas de filtre de période sur les devis : un devis n'a pas de date de facturation, et sa date d'émission peut être vide

## note — stock produits : pas de lien avec les factures

- `nextmotion_product` lit le stock de la clinique (un lot par ligne : numéro de lot, péremption, niveaux de stock, prix unitaire, produit et marque), en lecture seule
- l'API Nextmotion n'expose **aucun consommable ni lot par facture ou par soin** : une ligne de facture ne porte que le nom de l'acte, son prix et sa quantité, et rien ne relie un lot à une facture ou à un patient

## note — données de santé : ce qui n'est pas servi

- **aucun contenu médical** : dossier patient, antécédents, photos et médias, ordonnances, consentements, soins réalisés et consultations restent hors du connecteur
- **le patient n'est servi que par son id** dans les rendez-vous, devis et factures (aperçu `dry_run` compris) : ni nom, ni prénom, ni email, ni téléphone, et aucun outil ne résout cet id en personne — n'essaie pas de deviner qui c'est
- retirés aussi : date de naissance, sexe, commentaires du praticien, titres et notes d'agenda, titres de devis/facture, détails de ligne et tout texte libre, **sans option pour obtenir le brut**
- restent en texte les libellés du catalogue (type de visite, nom d'une ligne de devis/facture) et le nom des praticiens ; si un praticien a saisi le nom d'un patient dans un libellé de ligne, il passerait

## note — ce qui touche un vrai patient

- `op="reschedule"` et `op="delete"` ont `dry_run=True` **par défaut** : l'appel rend le rendez-vous tel qu'il est et ce qui changerait, sans rien écrire. passe `dry_run=False` pour agir
- que Nextmotion prévienne le patient (SMS, email) lors d'un report ou d'une suppression n'est pas documenté
- dérivé de la spec OpenAPI publique, jamais exercé avec une vraie clé
