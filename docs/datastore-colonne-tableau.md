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

- `max_items` (entier, optionnel) — borne la liste à l'écriture **et** fixe le nombre
  de colonnes de l'export à plat (§5.3). Sans lui, l'export est borné au maximum
  observé, ce qui le rend non déterministe d'un jour à l'autre : c'est pour ça qu'il
  faut le déclarer sur un tableau qu'on exporte.
- `description` — aujourd'hui ni validé ni servi (le module pur laisse passer les clés
  inconnues). À faire traverser jusqu'aux consommateurs, sinon scout n'a rien à
  afficher sous un intitulé.

> **Correction de fait (revue du 13/08, hors amendement)** : « `description` sera enfin
> servie » ne change RIEN au premier niveau — les descriptions traversaient déjà (schéma
> stocké et rendu tel quel), l'étape 2 (l'ordre d'implémentation est au chantier) n'a fait
> que FIGER l'existant par un test. L'exposition de descriptions écrites pour des agents
> sur des écrans clients est PRÉEXISTANTE — c'est une relecture ÉDITORIALE côté mission,
> pas un gate de plateforme.

**Sur un tableau `strict`, `of.fields` FERME la fiche (#544, 29/08/2026).** C'est le
principe de la FEUILLE appliqué au référentiel : ce qui vaut au premier niveau vaut un
cran plus bas — à ceci près que le sens s'y **inverse**, et il faut le dire. En tête de
ligne, une clé inconnue crée une **colonne** libre, que l'interface affiche : elle est
signalée (`hors_schema`), jamais refusée, parce que c'est ce qui permet d'explorer un
tableau avant de le typer. Dans un item, il n'existe pas de sous-colonne libre :
`of.fields` est le seul référentiel, l'export à plat (§5.3) dérive ses colonnes de lui,
et un attribut non déclaré serait stocké là où **rien** ne le lit. Il est donc
**refusé**, en nommant l'élément : `contacts[1].email_pattern`.

Trois conséquences pour qui déclare une colonne-tableau :

- **déclarer `of.fields`, c'est fermer la fiche** sur un tableau `strict` — un attribut
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
  nommant** (« une colonne-tableau ne se compare pas : viser `contacts[].<attribut>` »)
  — jamais un tri arbitraire silencieux, qui rendrait un ordre reproductible et faux.
- **Une clé métier n'est JAMAIS un sous-tableau.** Refus à la DÉCLARATION du schéma,
  pas à la première écriture.

## 5. Les quatre fonctions natives

### 5.1 Existence et agrégat à travers les items

Le chemin `contacts[].fonction` devient une cible de `filters` et de `group_by`, avec
la grammaire du barreau 1 inchangée :

```jsonc
filters: [{"field": "contacts[].fonction", "op": "in", "value": ["DRH", "DAF"]}]
group_by: "contacts[].fonction"
```

`match` garde son sens (`any` = un item suffit ; `all` = tous les items). SQL :
`jsonb_path_exists` pour l'existence, `jsonb_array_elements` pour le dégroupement —
**le même patron `LATERAL` que l'union multi-colonnes du barreau 1**, dont la sortie
distingue déjà occurrences (`count`) et fiches (`count_rows`). Mesurer avant tout
index : une liste de 4 items sur 9 000 lignes ne justifie sans doute rien.

### 5.2 Adressage d'un rang à l'écriture

L'enrichissement pose une valeur sur un item sans réécrire la liste :

```jsonc
data_write(id=…, row={"contacts[1].email": "d@x.fr",
                      "contacts[1].email.origine": "hunter"})
```

La fusion est celle de #322/#326, un cran plus bas : **l'écriture ne touche que ce
qu'elle nomme**. Écrire `contacts` en entier remplace la liste ; écrire un rang ne
touche que lui. Un rang au-delà de la longueur actuelle **étend** la liste, un rang
au-delà de `max_items` est refusé.

**Un trou est servi comme `{}`, jamais `null`** — le rang est RÉSERVÉ, pas absent.
Trois conséquences, toutes voulues : un consommateur itère et lit `item.get("nom")`
sans garde de type (un `null` en imposerait une partout) ; `contacts[].attr` ne matche
rien sur un trou, ce qui est la bonne réponse ; l'export rend des colonnes vides à ce
rang. Même règle qu'au-dessus : une colonne-tableau vide rend `[]`, jamais `null`.

### 5.3 Aplatissement d'export DÉTERMINISTE

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

> ⚠️ **Il n'existe aujourd'hui AUCUN export CSV/XLSX du datastore côté backend**
> (`data_url` rend l'URL du dashboard, rien d'autre). Cette fonction est donc à créer,
> pas à étendre — à chiffrer comme telle.

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
