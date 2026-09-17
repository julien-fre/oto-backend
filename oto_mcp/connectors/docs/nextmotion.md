## prerequisite — ta clé api nextmotion

connecte-toi à l'[application web Nextmotion](https://app.nextmotion.net), puis **Settings → API Keys** → génère une clé et copie-la : elle n'est plus affichée ensuite. colle-la dans tes clés de connecteur oto sous `nextmotion`.
- la clé agit **au nom de l'utilisateur qui l'a générée**, sur les cliniques dont il est employé ; elle n'expire pas, se révoque par « Reroll » ou suppression au même endroit
- l'accès API est inclus dans l'offre Scale, ou en option payante à partir de l'offre Growth (grille publique [nextmotion.net/tarifs](https://www.nextmotion.net/tarifs)) ; qui peut générer une clé selon le rôle dans la clinique n'est pas documenté
- BYO seulement : pas de clé oto partagée

## usage — agenda, catalogue, devis et factures

commence par `nextmotion_clinic()` : chaque autre outil demande un `clinic_id`.
- « qui travaille dans la clinique ? » → `nextmotion_practitioner(op="list", clinic_id=…)`
- « l'agenda du jour » → `nextmotion_appointment(op="list", clinic_id=…, date="AAAA-MM-JJ")`
- « quels créneaux libres ? » → `nextmotion_availability(clinic_id=…, start_date=…, end_date=…)`
- « déplace ce rendez-vous » → prends UN créneau de `nextmotion_availability`, puis `nextmotion_appointment(op="reschedule", appointment_id=…, visit_type_opening_hour_id=<id du créneau>, time_slot=<time_slot du créneau>)`
- « qu'est-ce qu'on propose, à quel prix ? » → `nextmotion_catalog(kind="visit_type"|"treatment_type"|"treatment_pricing"|…, clinic_id=…)`
- « les devis / factures » → `nextmotion_quote(op="list", clinic_id=…)`, `nextmotion_invoice(op="list", clinic_id=…)`

## note — données de santé : ce qui n'est pas servi

- **aucun contenu médical** : dossier patient, antécédents, photos et médias, ordonnances, consentements, soins réalisés et consultations restent hors du connecteur
- **le patient n'est servi que par son id** dans les rendez-vous, devis et factures (aperçu `dry_run` compris) : ni nom, ni prénom, ni email, ni téléphone, et aucun outil ne résout cet id en personne — n'essaie pas de deviner qui c'est
- retirés aussi : date de naissance, sexe, commentaires du praticien, titres et notes d'agenda, titres de devis/facture, détails de ligne et tout texte libre, **sans option pour obtenir le brut**
- restent en texte les libellés du catalogue (type de visite, nom d'une ligne de devis/facture) et le nom des praticiens ; si un praticien a saisi le nom d'un patient dans un libellé de ligne, il passerait

## note — ce qui touche un vrai patient

- `op="reschedule"` et `op="delete"` ont `dry_run=True` **par défaut** : l'appel rend le rendez-vous tel qu'il est et ce qui changerait, sans rien écrire. passe `dry_run=False` pour agir
- que Nextmotion prévienne le patient (SMS, email) lors d'un report ou d'une suppression n'est pas documenté
- dérivé de la spec OpenAPI publique, jamais exercé avec une vraie clé
