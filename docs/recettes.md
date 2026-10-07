---
title: Les recettes — un outil de connecteur vers un tableau, sans modèle
type: explanation
description: >-
  `oto_recipe` : une description stockée et versionnée de la façon dont les résultats
  d'un outil de connecteur arrivent dans un tableau — outil, arguments, pagination,
  correspondance champ → colonne, clé —, exécutée par le serveur sans qu'aucun modèle
  ne relise ni ne recopie les lignes. Ce que ce premier lot couvre (le mode `pull`),
  ce qu'il borne (un agent hébergé, à la liste d'outils de son travail), et ce qui vient ensuite. À lire avant de
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
- **Gabarits** `{{params.x}}`, `{{item.a.b}}`, filtres `slug`, `lower`, `upper`, `strip`,
  `unaccent`. Un gabarit seul garde le type de sa valeur ; mêlé à du texte, il devient
  du texte.
- ⚠️ **`slug` reproduit la forme des clés déjà écrites** par les procédures de sourcing :
  minuscules, accents retirés, chaque suite non alphanumérique réduite à `_`, aucun `_`
  aux bords. La changer dédoublerait chaque ligne au premier passage.
- **`where`** : `eq`, `ne`, `in`, `not_in`, `contains_any`, `empty`, `not_empty`, sans
  casse ni accents.
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
- **Budget d'horloge de 30 s** et **`max_pages` par appel** : au-delà, reçu partiel et
  `resume`, que l'appel suivant passe pour continuer sans repayer les pages faites. Le
  plafond de dépense, lui, vaut pour toute la chaîne (le jeton porte la dépense faite).
- **Seule une version PUBLIÉE écrit.** `publish` éprouve une page réelle sans écrire ;
  une recette passée en ligne (`recipe`) ne sert qu'à `test` — `run` la refuse
  (`inline_recipe_cannot_write`).
- **Le reçu ne porte que des comptes et des codes** : pages, éléments vus, unités,
  lignes écrites / mises à jour / laissées intactes / écartées, codes d'échec.
- **Dans un agent hébergé, l'outil de la recette doit être dans la liste du travail.**
  Le jeton d'un travail du runner porte son travail (`user_api_tokens.job_id`) ; `sample`,
  `test`, `publish` et `run` vérifient que l'outil appelé figure EN TOUTES LETTRES dans
  `payload.tools` de ce travail (noms canoniques, alias dépréciés compris), avant tout
  appel — sinon `recipe_tool_not_in_agent_tools`. ⚠️ `oto_call` dans la liste n'ouvre
  rien : sinon une recette rendrait à l'agent tous les outils de son porteur. Le
  déclencheur liste donc `oto_recipe` ET l'outil de la recette. Un jeton de délégation qui
  ne porte pas son travail (émis avant) reste refusé (`hosted_runs_not_supported`). L'org
  est tenue par le verrou du jeton (`verrou_org.py`) : une recette pour une autre org du
  porteur est refusée (`org_out_of_job`).

## Ce qui vient ensuite

`for_each` (une ligne qui déclenche un appel dont les éléments deviennent des lignes —
les personnes d'une société), le bloc `async` (soumettre puis collecter : Dropcontact,
FullEnrich, Apify), le mode par ligne et la poussée vers un CRM, les correspondances
avec d'autres tableaux, les normaliseurs (`domain`, `linkedin_slug`, `phone_e164`), la
détection de dérive contre le remplissage gardé à la publication, et les travaux de
fond déclenchés par une planification ou un webhook.
