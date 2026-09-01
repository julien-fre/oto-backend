# Migrations vivantes sur la DB partagée canari/prod — le playbook

> Extrait des chantiers du cadrage objets/visibilité (2026-07-10) : Phase H datastore,
> fusion des procédures d'équipe, unification des ACL connecteurs. À relire AVANT toute
> migration qui renomme/droppe une table ou une contrainte.

**Le fait structurel** : canari (preprod) et prod partagent LA MÊME base. Un DDL exécuté
au boot canari s'applique instantanément à la prod — qui tourne encore l'ANCIEN code.
Toute migration destructive se découpe donc en **lots promus séparément**, chaque lot ne
détruisant que ce que le code prod COURANT ne référence plus.

## La danse en N lots

1. **Lot A (additif)** — nouvelles colonnes/tables + backfill + le code bascule dessus.
   Zéro DDL destructif. Les objets legacy restent en place, encore écrits par la prod.
2. **Promotion A** (PR canari→main). La prod cesse de référencer les objets legacy.
3. **Lot B (bascule/destruction partielle)** — ce que le Lot A a rendu orphelin peut
   tomber ; ce que la prod (Lot A) lit encore attend le lot suivant.
4. **Promotion B**, puis **Lot C** (drops finaux), etc. Un lot = un boot canari vérifié
   (deploy vert + smoke + lecture d'une surface réelle) AVANT sa promotion.

## Les techniques (toutes vécues, toutes nécessaires)

- **Copie legacy→cible à CHAQUE boot, gardée `to_regclass`** : tant que la table legacy
  existe, on recopie (la prod y écrit pendant la fenêtre) ; après le DROP, no-op — un
  boot ne casse jamais, quel que soit l'ordre des déploiements. **Newer-wins** sur
  `set_at`/`updated_at` (ON CONFLICT DO UPDATE … WHERE EXCLUDED.x > cible.x) pour les
  données mutables ; DO NOTHING suffit pour les grants immutables (une révocation prod
  pendant la fenêtre ressuscite — assumé si la fenêtre est courte, le dire dans le commit).
- **DROP au même boot que la copie finale** : le DROP suit la copie dans le même
  `_init` → la dernière écriture prod de la fenêtre est rattrapée.
- **Basculer l'ARBITRE `ON CONFLICT` avant de dropper une contrainte** : la prod fait
  `ON CONFLICT (ancienne_clé)` → il faut d'abord poser l'index unique cible + promouvoir
  le code qui arbitre dessus (Lot A), et seulement ensuite dropper la PK legacy (Lot B).
  Deux index uniques coexistent pendant la fenêtre, les deux arbitres marchent.
- **Nommer les nouvelles PK** (`CONSTRAINT x_owner_pkey PRIMARY KEY …`) : le
  `DROP CONSTRAINT IF EXISTS x_pkey` de la migration ciblerait sinon la PK toute neuve
  d'une install fraîche (même nom par défaut).
- **Ids fusionnés = la MÊME séquence** : des lignes migrées vers une table à id
  surrogate prennent `nextval` de la séquence EXISTANTE — jamais une séquence neuve ni
  un offset (collision garantie avec les refs déjà distribuées : project_links, grants).
  ⚠️ **La recette ne marche qu'à UNE source.** Dès que DEUX tables à séquence
  indépendante convergent vers la même cible (cas `projects` + `docs` → `nodes`,
  lot M2 du modèle de contenu), aucune des deux ne peut garder son id : la ligne 12
  de l'une et la ligne 12 de l'autre réclament la même. L'id legacy descend alors
  dans une propriété, et **le vrai coût n'est pas là** — il est dans tout ce à quoi
  cet id avait déjà été distribué (routes de front, refs de liens, grants, portées de
  jeton, colonnes d'ancrage). Le mesurer AVANT de promettre une bascule de lecture :
  c'est ce qui décide si le lot est une conversion ou un changement de régime.
- **Fusion de tables jumelles → prédicats de scope PARTOUT** : quand des lignes d'un
  autre grain entrent dans une table, chaque requête existante doit gagner son
  `owner_type='…'` — chercher en priorité les requêtes SANS filtre (list_all, by_id).
- **Seed gardé sur le sous-ensemble sémantique** (ex. lignes `scope='platform'`), pas
  sur `COUNT(*)` global : une table unifiée non vide n'implique pas que le seed a tourné.

## Les pièges

- **Fail-open silencieux sur les gates** : `require_connector_access` et
  `session_visibility` avalent les erreurs DB (fail-open voulu par palier). Pendant une
  fenêtre de migration ratée, le deny se dégrade en allow SANS erreur visible — vérifier
  les surfaces RBAC en lecture réelle après chaque boot, pas seulement le smoke HTTP.
- **`gh pr merge` juste après `pr create`** : le check `guard` n'est pas encore rapporté
  → GitHub répond « add --admin » et NE merge PAS (silencieux dans un script). Attendre
  `gh pr checks | grep 'guard.*pass'` avant de merger ; re-vérifier `state=MERGED` et
  `git merge-base --is-ancestor <tip> origin/main` (avec le SHA POST-rebase, pas le
  SHA du commit local d'avant `pull --rebase`).
- **Les one-shots du boot qui lisent une table vouée au drop** (backfills historiques) :
  les retirer (ou les garder `to_regclass`) DANS le lot qui précède le drop — sinon le
  premier boot prod post-drop crashe sur un backfill spent.
- **Tree partagé** : une session parallèle peut committer TON `_init.py` en vol dans son
  propre commit (absorption). Avant de diagnostiquer un diff stagé « incomplet »,
  vérifier si HEAD contient déjà tes hunks (`git log -S <marqueur>`).


## Piège : `CREATE INDEX` d'une NOUVELLE colonne dans `_schema.py` (vécu 20/07)

`_init.init_db` fait `conn.execute(_SCHEMA)` PUIS les `ALTER TABLE … ADD COLUMN`. Sur
une table EXISTANTE, `CREATE TABLE IF NOT EXISTS` est sauté — mais un `CREATE INDEX …(col)`
posé **dans `_schema.py` juste après le CREATE TABLE** s'exécute quand même, contre
l'**ancienne** table qui n'a pas encore la colonne → `column "col" does not exist`, init_db
KO, service down, **rollback auto**. (Vécu : `doc_change_requests(project_id)`.)

**Règle** : les index d'une colonne AJOUTÉE par migration vivent UNIQUEMENT dans `_init.py`
**après l'ALTER** (idempotents `IF NOT EXISTS` — le fresh install les crée aussi). Le
`CREATE INDEX` reste dans `_schema.py` SEULEMENT si la table (et la colonne) sont **neuves**
au même endroit (ex. `doc_links`, `doc_embeddings` : table + index créés ensemble sur une
table fraîche = sûr). Corollaire extension : `CREATE EXTENSION vector` doit précéder
`_SCHEMA` si une table de `_SCHEMA` utilise `halfvec`/`vector`.

**La règle est tenue mécaniquement depuis le 2026-09-01 (#781).** Elle ne l'était pas :
`tests/test_boot_order_replay.py` avait été écrit pour ce piège précis et **ne le jouait
jamais**, parce qu'il bootait une base **vierge** — où le `CREATE TABLE IF NOT EXISTS`
pose la colonne inline, donc où l'index la trouve toujours. Le défaut n'existe que là où
le `CREATE TABLE` est SAUTÉ. Mesuré le 01/09 sur un lot qui portait le motif : CI verte,
87 tests verts sur les 7 fichiers de garde du domaine boot, **boot réel rouge** contre une
base construite par le tronc précédent — et la base étant partagée prod/preprod, un push
sur `main` l'appliquait à la production.

Ce que le cliquet joue maintenant : il part de la base neuve, **retire une colonne que le
boot pose par `ALTER`** — ce qui est exactement l'état d'une base d'avant le lot qui l'a
introduite — et rejoue la séquence. Pas pour une colonne choisie à la main : pour **les
132**, relevées sur le SQL que le boot exécute vraiment (donc une colonne ajoutée demain y
entre toute seule). Coût : ≈ 4 s. Aucun commit à extraire, aucun jeu de DDL figé à tenir à
jour.

Il vérifie deux choses, et la seconde ferme un défaut plus silencieux que le premier :
le boot **passe**, et le schéma **converge** — tout ce que porte une base neuve doit se
retrouver sur la base remise à niveau. Un `ALTER` qui pose la colonne sans la contrainte
que le `CREATE TABLE` déclare inline (PK, UNIQUE, CHECK, FK) donne une base neuve avec la
contrainte et une production sans : la FK ne mord pas, l'unicité n'unifie pas, et **rien
ne rougit jamais** puisque les deux « marchent ». Huit divergences de cette famille
existaient au 01/09 ; elles sont nommées dans `_DIVERGENCES_CONNUES` (les réparer demande
un `DROP CONSTRAINT`, donc un ACTE sur base partagée, pas une ligne de boot) et **le
cliquet refuse la neuvième**.

Trois violations de la règle dormaient sur le tronc, inertes seulement parce que la
production a ces colonnes depuis longtemps — corrigées dans le même lot : l'index
`idx_unipile_accounts_org`, posé dans `_schema.py` **et** dans `_init.py` ; les index de
recherche de `guides`, dont le prédicat lit `delivery` ; la conversion #317, qui lit
`user_datastores.schema`. Les deux dernières se corrigent en **remontant l'`ALTER`** avant
son premier lecteur : dans `_init.py`, l'ordre des lignes est une contrainte d'exécution,
pas une mise en page.

## PROD et PREPROD partagent la MÊME base (constaté 07/08)

> ⚠️ **PROD et PREPROD partagent la MÊME base** (constaté 07/08 : DSN **identiques** — même
> hôte, même DB — entre `/opt/oto-mcp/.env` et `/opt/oto-mcp-canari/.env`). Le « DB découplée »
> du bloc CUTOVER plus haut ne décrit **pas** l'état réel. Deux conséquences pratiques : une
> donnée écrite depuis la preprod est **la donnée de prod** (pas un bac à sable) ; et toute
> config portée par une COLONNE ne peut avoir qu'**une** valeur pour les deux environnements
> — ce qui exclut de distinguer prod/preprod par la base (vécu sur `orgs.front_base_url`, où
> la preprod émet donc des liens vers le front de prod). Vérifier avant de raisonner dessus :
> comparer les DSN par hash, jamais en les lisant en clair.
