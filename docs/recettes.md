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

## Le mode par ligne (`per_row`)

Une recette `per_row` ne crée pas de lignes : elle **enrichit celles d'un tableau**. Chaque
ligne EN ATTENTE de `datastore` — colonne d'état vide, dans le `filter`, chaque colonne
citée par les `arguments` remplie — déclenche UN appel, et son résultat est écrit dans
CETTE ligne.

```json
{
  "mode": "per_row",
  "tool": "fr_get",
  "arguments": {"siren": "{{row.siren|digits}}"},
  "rows": {"status_column": "fr_status", "filter": {"country": "FR"}, "max_rows": 25},
  "source": {"items": ""},
  "map": {"naf": "activite_principale", "headcount": "tranche_effectif"},
  "limits": {"max_units": 200}
}
```

- **`require`** (dans `rows` ou `for_each`) : les colonnes qui doivent être remplies pour
  qu'une ligne soit prise. Sans lui, chaque colonne citée par les `arguments` l'est — trop
  strict dès qu'un argument est facultatif. Un argument dont la valeur est vide n'est pas
  envoyé (ni `null` ni `""`).
- **Un résultat dont toutes les colonnes de la correspondance sont vides est `not_found`**
  (un profil vide n'est pas un succès) : rien n'est écrit, pas même les `values`.
- **Les cases vides seulement**, par défaut (`on_existing: "fill_empty"`) : une valeur
  posée par quelqu'un n'est pas écrasée ; `update` réécrit les colonnes de la
  correspondance. Une valeur absente du résultat ne vide jamais une case.
- **Jamais par-dessus une saisie faite pendant l'exécution** : l'écriture porte la
  révision lue (`row_changed`, la ligne reste en attente).
- **État** : `done`, `not_found` (aucun résultat, ou écarté par `where`),
  `failed:<code>` (l'entrée de la ligne refusée), `failed:ambiguous` — le résultat est
  une liste de plusieurs éléments et la recette n'a pas dit `pick: "first"` : choisir
  entre des candidats, c'est résoudre une identité, et ça ne se fait pas en silence.
- Ni `key`, ni pagination, ni `for_each` ; `units` vaut `calls` par défaut. `test` et
  `publish` lisent `datastore` (obligatoire) et essaient jusqu'à trois lignes sans rien
  écrire. Le reçu compte `rows: {done, not_found, failed}`.
- Même boucle que `for_each` : refus qui tiennent à la ligne, disjoncteur, plafond de
  dépense, budget d'horloge, dérive (sur dix lignes d'affilée).

**Une exécution à la fois par tableau.** Une exécution qui écrit prend le BAIL du tableau
dont elle écrit l'état des lignes (le parent sous `for_each`, sinon la cible ;
`recipe_leases`) et le rend en sortie ; une deuxième, même d'une autre recette, est
refusée avant tout appel (`run_in_progress`) — sinon elle paierait deux fois les mêmes
lignes, ou créerait deux fois la même fiche chez un tiers. Un processus mort ne bloque
pas le tableau au-delà de son budget d'horloge plus deux minutes. L'épreuve, qui n'écrit
rien, n'en prend pas.

## Soumettre puis collecter (`async`, en `per_row`)

Certains enrichissements ne répondent pas tout de suite : on soumet, le fournisseur
travaille, on revient chercher le résultat (Dropcontact, FullEnrich, révélation de
téléphone Apollo, enrichissement lemlist). Ces outils ne sont pas « en lecture » — ils
dépensent des crédits — mais ne changent rien de visible chez le fournisseur : ils sont
nommés, avec leur outil de collecte, dans une liste fermée (`oto_mcp/recipes/outils.py`,
`SOUMISSIONS`).

```json
{
  "mode": "per_row",
  "tool": "dropcontact_enrich",
  "arguments": {"contacts": [{"first_name": "{{row.first_name}}",
                              "last_name": "{{row.last_name}}",
                              "company": "{{row.company}}"}]},
  "rows": {"status_column": "dc_status", "require": ["first_name", "last_name", "company"]},
  "async": {"id": "request_id", "ready": "done",
            "collect": {"tool": "dropcontact_result",
                        "arguments": {"request_id": "{{job.id}}"}}},
  "source": {"items": "profiles"}, "pick": "first",
  "map": {"email": "email[0].email"},
  "limits": {"max_units": 200}
}
```

