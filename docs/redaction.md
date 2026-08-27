---
title: Rédaction de champs (anonymisation des sorties connecteurs)
type: explanation
description: >-
  Explique le mécanisme de rédaction/anonymisation des sorties de connecteurs dans
  oto-backend : middleware unique FieldRedactionMiddleware (enregistré en dernier,
  retouche le résultat final via access.resolve_field_filter), fail-closed si la
  policy existe (sortie retenue, jamais le brut), rien par défaut avec templates
  1-clic (candidate, bank_details). Détaille la capture passive du schéma observé
  via connector_schema_store (squelette clés+types, jamais de valeurs/PII, table
  connector_schemas, cap 1000 clés) car les API tierces (Unipile, Apollo…) ne
  publient pas de schéma de réponse. Couvre le dry-run preview (capacité
  org.field_filters.preview, REST POST /api/orgs/{id}/field-filters/{service}/preview)
  et le moteur FieldFilter d'oto-core (mask/pseudonym/generalize/hash/drop). Porte
  enfin la règle de rendu du VIDE (EmptyResultMiddleware) : un résultat sans aucun
  résultat se sert au modèle en PHRASE dans le canal texte, jamais en structure nue.
  À lire pour configurer ou étendre la rédaction de PII dans une org, ou pour toucher
  à la couche qui met en forme le résultat servi à l'agent.
---

# Rédaction de champs (anonymisation des sorties connecteurs)

Masquer/pseudonymiser des champs des **réponses d'outils** avant qu'elles atteignent
l'agent (use-case d'origine : analyser un profil/CV candidat sans son identité).

## Principe : un middleware unique (pas de câblage par connecteur)

`middleware.FieldRedactionMiddleware` (`on_call_tool`, **enregistré en dernier** dans
`server._build_mcp` → enveloppe les autres, retouche le **résultat final**). Pour tout
tool : `access.resolve_field_filter(namespace_of(name))` → applique le `FieldFilter`
(oto-core) au résultat. Donc la rédaction est **disponible sur TOUS les connecteurs**
sans code par-connecteur (≠ l'ancien filtrage client-level de folk/silae/pennylane,
retiré).

