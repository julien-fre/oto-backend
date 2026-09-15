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
  Documente aussi (oto#86) l'absence de couche de rédaction sur la face REST — deux
  middlewares MCP-only, une allow-list explicite `redaction.champs_autorises` posée
  à la main sur les routes anonymes qui en ont besoin — et les trois fuites qu'elle
  a fermées. À lire pour configurer ou étendre la rédaction de PII dans une org, ou
  pour toucher à la couche qui met en forme le résultat servi à l'agent, MCP ou REST.
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
  `content` (TextContent JSON) — sinon le canal brut fuit. Le canal structuré est ensuite
  retiré, plus haut dans la chaîne, des outils sans schéma de sortie (§ « Un seul canal »).
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
sans contenu, et c'est cette absence qui le fait dérailler (le cas fondateur :
`fr_directors` sur un SIREN sans dirigeant — **il ne rend plus de liste nue depuis
le 2026-09-01, #612**, mais le mode de panne, lui, reste ouvert pour tout outil qui
rend une liste). La phrase remplace ce silence ; elle ne **fabrique jamais** de
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

## Un seul canal porte la donnée — le canal structuré se mérite, il ne se déduit pas

Un résultat d'outil MCP a deux canaux : `content` (du texte, ce qu'un modèle lit) et
`structuredContent` (un JSON validable contre l'`outputSchema` de l'outil, ce qu'un
client qui **parse** consomme ; la spec l'exige dès qu'un schéma est déclaré). FastMCP
déclare ce schéma **par inférence** : toute fonction `-> dict` reçoit « un objet, tout
est permis », donc un canal structuré. Recompté sur le montage complet (717 outils
servis) : **473** avec ce schéma vide, 124 sans aucun schéma, 120 enveloppes
`x-fastmcp-wrap-result` (annotés `-> list` ou `-> object`), **zéro dont le schéma
décrive un champ**. Le contrat typé n'existe pas ; la copie, elle, part à chaque appel.

Et cette copie est LUE : **Claude Code et `oto-runner` donnent au modèle le canal
structuré à la place du texte** (marqueurs distincts sur les deux canaux, trois runs sur
trois, avec ou sans schéma). Tout ce que cette chaîne fait au texte — le rendu du vide,
la rédaction, le TOON — partait donc à un canal que ces clients ne lisent pas.

La règle, `middleware/un_seul_canal.py`, en deux gestes **indissociables et dans cet
ordre** :

1. **au montage** (`server._build_mcp`, après le dernier `register`), le schéma DÉDUIT
   est effacé de chaque outil — un outil neuf naît sans contrat de sortie, sauf s'il en
   déclare un vrai (un modèle pydantic rendu) ;
2. **à l'appel**, `UnSeulCanalMiddleware` retire `structuredContent` des outils dont le
   schéma est **absent**. Juste sous `ToolAlias`, donc plus externe que tout ce qui
   réémet les deux canaux : plus interne, le rendu du vide ou la rédaction rétablirait
   le canal qu'il vient de retirer.

⚠️ **Pourquoi « absent » et jamais « vide »** : un client conforme valide, et le client
FastMCP refuse un résultat sans canal structuré dès qu'un schéma est encore annoncé
(« outputSchema defined but no structured output returned »). Le middleware seul, sans
le geste de montage, casserait ces clients. Il ne juge donc que l'absence ; c'est le
montage qui la crée.

