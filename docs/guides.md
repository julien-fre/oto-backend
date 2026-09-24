---
title: Guides & instructions d'org
type: reference
description: >-
  Référence du mécanisme de guide oto-backend : prose opératoire métier par org,
  structurée en skills identifiés par slug et versionnés dans org_instructions +
  org_instruction_revisions. Détaille la surface (consolidée en `oto_procedure`, ADR 0047 — op=get sans slug =
  call de début de session renvoyant base + index, avec slug = skill nommé ;
  op=set/list/delete), l'autz conditionnelle
  org_admin self-service vs platform_admin cross-org, le versioning append-only avec
  revert via from_version, et les gotchas (verrou advisory par org/slug, pas de cache,
  pas d'instruction par namespace d'outil). Aligne sur ADR 0006 (harnais sans état).
adr:
  - "0006"
---

# Guides & instructions d'org

> ⚠️ **« doctrine » = « guide » depuis le 28/08/2026** (#519) : le mot a disparu de l'interne du backend (modules, symboles, prose). Les noms SERVIS qui le portent encore se **doublent** au lot B — le nouveau nom naît, l'ancien reste servi avec une date de retrait écrite (premier tag à partir du 29/10/2026 ; retrait = lot D, #526). **Table unique de ces alias : [`alias-deprecies.md`](alias-deprecies.md).** Déjà fait : l'outil s'appelle `oto_admin_guide` (son ancien nom répond encore).

Prose opératoire métier (workflows validés, règles, vocabulaire) pour les users qui pilotent
oto **sans produit applicatif dédié** (ex. un process avoir compta client
GoCardless → Pennylane → back-office, piloté directement depuis Claude sur un sous-ensemble
de tools). oto est la maison naturelle de cette prose faute de produit. Aligné
**ADR 0006** (harnais-vs-substrat, repo public `otomata-tech/oto`) : une org oto + sa
guide = un **harnais sans état** (étage zéro) ; le jour où un workflow doit persister un
pipeline/des statuts, il graduate en harnais à part.

**Modèle = skills, à la Claude Code.** Une org possède des **instructions markdown**
identifiées par `slug`, chacune versionnée :
- Le **guide de base** (slug réservé **interne** `BASE_SLUG`, jamais vu de l'user) est servi
  d'office — accédée via `oto_procedure(op='get')` **sans slug**.
- Les autres slugs = des **skills** chargés à la demande (progressive disclosure) : la
  guide de base ne porte que l'**index** (slug + titre + quand-l'utiliser), le détail
  se charge au besoin.

**Surface = 4 tools** (refacto 2026-06-18, ex-11 ; « moins d'outils, plus d'args »). Un `org_id`
optionnel **fond membre↔platform-admin** : absent = ton **org active** ; présent = une **autre org**
par id (réservé platform_admin). Autz conditionnelle dans `tools/orgs.py`
(`_resolve_org_read`/`_resolve_org_write`).
- **Lecture** : `oto_procedure(op='get'[, slug, scope, version, with_history, full])` — sans `slug` =
  `{doctrine, group_doctrine, doctrines[]}` (base org + base groupe + index), le call de **DÉBUT DE
  SESSION** ; avec `slug` = le markdown d'un guide nommé — **sur la face MCP, sans son dessin ni
  sa description** (§ « Ce que l'agent lit »), `full=true` rend tout. `oto_procedure(op='list'[,
  query, scope, verbose])` = catalogue/recherche — face MCP, la description est un `summary` de
  200 caractères, `verbose=true` la rend entière. Scopés à l'**org active** (+ groupe actif) — servis aux seuls
  membres. **Vide sans erreur** si pas d'org active (`_SERVER_INSTRUCTIONS` invite à `oto_procedure(op='get')`).
- **Écriture** : `oto_procedure(op='set'[, body_md, slug, scope, org, group, title, desc,
  from_version])` (base = slug omis ; nommée sinon ; `from_version` = revert) +
  `oto_procedure(op='delete', slug[, scope, org, group])`. Autz **par PALIER** (`scope`,
  #681 — 31/08/2026) :
  - `scope='user'` (**défaut** depuis l'ADR 0068, 04/09/2026) : procédure PERSONNELLE,
    visible de toi seul, aucun droit d'org requis. ⚠️ Cette ligne annonçait `org` par
    défaut alors que le code rendait déjà `user` (`_ECRIT_SCOPE`) : un agent y lisait
    qu'il écrivait pour l'équipe en écrivant pour lui — l'objet introuvable, pas la
    fuite, mais la même journée perdue (corrigé le 08/09/2026, texte servi compris) ;
  - `scope='org'` : `org` absent → org active, **org_admin** ; présent → autre org,
    **platform_admin** (l'opérateur provisionne n'importe quelle org) ;
  - `scope='group'` : `group` absent → équipe active, présent → l'équipe nommée ; **chef
    d'équipe** requis (escalade `roles.can_admin_group` : org_admin parent, platform_admin).

  ⚠️ **Pourquoi ce second palier existe** : celui qui DÉROULE une procédure est un opérateur
  métier, et le seul qui pouvait l'écrire était un administrateur d'org. Améliorer son propre
  mode d'emploi supposait donc les clés de toute l'organisation — membres, connecteurs,
  secrets — que personne n'accorde pour ça ; la boucle d'auto-amélioration que la procédure
  promet ne se fermait jamais.

  ⚠️ **La garde suit le VERBE, pas la surface** (corrigé le 01/09/2026, avant fusion de
  #695) : au palier équipe, `set` demande d'être **membre** de l'équipe, `delete` d'en être
  le **chef**. La première rédaction du lot gardait l'écriture sur « chef d'équipe » et ça
  s'est payé tout de suite : pour laisser une opératrice annoter le mode d'emploi qu'elle
  déroulait, il a fallu la faire cheffe de son équipe — un rôle qui emporte les **clés
  partagées** de l'équipe. Une garde d'écriture trop grossière force une élévation de droits
  dans un domaine sans rapport. Ce qui rend l'ouverture tenable est que l'écriture est
  **réversible** (une version de plus, `from_version` restaure) alors que la suppression
  emporte l'historique sans corbeille.

  Les faces REST restent **une route par palier** : `/api/me/instructions*` (org_admin de
  l'org active) et `/api/groups/{id}/instructions*` (membre pour écrire et restaurer, chef
  pour supprimer — **même partage que la console**, sinon « qui peut annoter » deviendrait
  une propriété du transport). `scope`/`group` sont des axes de la CONSOLE MCP seulement —
  les publier dans le corps d'une route qui les refuserait décrirait une porte qui n'existe
  pas.

  **Deux gardes ⟹ deux droits SERVIS** (01/09/2026, suite de #695). `can_edit` est resté
  le droit d'ADMINISTRER (readme d'équipe, membres, secrets, suppression) et rendait
  `false` à une membre qui avait pourtant le droit d'écrire : une porte fermée à tort.
  L'élargir en aurait ouvert une autre — le bouton de suppression, que le serveur refuse.
  Le sens s'est donc **dédoublé** : `can_write_instructions` (écrire/restaurer) et
  `can_delete_instructions` (supprimer) sont servis **à côté** de `can_edit`, dont ni la
  valeur ni le sens ne bougent. Mêmes deux noms sur les **deux** bundles de la famille
  (`GET /api/groups/{id}/instructions` et `GET /api/me/instructions`) : les servir d'un
  seul côté remettrait « qui peut annoter » dans les mains de la page.

  ⚠️ **Le drapeau et le refus sont la MÊME fonction.** Chaque droit annoncé NOMME la
  capacité dont il rend la règle (`_DROITS_SERVIS`), et le bundle exécute cette règle
  d'autz déclarée — `_authz.capacite_autorise`, qui ne lance jamais le handler. Aucun
  critère n'est recopié dans le handler du bundle : déplacer une garde déplace son
  drapeau avec elle. C'est le défaut d'origine, et il ne se reconstruit pas un cran plus
  loin. Cliquets : `tests/test_droits_procedure_servis_695.py` (le drapeau vaut ce que
  fait la règle, pour chaque acteur ; et chaque nom de drapeau doit nommer la capacité
  de SON verbe — sans quoi une table qui se trompe de capacité resterait cohérente avec
  elle-même).
- **Versioning** : chaque écriture incrémente `version` (sur le courant) et archive un snapshot
  append-only. Revert = re-poser le corps d'une version → nouvelle version (jamais d'effacement
  d'historique sauf `delete`).
- **Renommer** (issue `oto`#261) : `oto_procedure(op='rename', slug, new_slug[, scope, org,
  group])` + `POST /api/me/instructions/{slug}/rename` (palier org). Le slug change, l'IDENTITÉ
  reste : l'`id` stable (`guide_id`, que les liens de projet, les partages et le nœud dérivé
  désignent), la version (renommer n'est pas écrire le contenu) et l'historique entier, qui
  suit sous le nouveau nom dans la même transaction (`org_store.rename_instruction`). Même
  garde que `set` — un membre renomme la procédure de son équipe : rien n'est détruit, et le
  geste se défait en renommant de nouveau. **Pas d'alias** : l'ancien slug ne résout plus et
  redevient libre ; une prose qui le cite (autre procédure, readme) est à reprendre à la main,
  la réponse le rappelle. Refus, rien de changé : `new_slug` pris (409 `slug_taken`).
  **Le runner suit**, dans la même transaction : `runner_triggers.procedure`,
  `runner_fleets.procedure` et la charge des travaux en attente (`runner_jobs.payload`,
  `pending`/`held`) portent le SLUG, pas l'id — ils sont repointés, et leur instruction de
  départ réécrite là où elle cite `` `ancien` `` (la forme que `_instruction` dérive). Seules
  suivent les lignes pour lesquelles l'ancien slug résolvait vers CETTE procédure (cascade
  de lecture de l'agent : sa procédure personnelle d'abord, l'org, puis l'équipe). Un
  travail déjà pris (`claimed`) tourne avec ce qu'il a lu : nommé (`in_flight_jobs`),
  jamais réécrit ; une instruction libre qui cite encore l'ancien nom est nommée
  (`inputs_to_review`). ⚠️ La procédure d'une campagne est figée à sa déclaration pour
  que l'attribution des lignes reste vraie : le gel porte sur l'OBJET, que le renommage
  ne change pas (même id, même contenu, même version). La lecture par slug rend désormais `guide_id` : sans
  lui, l'identité qu'on préserve était illisible.
- **Store** : `org_instructions(owner_type, owner_id, slug, org_id, title, description,
  body_md, slots, version, set_by, archived_at, created_at, updated_at)` +
  `org_instruction_revisions(owner_type, owner_id, slug, version PK, …)` (`db/schema/procedures.py`).
  ⚠️ **UN seul jeu de fonctions**, keyé sur `(owner_type, owner_id)` — la clé d'unicité que la
  table porte, sur la table ET sur ses révisions : `org_store.<fn>('org'|'group', id, …)`
  (`org_store/instructions.py`). `org_id` reste la colonne dénormalisée de l'org PARENTE (FK,
  NOT NULL, cascade de suppression) : org et équipe en ont toutes deux une.

  ⚠️ **Il en a existé DEUX jusqu'au 31/08/2026** (#681) : celui-ci filtrait `owner_type='org'`
  en dur, `group_store` filtrait `owner_type='group'` en dur, sur la MÊME table — et ils avaient
  déjà divergé (le palier équipe écrivait `slots='[]'` en dur, ne relisait pas les slots,
  ignorait l'archivage). Ajouter un palier par la même méthode en aurait fait un troisième :
  **le propriétaire est une DIMENSION, pas trois cas particuliers.** Le palier `user` est
  OUVERT depuis l'ADR 0068 (04/09/2026) — `OWNER_TYPES = ("org","group","user")`, `org_id`
  rendu nullable. ⚠️ Cette ligne a annoncé le contraire jusqu'au 09/09/2026, alors que la
  ligne 50 de cette même page disait déjà l'inverse : une page qui se contredit ne se lit
  pas, elle se cite au hasard.

  **En clair** (prose, pas un credential → hors coffre chiffré). **Pas de cache** : lecture DB
  à l'appel. Écriture sérialisée par `(owner_type, owner_id, slug)` via verrou advisory.
- **Pas d'instruction par namespace d'outil** : un gotcha d'outil est vrai pour tout le monde et
  évolue avec le code du connecteur → sa place reste le repo (docstring, `_SERVER_INSTRUCTIONS`),
  versionné avec l'outil.

## Le dessin n'est plus exigé (retiré le 18/09/2026)

⚠️ **Une procédure n'a plus à porter de dessin.** Il avait été rendu obligatoire le
23/08/2026 (b34af1cc) pour un besoin d'affichage d'un front partenaire, qui faisait du
dessin la vue par défaut de sa page de procédure. Une règle de RENDU d'un seul
consommateur était devenue une règle du cœur, servie à tous les agents de toutes les
orgs : dans la description de `oto_procedure`, celle de l'écriture d'org, le socle de
session, le guide `notice` et un guide plateforme dédié (`procedure-flowchart`) — et
figée par des tests qui vérifiaient la présence de chaque renvoi. Décision d'Alexis :
une procédure se lit en prose, en étapes numérotées ; c'est ce que l'agent qui
l'exécute lit, et le dessin lui coûtait des jetons sans rien lui apprendre. Un front qui
veut un dessin le demande au niveau de SON TENANT — et bientôt de sa propre instance
(ADR 0070) —, jamais du cœur ni de chaque org.

Ce qui part : l'avertissement « aucun dessin » (`diagram_check` rend `None` sans
dessin), les quatre textes qui le réclamaient, le guide plateforme `procedure-flowchart`
(fichier seed retiré ; ⚠️ le nœud déjà semé en base se retire à part, par
`oto_guide op=delete scope=platform`), et les tests qui figeaient ces renvois — remplacés
par un cliquet qui refuse leur retour.

Ce qui reste, pour les procédures qui ont DÉJÀ un dessin : il s'affiche comme avant, il
est servi à l'agent sous la forme d'un marqueur et remis à l'écriture (section
suivante), et `diagram_warning` parle encore d'un dessin PRÉSENT — deux dessins dans un
corps (la page n'en rend qu'un), ou un tracé que le parseur du front refusera (lint
ligne à ligne, `procedure_diagram.lint_du_trace`).

⚠️ Le « Self-improvement digest », imposé par le même commit, avait déjà été retiré le
10/09/2026 (oto#159) pour la même raison : la plateforme portait déjà ce qu'il racontait
(version, historique des versions, documents de projet).

## Ce que l'agent lit : la consigne, pas la vitrine

Une procédure est relue à chaque run, et tant qu'elle reste dans le contexte, chaque
jeton lu est repayé à chaque tour. Mesuré le 10/09/2026 sur trois procédures d'une org
cliente (≈100 000 caractères servis par run, ≈27 000 jetons) : le **dessin** pèse ~10 %
de la lecture (3 304 caractères = 983 jetons Haiku — la prose fait 3,6 caractères par
jeton, le tracé 3,4, donc c'est sa taille qui coûte, pas ses caractères), la
**description** recopiée à côté du corps ~5 %. Ni l'un ni l'autre n'apprend rien à
l'agent qui EXÉCUTE : le dessin est la vue de la page (un humain le regarde, les
étapes disent le même flux en prose), la description est la ligne du catalogue (il l'a
lue pour choisir).

Sur la **face MCP seulement** (`ctx.channel == "mcp"` — la face REST nourrit la page,
qui a besoin du dessin ; un appel interne sert tout), et **par défaut** (une économie
qu'il faut demander ne bénéficie à personne) :

- `op=get` sert le corps avec le dessin remplacé par **une ligne**, un marqueur
  `<!-- flowchart: v<n>, <k> lines, … -->`, et sans `description`. `full=true` rend tout.
- `op=list` sert un `summary` de 200 caractères (coupé au dernier espace) à la place de
  `description`, sans `updated_at`. `verbose=true` rend la fiche entière.

⚠️ **Le marqueur n'est pas un commentaire, c'est ce qui garde le dessin.** L'agent qui
édite RELIT puis RÉÉCRIT (`op=get` → `op=set`) ; servi sans dessin et sans marqueur,
chaque édition d'agent viderait la page du process — et personne ne le verrait avant
de l'ouvrir. À l'écriture, `procedure_diagram.avec_le_dessin` remplace le marqueur par
le dessin de la **version courante** ; un corps qui arrive avec un vrai dessin le garde ;
un corps sans marqueur ni dessin s'écrit tel quel, ce qui est permis. Les
corps **stockés** ne portent jamais le marqueur — il ne vit qu'entre les deux appels.
Banc : `tests/test_procedure_servie_lean.py`, dont l'aller-retour à l'identique.

⚠️ **Ce que le marqueur ne promet pas.** Il a d'abord dit « keep this line and op=set
keeps the drawing », sans condition — et `avec_le_dessin` relit le corps courant de la
**ligne visée par l'écriture**. Une écriture qui vise ailleurs n'a rien à relire, et le
marqueur s'efface : `op=create`, un slug neuf, et surtout un **`scope` omis** — le
défaut d'écriture de `oto_procedure` est `user` (`_ECRIT_SCOPE`), donc relire l'org et
réécrire sans `scope` publie chez soi une procédure sans son dessin. La ligne servie dit
désormais « same slug and scope you read ». La perte n'est pas silencieuse
(`diagram_warning`), mais elle est constatée APRÈS.

⚠️ **Marqueur + vrai dessin dans le même corps = DEUX blocs dessinants.** La page n'en
rend qu'un, le premier ; `has_diagram` répondait « oui, il y a un dessin » et se taisait.
`procedure_diagram.compter_les_dessins` les compte, et `diagram_check` le dit
(`DOUBLE`) : c'est le seul cas où la perte était muette.

Ce que ça ne fait **pas** : servir la procédure « par étape ». Découper la lecture en
une lecture par étape échange la résidence (payée au tarif du cache) contre des tours
supplémentaires (chacun relit tout le contexte) — mesuré sur ces trois procédures, le
run y perd 10 à 20 %, et les règles transversales (le filtre sur la société, la liste
blanche des champs, la borne de 255 caractères) vivent hors des étapes. La seule
économie qui tienne est de servir MOINS, sur les blocs que la forme délimite.

## L'écriture rend l'empreinte du corps stocké (oto#133, 24/09/2026)

Publier un corps ne rendait rien qui prouve que la base avait gardé ce texte-là : il
fallait relire (`op=get`) et comparer à la main — contrôle qui a trouvé dix-huit lignes
servies aux agents qu'aucun fichier source ne contenait. `op=set`/`op=create` (et leurs
faces REST, `PUT /api/groups/{id}/instructions/{slug}` compris) rendent désormais
**`body_sha256`** : le SHA-256 hex des octets UTF-8 du corps **relu en base pour la
version écrite** (`procedure_empreinte.empreinte_check`, sur la révision — pas la ligne
vivante, qu'une édition concurrente a pu déjà remplacer). Jamais l'empreinte de l'envoi :
c'est l'écart entre les deux qu'on veut voir.

⚠️ Le corps stocké n'est pas l'envoi à l'octet près : blancs de tête et de fin retirés,
outils cités sous un nom de produit ramenés au canonique, dessin remis à la place du
marqueur. Comparer donc au SHA-256 de l'envoi **stripé** (`sha256sum` d'un fichier qui
finit par `\n` ne correspondra jamais) ; un écart qui subsiste dit que la base porte autre
chose que ce qu'on croit avoir publié.

## Renommer un outil = migrer les procédures

Une procédure référence ses outils par `<tool:slug>` (ADR 0014), et ces refs vivent **en DB, par
org** — hors du repo. Un renommage d'outil est donc un breaking qui traverse le **code ET les
données**, dont le CI ne voit que la moitié : `test_tools_client_methods_exist` garde le skew
tool↔oto-core, `connector_docs/<nom>.md` se relit en PR, mais **rien ne lit `org_instructions`**. Une
suite verte ne dit donc rien de l'état des procédures.

Vécu le 2026-07-31 (consolidation pennylane 25→9 outils, v1.38.0, ADR 0047 étendu aux
connecteurs) : `rapprochement-pennylane` (org maison, qui arme une routine planifiée quotidienne) et
`agent-avoirs-compta` (une org cliente, agent sous supervision) sont parties **en prod** avec
respectivement 2 et 10 refs mortes, réparées seulement après coup.

Le détecteur, lui, existe déjà : `tool_registry.manifest_for(body_md)` rend
`referenced_tools[].status` et `unresolved_tools` — c'est ce que `oto_procedure(op='get')` et le
retour d'`op='set'` affichent. La migration est donc mécanique : balayer les orgs, réécrire le
corps, vérifier `unresolved_tools == []`. ⚠️ Vérifier contre le serveur qui porte DÉJÀ la nouvelle
surface — tant que le tag n'est pas en prod, les anciens noms y résolvent encore et le contrôle
est faussement vert. **Aucun garde-fou automatique à ce jour** : la migration reste à la charge
de qui renomme.

⚠️ **À ne pas confondre avec le préfixe d'outils d'un tenant** (`tenants.tool_prefix`,
`oto_mcp/tool_alias.py`) : celui-là n'est PAS un renommage. C'est une traduction posée au bord du
protocole — `oto_doc` devient `acme_doc` dans le `tools/list` servi, et redevient `oto_doc`
avant que quoi que ce soit d'autre ne le lise. Les refs `<tool:slug>` restent donc écrites en
canonique, continuent de résoudre, et **il n'y a rien à migrer**. Les deux formes sont d'ailleurs
acceptées à l'appel, précisément pour que la prose déjà écrite aboutisse.

## Le semis des guides plateforme : le dépôt atteint la base

Un guide plateforme (`oto_mcp/guides/<slug>.md`) est un **texte servi à l'agent**.
Servi périmé, il lui fait appliquer des gestes retirés et ignorer ceux qui existent.
Mesuré le 13/09/2026 : le guide `datastore-semantics` servi en production avait
**environ 250 lignes de retard** sur le fichier du dépôt, depuis plusieurs livraisons —
il ne connaissait ni `@clear` ni le paramètre de lecture `empties`, et présentait
encore comme active une forme retirée. Cause : le semis de démarrage n'**insérait**
que les slugs absents (`INSERT … ON CONFLICT DO NOTHING`), donc **une mise à jour d'un
guide dans le dépôt n'atteignait jamais un environnement existant**.

**Contrat (validé le 13/09/2026, livré le 23/09/2026 — otomata-tech/oto#236), sans
écart à l'ADR 0042 :**

1. **Qui fait foi.** La base reste la source **éditable** d'un guide plateforme
   (`oto_admin_guide` / `oto_guide` n'y perdent aucun droit) ; le fichier du dépôt est
   la source du **semis**.
2. **Le semis empreinte ce qu'il pose.** `props->>'seed_sha256'` = l'empreinte des
   trois champs écrits (titre, description, corps — `db.empreinte_de_couche`). Au
   démarrage (`guide_store.seed_platform_guides`), pour chaque fichier :
   - fichier changé **et** base encore au dernier semis → le guide est **mis à jour** ;
   - base éditée depuis le dernier semis → le guide est **conservé**, la divergence est
     **signalée** ;
   - même empreinte → **aucune écriture**.

   Décision et écriture dans la même transaction, ligne verrouillée (`FOR UPDATE`) :
   une édition admin concurrente ne se glisse pas entre le constat et l'écrasement.
3. **Aucun refus au démarrage.** Le boot reste en DDL additif et échec ouvert, et la
   fenêtre du healthcheck est finie : un refus empêcherait toute bascule de version.
   Un semis en échec ou un guide divergent est un **défaut de santé d'instance**,
   servi par `oto_admin_guides_semis` (`capabilities/guides_semis.py`, PLATFORM_ADMIN,
   lecture seule) — codes `guides_non_semes`, `guide_divergent`,
   `guides_sans_empreinte`, `semis_absent` — **plus une remontée Sentry** (tag
   `oto.semis_guides`, même canal que `tenancy._refus`). Le témoin est le défaut, pas
   le refus. ⚠️ Le rapport est celui **du process** qui a semé : `fait: false` ne dit
   pas « tout va bien », il dit « ce serveur n'a rien semé ».
4. **Les lignes d'avant l'empreinte** (toute la population de production au 23/09) ne
   sont **jamais devinées au démarrage** : le semis ne saurait pas distinguer « jamais
   touché » de « réécrit par un admin ». Elles sont alignées par un **geste unique**,
   `scripts/aligner_guides_plateforme.py` : il énumère la population (fichiers ∪ base),
   **montre chaque écart**, puis écrit — et seulement avec `--aligner`.
5. **Un guide plateforme servi sans fichier dans le dépôt** (cas mesuré :
   `procedure-en-routine`) est **exporté en fichier** par ce même geste, et ce fichier
   devient sa source de semis. Aucun guide plateforme ne reste servi sans source
   versionnée : sans fichier, il n'est relu par personne et le prochain environnement
   ne l'a pas. ⚠️ Le fichier exporté est à **committer**.
6. **Les sous-dossiers de `oto_mcp/guides/` ne sont pas semés** et le semis n'y descend
   pas (`glob("*.md")`, NON récursif) : la frontière est tenue comme une propriété par
   `tests/test_guides_seeds_foyer.py`. Un fichier posé à la racine par habitude, ou un
   `rglob` « de propreté », sèmerait en production des guides que personne n'a décidés.
7. **Bancs** : `tests/test_semis_guides_plateforme.py` — le rapport et ses défauts sans
   base, puis sur base jetable base neuve, fichier modifié, base éditée, idempotence,
   rejeu du démarrage, et la durée mesurée dans la fenêtre du healthcheck (120 s).

## Détail accumulé (migré de la carte)

**Livraison au LLM = injection, plus un appel d'outil (otomata-private#49 puis #50, amende ADR 0014).**
Le canal de bootstrap = les `instructions` du `initialize` (FastMCP les relit par
session ; Claude rehandshake par conversation). ⚠️ **Cru « fiable » jusqu'au 2026-09-01,
ce canal ne l'est PAS** (#478, mesuré) : Claude Code coupe l'artefact composé à
**2 048 caractères** et claude.ai ne le transmet pas au modèle. Le bloc A est depuis un
**socle-résumé ≤ 2 000 c.** (budget cassant en CI, `tests/test_instructions_budget.py`)
qui pointe la version intégrale — le **guide plateforme `notice`**
(`oto_mcp/guides/notice.md`) — et `oto_context` ; les couches suivantes (catalogue,
bloc C) restent composées, mais seuls les clients qui ne tronquent pas les reçoivent.
`DynamicInstructionsMiddleware.on_initialize`
(`middleware/dynamic_instructions.py`) **remplace** `result.instructions` par `instructions.compose_session(sub, org_id)`
— un **artefact composé de 2 blocs** (`instructions.py`, #50 ; l'ex-bloc B onboarding a été
retiré le 2026-07-01 — l'onboarding est un projet, ADR 0032 §7) :
- **bloc A « secret sauce »** (posture + boucle d'usage + **catalogue de namespaces** dérivé) —
  prose en DB — une couche de contexte `init` de slug `secret_sauce` dans `nodes` (⚠️ plus
  la table `platform_instructions`, dont le backfill de boot est parti avec la table `guides`
  le 23/09/2026, oto#239 ; la surface d'administration lit et écrit `nodes` depuis le 28/07) —,
  éditable admin plateforme, **inviolable par l'org**, toujours injecté (la constante
  `_SECRET_SAUCE` reste le défaut ET le repli) ; le catalogue est appendé à la composition ;
- **bloc C « contexte dynamique »** par-(sub, org) — section de contexte résolu (org / équipe /
  connecteurs actifs / N derniers projets / derniers déroulés via `db.recent_runs` / fiche profil
  « situation avec oto » de l'user) + **agent readme cumulés** org → équipe active → user
  (`_format_org_readme`/`_format_group_readme`/`_format_user_readme`), chacun avec substitution
  `{{org}}`/`{{user}}`/`{{équipe}}`/`{{connecteurs_actifs}}`.

⚠️ Corrigé 2026-09-01 : « le guide est injecté, ne plus prescrire sa lecture au
démarrage » ne tient que pour les clients qui livrent l'artefact entier. Pour les
autres (Claude Code, claude.ai — #478), la lecture au démarrage EST le canal : le socle
prescrit `oto_guide op=read slug=notice` puis `oto_context`, et la description
d'`oto_context` (toujours livrée, elle) porte la même consigne.
Les **guides nommés (skills)** ne sont pas des outils → absents de `tools/list` → `on_list_tools`
**enrichit la description de `oto_procedure`** avec leur index per-**(compte, org, équipe
active)** (`instructions.skills_index_md`, Tool non-frozen → `model_copy`). ⚠️ Les TROIS
paliers depuis le 09/09/2026, marqués `[perso]` / `[équipe]` / sans marque pour l'org — cet
index ne lisait que l'org, alors que l'écriture sans `scope` va au palier personnel depuis
l'ADR 0068 : une procédure écrite à soi n'apparaissait ni ici ni dans le bundle de session
(`op=get` sans slug), donc dans RIEN de ce que l'agent reçoit sans le demander. Les deux
index et `op=list` doivent cumuler les mêmes paliers — garde
`tests/test_index_paliers_perso.py::test_les_deux_index_lisent_les_memes_paliers`. Coût
mesuré du cumul en production le 09/09 : +2 lignes / +154 caractères (+1 % sur l'index le
plus gros, +5 % sur un petit). Composé **hors boucle** (`run_in_threadpool`) : ces lectures
tournaient dans l'event loop. `render()` reste la surface STATIQUE (boot / fallback, sans DB).
Tout **fail-open** (pas de sub/org/guide/DB → surface statique). Édition des blocs A/B : capacité
`oto_admin_platform_instructions` (+ REST `/api/admin/platform-instructions`, `PLATFORM_ADMIN`) →
éditeur dashboard `/platform/instructions`. Transparence : `/api/me/agent-context` rend le même
artefact composé. **Reste (#54)** : anticipation **pilotée** (message proactif amorcé par l'admin).

**Slots de procédure (ADR 0035, B1–B3 déployés).** Une procédure déclare ses **entités
à instance** (quel tableau, quel compte de connecteur, quelle page Documents) en **JSON propre** :
colonne `org_instructions.slots` JSONB (`{name, type ∈ tableau|connecteur|doc,
description?, connector?}`), la prose les référence **par nom** via `<slot:name>` (même
famille que `<tool:slug>` 0014 ; le binding nom→instance vit dans le PROJET,
`project_links.slot` — vocabulaire DU projet, unicité `(project_id, slot)` → 409
`slot_taken` au link). Module `slots.py` = source unique (validation dure
`validate_slots`/`normalize_name` + check croisé non bloquant `slots_check` : refs
mortes, slots jamais cités, cohérence connecteurs déclarés ↔ refs `<tool:>`, suggestion
quand un connecteur à identités est référencé sans slot). Écriture : `oto_procedure(op='set')`/
`PUT /api/me/instructions/{slug}` (param `slots`, warnings en réponse) ; transport
revisions + revert + `copy_instruction_to_org` + publish/fork bibliothèque +
`duplicate_project`. **Runtime (B3)** : les tools `data_*` acceptent
`namespace='slot:<name>'` → `access.resolve_slot_tableau` résout contre les bindings du
**projet actif** ; pas de projet / slot non bindé / binding pendouillant = **McpError
actionnable, jamais de fallback** (bracelet serveur 0023) ; `data_create_namespace`
refuse le préfixe (un slot binde un tableau existant). Bloc A : §« Slots » (⚠️ prose
seedée en DB — une évolution du texte passe par `oto_admin_platform_instructions`, pas
seulement la constante). Grandfathering : procédure sans slots / nom nu = inchangés.
Restent B4 (inventaire dérivé) + B5 (vérifications) — épic otomata-private#59.

## Agent readme (cumulable) & procédures — le vocabulaire produit

Vocabulaire produit (unbundle 2026-07) : **agent readme** = prose libre **injectée à
chaque session**, cumulée du général au spécifique — **plateforme** (bloc A) → **org** →
**équipe active** → **user**. Les 4 étages vivent dans `nodes`, `delivery='init'` (0042 ;
la table `guides` est sortie du code le 23/09/2026, oto#239) ET
**s'éditent par UNE surface** depuis le 28/07 (§Convergence des surfaces) : la capacité
`me.guide{,s}` — `oto_guide(op=…, scope=…, delivery='init')` en MCP, `/api/me/guides/{scope}/readme`
(+ variantes `/api/{orgs,groups}/{id}/…` pour viser une cible explicite) en REST. ⚠️ Le
routage `claude_md`→`guides` qui vivait DANS `org_store`/`group_store` est RETIRÉ : le store
de procédures ne sert plus le readme (`get_instruction` → None, `set_instruction` → ValueError),
les appelants qui le veulent lisent `guide_store.init_guide_body(scope, id)`. `me.agent_readme` +
`/api/me/agent-readme` + `db.{get,set}_user_readme` supprimés (table `user_agent_readme` laissée
en place — plus aucun backfill ne la lit ; son DROP est une migration à part). Chaque niveau passe par `_apply_vars`
({{org}}/{{user}}/{{équipe}}/{{connecteurs_actifs}}). **Procédure** = guide nommé
(skill), chargé à la demande. Prose opératoire versionnée par org — le reste de ce
document en détaille le mécanisme.