- **Deux canaux réémis** depuis la version redactée : `structured_content` **ET**
  `content` (TextContent JSON) — sinon le canal brut fuit (l'agent lit surtout `content`).
- **Fail-closed** : si une policy existe et que `apply` lève (ex. Faker absent) → on
  **retient** la sortie (`_withheld`), jamais le brut. `is_empty` (pas de policy) =
  passe-through. Échec de *résolution* (aléa DB) → passe-through, sauf service à défaut
  serveur (aucun aujourd'hui).
- `FieldFilter` matche par **nom de clé feuille, récursif** (à toute profondeur). ⚠️
  aveugle au contexte : une règle sur `name` touche aussi `skills[].name` — d'où
  l'importance du schéma observé + dry-run pour ne pas corrompre.

## Rien par défaut + templates 1-clic

`field_filter_defaults.SERVER_DEFAULTS = {}` — **aucune** rédaction par défaut (la PII
n'est pas toujours un risque : CRM/inbox/annuaire = c'est le but ; un défaut large
casserait ces connecteurs). L'org **active explicitement** ce qu'elle veut.
`TEMPLATES` (`candidate`, `bank_details`) = jeux de règles **applicables en 1 clic**
depuis le dashboard (≠ défaut imposé).

## Schéma OBSERVÉ = source de vérité (pas déclaré)

Les sorties connecteurs sont des **passthrough d'API tierces qu'on ne possède pas**
(Unipile, ATS, Apollo…) — leur réponse passe quasi telle quelle à l'agent. Donc :
- on **ne peut pas déclarer** un schéma fiable (il dérive ; vérifié : les API ne
  publient pas le schéma de **réponse** — Unipile = « Try It! »).
- le schéma juste = **ce qui transite** → `connector_schema_store` extrait, de chaque
  réponse, un **squelette clés+types** (JAMAIS de valeurs/PII : feuilles scalaires +
  listes de scalaires, avec leurs chemins) et le persiste par service (table
  `connector_schemas`, fusion incrémentale, cache process anti-write-par-appel).
- Multi-chemins gardés (`name → skills[].name · languages[].name`) → rend l'ambiguïté
  du matching par clé **visible** dans l'UI.
- **Garde-fou anti-empilement** : union-only donc monotone, mais converge (clés nommées,
  tableaux collapsés en `[]`) ; cap `_MAX_KEYS=1000` / `_MAX_PATHS_PER_KEY=50` contre les
  réponses à **clés dynamiques** (map keyée par id). Spine/données user (`oto`/`run`/
  `feedback`/`data`) exclus de la capture. Pas de purge par fraîcheur (clé
  retirée par l'API = persiste, inoffensif : règle no-op).

Le bundle `GET /api/orgs/{id}/field-filters` fusionne **observé + curé**
(`connector_field_schema`, libellés/sensibilité) → l'UI affiche le vrai schéma sans
dry-run dès qu'un peu de trafic a coulé. Cold-start (connecteur jamais appelé) = vide →
le dry-run charge depuis un échantillon.

## Dry-run (preview)

Capacité `org.field_filters.preview` (MCP `oto_preview_org_field_filter` + REST
`POST /api/orgs/{id}/field-filters/{service}/preview`) : passe un échantillon réel dans
le filtre, renvoie le redacté → on **voit** ce qui est masqué (clés imbriquées incluses),
sans deviner. Alimente le panneau « tester le filtrage » du dashboard.

## Moteur (oto-core `FieldFilter`)

Actions : `mask` (preserve email/phone/iban, keep_first/last), `pseudonym` (kind, **Faker**
→ extra `oto-core[anonymize]`), `generalize`, `hash`, `anonymize`, `drop`. ⚠️ une clé
matchée à valeur **liste de scalaires** (`emails: [...]`) est masquée **élément par
élément** (corrigé v1.10.0/1.10.1 — sinon fuite ; couvre aussi les listes mixtes).

## Le VIDE se sert en PHRASE, jamais en structure nue

**Règle** (2026-08-27, `otomata-tech/oto#32`) : un résultat d'outil qui ne porte
**aucun** résultat part au modèle sous forme de **phrase seule** dans le canal texte
— `structuredContent` gardant, lui, la structure vide intacte. Générique, appliquée
par `middleware.EmptyResultMiddleware` à **tout** outil : ce n'est pas un correctif
par connecteur.

**L'incident fondateur.** Une flotte d'agents interrogeait une base sur des cibles
souvent absentes. Le `{"total_count": 0, "rows": []}` rendu tel quel dans le canal
texte faisait **dégénérer le décodage du modèle** : recopie de la structure, boucle
sur des centaines de `]}`, reprise en prose — et le fournisseur encadrait toute la
sortie comme un **appel d'outil dont le nom est la narration**, renvoyé au client.
Un runner en une passe ne joue pas cet appel : le travail est perdu, la ligne
repayée à l'identique. **16 des 26 faux départs d'une campagne, 10 des 11 d'une
vague de production**, ~23 k jetons par job perdu. Trois sessions ont soupçonné la
consigne pendant trois heures avant qu'une capture du texte final ne montre le
mécanisme — le défaut est **invisible partout où le vide est l'exception, et
dominant là où il est la norme**.

**Détection** (`redaction.is_empty_payload`, volontairement syntaxique — elle ne
connaît aucun outil) : une **liste** sans élément ; un **dict** qui porte au moins
une collection (toute clé dont la valeur est une liste), les a **toutes** vides, et
dont tout compteur présent (`total_count`, `total`, `count`) vaut 0. Un compteur non
nul **contredit** la collection vide → on rend la structure telle quelle plutôt que
d'affirmer un vide. Un scalaire, un dict **sans** collection, une collection peuplée
ne sont pas vides ; le vide ne se cherche **qu'à la racine**.

**Phrase servie** (`redaction.EMPTY_MESSAGES`) : le gabarit déclaré pour l'outil,
sinon `EMPTY_MESSAGE_DEFAULT`. La table vit dans la couche de rendu, pas au registre
des connecteurs : un outil n'a pas à savoir comment on le rend.

⚠️ **Jamais phrase + structure dans le même canal texte** — y rajouter la structure
« pour information » rétablirait exactement le déclencheur qu'on retire.

⚠️ **L'ordre des middlewares EST la moitié du correctif** : `EmptyResultMiddleware`
est monté **juste sous `ToolAliasMiddleware`**, donc plus externe que la rédaction et
que l'écho de compte — qui réémettent tous deux le payload en JSON dans le canal
texte (`rebuild_result`). Plus interne, la structure serait rétablie juste après
avoir été retirée. Contrat figé par `tests/test_middleware_order.py`.

⚠️ **La face REST ne change pas d'un octet** : elle ne partage aucun code de rendu
avec la chaîne MCP (`_rest_adapter` → `_json`), et continue de servir la structure
vide aux clients qui parsent.

## Surfaces & fichiers
- backend : `redaction.py` (logique partagée : extraction, rédaction, réémission,
  **rendu du vide**), `middleware.py` (FieldRedactionMiddleware, EmptyResultMiddleware),
  `connector_schema_store.py`,
  `field_filter_defaults.py` (SERVER_DEFAULTS vide + TEMPLATES), `connector_field_schema.py`
  (curé, libellés), `capabilities/orgs_field_filters.py` (get/set/preview), `db.py`
  (`connector_schemas`).
- oto-core : `oto/tools/common/field_filter.py`.
- dashboard : `ConnectorTransforms.vue` (schéma + toggle on/off + éditer + templates),
  `FieldRuleDialog.vue`, `RedactionPreview.vue` (dry-run).
