---
title: Les recettes — un outil de connecteur vers un tableau, sans modèle
type: explanation
description: >-
  `oto_recipe` : une description stockée et versionnée de la façon dont les résultats
  d'un outil de connecteur arrivent dans un tableau — outil, arguments, pagination,
  correspondance champ → colonne, clé —, exécutée par le serveur sans qu'aucun modèle
  ne relise ni ne recopie les lignes. Ce que ce premier lot couvre (le mode `pull`),
  ce qu'il refuse (les agents hébergés), et ce qui vient ensuite. À lire avant de
  toucher `oto_mcp/recipes/`, `db/recipes.py` ou `capabilities/recipes.py`.
---

# Les recettes

## Le problème

Faire passer les résultats d'un outil de connecteur dans un tableau, c'est aujourd'hui
un travail de modèle : il appelle l'outil, lit la page entière dans son contexte, puis
la retape en `data_write`, 25 lignes à la fois. Ça coûte deux fois (les jetons d'entrée
pour lire, ceux de sortie, plus chers, pour retaper), c'est l'étape où les runs meurent
avant d'avoir écrit (réponse tronquée à la limite de sortie), et les données de
personnes traversent un contexte de modèle. Il n'y a aucun jugement dedans.

## Le principe : le modèle écrit la recette une fois, le serveur l'exécute ensuite

Une **recette** dit quel outil appeler, avec quels arguments, comment parcourir ses
pages, quel champ de chaque élément va dans quelle colonne, et quelle colonne
identifie une ligne. Un agent l'écrit à partir de la forme d'une page (`sample`),
l'éprouve sur une vraie page sans rien écrire (`test`), la publie ; ensuite `run`
l'exécute autant qu'on veut, et ne rend que des comptes.

Une recette est une DONNÉE de l'org (tables `recipes` / `recipe_versions`), pas du code
du dépôt : ajouter un connecteur, c'est écrire une recette — ni PR ni version.

## Exemple (forme générique)

```json
{
  "tool": "linkedin_aiark_search",
  "params": {"company_uuid": {"required": true}, "company": {"required": true},
             "country": {"default": "France"}},
  "arguments": {"op": "people",
                "account": {"id": {"any": {"include": ["{{params.company_uuid}}"]}}},
                "contact": {"location": {"any": {"include": ["{{params.country}}"]}}}},
  "source": {"items": "content",
             "pagination": {"type": "page", "param": "page", "start": 0,
                            "size": 50, "size_param": "size", "last": "last"}},
  "where": [{"path": "location.country", "op": "eq", "value": "{{params.country}}"}],
  "map": {"linkedin_url": "link.linkedin", "full_name": "profile.full_name",
          "title": "profile.title", "headline": {"path": "profile.headline", "max": 300},
          "city": "location.city", "seniority": "department.seniority", "aiark_id": "id"},
  "values": {"company": "{{params.company}}", "status": "sourced"},
  "key": {"column": "contact_key",
          "template": "{{params.company|slug}}::{{item.link.linkedin}}"},
  "on_existing": "skip",
  "limits": {"max_units": 500, "max_pages": 20}
}
```

## Le langage, volontairement petit

- **Chemins** pointés avec index : `profile.title`, `emails[0].email`. Un chemin qui ne
  mène nulle part rend une case vide, jamais une erreur.
- **Gabarits** `{{params.x}}`, `{{item.a.b}}`, `{{row.col}}` (sous `for_each`), filtres
  `slug`, `lower`, `upper`, `strip`, `unaccent`, et les **normaliseurs** `domain`
  (`https://www.Acme.com/x` et `jane@acme.com` → `acme.com`), `email` (vide si la valeur
  n'en a pas la forme : jamais une phrase d'erreur dans une colonne `email`),
  `email_domain`, `linkedin_slug` (`…/in/Jane-Doe/?trk=…` → `jane-doe`), `url`, `digits`.
  Un gabarit seul garde le type de sa valeur ; mêlé à du texte, il devient du texte.
- ⚠️ **`slug` reproduit la forme des clés déjà écrites** par les procédures de sourcing :
  minuscules, accents retirés, chaque suite non alphanumérique réduite à `_`, aucun `_`
  aux bords. La changer dédoublerait chaque ligne au premier passage.
