## prerequisite — créer une connected app salesforce

Salesforce n'a pas de client OAuth partagé entre clients (contrairement à Google) — chaque org Salesforce doit créer sa propre **Connected App** :
- Setup → App Manager → **New Connected App**
- coche **Enable OAuth Settings**
- **Callback URL** : celle affichée sur la fiche du connecteur, sous « Autoriser oto chez Salesforce » — copie-la telle quelle (un espace ou un slash final en trop suffit à faire échouer le consentement)
- **OAuth Scopes** : ajoute `Manage user data via APIs (api)` et `Perform requests at any time (refresh_token, offline_access)`
- une fois enregistrée, copie le **Consumer Key** et le **Consumer Secret** (Manage Consumer Details)
- colle Consumer Key / Consumer Secret / Login URL dans oto (page compte / connecteurs) — l'enregistrement est accepté même si la connexion n'est pas encore complète, c'est normal
- puis lance l'autorisation : demande à ton agent « connecte Salesforce », il te rend le lien de consentement à ouvrir. C'est le SEUL moyen d'obtenir le jeton désormais — il n'y a plus de champ Refresh Token à remplir à la main ni de code à copier

## usage — contacts, comptes, leads, opportunités

CRUD générique par sObject (Contact, Account = « companies », Lead, Opportunity, objets custom) + SOQL/SOSL brut.
- « liste les contacts de l'account Acme »
- « crée un contact Ada Lovelace chez Acme Corp »
- « cherche les opportunités ouvertes de plus de 50k€ »
- « ajoute une note à ce compte »