- **Une exécution collecte d'abord** les lignes `submitted` (travail noté dans
  `async.job_column`, `<status_column>_job` par défaut) — encore en cours, elles
  attendent — **puis soumet** les lignes en attente. On la relance donc : le reçu dit
  `next_step` tant que des travaux tournent. Une exécution ne soumet jamais deux fois la
  même ligne (le travail et l'état `submitted` s'écrivent d'un seul geste). ⚠️ La ligne
  soumise par l'épreuve de publication, elle, n'est rien noté : la première exécution la
  soumet à nouveau (un crédit de plus).
- **Le travail est noté sans garde de révision** : une soumission payée et non notée
  serait refaite, et repayée. Notée impossible : `failed:unknown_outcome`.
- **Au-delà de `max_wait_seconds`** (30 min par défaut) : `failed:timeout`, jamais
  resoumis — la plupart des fournisseurs factureraient deux fois.
- `{{job.id}}`, `{{job.at}}` et les champs gardés par `async.keep` ({nom: chemin dans la
  réponse de soumission}) se citent dans les arguments de collecte.
- **`rows.require` est obligatoire** : les colonnes qu'une ligne doit avoir avant qu'on
  dépense des crédits pour elle.
- **L'épreuve prouve la correspondance sur un VRAI résultat** : `test` sans `resume`
  soumet UNE ligne (rien n'est écrit) et rend un jeton (`job_submitted`) ; `test` avec ce
  jeton collecte (`job_running` tant que ce n'est pas prêt) puis rend le remplissage.
  `publish` se passe le même jeton, et refuse tant que le résultat n'est pas collecté
  (`test_pending`).
- ⚠️ Apify (lancer, suivre, lire le jeu de données) est un `pull` asynchrone : pas
  couvert. Un travail par ligne : les envois groupés (cent contacts par soumission)
  viendront ensuite.

## Pousser vers une autre app (`mode: push`)

Une recette `push` crée ou met à jour UNE fiche chez un tiers par ligne en attente, et
l'identifiant de la fiche revient dans la ligne. ⚠️ **C'est une extension assumée de la
règle « une recette ne fait que lire »**, tenue par une liste fermée d'outils et d'ops
(`oto_mcp/recipes/outils.py`, `POUSSEES` : `create`, `update`, `upsert`, `search` —
jamais `delete`, `merge` ni les `bulk_*`), gardée par `tests/test_recettes_outils.py`.

```json
{
  "mode": "push", "side_effects": true,
  "tool": "hubspot_object",
  "arguments": {"op": "create", "object_type": "contacts",
                "properties": {"email": "{{row.email}}", "firstname": "{{row.first_name}}"}},
  "rows": {"status_column": "hs_status", "require": ["email"], "filter": {"status": "validated"}},
  "id": {"column": "hubspot_id", "path": "id"},
  "lookup": {"arguments": {"op": "search", "object_type": "contacts",
                           "filters": [{"propertyName": "email", "operator": "EQ",
                                        "value": "{{row.email}}"}]},
             "items": "results", "id_path": "id"},
  "update": {"arguments": {"op": "update", "object_type": "contacts",
                           "object_id": "{{row.hubspot_id}}",
                           "properties": {"firstname": "{{row.first_name}}"}}},
  "limits": {"max_units": 100}
}
```

- **Tout se dit** : `side_effects: true` ; l'`op` est écrite en toutes lettres (jamais un
  gabarit) et revérifiée sur ce qui part ; un outil qui peut déclencher un envoi (une
  piste ajoutée à une campagne lemlist) exige `allow_sending: true`, où qu'il soit dans
  la recette ; une recherche (`lookup`) est un outil en lecture ou l'op `search` d'un
  outil de poussée ; `rows.require`
  est obligatoire.
- **Jamais deux fois** : une ligne qui porte déjà son identifiant est mise à jour
  (`update` déclaré → `updated`) ou laissée (`exists`, aucun appel) ; sinon `lookup`,
  s'il est déclaré, cherche la fiche — trouvée, elle est liée (`linked`), plusieurs
  marquent `failed:ambiguous`, une fiche sans identifiant lisible `failed:lookup_no_id`
  (jamais une création à côté) ; sinon création (`created`). `lookup.items` (le chemin
  de la LISTE des fiches trouvées) est obligatoire. ⚠️ La recherche doit être
  EXACTE (un filtre d'égalité, pas une recherche plein texte) : une seule fiche voisine
  trouvée serait liée, puis mise à jour. L'identifiant est noté SANS
  garde de révision, aussitôt la fiche créée.
- **Une création dont l'issue est inconnue** (délai, panne) marque
  `failed:unknown_outcome` et n'est jamais refaite d'office : la fiche existe peut-être.
  Trois d'affilée arrêtent l'exécution — chacune déjà marquée.
  Un refus qui dit que rien n'a été fait (clé, crédits, débit) arrête l'exécution et
  laisse la ligne en attente.
- **Jamais un champ vidé** : un argument dont la valeur est vide n'est pas envoyé.
- **`errors`** (un chemin dans la réponse) : une réponse « réussie » qui y porte quelque
  chose (doublon, champ refusé) marque `failed:provider_error`.
- **`test` et `publish` sont une marche à blanc** : rien n'est écrit chez le tiers — seule
  la recherche (`lookup`), qui lit, est exécutée ; le reçu dit ce qui serait créé, lié,
  mis à jour ou laissé (`dry_run`) et le remplissage de chaque argument — des comptes,
  jamais une valeur.
- **Une entrée exigée qui se rend vide** (`{{row.email|email}}` sur `n/a`) n'appelle pas :
  `failed:invalid_input`. L'identifiant qu'on ne peut pas réécrire dans la ligne la
  marque `failed:id_not_written` ; si même cela échoue, l'exécution s'arrête en le disant.
- ⚠️ **Ni `push` ni `async` dans un agent hébergé** (`side_effect_recipes_not_in_hosted_agents`),
  même quand la liste de son travail porterait l'outil : un agent hébergé lit du texte
  non sûr (webhook, e-mails, CRM), il ne pilote pas une écriture chez un tiers.

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

Apify en `pull` asynchrone, les soumissions et poussées groupées (un appel pour
cinquante lignes), puis les travaux de fond déclenchés par une planification ou un
webhook (l'exécutant reste à choisir).
