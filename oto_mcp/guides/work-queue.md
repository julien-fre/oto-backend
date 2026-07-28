---
title: "File de travail : drainer un vivier avec N agents"
description: claim atomique, bail, statut terminal — le cycle qui garantit qu'une flotte de sous-agents traite un tableau sans doublon ni ligne perdue
---

# Drainer un tableau avec plusieurs agents

À lire **avant** tout fan-out sur un vivier partagé : enrichir N leads, qualifier N
sites, traiter N dossiers avec plusieurs sous-agents en parallèle. Le réflexe naturel —
lire la table, découper en lots, distribuer — est **non atomique** : deux agents qui
lisent au même instant voient la même ligne libre et la traitent tous les deux.

Le datastore a la primitive qui règle ça côté serveur : **`data_claim_next`**.

## Le principe : un bail, pas une liste d'exclusion

`data_claim_next(namespace, worker, filter?, lease_s?)` prend **la prochaine ligne
claimable et la réserve** dans la même transaction (`FOR UPDATE SKIP LOCKED`, patron
file de travail PostgreSQL). Deux workers concurrents n'obtiennent **jamais** la même
ligne — le second saute simplement à la suivante.

Le claim pose un **bail** : `_claimed_by` (ton libellé de worker) et `_claimed_until`
(maintenant + `lease_s`). Une ligne sous bail actif est invisible aux autres claims.

⚠️ **Le claim ne modifie PAS le contenu de la ligne** — pas de passage automatique en
« en cours ». C'est le bail, pas le statut, qui protège du double traitement. Ne
construis donc rien qui suppose que la ligne a changé après le claim.

Réponse : `{namespace, row}` avec `row = null` quand il n'y a plus rien à prendre
(file vide pour ce filtre, ou tout est déjà sous bail).

## Le cycle complet

```
1. row = data_claim_next(namespace="<table>", worker="<mon-libellé>",
                         filter={"status": "nouveau"})
2. si row == null  → terminé, le worker s'arrête
3. traiter row (enrichissement, appels connecteurs, raisonnement…)
4. data_write(namespace="<table>", id=row["_id"],
              row={"status": "traité", ...livrables})   ← libère le bail
5. reboucler en 1
```

**L'écriture d'un statut terminal libère le bail automatiquement.** C'est la fin
nominale d'un traitement — pas besoin d'appeler `data_release` derrière.

`data_release(namespace, id, worker)` ne sert qu'à **abandonner sans verdict** : tu
renonces à la ligne, elle redevient claimable immédiatement au lieu d'attendre la fin
du bail. Le `worker` y est rejoué comme garde — on ne libère pas le claim d'un autre.

## Les trois paramètres qui comptent

**`worker`** — un libellé que tu choisis (ex. `"enrich-13"`, `"qualif-nord-2"`), stable
pour un sous-agent donné et **rejoué verbatim** sur `data_release`. Il sert de garde et
rend la file lisible en supervision (« qui tient quoi »). Donne un libellé distinct par
sous-agent : c'est ce qui permet de voir lequel est mort.

**`filter`** — égalité exacte `{colonne: valeur}`, ce qui définit ce qui **compte comme
claimable**. Typiquement `{"status": "nouveau"}`. Sans filtre, toute ligne dont le bail
est libre est candidate — y compris celles déjà traitées. **Mets toujours un filtre**
dès que la table porte un statut.

**`lease_s`** — durée du bail, 900 s (15 min) par défaut. C'est le mécanisme de
récupération : un worker qui meurt en cours de route ne bloque pas sa ligne
éternellement, elle redevient claimable à l'expiration. Cale-le sur la durée réelle
d'un traitement, avec de la marge : trop court, une ligne lente se fait voler et
traiter deux fois ; trop long, une ligne abandonnée dort.

## ⚠️ Le piège : sans états terminaux déclarés, rien n'est libéré

L'auto-release ne se déclenche que si le schéma du namespace déclare un champ
`role: "status"` **avec un `lifecycle` dont on peut dériver des états terminaux** :

```json
{"key": "status", "role": "status",
 "lifecycle": {"states": ["nouveau", "en_cours", "traité", "écarté"],
               "transitions": {"nouveau": ["en_cours", "écarté"],
                               "en_cours": ["traité", "écarté"]},
               "terminal": ["traité", "écarté"]}}
```

Les états terminaux sont ceux de `terminal` s'il est déclaré, sinon **dérivés** = les
états sans transition sortante. Conséquences :

- **pas de `lifecycle`** (juste `role: "status"`) → aucun état terminal → **aucune
  libération automatique**, chaque ligne reste sous bail jusqu'à expiration ;
- `lifecycle` où **tout état a une transition sortante** → ensemble terminal vide,
  même symptôme. Déclare `terminal` explicitement, c'est plus sûr que de le dériver.

Symptôme à reconnaître : la file « se vide » alors que les lignes ne sont pas traitées,
puis se remplit à nouveau ~15 min plus tard. C'est le bail qui expire, pas un bug.

## Fan-out : ce que ça remplace

Avec le claim, un sous-agent n'a **pas besoin de savoir ce que font les autres**. Tu
n'as donc plus à :

- injecter une **liste d'exclusion** des lignes déjà prises à chaque agent (elle est
  périmée à la seconde où tu l'écris) ;
- découper en **lots de 3-4** figés à l'avance (un agent lent bloque son lot, un agent
  mort le perd) ;
- **dédupliquer en relisant la table** avant chaque traitement (coûteux et non atomique
  — deux relectures simultanées donnent le même verdict).

Le patron : lance N sous-agents identiques, chacun avec son `worker`, chacun bouclant
`claim → traiter → écrire un statut terminal` jusqu'à `row == null`. Le parallélisme se
règle par le nombre d'agents, pas par un découpage. Pour les gros volumes, combine avec
le guide `bulk-load` (garder les payloads hors du contexte principal).

## Divers

- `namespace` accepte `slot:<nom>` — le tableau bindé par le projet actif.
- Le bail n'apparaît sur une ligne (`_claimed_by` / `_claimed_until`) **que s'il est
  posé** : une lecture ordinaire d'une ligne libre n'a pas ces champs.
- L'ordre de service est l'ordre de création (les plus anciennes d'abord).
- Le dashboard montre les lignes sous bail (« en cours · worker ») et permet une
  libération forcée par un humain, sans garde de worker.
