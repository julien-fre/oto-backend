---
title: Migrations versionnées — note de conception
type: explanation
description: >-
  L'inventaire mesuré de ce que `db/_init.py` exécute à CHAQUE démarrage (143 ALTER
  écrits, 297 ordres SQL réellement émis, 40 écritures dont le coût suit la taille de
  la base, plus quatre travaux de maintenance hors transaction), la datation de chaque
  migration, les 79 devenues inertes, deux défauts constatés au passage — puis trois
  options pour sortir les migrations du chemin de démarrage, avec leurs risques et ce
  que chacune change pour la fenêtre de healthcheck. AUCUNE recommandation : le choix
  est une décision d'architecture (ADR), ce document lui fournit ses chiffres.
---

# Migrations versionnées — note de conception

> **Ce document ne décide rien.** Il mesure l'existant, l'inventorie, et pose trois
> options avec leurs risques. Le choix relève d'une ADR — c'est un changement de
> régime pour un mécanisme qui touche une base **partagée prod/preprod**, pas une
> amélioration qu'on glisse dans un lot.
>
> Rédigé le 2026-08-27, en marge du lot qui a découpé `db/_schema.py` par domaine
> (déplacement pur, DDL inchangé au caractère près). Ce lot n'a **pas** touché
> `_init.py` : la §0 dit pourquoi, et c'est la première donnée d'entrée du choix.

## 0. Pourquoi `_init.py` n'a pas été découpé par domaine

La question posée était : peut-on regrouper les ALTER par domaine, comme on vient de
le faire pour le DDL, sans rien changer à l'ordre d'exécution ? La réponse est non,
et elle se mesure plutôt qu'elle ne s'argumente.

`_init_db_once` est **une** fonction de 914 lignes dont le corps utile est un seul
`with _connect() as conn:` portant **226 instructions de premier niveau** (209
appels, 5 `for`, 7 `if`, 4 imports locaux, 1 affectation). En attribuant chaque
instruction au domaine de la table qu'elle touche — avec la carte des domaines que
la découpe du DDL vient de fixer — on obtient **97 blocs contigus de même domaine**
pour 226 instructions.

Autrement dit : `projects` apparaît en **13 morceaux séparés**, `orgs` en 8,
`doctrine` en 6, `usage` et `users` en 4. Les migrations sont écrites dans l'ordre
**chronologique** — chaque lot ajoute à la fin, quel que soit son domaine — et cet
ordre n'est pas commutatif :

