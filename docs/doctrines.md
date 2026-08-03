---
title: Doctrines & instructions d'org
type: reference
description: >-
  Référence du mécanisme de doctrine oto-backend : prose opératoire métier par org,
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

# Doctrines & instructions d'org

Prose opératoire métier (workflows validés, règles, vocabulaire) pour les users qui pilotent
oto **sans produit applicatif dédié** (ex. un process avoir compta client
GoCardless → Pennylane → back-office, piloté directement depuis Claude sur un sous-ensemble
de tools). oto est la maison naturelle de cette prose faute de produit. Aligné
**ADR 0006** (harnais-vs-substrat, repo public `otomata-tech/oto`) : une org oto + sa
doctrine = un **harnais sans état** (étage zéro) ; le jour où un workflow doit persister un
pipeline/des statuts, il graduate en harnais à part.

**Modèle = skills, à la Claude Code.** Une org possède des **instructions markdown**
identifiées par `slug`, chacune versionnée :
- La **doctrine de base** (slug réservé **interne** `BASE_SLUG`, jamais vu de l'user) est servie
  d'office — accédée via `oto_procedure(op='get')` **sans slug**.
- Les autres slugs = des **skills** chargés à la demande (progressive disclosure) : la
  doctrine de base ne porte que l'**index** (slug + titre + quand-l'utiliser), le détail
  se charge au besoin.

**Surface = 4 tools** (refacto 2026-06-18, ex-11 ; « moins d'outils, plus d'args »). Un `org_id`
optionnel **fond membre↔platform-admin** : absent = ton **org active** ; présent = une **autre org**
par id (réservé platform_admin). Autz conditionnelle dans `tools/orgs.py`
(`_resolve_org_read`/`_resolve_org_write`).
- **Lecture** : `oto_procedure(op='get'[, slug, scope, version, with_history])` — sans `slug` =
  `{doctrine, group_doctrine, doctrines[]}` (base org + base groupe + index), le call de **DÉBUT DE
  SESSION** ; avec `slug` = le markdown d'une doctrine nommée. `oto_procedure(op='list'[, query,
  scope])` = catalogue/recherche. Scopés à l'**org active** (+ groupe actif) — servis aux seuls
  membres. **Vide sans erreur** si pas d'org active (`_SERVER_INSTRUCTIONS` invite à `oto_procedure(op='get')`).
- **Écriture** : `oto_procedure(op='set'[, body_md, slug, org, title, desc, from_version])` (base = slug
  omis ; nommée sinon ; `from_version` = revert) + `oto_procedure(op='delete', slug[, org])`. Autz :
  `org_id` absent → org active, **org_admin** requis (self-service MCP, NOUVEAU) ; présent → autre
  org, **platform_admin** requis (l'opérateur provisionne n'importe quelle org). La SPA dashboard
  édite aussi via REST `/api/me/instructions*` (org_admin de l'org active).
- **Versioning** : chaque écriture incrémente `version` (sur le courant) et archive un snapshot
  append-only. Revert = re-poser le corps d'une version → nouvelle version (jamais d'effacement
  d'historique sauf `delete`).
- **Store** : `org_instructions(org_id, slug PK partiel, title, description, body_md, version,
  set_by, created_at, updated_at)` + `org_instruction_revisions(org_id, slug, version PK, …)`
  (`db._SCHEMA`, palier org) ; accès dans `org_store.py` (`get/list/search/set/delete_instruction`,
  `list_instruction_versions`, `normalize_slug`, `BASE_SLUG`). **En clair** (prose, pas un
  credential → hors coffre chiffré). **Pas de cache** : lecture DB à l'appel. Écriture sérialisée
  par `(org, slug)` via verrou advisory (mirroir `add_org_member`).
- **Pas d'instruction par namespace d'outil** : un gotcha d'outil est vrai pour tout le monde et
  évolue avec le code du connecteur → sa place reste le repo (docstring, `_SERVER_INSTRUCTIONS`),
  versionné avec l'outil.

## Renommer un outil = migrer les procédures

Une procédure référence ses outils par `<tool:slug>` (ADR 0014), et ces refs vivent **en DB, par
org** — hors du repo. Un renommage d'outil est donc un breaking qui traverse le **code ET les
données**, dont le CI ne voit que la moitié : `test_tools_client_methods_exist` garde le skew
tool↔oto-core, `connector_docs.py` se relit en PR, mais **rien ne lit `org_instructions`**. Une
suite verte ne dit donc rien de l'état des procédures.

Vécu le 2026-07-31 (consolidation pennylane 25→9 outils, v1.38.0, ADR 0047 étendu aux
connecteurs) : `rapprochement-pennylane` (org 2, qui arme une routine planifiée quotidienne) et
`agent-avoirs-compta` (org 35, agent client sous supervision) sont parties **en prod** avec
respectivement 2 et 10 refs mortes, réparées seulement après coup.

Le détecteur, lui, existe déjà : `tool_registry.manifest_for(body_md)` rend
`referenced_tools[].status` et `unresolved_tools` — c'est ce que `oto_procedure(op='get')` et le
retour d'`op='set'` affichent. La migration est donc mécanique : balayer les orgs, réécrire le
corps, vérifier `unresolved_tools == []`. ⚠️ Vérifier contre le serveur qui porte DÉJÀ la nouvelle
surface — tant que le tag n'est pas en prod, les anciens noms y résolvent encore et le contrôle
est faussement vert. **Aucun garde-fou automatique à ce jour** : la migration reste à la charge
de qui renomme.

## Détail accumulé (migré de la carte)

**Livraison au LLM = injection, plus un appel d'outil (otomata-private#49 puis #50, amende ADR 0014).**
Le canal FIABLE de bootstrap = les `instructions` du `initialize` (FastMCP les relit par
session ; Claude rehandshake par conversation). `DynamicInstructionsMiddleware.on_initialize`
(`middleware.py`) **remplace** `result.instructions` par `instructions.compose_session(sub, org_id)`
— un **artefact composé de 2 blocs** (`instructions.py`, #50 ; l'ex-bloc B onboarding a été
retiré le 2026-07-01 — l'onboarding est un projet, ADR 0032 §7) :
- **bloc A « secret sauce »** (posture + boucle d'usage + **catalogue de namespaces** dérivé) —
  prose en DB `platform_instructions['secret_sauce']`, éditable admin plateforme, **inviolable par
  l'org**, toujours injecté (seedé depuis la constante = fallback) ; le catalogue est appendé à la composition ;
- **bloc C « contexte dynamique »** par-(sub, org) — section de contexte résolu (org / équipe /
  connecteurs actifs / N derniers projets / derniers déroulés via `db.recent_runs` / fiche profil
  « situation avec oto » de l'user) + **agent readme cumulés** org → équipe active → user
  (`_format_org_readme`/`_format_group_readme`/`_format_user_readme`), chacun avec substitution
  `{{org}}`/`{{user}}`/`{{équipe}}`/`{{connecteurs_actifs}}`.

Donc **ne plus prescrire « appelle la lecture de doctrine au démarrage »** — la doctrine est injectée.
Les **doctrines nommées (skills)** ne sont pas des outils → absentes de `tools/list` → `on_list_tools`
**enrichit la description de `oto_procedure`** avec leur index per-org (`instructions.skills_index_md`,
Tool non-frozen → `model_copy`). `render()` reste la surface STATIQUE (boot / fallback, sans DB).
Tout **fail-open** (pas de sub/org/doctrine/DB → surface statique). Édition des blocs A/B : capacité
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
