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
`type`, `label`, `description`, `required`, `max_length`, `enum`+`options`…) — scout
dérive tous ses écrans du schéma, il n'a donc rien de spécial à apprendre.

**Deux ajouts** :

- `max_items` (entier, optionnel) — borne la liste à l'écriture **et** fixe le nombre
  de colonnes de l'export à plat (§5.3). Sans lui, l'export est borné au maximum
  observé, ce qui le rend non déterministe d'un jour à l'autre : c'est pour ça qu'il
  faut le déclarer sur un tableau qu'on exporte.
- `description` — aujourd'hui ni validé ni servi (le module pur laisse passer les clés
  inconnues). À faire traverser jusqu'aux consommateurs, sinon scout n'a rien à
  afficher sous un intitulé.

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

## 6. Le chemin de MIGRATION — double-service

Le premier tableau visé est regardé quotidiennement par une cliente. La bascule ne
peut donc pas être un basculement.

**La colonne-tableau devient la vérité ; une projection à plat reste SERVIE EN LECTURE
pendant la fenêtre.** Les écrans, filtres et réglages qui parlent `contact1_nom`
continuent de répondre ; chaque consommateur bascule à son rythme.

```jsonc
{"key": "contacts", "type": "list", "of": {…},
 "flat_alias": "contact{n}_{attr}"}     // ⟵ DÉCLARÉ, jamais deviné
```

> ⚠️ **C'est le point de conception le plus délicat, et il touche à la contrainte
> ferme du barreau 1** : le serveur n'interprète aucun motif de nom. Résoudre
> `contact1_nom` vers `contacts[0].nom` en le devinant rouvrirait exactement ce qu'on
> a fermé. D'où `flat_alias` : celui qui migre DÉCLARE le gabarit, le serveur
> l'applique. Exécuter une déclaration n'est pas deviner une convention.

La projection est **calculée, jamais stockée** (deux vérités à réconcilier sinon), en
lecture seule — une écriture sur `contact1_nom` est **refusée en nommant la cible
neuve**, plutôt que réécrite en douce. Fin de fenêtre = retrait du `flat_alias`, un
tableau à la fois.

**Conversion** : elle lit les colonnes plates existantes et écrit la liste, en une
passe idempotente, avec un compte avant/après par ligne. Puis — et c'est la moitié qui
manquait — **elle SUPPRIME les colonnes plates sources**, une fois la copie vérifiée
(même geste que la purge de colonne morte). Les laisser ferait de `contact1_nom` deux
choses à la fois : la colonne résiduelle ET l'alias calculé, donc deux vérités à
réconcilier — exactement ce que la projection calculée-jamais-stockée évite par
ailleurs. Après la conversion, l'alias est l'**unique** résolveur du nom.

Ordre non négociable : copier → **vérifier** (compte par ligne) → purger. Une purge
avant vérification transforme une conversion ratée en perte. Instruite, annoncée,
jamais un soir sur un rapport.

## 7. L'homologue côté PAGES — la question, posée

La discipline de concept demande de la poser, pas d'y répondre en douce : **un bloc de
page porte-t-il, lui aussi, des feuilles répétées ?** Si oui, il devra la même
machinerie de chemins, et la nommer autrement serait fabriquer un second vocabulaire
pour la même idée. Question ouverte, à trancher avant que les pages n'inventent leur
forme.

## 8. Ce que je tranche ici sans mandat explicite

À retourner en une ligne, ce sont des choix de FORME :

1. **couches d'item aplaties DANS l'item** (`item["email.origine"]`) plutôt qu'un
   chemin global `contacts.0.email.origine` — parce que le consommateur applique une
   règle qu'il connaît déjà, et que l'aplat global explose en largeur ;
2. **rangs 0-indexés à l'adressage, 1-indexés à l'export** — la machine compte comme
   la machine, l'humain lit « contact1 ». C'est une incohérence assumée : l'inverse
   ferait mentir l'une des deux faces. **Elle ne survit que documentée AUX DEUX
   BOUTS** : la fiche d'écriture dit « rangs 0-indexés ; l'export les nomme
   1-indexés », et la réponse d'export le rappelle. Non écrite quelque part, elle
   devient un piège au lieu d'un choix ;
3. **`flat_alias` déclaré** comme réponse à la migration (§6) ;
4. **couches exclues de l'export par défaut** ;
5. **`max_items` porte double sens** (borne d'écriture + largeur d'export) plutôt que
   deux clés — une seule chose à déclarer, et elle est vraie des deux côtés.

## 9. Ordre d'implémentation

Ce qui débloque la migration d'abord, l'exhaustivité ensuite :

1. `unwrap` en profondeur + forme servie (§1, §2) — **rien ne marche sans ça** ;
2. schéma : `max_items`, `description` servie, refus de clé métier sur liste (§3, §4) ;
3. projection `flat_alias` en lecture + refus d'écriture dessus (§6) ;
4. existence/agrégat `contacts[].attr` (§5.1) ;
5. écriture par rang (§5.2) ;
6. export à plat déterministe (§5.3) — le plus gros, il n'existe rien à étendre.