Les 120 enveloppes `x-fastmcp-wrap-result` (une valeur annotée `-> list` ou `-> object`,
emballée en `{"result": …}`) sont **gardées** et nommées dans
`tests/structured_output_debt.txt`, liste qui ne peut que décroître : là, les deux canaux
n'ont pas la même forme, et un client qui parse `.result` ne retrouverait pas la donnée
dans le texte sans la désemballer. Payer une ligne = annoter `-> dict` **nu** et rendre
un dict aux clés nommées, ou déclarer un vrai `Output`. Composition recomptée : **20**
dont le `result` est une `array` (annotés `list` / `list[dict]`) et **100** dont le
`result` n'a pas de type (88 `object`, 6 `Optional[dict]`, 6 `dict | list`) — ce n'est
donc pas `-> object` qui fabrique l'enveloppe, c'est **toute annotation qui n'est pas un
`dict` nu**. ⚠️ Un poste en retard sur le pin oto-core en voit AUTANT : mesuré à
oto-core 1.116.0 contre un pin v1.120.0, mêmes 717 outils et mêmes 120 enveloppes — un
connecteur dont le cœur manque est monté en REFUS, pas absent du catalogue.

**Ce qui change de contrat** : `/openapi.json`, `oto_tool_schema` et `/api/tools`
servent `output_schema: null` pour 597 outils — les 473 dont le schéma déduit est
effacé, plus les 124 qui n'en avaient aucun (`scripts/empreinte_servie.py` le
mesure désormais — il ne voyait pas le schéma de sortie, et lisait « aucun outil servi
n'a changé » sur ce lot). `me.tools` le disait déjà : « souvent `null`, un outil n'est
pas tenu d'en déclarer un ».

**Ce qui est vérifié chez le client** : une fois le canal retiré, Claude Code recopie le
marqueur du **texte** (deux runs sur deux, 10/09/2026). C'est ce qui rend durable tout
le reste de cette page : quand un seul canal porte la donnée, le comportement du client
cesse de compter.

## Un corps markdown se sert en markdown, jamais en JSON échappé

Une fiche — un dict dont `body_md` porte l'essentiel : procédure, guide, document —
était servie sur le canal texte comme du JSON, le corps en chaîne échappée (`\n`,
`\"`, par centaines dans une procédure). Mesuré le 10/09/2026 sur Haiku, sur le même
texte : **+4 à +7 % de jetons** (un dessin de 3 304 caractères 983 → 1 048, 2 845
caractères de prose 781 → 814), payés à chaque lecture puis à chaque tour tant que le
corps reste dans le contexte.

`MarkdownBodyMiddleware` (`middleware/markdown_body.py`) réémet le canal texte en
markdown : les autres champs en en-tête (`clé: valeur`, structures en JSON compact),
une ligne vide, le corps tel quel. Le canal structuré garde son JSON. Ne s'applique
qu'à un dict dont `body_md` fait au moins la **moitié** du payload — la forme d'une
fiche ; une liste, une enveloppe, un résultat sans corps sont servis comme avant.
Place : juste sous `EmptyResult`, plus externe que l'écho de compte et la rédaction,
qui réémettent les deux canaux en JSON (`tests/middleware/test_middleware_order.py`).
Banc : `tests/middleware/test_markdown_body.py` — dont « la rédaction a tourné avant,
l'en-tête ne rend pas un champ rédigé ».

## Face REST : allow-list explicite sur une route anonyme (oto#86)

`FieldRedactionMiddleware` et `EmptyResultMiddleware` n'ont qu'un point d'accroche,
`on_call_tool` — un concept MCP. La face REST ne le traverse jamais : **aucune
couche n'y retire un champ**, la liste de colonnes du magasin de données EST
l'unique contrôle. Pour une route **authentifiée** ce n'est pas un défaut (l'autz
gouverne déjà ce qui est lu, comme pour le vide § ci-dessus). Pour une route
**anonyme** qui réutilise un magasin écrit pour un appelant authentifié, c'est un
défaut de classe : elle sert tout ce que le magasin sait, sans qu'aucun refus,
avertissement ou trace ne s'allume.

