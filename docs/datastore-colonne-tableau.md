---
title: Spec — la colonne-tableau
type: explanation
description: >-
  Comment une colonne de datastore porte une petite liste de fiches (les
  interlocuteurs d'une entreprise) et reste interrogeable : forme servie et garantie
  du nom nu, couches d'un attribut d'item, ce que déclare le schéma (`type: list`,
  `of`, `max_items`), les quatre fonctions natives et les deux non-définitions
  assumées. Le double-service qui servait les anciens noms `contactN_*` pendant une
  bascule est RETIRÉ (07/09/2026, §6). À charger avant de toucher aux sous-tableaux,
  à la conversion (#332) ou aux étapes 5-6.
adr: [0046]
---

# Spec — la colonne-tableau (oto#22, barreau 2)

**But** : une colonne porte une petite liste de fiches (les interlocuteurs d'une
entreprise : 1 à 4 contacts × nom, fonction, email, téléphone…) et reste
interrogeable, écrivable et exportable — sans qu'aucun consommateur ne reconstruise sa
convention. Aujourd'hui la même notion occupe 21 colonnes numérotées sur un vivier
réel, et scout a retiré la lecture des listes le 10/08 faute de contrat.

**Principe de la FEUILLE** (cadre posé le 14/08) : rien ne se conçoit « pour les
listes ». Tout se conçoit pour la FEUILLE — l'unité terminale porteuse de valeur — et
la liste compose. Chaque règle ci-dessous est donc une règle déjà vraie au premier
niveau, appliquée un cran plus bas.

**Ce qui existe déjà et n'est pas à refaire** : la DÉCLARATION. `type: "list"` + `of:`
est dans le schéma depuis ADR 0046, validée, et `patch_schema` sait déjà descendre
dans `of`. Le barreau 2 n'ajoute pas un type : il ajoute ce qui rend ce type
utilisable.

---

## 1. La forme SERVIE (question 1 de scout)

```jsonc
row["contacts"]  →  [ {"nom": "Dupont", "fonction": "DRH", "email": "d@x.fr"},
                      {"nom": "Martin", "fonction": "DAF", "email": null} ]
```

**Le nom nu rend toujours la valeur, jamais la structure interne** — la garantie du
premier niveau, transposée. Un attribut d'item est une feuille : `row["contacts"][0]
["email"]` est une chaîne, qu'il porte une provenance ou non.

> ⚠️ **Ce n'est pas le comportement actuel.** `unwrap` ne descend pas dans les listes :
> un item dont l'attribut porte des couches ressort aujourd'hui enveloppé
> (`{"nom": {"valeur": "A", "origine": "socle"}}`). C'est la rupture exacte du contrat
> « le nom nu rend la valeur ». **Le barreau 2 étend `unwrap` en profondeur** (listes
> et objets), et c'est son premier changement.

Une colonne-tableau vide rend `[]`, jamais `null` — un consommateur itère sans garde.

## 2. Les couches d'une feuille d'item (question 2)

Au premier niveau, une couche s'aplatit en `champ.couche`. **Un item applique la même
règle chez lui** :

```jsonc
row["contacts"][0]  →  {"nom": "Dupont", "email": "d@x.fr",
                        "email.origine": "socle client", "email.comment": "hunter"}
```

Rien de neuf à apprendre : qui sait lire `row["email.origine"]` sait lire
`item["email.origine"]`. Les couches vides ne sont pas rendues, comme au premier
niveau.

**Adressage** (filtres, projection, écriture) — deux formes, et l'ambiguïté est levée
par la syntaxe, jamais devinée :

| chemin | désigne |
|---|---|
| `contacts[].email` | l'email de N'IMPORTE QUEL item (existence, agrégat) |
| `contacts[].email.origine` | idem, sa couche |
| `contacts[0].email` | l'email de l'item de rang 0 (écriture, projection) |

Le rang est **0-indexé**, comme partout ailleurs dans le produit. `split_layer`
s'étend en un résolveur de chemin unique — il ne se duplique pas.

## 3. Ce que déclare le schéma (question 3)

```jsonc
{"key": "contacts", "type": "list", "label": "Interlocuteurs",
 "max_items": 4,
 "of": {"type": "object", "fields": [
    {"key": "nom",      "type": "text",  "label": "Nom",      "description": "…"},
    {"key": "fonction", "type": "text",  "label": "Fonction"},
    {"key": "email",    "type": "email", "label": "E-mail"}]}}
```

Les attributs se déclarent **exactement comme un field de premier niveau** (`key`,
`type`, `label`, `description`, `required`, `max_length`, `options`…) — scout
dérive tous ses écrans du schéma, il n'a donc rien de spécial à apprendre.

⚠️ `enum` n'est pas un attribut : c'est une VALEUR de `type` (`"type": "enum"`), et
c'est `options` qui porte les valeurs permises. La liste faisant autorité, avec le
lecteur de chaque attribut, est servie sur `GET /api/datastore/schema/keys`.

**Deux ajouts** :

- `max_items` (entier, optionnel) — borne la liste à l'écriture. Il ne fixe aucune
  colonne d'export : l'export CSV rend la colonne-liste en une seule cellule, chaque élément en JSON, joints par `; ` (§5.3).
- `description` — aujourd'hui ni validé ni servi (le module pur laisse passer les clés
  inconnues). À faire traverser jusqu'aux consommateurs, sinon scout n'a rien à
  afficher sous un intitulé.

> **Correction de fait (revue du 13/08, hors amendement)** : « `description` sera enfin
> servie » ne change RIEN au premier niveau — les descriptions traversaient déjà (schéma
> stocké et rendu tel quel), l'étape 2 (l'ordre d'implémentation est au chantier) n'a fait
> que FIGER l'existant par un test. L'exposition de descriptions écrites pour des agents
> sur des écrans clients est PRÉEXISTANTE — c'est une relecture ÉDITORIALE côté mission,
> pas un gate de plateforme.

**Sur un tableau dont le format fait contrat (`unknown_columns` à `"report"` ou `"reject"`, ex-`strict`), `of.fields` FERME la fiche (#544, 29/08/2026).** C'est le
principe de la FEUILLE appliqué au référentiel : ce qui vaut au premier niveau vaut un
cran plus bas — à ceci près que le sens s'y **inverse**, et il faut le dire. En tête de
ligne, une clé inconnue crée une **colonne** libre, que l'interface affiche : elle est
signalée (`hors_schema`), jamais refusée, parce que c'est ce qui permet d'explorer un
tableau avant de le typer. Dans un item, il n'existe pas de sous-colonne libre :
`of.fields` est le seul référentiel, et un attribut non déclaré serait stocké là où ni
le schéma ni l'interface ne le lisent (l'export CSV rend la colonne-liste en une seule cellule, chaque élément en JSON, joints par `; `, sans colonne par attribut). Il est donc
**refusé**, en nommant l'élément : `contacts[1].email_pattern`.

Trois conséquences pour qui déclare une colonne-tableau :

- **déclarer `of.fields`, c'est fermer la fiche** sur un tableau `unknown_columns` autre que `"create"` — un attribut
  de plus se déclare (`data_patch_schema` descend dans `of`) avant d'être écrit ;
- **ne pas déclarer de champs sous `of` laisse la liste LIBRE**, à tout étage : sans
  référentiel, rien n'est hors référentiel. C'est le choix à faire tant que la forme
  d'un item bouge encore ;
- **les couches d'un attribut ne sont pas des attributs** : `email.origine` et
  `email.comment` traversent la fermeture — c'est la forme SERVIE d'un item (§2), donc
  ce qu'un aller-retour lecture → écriture repose tel quel.

## 4. Les deux NON-définitions, assumées

- **Ni égalité ni tri sur la colonne ENTIÈRE.** Trois emails n'ont pas « une » valeur ;
  une colonne ne se réduit pas. `{"field": "contacts", "op": "eq"}` est **refusé en le
  nommant** (« `contacts` est une colonne `list`, qui ne se compare pas en bloc… Vise
  un attribut de ses éléments : `contacts[].fonction` ») — jamais un tri arbitraire
  silencieux, qui rendrait un ordre reproductible et faux. Armé le 30/09/2026 : la
  garde lit le type déclaré (`list` ou `object`) et refuse `eq`, `ne`, `in`, `gt`,
  `gte`, `lt`, `lte` ; `empty`, `not_empty` et `contains` restent permis sur la colonne
  entière. ⚠️ `not_empty` y lit le texte : une liste vide `[]` compte comme remplie.
- **Une clé métier n'est JAMAIS un sous-tableau.** Refus à la DÉCLARATION du schéma,
  pas à la première écriture.

## 5. Les quatre fonctions natives

### 5.1 Existence et agrégat à travers les items

Le chemin `contacts[].fonction` est une cible de `filters`, de `group_by` et des
métriques, avec la grammaire du barreau 1 inchangée :

```jsonc
filters: [{"field": "contacts[].fonction", "op": "in", "value": ["DRH", "DAF"]}]
group_by: "contacts[].fonction"
metrics: [{"op": "count"}, {"op": "count_rows"},
          {"op": "avg", "field": "contacts[].anciennete"}]
```

- **Filtre = existence** : « il existe un contact dont… ». `match` ne descend jamais
  dans les items, il joint les CIBLES déclarées (`fields`), comme au premier niveau.
  Le filtre choisit des LIGNES : une fiche retenue apporte tous ses contacts à
  l'agrégat.
- **`group_by` = occurrences** (livré le 30/09/2026) : chaque item de chaque ligne
  retenue compte une fois. Même vocabulaire que l'union multi-colonnes : `count`
  compte les contacts, `count_rows` les fiches. Un item sans l'attribut tombe dans le
  groupe `null`, comme une ligne sans valeur sous un `group_by` ordinaire ; une liste
  vide, absente ou pas encore convertie n'apporte rien. La couche d'un attribut se
  regroupe pareil (`contacts[].email.origine` : « combien d'adresses viennent de
  telle source »).
- **Métriques** (`count`, `sum`, `avg`, `min`, `max` sur `contacts[].<attribut>`) :
  sur tous les items. Groupé par la même liste, l'attribut se lit sur l'item courant ;
  sinon chaque ligne réduit ses items avant l'agrégat, et `avg` pèse chaque ITEM (somme
  des sommes sur somme des comptes), jamais une moyenne de moyennes par fiche.
- **Refusés en le nommant** : le tri, la mise en commun d'un `contacts[].x` avec
  d'autres colonnes (`group_by` en liste), et une métrique sur une AUTRE liste que
  celle que `group_by` déroule (le grain serait ambigu).
- **Une liste de VALEURS se lit par l'élément nu** (oto#102, 04/10/2026) : `tags[]`
  est l'élément lui-même — `group_by: "tags[]"` (un groupe par valeur, `count` les
  occurrences, `count_rows` les lignes), `filters` par existence, `count` en métrique
  — et `tags[0]` l'élément de rang précis, une valeur par ligne. Même grammaire que
  l'écriture par rang : le nom de colonne n'y porte ni espace ni point, `Note [1]`
  reste une colonne.

SQL : `jsonb_array_elements` sous garde de type (`list_items_sql`, `db/paths.py`) —
dans un `EXISTS` pour le filtre, dans un `LATERAL` pour le `group_by`, au même endroit
que l'union multi-colonnes. Mesurer avant tout index : une liste de 4 items sur 9 000
lignes ne justifie sans doute rien.

### 5.2 Adressage d'un rang à l'écriture — livré (oto#22, point c)

L'enrichissement pose une valeur sur un élément sans réécrire la liste. La forme
canonique est le **chemin à plat de la lecture** (`split_list_path`, §5.1) : une case
n'a qu'une adresse, la même pour lire, filtrer, agréger et écrire.

```jsonc
data_write(id=…, row={"contacts[1].email": "d@x.fr",            // l'attribut
                      "contacts[1].email.comment": "site officiel"}) // une couche
data_write(id=…, row={"contacts[+]": {"nom": "Cy", "email": "c@x.fr"}}) // ajout
data_write(id=…, row={"contacts[0]": null})                        // suppression
data_write(id=…, row={"tags[+]": ["relance", "chaud"]})             // ajout de plusieurs
data_write(id=…, row={"tags[-]": "chaud"})                          // retrait par valeur
```

- **Un attribut s'écrit comme une colonne**, et se FUSIONNE dans l'élément en place par
  la règle de #322/#326 (`_merge_column`) : valeur nue ou `{"valeur": …, "comment": …}`,
  l'origine survit, `comment`/`link` tombent avec une valeur qui change, `null` efface
  l'attribut, `@empty` dit « cherché, rien ». Les autres attributs et les autres
  éléments ne bougent pas — couches comprises, et les couches de la COLONNE aussi.
- **Une couche seule** (`contacts[1].email.comment`) annote l'attribut en place.
- **`contacts[+]`** ajoute UNE fiche complète en fin de liste ; ses couches pointées
  (`"email.comment"`) se rangent comme dans une liste posée entière. **Une LISTE
  d'éléments** s'ajoute dans l'ordre (oto#102) ; les doublons sont gardés — une liste
  est ordonnée, pas un ensemble —, une identité `of.key` doublée reste refusée. Une
  colonne dont les éléments sont eux-mêmes des listes enveloppe l'élément : `[[…]]`.
- **`tags[-]`** retire une valeur, ou chaque valeur d'une liste, toutes ses occurrences
  (oto#102). Liste de VALEURS seulement : une fiche n'a pas d'égalité servie et se
  retire à son rang. L'égalité est celle du JSON (`true` n'est pas `1`, `1` vaut
  `1.0`), après la normalisation des dates. Une valeur absente se refuse comme un rang
  hors bornes : rien n'est écrit.
- **L'ajout et le retrait ne demandent aucune lecture** : résolus sous le verrou, deux
  ajouts simultanés sur la même ligne arrivent tous les deux, sans `expected_revision`
  ni réservation. Ordre dans un geste : rangs, retraits, ajouts.
- **`contacts[n]: null`** supprime l'élément. Supprimer le dernier efface la colonne,
  comme un `null` (la valeur partie revient dans `valeurs_effacees`).
- **Tous les rangs d'un geste désignent la liste EN PLACE**, avant le geste ; l'ajout se
  fait en dernier. Le geste se résout sous le verrou de la ligne : deux écritures
  concurrentes sur deux éléments ne s'écrasent pas.
- **Validation** (J4) : seuls les éléments que le geste modifie ou ajoute sont jugés —
  types, `required`, `options`, `required_layers` —, avec ou sans `of.key` ; la charge
  `a_renvoyer` ne porte que l'élément fautif.
- **Même fusion, même journal, toutes les faces** : `data_write` (unitaire, `id`, lot),
  REST (`POST`/`PATCH` d'une ligne, lot). La révision porte l'avant et l'après de la
  colonne entière.

**Refus nommés, chacun avec la forme qui aboutit** :

| geste | refus |
| --- | --- |
| `contacts[5].email` sur 2 éléments | « `contacts` a 2 éléments (rangs 0 à 1) ; rang 5 inexistant. Pour ajouter : `contacts[+]` » — sur une ligne créée par le geste : « cette écriture CRÉE la ligne » |
| `contacts[0]: {…}` | la forme imbriquée n'est pas servie : `contacts[0].<attribut>` |
| `contacts[].email` | adresse de lecture (TOUS les éléments), pas d'écriture |
| `contacts[+].email` | un élément s'ajoute entier : `"contacts[+]": {…}` |
| `contacts[+]: null`, `[]`, un `null` dans la liste | rien à ajouter ; pour effacer : `"contacts": null` |
| `contacts[-]` (liste de fiches) | une fiche se retire à son rang : `"contacts[<rang>]": null` |
| `tags[-]: "zz"` absent | « `tags` ne porte pas `"zz"` … rien à retirer » |
| `contacts[0].adresse.ville` | un attribut d'élément s'écrit entier, ou par une couche |
| `contacts` et `contacts[0].x` ensemble | deux écritures d'une même colonne |
| `contacts[0]: null` et `contacts[0].x` | supprimé et modifié à la fois |
| `siren[0].x` (colonne déclarée non-liste) | seule une colonne `type: list` s'adresse par rang |
| sous `of.key`, une identité qui deviendrait double | refus nommant la valeur |

**Écarté** : la spec d'origine voulait qu'un rang au-delà de la longueur ÉTENDE la
liste, avec des trous servis `{}`. Un rang hors bornes est une adresse fautive — le
plus souvent une liste relue avant qu'un autre geste ne la raccourcisse — et
l'étendre fabriquerait des éléments vides que personne n'a demandés : il se refuse,
et l'ajout a son verbe (`contacts[+]`).

**Proposé, non livré : la désignation par identité** — `contacts[role=DAF].email`, sur
une liste qui déclare `of.key`. Plus robuste qu'un rang (il ne bouge pas quand un
élément part), mais il faut une règle de citation pour une valeur d'identité qui
porte un point ou un crochet, et la même adresse en LECTURE (filtre, `group_by`) —
sinon on ouvre une forme d'écriture que rien ne relit. Refusée aujourd'hui en
orientant vers le rang.

**Ce qui ressemble sans être une adresse de rang reste un nom de colonne** : la
grammaire exige un nom sans espace ni point et un rang entier, `+`, vide ou
`clé=valeur`. `Prix [EUR]` ou `note[a]` restent des colonnes ordinaires, comme avant.

### 5.3 Aplatissement d'export DÉTERMINISTE — non livré, écarté le 30/09/2026

> ⚠️ **Ce qui est servi** : l'export CSV du tableau (#1006) existe, et l'export CSV rend la colonne-liste en une seule cellule, chaque élément en JSON, joints par `; `. Ce
> comportement RESTE (décision du 30/09/2026, oto#22 point d) ; l'export sera repensé
> à terme. Rien de ce qui suit n'est implémenté : c'est la spec d'origine, gardée pour
> le jour où l'aplatissement reviendra au programme.

Sans lui, chaque consommateur reconstruit `contact1_*` en sortie — la forme qu'on
quitte. La projection à plat est native et **déterministe** : mêmes colonnes, même
ordre, quel que soit le contenu des lignes.

- colonnes = produit `(rang, attribut)` dans l'ordre **déclaré** (rang 0 d'abord,
  attributs dans l'ordre de `of.fields`) ;
- nombre de rangs = `max_items`, sinon le maximum observé (et alors **annoncé** dans
  la réponse, parce que le fichier de demain n'aura pas les mêmes colonnes) ;
- nom de colonne = gabarit **déclaré**, défaut `contact1_nom` → `{key}{n}_{attr}` avec
  `n` **1-indexé** (les humains lisent « contact1 », pas « contact0 ») ;
- **les couches sont EXCLUES par défaut** — les inclure quadruple la largeur (7
  attributs × 4 rangs × 4 couches = 112 colonnes, l'écueil mesuré qui avait fermé ce
  dossier). Option explicite pour les demander.

**Éprouvé contre le cas Excel** : le test d'acceptation est un export du vivier réel
ouvert dans Excel — colonnes stables entre deux exports, aucune colonne à rallonge,
et la relecture du fichier redonne les mêmes items.

### 5.4 Composition avec les couches

Rien de plus que §1 + §2 : le point de lecture unique descend, la machinerie de
chemins est la même. C'est la seule façon d'éviter deux vocabulaires.

## 6. Le chemin de MIGRATION — double-service ~~servi~~ RETIRÉ le 07/09/2026

> ⚠️ **`flat_alias` n'existe plus.** Le double-service décrit ici a été livré le
> 13/08/2026 puis retiré le 07/09/2026, sur mesure de production : **0 colonne sur
> 5 615, 0 tableau sur 383**. Et la migration qu'il devait couvrir n'a jamais
> commencé — la **conversion**, que ce paragraphe décrit comme le geste central,
> n'a jamais été écrite.
>
> Le point qui a tranché n'est pas le non-emploi : c'est que l'attribut était
> **déclaré lu par le FRONT** dans le registre servi (`GET
> /api/datastore/schema/keys`), ce qui était faux. Une capacité non employée coûte
> peu ; une capacité dont le contrat affirme qu'elle est consommée fait construire
> dessus. Cf. le même arbitrage six jours plus tôt sur le cran de valeur `system` :
> une capacité annoncée et jamais employée s'implémente pour de bon ou se dé-annonce.

**Ce que le besoin réclamait**, et qui reste vrai le jour où une bascule se présente :
le premier tableau visé est regardé quotidiennement par une cliente, donc la bascule ne
peut pas être un basculement sec. Il faudra alors une fenêtre pendant laquelle les
écrans qui parlent `contact1_nom` continuent de répondre.

**Ce qu'il faudra reprendre tel quel**, parce que ces trois points ont coûté leur revue
et qu'ils ne dépendent pas de la forme retenue :

1. le gabarit est **DÉCLARÉ, jamais deviné** — résoudre `contact1_nom` vers
   `contacts[0].nom` en interprétant un motif de nom rouvrirait exactement ce que le
   barreau 1 a fermé, et il n'y a pas de défaut possible (`{key}{n}_{attr}` rend
   `contacts1_nom`, pas `contact1_nom`) ;
2. la projection est **calculée, jamais stockée** — la stocker ferait deux vérités à
   réconcilier — donc en **lecture seule** : une écriture sur le nom projeté se refuse
   en nommant une destination qui EXISTE (oto#121 — l'ancienne prescrivait
   `contacts[0].nom`, refusée deux gardes plus loin) ;
3. **la conversion est la moitié qui manquait, et c'est elle qu'il faut écrire
   d'abord.** Elle lit les colonnes plates, écrit la liste en une passe idempotente
   avec un compte avant/après par ligne, puis **supprime les colonnes plates
   sources** — ordre non négociable : copier → **vérifier** → purger. Une purge avant
   vérification transforme une conversion ratée en perte.

Le retrait de la fenêtre reste ce que la revue du 13/08 en disait (§10.1) : un geste
**DISTINCT et annoncé**, jamais la suite mécanique de la purge — les consommateurs
gardent des listes qui nomment des colonnes, et elles pointeraient dans le vide en
silence.

## 7. L'homologue côté PAGES — la question, posée

La discipline de concept demande de la poser, pas d'y répondre en douce : **un bloc de
page porte-t-il, lui aussi, des feuilles répétées ?** Si oui, il devra la même
machinerie de chemins, et la nommer autrement serait fabriquer un second vocabulaire
pour la même idée. Question ouverte, à trancher avant que les pages n'inventent leur
forme.

## 8. Les arbitrages, l'ordre d'implémentation et les revues — au chantier

> Les **choix de forme** tranchés sans mandat (ex-§8), l'**ordre d'implémentation** en six
> étapes (ex-§9), les **amendements des deux revues du 13/08** (ex-§10 et ex-§11) et les
> deux arbitrages qui suivaient (ex-§12 et ex-§12 — la spec en portait **deux** du même
> numéro : `match` joint les cibles, et le patron d'aplatissement de référence) vivent
> dans `oto-private`, `docs/chantiers/chantier-colonne-tableau.md`.
>
> ⚠️ **Ces amendements amendent les sections ci-dessus** — l'ordre des items (§1), le
> gabarit d'export **obligatoire**, sans défaut (§5.3), le verbe d'ajout `contacts[+]`
> (§5.2), la borne de longueur qui ne juge que les feuilles écrites (§3 et §5.2) : les
> lire au chantier **avant** d'implémenter une étape. Ce qu'une étape livrée rend vrai
> revient ici, dans le même commit.
