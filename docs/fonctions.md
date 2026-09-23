---
title: Les fonctions — du code pur, stocké et exécuté par Oto
type: explanation
description: >-
  `oto_function` (ADR 0073) : un calcul propre à un client — entrée JSON, résultat,
  avertissements, fichiers — stocké en versions immuables, publié par la plateforme
  sous la garde de ses tests, et exécuté dans un bac à sable Pyodide sous Deno sans
  réseau. Ce que le bac à sable refuse au code et comment on le prouve, où il vit sur
  une instance, ce que les tests garantissent et ce qu'ils ne garantissent pas. À lire
  avant de toucher `oto_mcp/functions/`, `db/functions.py` ou `capabilities/functions.py`.
---

# Les fonctions

Une **fonction** remplace le micro-service qu'on montait à côté d'Oto pour quelques
centaines de lignes de calcul (le pattern bridge d'ADR 0037). Elle **ne lit rien d'Oto,
n'appelle aucun réseau et ne voit aucun secret** : l'agent lui passe tout ce dont elle a
besoin, elle rend un résultat exact. Décision et arbitrages : ADR 0073 (meta-repo).

## Les verbes, et qui peut quoi

| op | ce qu'elle fait | qui |
|---|---|---|
| `list`, `get`, `versions` | lire (le code n'est rendu que par `get`) | membre du scope |
| `create`, `propose` | écrire une version, toujours PROPOSÉE | membre du scope |
| `run` | exécuter la version PUBLIÉE ; ses fichiers vont dans `project` | membre du scope |
| `test`, `publish`, `refuse` | exécuter du code non publié, et en décider | **plateforme** |

Scope : l'org active (défaut) ou `scope='user'`. Surface bêta (`BETA_TOOLS`), REST
`POST /api/me/functions`.

- **Une version est immuable.** Corriger, c'est proposer la suivante (`expected_version`
  refuse une proposition faite sur une lecture périmée). Seul change son statut :
  `proposee` → `publiee` | `refusee`.
- **Publier rejoue les tests dans le bac à sable** et refuse une version sans test
  (`no_tests`) ou avec un test rouge (`tests_failed`, les cas en `details`).
- **Republier une version antérieure est le retour arrière** : le pointeur
  `functions.published_version` revient dessus, rien n'est réécrit.
- **Stockage** : deux tables à part (`functions`, `function_versions`), pas des nœuds.
  ADR 0073 range une fonction comme une page de rôle `fonction` ; les pages natives
  n'ont encore ni versions ni verrou (ADR 0072), le rattachement viendra avec elles.

## Le contrat d'une version

`sources` = `{nom: contenu}` à plat : modules `.py`, tests `test_*.py`, données `.json`
(`functions/contract.py`, qui valide à l'écriture ce que l'exécuteur chargera).
`entrypoint` = `module:fonction`, appelée avec l'entrée, qui rend
`{"result": …, "warnings": [str], "files": [{"name", "content": bytes, "mime"?}]}`.
Dépendances : la liste blanche `contract.DEPENDANCES` (aujourd'hui `openpyxl`), des roues
Python PURES embarquées avec le bac à sable.

Les tests sont des **fonctions `test_*` simples** (pas de pytest) : le harnais les appelle
une à une. Un test peut lire un `.json` posé à côté (`open('cas.json')`).

## Le bac à sable

`functions/executor.py` lance **un processus Deno par exécution** (`functions/sandbox.ts`),
hors de la boucle (`run_in_threadpool`), qui charge Pyodide et joue le harnais
(`functions/harness.pyodide`, lu comme du texte — extension non `.py`, pour que rien ne l’importe). Les sources vivent dans le système de
fichiers EN MÉMOIRE de Pyodide.

| barrière | comment elle tient | preuve |
|---|---|---|
| lecture du disque, écriture, environnement, sous-processus | une seule permission Deno : `--allow-read` sur Pyodide et les roues | `test_le_code_ne_sort_pas_du_bac_a_sable` (éprouvé : ouvrir les permissions le fait rougir) |
| réseau | aucune permission ; paquets résolus en `--cached-only` contre `deno.lock` | lu sur la commande lancée |
| secrets du serveur | environnement du processus VIDÉ (`DENO_DIR`, `NO_COLOR`, `PATH`) | lu sur l'appel |
| réponse forgée | la réponse porte un nonce gardé hors de `globalThis` ; sans lui, échec | `test_une_reponse_forgee_sans_nonce…` |
| boucle sans fin | délai (`DELAI_S`), processus tué | `test_une_boucle_sans_fin…` |

⚠️ **Les tests prouvent la non-régression d'un code HONNÊTE, pas l'innocuité d'un code
hostile** : le code tourne dans le même interpréteur que le harnais et pourrait le
tromper. C'est la relecture avant publication, réservée à la plateforme, qui garde ce
point — et la raison pour laquelle `test`/`publish` n'ont pas été ouverts aux orgs.

⚠️ **Pas de plafond mémoire propre au processus** : `--max-old-space-size` borne le tas
V8, pas la mémoire WASM de Pyodide. Un code qui alloue sans fin est coupé par le délai.
Plafonds chiffrés : à mesurer sur le pilote (ADR 0073 §5).

⚠️ **Pyodide 0.26 tourne sous Deno par la compatibilité Node**, donc en `npm:` : importé
depuis un fichier local, il se croit sous Node et appelle `require`. Le cache npm se
remplit par un PREMIER LANCEMENT réel (`deno cache` ne rapatrie pas les dépendances
optionnelles de `ws`, que Deno résout au lancement) et l'exécuteur lance Deno contre le
verrou (`--lock --frozen`) : sans lui, Deno refait une résolution que le cache n'a pas.

## Où vit le bac à sable

`OTO_FUNCTIONS_SANDBOX_DIR` → un répertoire posé par
`python scripts/installer_bac_a_sable.py <dir>` : `deno` (binaire officiel, SHA-256),
`deno_dir/` (cache npm, intégrité SHA-512 du verrou), `wheels/` (SHA-256 PyPI). Le réseau
ne sert qu'à l'installation ; le répertoire peut ensuite passer en lecture seule.
L'installation se termine par une exécution de contrôle.

Absente, l'instance n'exécute pas de fonction : `run`/`test`/`publish` rendent 503
`sandbox_unavailable`, le reste répond. La CI l'installe avant la suite (sinon les bancs
de l'exécuteur ÉCHOUENT en CI ; sur un poste sans bac à sable ils sont passés en le
disant).

**Mettre à jour Pyodide** : la version est dite à trois endroits — `sandbox.ts`
(`npm:pyodide@…`), `executor.PYODIDE`, `deno.lock` — que
`test_la_version_de_pyodide_est_la_meme_partout` garde alignés ; régénérer le verrou par
`deno cache --no-config --lock=oto_mcp/functions/deno.lock oto_mcp/functions/sandbox.ts`.

## Ce que le journal garde

Chaque appel passe par `tool_calls` comme tout outil — arguments compris
(`calllog.truncated_args`, bornés à `MAX_ARG_CHARS` par valeur, la coupe déclarée) : l'entrée d'une fonction n'y échappe pas.
Les fichiers produits vont dans `project_files` (qui, quand, quel projet).
