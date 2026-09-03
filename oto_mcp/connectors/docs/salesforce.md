## prerequisite — créer l'application Salesforce

Salesforce n'a pas de client OAuth partagé entre clients (contrairement à Google) : chaque org doit créer la sienne. Compte une quinzaine de minutes, et un profil administrateur.

Deux modèles coexistent. **Les Connected Apps sont désactivées sur les orgs récentes** (Salesforce répond « contactez le Support ») — c'est alors une **External Client App**, et les chemins ci-dessous en tiennent compte.

**1. Créer l'application.** Configuration → recherche rapide « **applications clientes externes** » → **Nouvelle application cliente externe**, puis active **OAuth**.

**2. URL de rappel.** Celle affichée sur cette fiche, sous « Autoriser oto chez Salesforce ». Copie-la telle quelle : un espace ou un slash final en trop suffit à faire échouer le consentement.

**3. Portées OAuth.** Coche **`api`** (Gérer les données utilisateur via des API) **et `refresh_token`** (Effectuer des requêtes à tout moment).

⚠️ **`full` ne suffit pas.** L'accès complet n'inclut PAS `refresh_token` / `offline_access`, qui est une portée distincte. Sans elle, le consentement peut réussir mais Salesforce ne délivre aucun jeton durable — échec en `invalid_scope`. C'est le piège le plus fréquent.

**4. Récupérer les identifiants.** Onglet **Paramètres** → **Paramètres OAuth** → **Détails du consommateur**. Salesforce envoie un code de vérification par email avant de les révéler. Note la **clé** et le **secret du consommateur**.

**5. Autoriser les appels serveur.** Onglet **Stratégies** → **Relaxe d'IP** → « **Relâcher les restrictions IP** ».

⚠️ **Le piège le moins intuitif.** Tu donnes ton consentement depuis ton navigateur, mais c'est **notre serveur** qui rafraîchit ensuite le jeton, depuis une autre adresse. Restrictions appliquées, Salesforce refuse ces appels avec un `invalid_grant` au libellé trompeur (« expired token ») alors que le jeton est valide. Si ta politique interdit de relâcher, autorise plutôt l'adresse `151.115.148.128` dans tes plages IP approuvées.

## setup — connecter oto

1. Colle la **clé du consommateur**, le **secret du consommateur** et la **Login URL** sur cette fiche. La Login URL est ton domaine : `https://<ton-domaine>.my.salesforce.com` — pas `login.salesforce.com` si tu as un My Domain, et **sans le `-setup`** du domaine de la console. Pour un sandbox : `https://<domaine>.sandbox.my.salesforce.com`.

2. L'enregistrement est accepté **même si la connexion n'est pas encore complète** : c'est normal, le jeton n'existe pas encore.

3. Clique sur **Autoriser oto chez Salesforce** et choisis pour qui ranger la connexion — toi, ton équipe ou toute l'org. Les deux derniers demandent d'en être administrateur, et lisent l'application enregistrée **à ce niveau-là** : pour connecter au nom de l'org, les identifiants doivent avoir été posés côté org.

C'est ce consentement qui produit le jeton ; il n'y a aucun champ à remplir à la main.

## note — rotation des jetons

Salesforce impose la **permutation des jetons d'actualisation** sur les applications externes — contrôle verrouillé, modifiable seulement par leur support. Chaque appel consomme le jeton et en reçoit un neuf.

Rien à faire de ton côté, oto le gère. C'est mentionné parce que ça explique un comportement qui pourrait sembler anormal : le jeton stocké change en permanence, et réutiliser un ancien jeton fait révoquer toute la connexion par Salesforce, imposant un nouveau consentement.

## usage — contacts, comptes, leads, opportunités

CRUD générique par sObject (Contact, Account = « companies », Lead, Opportunity, objets custom) + SOQL/SOSL brut.
- « liste les contacts de l'account Acme »
- « crée un contact Ada Lovelace chez Acme Corp »
- « cherche les opportunités ouvertes de plus de 50k€ »
- « ajoute une note à ce compte »

⚠️ **oto n'applique pas la déduplication de Salesforce, ni en lot ni à l'unité.** Que tes règles de doublon jouent ou non dépend du paramétrage de TON org Salesforce ; oto ne les active pas et ne vérifie pas qu'elles ont joué. Une création en lot a déjà rendu « succès » pour des fiches en doublon exact d'existantes (même nom, même compte). Une procédure qui prend ces règles pour filet de sécurité doit vérifier l'existence elle-même avant de créer. Et même règles actives, Salesforce compare les fiches d'un même lot à ce qui existe déjà, jamais entre elles : un lot qui contient deux fois la même personne passe entier.
