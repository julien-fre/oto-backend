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

`middleware.field_redaction.FieldRedactionMiddleware` (`on_call_tool`, **enregistré en dernier** dans
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
par `middleware.empty_result.EmptyResultMiddleware` à **tout** outil : ce n'est pas un correctif
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

**Seam unique** : `redaction.sert_du_vide(result)`, appelé au seul endroit où oto
rend le résultat à FastMCP. Aucune retouche par outil.

⚠️ **Le vide MUET, le pire cas.** Un outil qui rend `[]` ou `None` ne produit
**aucun bloc de contenu** (`_convert_to_content`, FastMCP 3.4.2) — là où `[1]` rend
un bloc texte et un dict rend son JSON. Le modèle reçoit alors un tour littéralement
sans contenu, et c'est cette absence qui le fait dérailler (`fr_directors` sur un
SIREN sans dirigeant). La phrase remplace ce silence ; elle ne **fabrique jamais** de
JSON pour le combler. Un retour `[1]` reste servi à l'identique.

**Détection** (`redaction.is_empty_payload`) : un résultat est vide quand il
**l'affirme**, jamais parce qu'il en a vaguement l'air. Une **liste** sans élément ;
un **dict** qui porte l'un des deux signaux reconnus :

- un **compteur** — `total_count`, `total`, `count` — qui vaut 0 ;
- une **clé de collection RECONNUE** dont la valeur est une liste sans élément. La
  liste est **FERMÉE**, déclarée dans `redaction._COLLECTION_KEYS` : `rows`,
  `results`, `items`, `matches`, `hits`, `data`, `entries`, `calls`, `jobs`,
  `documents`, `records`, `files`, `messages`, `events`, `result`.

Quatre contradictions **disqualifient**, parce qu'elles portent une information que
la phrase effacerait :

- une collection reconnue **non** vide, ou un compteur **non nul** ;
- une **notice** truthy — `note`, `hint`, `warning(s)`, `notices`, `error(s)`,
  `partial(_errors)`, `hors_schema`, plus les **familles** `*truncat*`, `*tronqu*`,
  `*warning*`, `*avertissement*` (le suffixe est trop productif pour une liste
  fermée : `_etablissements_truncated`, `{champ}_truncated` en clé dynamique chez
  unipile, `texte_tronque`, `truncated_results`, `filtre_ca_avertissement`).
  Cherchée à la racine **et un cran plus bas** — `fr_accords_search`, l'outil même de
  l'incident, porte la sienne sous `effectifs_filter.truncated` ;
- un **accusé d'écriture** (`ok`, `dry_run`, `created`, `deleted`, `failed`,
  `succeeded`, `imported`, `would_*`…). Le nom de la collection ne suffit **pas** à
  les écarter : ils portent aussi les signaux reconnus —
  `{"total": len(items), "succeeded": …, "failed": []}` (webflow) et
  `{"total": total, "imported": 0, "items": [], …}` (waalaxy) seraient lus comme
  vides **par le compteur**.

⚠️ **La liste fermée sous-détecte, et c'est assumé.** Ce backend ne nomme pas ses
collections de façon uniforme : les capacités en exposent à elles seules ~90
(`instances`, `seats`, `guides`, `signals`, `namespaces`…), et airtable calcule la
sienne à l'exécution (`{key: items}`). Mieux vaut servir une structure de trop
qu'affirmer un vide à tort. Le **compteur** rattrape l'essentiel, la convention maison
étant de poser `count: len(...)` à côté de la collection (34 sites sur 41).

⚠️ **La liste fermée est le cœur du garde-fou.** La première version disait « toute
clé dont la valeur est une liste » : elle lisait l'**accusé d'écriture**
`{"ok": true, "deleted": []}` comme un résultat vide et répondait « aucun résultat »
à qui venait de supprimer zéro ligne. `deleted`, `created`, `skipped` ne sont pas des
collections de résultats — un bilan d'écriture se rend tel quel. Y ajouter une clé
demande la même preuve que les autres : un outil qui la rend vraiment.

Une clé reconnue dont la valeur n'est **pas** une liste (`data` porte souvent un
objet) n'est ni signal ni contradiction : elle est ignorée. Le signal ne se cherche
**qu'à la racine**.

**Phrase servie** (`redaction.EMPTY_MESSAGES`) : le gabarit déclaré pour l'outil,
sinon `EMPTY_MESSAGE_DEFAULT`. La table vit dans la couche de rendu, pas au registre
des connecteurs : un outil n'a pas à savoir comment on le rend.

⚠️ **Jamais phrase + structure dans le même canal texte** — y rajouter la structure
« pour information » rétablirait exactement le déclencheur qu'on retire.

⚠️ **L'ordre des middlewares EST la moitié du correctif** : `EmptyResultMiddleware`
est monté **juste sous `ToolAliasMiddleware`**, donc plus externe que la rédaction et
que l'écho de compte — qui réémettent tous deux le payload en JSON dans le canal
texte (`rebuild_result`). Plus interne, la structure serait rétablie juste après
avoir été retirée. Contrat figé par `tests/middleware/test_middleware_order.py`.

⚠️ **La face REST ne change pas d'un octet** : elle ne partage aucun code de rendu
avec la chaîne MCP (`_rest_adapter` → `_json`), et continue de servir la structure
vide aux clients qui parsent.

## Surfaces & fichiers
- backend : `redaction.py` (logique partagée : extraction, rédaction, réémission,
  **rendu du vide**), `middleware/field_redaction.py` + `middleware/empty_result.py`,
  `connectors/schema_store.py`,
  `field_filter_defaults.py` (SERVER_DEFAULTS vide + TEMPLATES), `connectors/field_schema.py`
  (curé, libellés), `capabilities/orgs/field_filters.py` (get/set/preview), `db.py`
  (`connector_schemas`).
- oto-core : `oto/tools/common/field_filter.py`.
- dashboard : `ConnectorTransforms.vue` (schéma + toggle on/off + éditer + templates),
  `FieldRuleDialog.vue`, `RedactionPreview.vue` (dry-run).