**Trois fuites mesurées et corrigées (oto#86)**, toutes de la même forme —
un magasin conçu pour un appelant authentifié, relu en anonyme sans projection :

- l'accusé d'un dépôt de fichier par lien (`upload_tokens.materialize`, branche
  `project_file`) rendait la ligne de base entière (treize colonnes) moins UNE,
  retirée par un `pop()` ponctuel — dont l'identifiant du compte qui a ÉMIS le
  lien. Un tiers sans compte qui dépose son fichier apprenait donc qui le lui a
  envoyé. Corrigé par une allow-list (`redaction.champs_autorises`) : l'accusé ne
  porte plus que la confirmation, le nom de fichier et la taille.
- le libellé de la page de dépôt (`upload_tokens.target_label`, servie SANS
  authentification) interpolait un identifiant interne de ligne (document,
  projet) dans le HTML — alors que sa propre docstring promettait déjà « pas de
  secret, pas de contenu ». Corrigé en retirant l'identifiant du libellé, sans
  lui substituer un aller-retour base (choix délibéré : un aperçu anonyme ne
  vaut pas une résolution DB de plus rien que pour l'ergonomie).
- l'aperçu d'invitation (`org_store._PREVIEW_SELECT`, deux routes anonymes par
  token et par code) projetait `COALESCE(nom, email)` : nommer l'inviteur est
  intentionnel (accompagner l'accueil avant création de compte), mais le REPLI
  vers son adresse — quand son profil n'a pas de nom déclaré — ne l'était pas.
  Corrigé en retirant l'email du repli ; le champ reste optionnel côté client
  (`inviter: string | null`), qui dégrade déjà correctement vers un message
  générique quand il est absent.

**Le principe retenu, pas encore un mécanisme générique.** `redaction.
champs_autorises(payload, *noms)` est une allow-list explicite — le patron déjà
adopté par #43 (portées de jeton d'API, deny-by-default) — posée À LA MAIN sur
la route qui en a besoin, pas un middleware qui intercepterait `on_request` pour
toutes. Une liste de retraits ne protège que du passé (une colonne ajoutée demain
au magasin fuit en silence) ; une allow-list refuse par défaut ce qu'elle n'a pas
nommé. Portée assumée de ce lot : les trois routes fautives l'utilisent (ou son
principe, pour le libellé qui ne recopie pas un magasin) ; les onze autres routes
anonymes du dépôt ont été auditées sans trouvaille et n'ont pas été touchées. Un
véritable middleware REST — parité avec `FieldRedactionMiddleware` sur `/api/*` —
reste à faire si une prochaine fuite de la même famille apparaît ; il ne s'est
pas imposé pour trois routes.

⚠️ **Rate-limit vérifié, pas ajouté.** Les deux routes d'invitation sont écrites
à la main précisément parce que l'adaptateur REST des capacités authentifie
TOUJOURS — l'argument « single-use + TTL + rate-limit côté capacité » qui
justifie la longueur du code court ne s'applique donc PAS à elles. Confirmé en
lisant la chaîne de middlewares Starlette (`server.py`) : le seul token-bucket du
dépôt est posé par `subdomain_project.py`, clé `(IP, projet)`, et ne couvre QUE
les sous-domaines MCP de projet. Aucun limiteur applicatif ne garde
`/api/invitations/{token}` ni `/code/{code}` — seul un limiteur de PROXY,
hors de ce dépôt, pourrait les couvrir. Pas ajouté dans ce lot (hors périmètre
demandé) ; à nommer si un prochain audit y revient.

## Surfaces & fichiers
- backend : `redaction.py` (logique partagée : extraction, rédaction, réémission,
  **rendu du vide**, l'allow-list REST `champs_autorises`),
  `middleware/field_redaction.py` + `middleware/empty_result.py`,
  `connectors/schema_store.py`,
  `field_filter_defaults.py` (SERVER_DEFAULTS vide + TEMPLATES), `connectors/field_schema.py`
  (curé, libellés), `capabilities/orgs/field_filters.py` (get/set/preview), `db.py`
  (`connector_schemas`).
- oto-core : `oto/tools/common/field_filter.py`.
- dashboard : `ConnectorTransforms.vue` (schéma + toggle on/off + éditer + templates),
  `FieldRuleDialog.vue`, `RedactionPreview.vue` (dry-run).