- le tenant 1 doit être semé **avant** l'`ALTER` qui pose `orgs.tenant_id NOT NULL
  DEFAULT 1 REFERENCES tenants(id)` (garde-fou `test_tenant_l1_migration`) ;
- l'index unique cible doit exister **avant** le `DROP CONSTRAINT` de la PK legacy,
  sinon l'arbitre `ON CONFLICT` de la prod tombe (`live-migrations.md`) ;
- la copie legacy→cible doit précéder le `DROP` de la table legacy, **au même boot**.

Regrouper par domaine **en gardant l'ordre** produirait donc 97 fragments répartis
sur 20 modules, rappelés dans un ordre chronologique entrelacé : la même séquence,
avec 97 indirections de plus et 97 occasions nouvelles de se tromper d'ordre. Ce
n'est pas de la localité, c'est du camouflage. Et regrouper **sans** garder l'ordre
est précisément ce que les trois incidents ci-dessus interdisent.

**La conclusion utile n'est pas « on ne peut pas ranger `_init.py` »**, c'est : le
rangement de `_init.py` n'est pas un problème de fichiers. C'est le symptôme d'un
mécanisme qui rejoue tout l'historique à chaque démarrage parce qu'il n'a aucune
notion de « déjà fait ». C'est ce que les trois options adressent.

## 1. Ce que le boot fait aujourd'hui, mesuré

Mesures du 2026-08-27, `init_db()` rejoué contre un PostgreSQL 17 jetable
(`pgvector/pgvector:pg17`, conteneur local, RTT négligeable, base **sans données**),
deux passes sur la même base :

| passe | situation | ordres SQL émis | durée |
| --- | --- | ---: | ---: |
| 1 | base **vierge** (installation neuve) | 390 | ≈ 170–190 ms |
| 2 | base **déjà migrée** — le cas de la PRODUCTION | 297 | ≈ 55–70 ms |

Répartition des 297 ordres de la passe 2 — celle qui tourne à **chaque**
redémarrage en production, y compris un `systemctl restart` sans nouveau code, et
où *aucun* n'a d'effet :

| ordre | n |
| --- | ---: |
| `ALTER TABLE` | 154 |
| `CREATE INDEX` (dont 10 `UNIQUE`) | 44 |
| `SELECT` (sondes, lectures de backfill) | 30 |
| `UPDATE` | 20 |
| `DROP TABLE` | 15 |
| `DELETE` | 13 |
| `INSERT` | 7 |
| `DROP INDEX` | 3 |
| autres (`CREATE EXTENSION` ×2, `CREATE TABLE` ×2, `DO`, `CREATE SEQUENCE`, `DROP SCHEMA`, le bloc `_SCHEMA` lui-même…) | 11 |

**Ce que ces chiffres disent, et ce qu'ils ne disent pas.** 55–70 ms sur une base
locale **vide** ne se transposent pas à la production : la base y est une RDB
managée (chaque ordre est un aller-retour réseau) et surtout **40 de ces ordres
écrivent des données** — leur coût suit la taille des tables, pas le nombre
d'ordres. Sur une base de test vide ils sont gratuits ; c'est précisément ce qui
rend la mesure locale rassurante à tort. Les 154 `ALTER` no-op, eux, sont bornés et
bon marché : ils **ne sont pas** le problème de la fenêtre de healthcheck. Ils sont
le problème de la lisibilité et du risque d'écriture.

### 1.1 Le boot n'est plus une migration, c'est un ordonnanceur de maintenance

C'est le constat le plus important de cette note, et il déborde le sujet des ALTER.
Après avoir commité sa transaction de schéma, `init_db` enchaîne **quatre travaux
qui n'ont rien de DDL**, tous sur le chemin du démarrage, tous `fail-open` (une
exception est loggée, pas relevée), tous à coût croissant avec la base :

| travail | ce qu'il fait | ce dont le coût dépend |
| --- | --- | --- |
| `backfill_node_blocks()` (`db/blocks.py`) | parse le corps markdown des nœuds en blocs, en Python | nombre de nœuds modifiés |
| `prune_tool_calls(30 j)` (`db/usage.py`) | purge du journal d'appels **et** des runs devenus orphelins — deux `DELETE` non bornés, sur sa propre connexion | volume du journal |
| `prune_run_messages(30 j)` (`db/run_thread.py`) | purge du fil des runs hébergés | volume du fil |
| `_ensure_datastore_key_indexes()` | par namespace : résorption des doublons puis `CREATE UNIQUE INDEX` | nombre de namespaces × leurs lignes |

Et à l'intérieur de la transaction, cinq conversions de contenu appelées depuis
`db/nodes.py` (`convert_projects`, `convert_docs`, `convert_doctrines`,
`convert_tables`, `convert_rows`) plus `db/guides.py` et `db/aux_embed.py`.

**Conséquence pour le choix** : une solution qui ne s'occuperait que des `ALTER`
laisserait sur le chemin de démarrage la totalité du travail dont la durée est
imprévisible. La rétention du journal en est l'exemple le plus net
(`OTO_MCP_CALL_LOG_RETENTION_DAYS`) : c'est un travail périodique, il a la forme
d'un `cron` — le dépôt en porte d'ailleurs un, `deploy/oto-journal-archive.timer` —
et il s'exécute pourtant dans la fenêtre de healthcheck de chaque déploiement.

### 1.2 Ce qui consomme la fenêtre de healthcheck, par ordre de gravité

Le déploiement prod (`tag v*` → `deploy.yml` → script serveur `oto-backend.sh`,
hors dépôt) enchaîne reset → install → restart → **smoke HTTP** → rollback auto si
le smoke échoue. La fenêtre observée est d'environ **60 s** (21/08, cf. CLAUDE.md
racine) et elle est **finie** : un lot qui ajoute du travail one-shot au boot doit
l'élargir *avant* de poser son tag, sinon un déploiement sain est rollbacké.

1. **Les travaux de maintenance et les backfills** (§1.1 et §2.4) — le seul poste
   qui grandit avec la base, donc le seul qui transformera un jour un déploiement
   vert en rollback sans que rien n'ait changé dans le lot.
2. **L'attente de verrous.** `init_db` prend un advisory lock de transaction (un
   seul migrateur à la fois : prod et preprod bootent sur la même base) puis pose
   `lock_timeout = 5 s`. Sur `DeadlockDetected`/`LockNotAvailable`, la transaction
   **entière** est rejouée, jusqu'à `OTO_MCP_INIT_DB_ATTEMPTS = 3`, avec 2 s puis
   4 s d'attente. Pire cas : ~6 s d'attente + 3 traversées complètes.
3. **Les 297 allers-retours** eux-mêmes, bornés et petits, payés à chaque
   redémarrage.

## 2. L'inventaire

### 2.1 Le compte exact

`_init.py` contient **143 occurrences** de la chaîne `ALTER TABLE`. Elles ne sont
pas homogènes, et c'est structurant pour toute solution :

| | n | remarque |
| --- | ---: | --- |
| en prose (docstring) | 1 | pas un ordre |
| de forme canonique (`ALTER TABLE <t> <action> <cible>`) | 136 | inventoriés ci-dessous |
| **hors forme** | 6 | 2 construites en `f"…{_t}…"` dans une boucle sur des tables, 3 `ADD PRIMARY KEY` (sans `IF NOT EXISTS` : l'idempotence tient à un `if` **Python**, pas au SQL), 1 `RENAME TO` |
| **total des ordres** | **142** | |

À l'exécution, le boot en régime de production émet **154** `ALTER TABLE` : les
boucles déplient les formes dynamiques. Toute solution « un fichier = une
migration » doit décider quoi faire des ordres générés, et des trois `ADD PRIMARY
KEY` dont l'idempotence n'est pas dans le SQL.

### 2.2 Par domaine

Domaines au sens de la découpe du DDL (`db/schema/<domaine>.py`).

| domaine | ALTER | dont `ADD COLUMN` | dont **inertes** | autres |
| --- | ---: | ---: | ---: | ---: |
| `projects` | 28 | 25 | 21 | 3 |
| `orgs` | 25 | 21 | 7 | 4 |
| `usage` | 15 | 13 | 12 | 2 |
| `doctrine` | 14 | 8 | 7 | 6 |
| `users` | 12 | 2 | 2 | 10 |
| `datastore` | 11 | 8 | 6 | 3 |
| `connectors` | 8 | 6 | 6 | 2 |
| `unipile` | 8 | 8 | 7 | 0 |
| `tenants` | 7 | 7 | 4 | 0 |
| `guides` | 2 | 2 | 1 | 0 |
| `runs` | 2 | 2 | 2 | 0 |
| `tokens` | 2 | 2 | 2 | 0 |
| `billing` | 1 | 1 | 1 | 0 |
| `grants` | 1 | 1 | 1 | 0 |
| **total** | **136** | **106** | **79** | **30** |

### 2.3 Par date d'apparition

Datation par `git log -S` sur le fragment SQL propre à chaque ordre
(`oto_mcp/db/_init.py` et son ancêtre `oto_mcp/db.py`), premier commit qui
l'introduit :

| mois | ordres introduits |
| --- | ---: |
| 2026-06 | 53 |
| 2026-07 | 67 |
| 2026-08 | 14 |

Deux ordres non datables (formes dynamiques : leur texte n'existe pas littéralement
dans l'historique). **Rien n'a jamais été retiré** : la file est strictement
croissante depuis l'origine du fichier. La seule raison pour laquelle elle ne
grandit pas plus vite est que l'essentiel du DDL neuf part directement dans
`_schema.py`, où il est déclaratif — et donc gratuit au boot suivant.

### 2.4 Les ordres qui touchent des DONNÉES

Ce sont eux, et eux seuls, dont le coût varie avec la taille de la base. Ils sont
idempotents **par prédicat**, jamais par marqueur : ils reposent la question à
toute la table à chaque démarrage. Dans le source de `_init.py` :

- **8 `INSERT … SELECT`** : seed du tenant 1, conversion des instructions plateforme
  en `guides`, fusion des procédures d'équipe dans `org_instructions` (+ révisions),
  dépliage de `user_disabled_tools`/`user_enabled_tools` par org, conversion
  `org_connector_access`/`group_connector_access` → `connector_acl`.
- **14 `UPDATE`**, dont plusieurs balayages complets : `docs`, `projects` et `guides`
  re-marqués `embed_dirty` par un `NOT IN (SELECT …)` sur la table d'embeddings ;
  renumérotation de `docs.position` par fonction de fenêtre `ROW_NUMBER() OVER
  (PARTITION BY project_id, parent_id)` ; réécriture du `schema` JSONB de tous les
  `user_datastores` ; adossement `orgs.kb_project_id`.
- **3 `DELETE FROM`** : `project_links` de type `doc`, l'instruction plateforme
  `onboarding`, et un `DELETE FROM {_t} WHERE org_id = 0` généré en boucle.

À l'exécution, ces 25 ordres écrits en deviennent **40** (20 `UPDATE`, 13 `DELETE`,
7 `INSERT`), les boucles et les conversions appelées dans `db/nodes.py` fournissant
le reste — encore un point où le compte du fichier ne dit pas le compte du boot.

> Le `UPDATE docs SET embed_dirty = TRUE WHERE embed_dirty = FALSE AND id NOT IN
> (SELECT doc_id FROM doc_embeddings)` est l'exemple canonique : il est *correct*,
> il est *idempotent*, il ne coûte rien sur une base de test — et il relit la
> totalité de `docs` et de `doc_embeddings` à chaque redémarrage, pour un travail
> fait une fois en juillet.

### 2.5 Les 79 `ADD COLUMN` devenus inertes

Un `ALTER TABLE t ADD COLUMN IF NOT EXISTS c` est **inerte** dès lors que `c`
figure aussi dans le `CREATE TABLE` de `_SCHEMA` : une installation neuve reçoit la
colonne par le DDL de base, et une base existante l'a reçue par l'ALTER, une fois,
il y a des semaines. C'est le cas de **79 des 106** `ADD COLUMN` — 21 sur
`projects`, 12 sur `usage`, 7 sur `doctrine`, `orgs` et `unipile`…

**C'est le seul gain que cette note identifie comme indépendant des trois
options** : ces 79 ordres sont supprimables sans changer de régime, à une condition
qui se vérifie en une requête sur les bases réelles — que la colonne y soit
effectivement présente :

```sql
-- Pour chaque (table, colonne) de la liste des 79 : doit rendre 79 lignes.
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema = 'public' AND (table_name, column_name) IN ( … );
```

⚠️ **Ce n'est pas gratuit pour autant.** Retirer l'ALTER rend le DDL de base **seul**
porteur de la colonne. Une base restaurée depuis une sauvegarde antérieure à
l'ALTER, ou un environnement oublié qui n'a pas booté depuis, ne rattrapera plus
jamais la colonne — et le symptôme sera une `UndefinedColumn` à l'exécution, pas au
boot. La vérification doit donc couvrir **toutes** les bases servies, pas seulement
celle de production. C'est aussi, exactement, l'auto-réparation dont §4.2 dit
qu'on la perd : la retirer ici en est le premier acompte.

### 2.6 Les 30 ordres non additifs

Ceux qu'aucune migration ne peut rejouer à l'aveugle, et qui sont la raison d'être
de la « danse en N lots ». Datés :

| date | ordre |
| --- | --- |
| 2026-06-09 | `DROP CONSTRAINT connector_credentials_pkey` |
| 2026-06-11 | `DROP COLUMN connector_credentials.secret` |
| 2026-06-13 | `RENAME COLUMN tool_calls.tool_name`, `tool_calls.called_at` |
| 2026-06-15 | `ALTER COLUMN users.role` |
| 2026-06-16 / 06-22 | `ALTER COLUMN org_invitations.org_id`, `.email` |
| 2026-07-01 | `ALTER COLUMN org_instructions.id` (×2), `DROP CONSTRAINT project_links_…_key`, `DROP COLUMN user_account_profile.{onboarded, onboarded_at, discovery_project_id}` |
| 2026-07-03 | `DROP COLUMN orgs.default_tools`, `org_groups.default_tools` |
| 2026-07-08 | `DROP COLUMN users.{access_status, invite_quota, invited_by, access_granted_at, referral_code}` |
| 2026-07-10 | `DROP COLUMN user_datastores.{sub, spreadsheet_id, owner_email}`, `ALTER COLUMN org_instruction{,_revision}s.owner_id`, `DROP CONSTRAINT org_instruction{,_revision}s_pkey` |
| 2026-07-20 | `ALTER COLUMN doc_change_requests.doc_id`, `ADD CONSTRAINT dcr_target` |
| (dynamique) | `DROP COLUMN users.{col}` généré en boucle |

Tous ont plus d'un mois. Aucun ne peut être supprimé sans la vérification de §2.5,
et pour les `DROP` la vérification est inverse (la colonne doit être **absente**
partout).

### 2.7 Deux défauts constatés au passage, **non corrigés**

Relevés pendant l'inventaire ; ils ne relèvent pas du lot de découpe, qui était un
déplacement pur. Ils sont notés ici pour ne pas se perdre.

1. **`migrate_business_key_indexes()` ne tourne jamais.** Elle est appelée à la
   dernière ligne d'`init_db`, *après* la boucle de retry — dont le corps `return`
   en cas de succès et `raise` à la dernière tentative. Aucun chemin n'atteint donc
   l'appel, et `attempts` ne peut pas valoir 0 (`max(1, …)`). **Vérifié
   empiriquement** : `init_db()` instrumenté sur un PostgreSQL jetable ne l'appelle
   pas. Le commentaire au-dessus explique soigneusement pourquoi elle doit vivre
   hors transaction (`CREATE INDEX CONCURRENTLY`, #318) — l'intention est claire,
   c'est le placement qui est faux. Impact : la matérialisation des index de clé
   métier ne s'est jamais faite au boot ; `_ensure_datastore_key_indexes()`, elle,
   tourne bien et couvre un besoin voisin, ce qui explique que ça ne se soit pas vu.
2. **`idx_doc_change_requests_doc` est posé loin de sa table.** L'index vit à la fin
   du bloc des embeddings, entre `datastore_row_embeddings` et `project_activity`,
   alors que `doc_change_requests` est déclarée ~90 lignes plus haut. Sans
   conséquence à l'exécution (l'ordre reste valide), mais c'est une trace de
   sédimentation : la découpe par domaine l'a laissé au début du fragment
   `projects.PROJECT_FILES` plutôt que de le déplacer, le lot étant un déplacement
   pur.

## 3. La contrainte que toute solution doit respecter

**Prod et preprod partagent LA MÊME base** (`docs/live-migrations.md`). Ce fait ne
change avec aucune des trois options, et il en découle trois invariants :

1. **La « danse en N lots » survit intacte.** Un DDL destructif reste découpé en
   lots promus séparément, chacun ne détruisant que ce que le code prod *courant*
   ne référence plus. Versionner les migrations ne rend pas les `DROP` sûrs — ça ne
   fait que les jouer une seule fois.
2. **Migrer « avant le restart » migre aussi la prod.** Le script de deploy de la
   preprod tourne sur la base de la prod. La seule fenêtre de test reste le
   décalage entre les deux redémarrages, comme aujourd'hui.
3. **Un rollback ne rembobine pas le DDL.** `oto-backend.sh` rollback en
   redéployant le tag précédent ; la base, elle, reste migrée. C'est déjà vrai, et
   c'est ce qui rend la porte de version de l'option A coûteuse (§4.1).

## 4. Trois options

### 4.1 Option A — le deploy migre, le boot vérifie

Un répertoire `oto_mcp/db/migrations/NNNN_<slug>.sql|py`, une table
`schema_migrations(version, applied_at, checksum)`, un point d'entrée
`python -m oto_mcp.db.migrate` que `oto-backend.sh` appelle **avant** le restart.
Le boot ne migre plus du tout : il lit la version courante, la compare à celle
qu'attend le code, et **refuse de démarrer** si elle est en retard.

- **Ce que ça donne.** Le travail de migration sort de la fenêtre de healthcheck
  (le smoke ne mesure plus que le démarrage applicatif) ; un fichier par migration
  rend le domaine lisible et la revue possible ; l'ordre est explicite et gelé ; le
  boot redevient O(1). C'est la seule option qui donne aussi un endroit naturel où
  reloger les travaux périodiques de §1.1.
- **Risque n° 1, sévère : la porte de version transforme un rollback automatique en
  panne dure.** Si la migration `N` passe et que le smoke échoue, le rollback
  redéploie le code `N-1` — qui refuse alors de démarrer, la base étant en version
  `N`. Aujourd'hui ce cas dégrade en « le vieux code tourne sur une base en
  avance », ce qui **marche** tant que les migrations sont additives. La porte
  échange une classe de bug silencieux contre une classe de panne bruyante ; c'est
  peut-être le bon échange, mais c'en est un, et il se paie le jour d'un incident.
- **Risque n° 2 : la migration sort de la protection de l'advisory lock du boot.**
  Il faut le reprendre dans le migrateur, sinon deux deploys concurrents (prod +
  preprod) exécutent du DDL en parallèle sur la même base — le `DeadlockDetected`
  déjà vécu, mais sans le retry qui l'absorbe aujourd'hui.
- **Risque n° 3 : le script de deploy vit hors dépôt** (`/opt/deploy/oto-backend.sh`,
  sudo NOPASSWD). L'option A le modifie sur la box, pour prod **et** preprod, et ce
  changement n'est ni revu ni testé par la CI. C'est le maillon le moins observable
  de la chaîne, et l'option en fait le maillon critique.
- **Coût d'entrée** : poser le ledger sur l'existant (marquer les 142 ordres comme
  déjà appliqués sans les rejouer), sinon le premier `migrate` réexécute tout.

### 4.2 Option B — le boot migre encore, mais seulement ce qui n'a jamais tourné

Même répertoire de migrations et même table `schema_migrations`, mais le migrateur
reste **dans `init_db`**, en tête de boot. La chaîne de déploiement ne change pas
d'une ligne : c'est un changement interne au backend.

- **Ce que ça donne.** Le boot passe de 297 ordres à *zéro* en régime stable (une
  lecture de `schema_migrations`), et à `k` ordres le jour où un lot ajoute `k`
  migrations. Les backfills de §2.4 cessent de rebalayer les tables à chaque
  redémarrage. `_init.py` devient découpable par domaine *pour de vrai*, puisque
  chaque migration devient un fichier autonome et daté — ce que §0 dit impossible
  aujourd'hui.
- **Risque n° 1 : la fenêtre de healthcheck reste sur le chemin.** Une migration
  lourde la consomme toujours, exactement comme aujourd'hui, et les quatre travaux
  de maintenance de §1.1 ne sont pas concernés du tout. L'option améliore le cas
  nominal (redémarrage sans nouveau code) et ne change rien au pire cas — qui est
  celui qui rollback.
- **Risque n° 2 : perte de l'auto-réparation.** Le régime actuel est brutal mais
  auto-cicatrisant : quel que soit l'état d'une base, un boot la ramène à l'état
  attendu. Avec un ledger, une base dont le ledger ment (restauration partielle,
  ligne semée à tort) reste cassée et le boot ne la répare plus. Une commande de
  **re-vérification** (rejouer les ordres idempotents en mode audit) devient une
  pièce nécessaire, pas un confort.
- **Risque n° 3 : deux boots concurrents** — couvert tel quel par l'advisory lock
  existant, à condition que lecture et écriture du ledger restent **dans la même
  transaction** que les migrations. Seul point d'attention technique, et il est
  petit.
- **Coût d'entrée** : identique à A (poser le ledger sur l'existant), sans toucher
  au script de deploy ni à la CI.

### 4.3 Option C — deux étages : DDL déclaratif au boot, one-shots versionnés hors boot

On assume que le mécanisme actuel a une vraie qualité — il est **déclaratif** et
auto-réparateur — et on ne sort du boot **que ce qui n'a rien à y faire** : les
ordres qui touchent des données (§2.4), les 30 non additifs (§2.6) et les quatre
travaux de maintenance (§1.1). Les 106 `ADD COLUMN IF NOT EXISTS` restent au boot,
idempotents et bornés.

- **Ce que ça donne.** Le poste dangereux — celui dont le coût suit la taille de la
  base — quitte la fenêtre de healthcheck ; le poste inoffensif garde son
  auto-réparation et son ergonomie (ajouter une colonne reste une ligne). C'est
  aussi l'option qui colle le mieux à la danse en N lots : un lot destructif EST
  déjà un acte manuel séquencé, le versionner ne fait que lui donner un nom, une
  trace et une garantie de non-rejeu. Et c'est la seule qui traite explicitement la
  rétention du journal comme ce qu'elle est : un travail périodique, pas une
  migration.
- **Risque n° 1 : deux mécanismes coexistent**, donc une frontière à tenir — « ceci
  va au boot, cela va en migration ». Une frontière de ce genre dérive dès qu'elle
  n'est pas mécanisée : il faut un garde-fou CI qui refuse un `UPDATE`/`DELETE`/
  `DROP`/`RENAME` dans `_init.py`, sinon la règle ne survit pas à trois lots.
- **Risque n° 2 : ça ne range pas `_init.py`.** Les 106 `ADD COLUMN` restants sont
  toujours chronologiques et toujours dans une fonction unique — moins de la moitié
  du fichier disparaît. Si l'objectif est la lisibilité autant que le boot, cette
  option ne le sert qu'à moitié.
- **Risque n° 3 : la migration hors boot doit être rejouable à la main** (un lot de
  la danse en N lots se pilote entre deux promotions). Il faut donc une commande,
  sa doc et ses droits sur la box — l'outillage de l'option A, mais pour ~55 ordres
  au lieu de 142.
- **Coût d'entrée** : le plus faible des trois pour le gain sur la fenêtre ; le plus
  élevé en discipline continue.

## 5. Ce que le choix devra trancher

Aucune de ces questions n'a de réponse évidente, et chacune décide en partie de
l'option :

1. **Que doit-il se passer quand le code est en retard sur la base ?** Refuser de
   démarrer (sûr, mais casse le rollback automatique) ou continuer (souple, mais
   c'est le bug silencieux d'aujourd'hui). C'est la question la plus structurante :
   elle sépare A de B et C.
2. **Qu'est-ce qui répare une base dont le ledger ment ?** Le régime actuel n'a pas
   besoin de réponse ; les trois options en ont besoin.
3. **Les travaux périodiques (§1.1) restent-ils au boot ?** Ils n'y ont leur place
   dans aucune des trois lectures, et ils sont la première cause probable d'un
   dépassement futur de la fenêtre. C'est peut-être le lot à faire en premier,
   indépendamment du régime de migration.
4. **Le script de deploy hors dépôt entre-t-il dans le périmètre ?** Sans lui,
   l'option A est impossible ; avec lui, une partie de la chaîne de production
   devient revue et testée — ce qui est souhaitable indépendamment.
5. **Les 79 ALTER inertes : on les retire quand ?** Lot à part entière, sans rapport
   avec le choix de régime, et qui vaut la peine dans les trois cas.
6. **Que fait-on des 6 ordres hors forme** (générés en boucle, `ADD PRIMARY KEY`
   gardés en Python) ? Ils ne se convertissent pas mécaniquement en fichiers SQL.

## 6. Références

- `docs/live-migrations.md` — la danse en N lots, les techniques et les pièges déjà
  payés sur la base partagée. **À lire avant toute migration destructive**, quelle
  que soit l'option retenue.
- `oto_mcp/db/_schema.py` — le DDL déclaratif, assemblé par domaine depuis le
  2026-08-27 (`db/schema/<domaine>.py`), gelé par
  `tests/test_schema_assembly_frozen.py`.
- `oto_mcp/db/_init.py` — l'objet de cette note.
- ADR 0020 (stratégie de release) et CLAUDE.md racine §Déploiement — le modèle
  tronc unique et la fenêtre de healthcheck.