- **`where`** : `eq`, `ne`, `in`, `not_in`, `contains_any`, `empty`, `not_empty`, sans
  casse ni accents — ou par le normaliseur de la clause (`normalize: "domain"`).
  `in_table` / `not_in_table` (`table`, `column`) comparent à la colonne d'un AUTRE
  tableau (liste d'exclusion, clients existants) : ses valeurs sont lues une fois par
  exécution, normalisées comme l'élément, au plus 50 000 (`match_table_too_large`). Un
  élément sans valeur n'est pas « dans » la liste : `not_in_table` le laisse passer.
- **`for_each`** (`datastore`, `status_column`, `filter`, `max_parents`,
  `max_items_per_row`) : chaque ligne d'un tableau PARENT dont la colonne d'état est vide
  déclenche l'appel (les personnes d'une société, les offres d'un domaine), citée
  `{{row.col}}` dans les arguments, la correspondance, les valeurs et la clé — et nulle
  part ailleurs : `row` hors `for_each` est refusé à l'écriture. Ses pages repartent de
  zéro à chaque parent ; `max_items_per_row` borne ce qu'un parent apporte.
- Tout ce qui demande davantage (expression régulière, condition, calcul) ira dans une
  fonction (`oto_function`) — on ne fait pas grandir ce langage.

## Les garde-fous

- **Seul un outil DÉCLARÉ EN LECTURE est appelable** (`@mcp.tool(annotations=LECTURE)`,
  `oto_mcp/tools/lecture.py` — l'annotation standard `readOnlyHint` du protocole). Un
  outil non déclaré est refusé (`recipe_tool_not_read_only`) avant tout appel : le défaut
  est le refus, jamais « présumé lecteur ». Un outil multiplexé par `op` ne se déclare que
  si toutes ses ops lisent. Les connecteurs à modèle (`jev`, `lighton`) sont refusés
  même s'ils lisent : une recette est « sans modèle ». Pour marquer un outil : le
  décorateur, et la garde `tests/test_outils_en_lecture.py` le vérifie.
- **Chaque page est un appel ordinaire de l'outil** (`tools/meta.executer_cible`, le
  corps d'`oto_call`) : activation du connecteur, axes, org du run, **journal sous le
  nom de l'outil avec sa `quantity`** — donc facturé comme un appel direct — et
  rédaction. Les lignes sont fabriquées depuis le résultat RÉDIGÉ ; un résultat retenu
  par la politique de l'org arrête l'exécution.
- **`limits.max_units` est obligatoire** (1 à 50 000) et se compte sur ce que l'outil
  **FACTURE** : la `quantity` de sa ligne `tool_calls` (profils AI Ark, crédits
  Serper…). Seul un outil qui n'en rend pas est compté dans les unités de la recette
  (`units`) ; le reçu le dit (`units_basis` : `billed`, `declared` ou `mixed`). En
  pagination par numéro, une page n'est jamais coupée : si elle peut dépasser le reste
  du plafond, l'exécution s'arrête avant (`spend_cap`) — une taille réduite décalerait la
  fenêtre sur des éléments déjà lus. Au curseur, la dernière page est réduite au reste.
- **Une ligne existante n'est pas touchée** par défaut (`on_existing="skip"`) ;
  `update` ne réécrit que les colonnes de la correspondance, jamais les valeurs fixes.
- **La clé de la recette devient la clé déclarée du tableau.** Un tableau qui en
  déclare une autre est refusé (`key_mismatch`) avant tout appel ; un tableau qui n'en
  déclare aucune reçoit celle de la recette au premier `run`, avant d'écrire — l'unicité
  est alors tenue par la base, et deux exécutions concurrentes ne dédoublent pas. Des
  lignes qui répètent déjà une valeur la rendent impossible (`key_not_declarable`). Un
  élément sans valeur de clé est écarté — un gabarit dont un morceau manque n'en
  produit pas.
- **Les colonnes manquantes sont créées** (texte) ; les existantes jamais retouchées.
- **Une ligne parente faite reçoit son état** — `done` (des éléments), `empty` (aucun),
  `failed:<code>` (l'outil a refusé SON entrée : `invalid_input`, `not_found`,
  `call_refused`) — dans `status_column`, déclarée si elle manque : c'est lui qui fait
  qu'une exécution suivante ne repaie pas une ligne faite. Un échec du compte ou du
  fournisseur (clé, crédits, délai, limite de débit) arrête l'exécution et laisse la ligne
  en attente. Trois lignes d'affilée qui échouent pareil arrêtent tout SANS les marquer
  (`repeated_failure`) : c'est systémique, pas trois mauvaises lignes — sauf
  `not_found` (une société inconnue du fournisseur), marqué aussitôt, hors disjoncteur.
  Une entrée qui se normalise en rien (`n/a|digits`) n'appelle pas : `failed:invalid_input`.
  Sous `for_each`, le reçu ne porte jamais le message du fournisseur (il peut citer une
  valeur de la ligne), seulement son code.
- **Ne sont sélectionnées que les lignes du `filter`** (la grammaire de `data_rows`,
  éprouvée avant tout appel : `invalid_filter`) **dont chaque colonne citée dans les
  `arguments` est remplie** : une ligne sans `siren` ne paie pas un appel pour rien, et
  attend qu'on la remplisse. Le tableau parent doit être ÉCRIVABLE et
  distinct de la cible (`for_each_same_table`), vérifié avant tout appel. Une ligne
  coupée par un plafond (dépense, pages, horloge) ou un refus de l'outil reste en
  attente : le jeton `resume` la reprend à sa page, une exécution neuve du début (les
  lignes déjà écrites sont reconnues par leur clé ; les pages, elles, se repaient).
  `max_parents` (25 par défaut, 200 au plus) borne les parents d'un appel (`max_parents`
  au reçu, avec `resume`). Le reçu compte `parents: {done, empty}`.
- **La dérive arrête l'exécution.** Les colonnes que l'épreuve de publication a remplies
  sur au moins 80 % des lignes (`test_report.fill`) sont surveillées : une page d'au
  moins 10 lignes où l'une revient vide PARTOUT n'est pas écrite, et l'exécution s'arrête
  (`mapping_drift`, `drifted_columns`) — un fournisseur qui change la forme de sa réponse
  remplirait sinon le tableau de lignes creuses. Une colonne clairsemée à la publication
  (un `headline`, une ville) n'est pas surveillée.
- **L'épreuve d'une recette `for_each`** essaie jusqu'à trois parents en attente, une page
  chacun, et s'arrête au premier qui produit des lignes : le premier peut légitimement
  ne rien rendre. Elle n'écrit ni la cible, ni l'état des parents.
- **Budget d'horloge de 30 s** et **`max_pages` par appel** : au-delà, reçu partiel et
  `resume`, que l'appel suivant passe pour continuer sans repayer les pages faites. Le
  jeton porte l'empreinte de la recette (une autre recette, ou une autre version, le
  refuse : `invalid_resume`) et jamais une dépense négative. Le
  plafond de dépense, lui, vaut pour toute la chaîne (le jeton porte la dépense faite).
- **Seule une version PUBLIÉE écrit.** `publish` éprouve une page réelle sans écrire ;
  une recette passée en ligne (`recipe`) ne sert qu'à `test` — `run` la refuse
  (`inline_recipe_cannot_write`).
- **Le reçu ne porte que des comptes et des codes** : pages, éléments vus, unités,
  lignes écrites / mises à jour / laissées intactes / écartées, codes d'échec.
- ⚠️ **Refusé dans un agent hébergé** (`hosted_runs_not_supported`), pour ce premier
  lot : une recette lancée par un travail du runner devra rester dans la liste d'outils
  de son déclencheur, ce que le serveur saura vérifier quand le jeton d'un travail
  portera son travail — un lot à part.

## Ce qui vient ensuite

Le bloc `async` (soumettre puis collecter : Dropcontact, FullEnrich, Apify), le mode par
ligne (`call` : remplir des cases d'une ligne existante) et la poussée vers un CRM
(`push` — un effet chez le tiers : il lui faut une marche à blanc obligatoire et une
liste des outils à effet, puisqu'une recette n'appelle aujourd'hui que des outils
déclarés en lecture), puis les travaux de fond déclenchés par une planification ou un
webhook (l'exécutant reste à choisir).
