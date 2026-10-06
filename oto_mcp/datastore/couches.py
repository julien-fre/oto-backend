"""Le vocabulaire des COUCHES d'une cellule, et les formes qu'elles prennent (#318).

Une colonne peut porter, à côté de sa `valeur`, trois couches qui la qualifient :
`origine` (le point de départ), `comment` (d'où vient ce qu'on affirme) et `link`
(l'URL qui atteste). Ce module tient ce vocabulaire fermé et les six gestes qui
l'entourent : reconnaître une cellule à couches (`names_layers`), en extraire la
valeur nue (`unwrap`), l'aplatir pour le service (`flat_layers`, `served_value`),
décomposer une adresse `champ.couche` (`split_layer`), lire une couche
(`layer_value`), et juger l'identité de deux valeurs (`same_value`) ou le vide
(`_is_empty`/`est_vide`).

**Ce module ne connaît AUCUN schéma, et c'est structurel.** Aucun attribut de
déclaration n'autorise ni n'interdit à une colonne de porter des couches : une
cellule dont la valeur est un objet portant `valeur` EN a, toute autre en est
dépourvue. Le contrat vaut pour toute colonne de tout tableau, à tous les étages.
C'est pourquoi il ne dépend de rien du domaine — il est le socle sur lequel les
autres modules s'appuient, jamais l'inverse.

Ce qu'il ne tient pas :
- **quelles couches une colonne EXIGE** (`required_layers`) → `couches_exigees.py` ;
- **la couche `origine` posée par la PLATEFORME** (le cran `origine: "system"`, son
  refus et son préavis) → `champs_reserves.py` ;
- **la forme imbriquée servie au lecteur** (`layers=nested`) → `layers.py` ;
- **le refus d'une couche mal orthographiée** → `hors_schema.py`.
"""
from __future__ import annotations

from typing import Any

# --- ce que les DEUX faces d'écriture disent des couches (oto#91) -------------
#
# Une seule phrase, servie par `data_write` (MCP) ET par `POST`/`PATCH …/rows`
# (REST). La face REST n'en portait qu'un renvoi vers un guide : la même vérité en
# deux textes, dont un sans le vocabulaire, est ce qui avait fait écrire à des agents
# la provenance dans `origine`. Le texte vit ICI, chaque face l'insère — jamais
# recopié.
DESCRIPTION_ECRITURE = (
    "Layers: `valeur`/`comment`/`link` are yours to write, `origine` is read "
    "only — write nested, `{\"field\": {\"valeur\": …, \"comment\": …, "
    "\"link\": …}}`, never these as top-level keys of your own row. Provenance "
    "(WHAT you established, WHERE it came from) goes in `<field>.comment`, the "
    "page in `<field>.link` — never in `origine`, the platform's layer. A "
    "misspelled layer name next to a known layer (`{\"valeur\": …, \"comnent\": "
    "…}`) is REFUSED; alone (`{\"field\": {\"comnent\": \"x\"}}`), it is no "
    "layer: that dict IS the value."
)


# --- couches d'une colonne (#318) ---------------------------------------------
# NATIF et universel : aucune déclaration ne dit qu'une colonne porte des couches.
# Une colonne dont la valeur est un objet portant `valeur` EN a ; toute autre en est
# dépourvue. C'est le contrat, et il vaut pour toute colonne de tout tableau.
VALUE_LAYER = "valeur"
ORIGIN_LAYER = "origine"
# Trois couches, pas cinq. `source` et `commentaire` disaient la même chose — d'où
# `comment` seul ; `link` porte l'URL qui atteste, quand il y en a une.
# ⚠️ Conséquence assumée : `group_by champ.comment` ne comptera les provenances que
# si elles sont écrites de façon régulière (« registre », « déduction »). C'est
# possible, ce n'est plus induit par la forme.
LAYER_KEYS = (ORIGIN_LAYER, "comment", "link")

# Tout ce qu'une colonne à couches peut porter, valeur comprise.
ALL_LAYER_KEYS = (VALUE_LAYER, *LAYER_KEYS)

# Les couches qui décrivent LA VALEUR : elles la suivent, et disparaissent avec elle
# — les garder au-dessus d'une valeur remplacée ferait affirmer une provenance fausse.
# `origine` n'en est pas : elle décrit le point de DÉPART, pas la valeur courante, et
# c'est pourquoi elle est la seule à survivre à une réécriture.
VALUE_BOUND_LAYERS = tuple(k for k in LAYER_KEYS if k != ORIGIN_LAYER)

# --- Le mot qu'une écriture peut poser à la place d'un contenu (oto#140) -----------
#
# Le contrat d'écriture d'une case tient en deux gestes : `null` efface, `@empty` dit
# « cherché, rien » (la raison dans `comment`) ; omettre un sous-champ n'y touche pas.
#
# ⚠️ **Collision assumée** : une colonne dont la vraie valeur serait la chaîne `@empty`
# ne peut pas l'écrire. C'est le prix d'une sentinelle dans le même espace que les
# données ; le préfixe `@` a été choisi parce qu'aucune valeur mesurée en production ne
# commence par lui.
VIDE_DELIBERE = "@empty"

#: Les deux mots RETIRÉS (oto#140) : `@keep` doublait l'omission, `@clear` doublait
#: `null`. Ils ne sont plus connus que pour être REFUSÉS à l'entrée de toute écriture
#: (`mots_deprecies.controler`) — jamais résolus, jamais stockés.
GARDE = "@keep"
EFFACEMENT = "@clear"

#: Le marqueur interne du vide ASSUMÉ (oto#204), rangé dans l'enveloppe de la cellule :
#: `{"valeur": "", "oto.vide_assume": true}`. Il distingue « aucune source ne donne ce
#: champ, et je l'écris » d'une chaîne vide ordinaire ou d'une cellule CSV vide : lui
#: seul satisfait `required`. JAMAIS servi, JAMAIS écrit par un client.
#:
#: Le POINT est ce qui le rend impossible à confondre avec une clé d'utilisateur :
#: `definition.py` refuse tout point dans un nom de colonne ou de sous-champ.
#:
#: ⚠️ **Livré en deux étapes, sur une base partagée entre préprod et prod.** Étape 1 :
#: ce code LIT, VALIDE et SERT une cellule marquée, et n'en CRÉE aucune. L'émission (par
#: la résolution de `@empty`) ne touche `main` qu'une fois l'étape 1 servie en prod —
#: sinon une version qui ne connaît pas le marqueur le lirait.
VIDE_ASSUME = "oto.vide_assume"
CLES_INTERNES = (VIDE_ASSUME,)

# ⚠️ **`@empty` se résout dans `columns._merge_column`** (et à la création, par la même
# fonction, `mots_resolus_a_la_creation`). Une surface d'écriture qui ne passerait pas
# par la fusion le stockerait tel quel — et la chaîne finirait servie à une cliente
# comme sa propre donnée. `@keep` et `@clear` sont refusés AVANT la fusion, sur tous
# les chemins : `test_controler_garde_toutes_les_ecritures.py` en tient la preuve.
#
# **La règle pour qui ajoute un chemin d'écriture : passer par `mots_deprecies.controler`
# puis par la fusion, ou refuser en nommant le geste qui marche. Jamais stocker.**


def est_vide_delibere(v: Any) -> bool:
    """Vrai si cette valeur est le mot `@empty` — jamais un contenu.

    Comparaison au TYPE près et au mot ENTIER : `0`, `False`, `None` n'en sont pas, et
    `contact@empty.fr` reste du texte."""
    return isinstance(v, str) and v == VIDE_DELIBERE

# La couche d'origine POSÉE PAR LE SYSTÈME (#586) : `{"key": "x", "origine": "system"}`
# au schéma. Vocabulaire fermé à UNE valeur — une origine « posée par l'agent » n'est
# pas une déclaration, c'est le défaut de départ (l'agent l'a réécrite une fois sur
# quarante et une, et c'était l'unique copie de la valeur remise).
SYSTEM_ORIGIN = "system"


def same_value(a: Any, b: Any) -> bool:
    """Deux valeurs IDENTIQUES — au type près : `0` n'est pas `False`, `1` n'est pas
    `1.0`. Un seul juge pour « rien n'a changé », partout où ça décide (la fusion des
    couches, les champs réservés)."""
    return type(a) is type(b) and a == b


def names_layers(value: Any) -> bool:
    """L'écriture NOMME-t-elle des couches ? — un dict fait UNIQUEMENT de couches
    connues. Strict, comme tout écrivain : `{"a": 1, "origine": "x"}` reste une donnée
    `json` métier qui se trouve avoir un champ nommé « origine ». UN seul juge, pour
    la fusion (`columns._writes_layers`) comme pour les champs réservés
    (`reserved_refusals`) — deux copies divergeraient un jour sur un cas limite."""
    return (isinstance(value, dict) and bool(value)
            and all(k in ALL_LAYER_KEYS for k in value))


def unknown_layers(value: Any) -> list:
    """Couches d'une colonne que CETTE version du serveur ne connaît pas.

    L'asymétrie est le cœur du contrat d'évolution : le LECTEUR tolère (une couche
    écrite par une version plus récente est ignorée, la valeur reste lisible — sinon
    un déploiement progressif casserait les anciens nœuds), l'ÉCRIVAIN refuse. C'est
    ce qui permet d'ajouter une couche sans jamais dégrader l'ancien.

    Refuser à l'écriture plutôt que stocker en silence, parce qu'on a déjà payé
    l'inverse : une clé `enum:` posée là où le validateur lit `options:` a été
    acceptée, stockée, jamais lue — et 504 lignes ont été écrites en croyant le champ
    contraint. Une couche mal orthographiée doit s'apprendre à l'écriture, pas se
    découvrir six semaines plus tard.

    Un dict sans AUCUNE clé de couche connue n'est pas une colonne à couches :
    c'est une valeur `json` ordinaire, on n'y touche pas. ⚠️ Le critère a été
    corrigé par #329 — jusque-là le court-circuit exigeait `valeur`, si bien
    qu'un `{"origine": x, "sourse": y}` (le geste du rattrapage #326, une faute
    de frappe plus loin) passait SANS refus et écrasait la valeur existante en
    silence. La même validation s'applique désormais dans les deux cas."""
    if not isinstance(value, dict):
        return []
    connues = {VALUE_LAYER, *LAYER_KEYS}
    if not (set(value) & connues):
        return []
    return sorted(k for k in value if k not in connues)


def unwrap(value: Any) -> Any:
    """La VALEUR d'une colonne, qu'elle porte des couches ou non.

    Le pendant Python de l'expression SQL polymorphe — MÊME règle, deux endroits
    parce que deux langages, jamais deux règles. Tout ce qui JUGE une valeur (types,
    requis, bornes, options) doit déballer d'abord : sinon un schéma strict qui
    déclare `email` en `text` refuse un objet, et l'écriture en couches devient
    impossible précisément sur les tableaux qu'on recommande de rendre stricts.

    ⚠️ Conséquence assumée du caractère universel : un champ `json` légitime dont le
    contenu porte une clé `valeur` (`{"valeur": 42, "unite": "kg"}`) est déballé lui
    aussi. C'est le prix de « pas de déclaration » — la convention s'applique partout,
    y compris là où l'auteur ne pensait pas à elle. Le repli est bénin (on rend la
    valeur au lieu de l'objet, souvent ce qu'on voulait), et l'alternative — un
    marqueur réservé, ou une déclaration par colonne — rachèterait un cas rare au prix
    de la simplicité qui fait tout l'intérêt de la primitive."""
    if not isinstance(value, dict):
        return value
    if VALUE_LAYER in value:
        return value[VALUE_LAYER]
    # Pas de `valeur`, mais QUE des couches connues ⟹ c'est bien une colonne à
    # couches, dont la valeur n'est pas encore posée. Le cas nominal d'un import de
    # socle : on remplit `origine` sur un champ qu'aucun agent n'a renseigné. Sans
    # ça la lecture rendait l'OBJET — donc tout ce qui attend une chaîne cassait,
    # précisément sur le chemin qu'on recommande.
    if value and all(k in LAYER_KEYS for k in value):
        return None
    return value


def split_layer(field: str) -> tuple:
    """`email.comment` → `("email", "comment")` ; `email` → `("email", None)`.

    Ne coupe qu'au DERNIER point, et seulement si le suffixe est une couche connue :
    un champ légitimement nommé `taux.2024` reste un nom de colonne entier. Le
    vocabulaire est FERMÉ, donc l'ambiguïté est décidable — pas de devinette. La
    valeur, elle, se désigne par le nom NU (`email`), jamais `email.valeur` : c'est
    pourquoi `VALEUR_LAYER` n'est pas un suffixe coupable.

    ⚠️ Domicile ICI depuis #377, plus dans `db/paths` : la validation de schéma en a
    besoin, et `db.paths` importe déjà ce module — l'inverse ferait un cycle.
    `db.paths.split_layer` la ré-exporte, si bien que la grammaire reste à UN endroit
    pour le SQL comme pour le schéma. Deux copies, c'est le défaut qu'on a déjà payé :
    le même chemin répondait juste sur un verbe et faux sur trois."""
    base, sep, last = str(field).rpartition(".")
    if sep and base and last in LAYER_KEYS:
        return base, last
    return str(field), None


def layer_value(column: Any, layer: str) -> Any:
    """La valeur d'UNE couche d'une colonne — `None` si la colonne n'en porte pas.

    Une colonne écrite en scalaire (`"hors_perimetre"`) n'a aucune couche : sa
    justification est ABSENTE, et c'est bien ce qu'un requis doit constater. Sans ce
    `None`, il suffirait d'écrire la valeur nue pour échapper au motif."""
    return column.get(layer) if isinstance(column, dict) else None


# --- `origine` devient une VERSION, pas une valeur nue (oto#140) --------------
#
# **Le défaut que ça ferme, et il est le cœur du contrat de refonte.** `origine`
# gardait ce que la cliente avait remis — mais pas le FAIT qu'elle l'avait remis :
# il n'existe qu'un seul emplacement de commentaire par colonne, celui de la valeur
# COURANTE. Dès qu'un agent enrichit, il y écrit sa propre provenance, et celle de la
# valeur de départ est écrasée. On conservait donc la donnée du client et on perdait
# d'où elle venait — précisément la moitié qu'une restitution doit produire.
#
# Désormais `origine` peut porter les MÊMES sous-champs que la valeur courante :
#
#     "raison_sociale": {
#       "valeur":  "Dupont SAS",
#       "comment": "INSEE, SIREN 123456789",
#       "origine": {"valeur": "DUPONT", "comment": "fichier de la cliente du 05/08"}
#     }
#
# ⚠️ **Rétro-compatible dans les deux sens, et ce n'est pas négociable** : 431 colonnes
# de production déclarent la capture, et un tableau de campagne porte 837 empreintes.
# Une `origine` SCALAIRE déjà en base se lit comme une version réduite à sa valeur —
# `"DUPONT"` ≡ `{"valeur": "DUPONT"}` — et rien n'a besoin d'être migré.
#
# ⚠️ **Et ce qui est SERVI ne bouge pas.** `champ.origine` continue de rendre la
# VALEUR d'origine, scalaire, comme depuis toujours : un écran qui la lit ne voit
# aucune différence. Les sous-champs de la version d'origine s'ajoutent à côté
# (`champ.origine.comment`), ils ne la remplacent pas. Déplacer une clé servie aurait
# cassé la garde d'un écran qui existe et qui protège une cliente.


def version_origine(column: Any) -> dict:
    """La version d'origine d'une colonne, sous sa forme d'OBJET — toujours un dict.

    Le point unique de lecture : personne ne doit avoir à savoir si l'origine a été
    écrite en scalaire (le socle) ou en version (le contrat). Rend `{}` quand il n'y
    a pas d'origine du tout — l'absence se distingue d'une origine vide, qui est le
    marqueur « rien n'avait été remis »."""
    brut = layer_value(column, ORIGIN_LAYER)
    if brut is None:
        return {}
    if isinstance(brut, dict):
        return brut
    return {VALUE_LAYER: brut}


def valeur_origine(column: Any) -> Any:
    """La VALEUR d'origine seule, quelle que soit la forme sous laquelle elle vit.

    ⚠️ À employer partout où le code attendait un scalaire — sinon un objet arrive là
    où une comparaison ou un test de vide attend une chaîne, et il est vrai par
    accident : `{"valeur": ""}` n'est pas vide au sens de Python, alors que l'origine
    qu'il porte l'est."""
    v = version_origine(column)
    return v.get(VALUE_LAYER) if v else None


def origine_vide(column: Any) -> bool:
    """L'origine porte-t-elle le marqueur « rien n'avait été remis » ?

    Distinct de « pas d'origine du tout » : le premier a été posé délibérément à la
    première écriture d'une case vide au départ, le second n'a jamais été renseigné.
    Les confondre ferait recapturer la valeur du premier agent comme si elle venait
    de la cliente — le défaut que `reserves.py` documente."""
    v = version_origine(column)
    return bool(v) and _is_empty(v.get(VALUE_LAYER))


def flat_layers(key: str, value: Any, *, origine: bool) -> dict:
    """Les couches RENSEIGNÉES d'une colonne, aplaties en `clé.couche`.

    Point unique : le premier niveau d'une ligne et les attributs d'un item de liste
    l'appellent tous les deux. Deux implémentations exposeraient deux formes de la
    même chose — et c'est le consommateur qui paierait la différence.

    `origine` (oto#273) : la couche `origine` et ses sous-champs ne sont fabriqués que
    DEMANDÉS (`versions=["current","origine"]`, `versions.sert_l_origine`). Le choix
    vit ICI, au point qui fabrique les noms, et nulle part en aval : un filtre posé
    après coup au premier niveau a laissé fuir `item["email.origine"]` un cran plus
    bas, sous une réponse qui déclarait `versions_servies: ["current"]`. Obligatoire
    et sans défaut, comme `layers.nested_value` : un lecteur neuf dit ce qu'il sert."""
    if not isinstance(value, dict) or not any(k in LAYER_KEYS for k in value):
        return {}
    plat = {f"{key}.{layer}": value[layer] for layer in LAYER_KEYS
            if value.get(layer) not in (None, "") and layer != ORIGIN_LAYER}

    # ⚠️ `origine` est servie à part, et sa VALEUR garde exactement sa place d'avant.
    #
    # Depuis oto#140 elle peut porter ses propres sous-champs — d'où vient ce que la
    # cliente a remis, et non plus seulement ce qu'elle a remis. Mais `champ.origine`
    # est LU par un écran, qui s'en sert pour ne pas présenter une phrase de la
    # plateforme comme la donnée d'une cliente. Y mettre un objet casserait cette
    # garde-là, sur ce cas-là.
    #
    # Donc : `champ.origine` reste la valeur, scalaire ; ses sous-champs s'AJOUTENT à
    # côté (`champ.origine.comment`). Aucun consommateur ne bouge aujourd'hui.
    #
    # Depuis la bascule du 02/10/2026, la lecture sert la version COURANTE seule et
    # l'origine se DEMANDE : sans `origine`, ni `champ.origine` ni ses sous-champs ne
    # sont fabriqués — au premier niveau comme dans un item de liste.
    version = version_origine(value) if origine else None
    if version:
        val = version.get(VALUE_LAYER)
        if val not in (None, ""):
            plat[f"{key}.{ORIGIN_LAYER}"] = val
        for sous in LAYER_KEYS:
            if sous == ORIGIN_LAYER:
                continue                      # pas d'origine d'une origine
            if version.get(sous) not in (None, ""):
                plat[f"{key}.{ORIGIN_LAYER}.{sous}"] = version[sous]
    return plat


def layer_address(name: Any):
    """L'INVERSE de `flat_layers` : `"site_web.comment"` → `("site_web", "comment")`.

    ⚠️ Elle vit ICI, collée à la fonction qu'elle inverse, parce que c'est la seule
    façon que les deux ne divergent pas : `flat_layers` fabrique `f"{clé}.{couche}"`,
    celle-ci le défait. **Ce qu'on sert doit pouvoir être réécrit tel quel** — et
    l'aller-retour se referme exactement là où ces deux-là s'accordent.

    Rend `None` dès que la forme n'est pas une adresse de couche, et les trois refus
    sont volontaires : un suffixe qui n'est pas une couche connue (`champ.inexistant`)
    n'en est pas une ; une base qui porte encore un point (`a.b.comment`) ne peut
    désigner aucune colonne, puisqu'un nom de colonne n'en porte jamais ; une base
    indexée (`contacts[0].email`) est un CHEMIN de lecture, pas une colonne.

    `valeur` n'en fait pas partie : `flat_layers` ne la sert jamais à plat (le nom nu
    la rend déjà), donc `champ.valeur` n'est le retour d'aucun aller."""
    if not isinstance(name, str) or "." not in name:
        return None
    base, _, couche = name.rpartition(".")
    if couche not in LAYER_KEYS or not base or "." in base or "[" in base:
        return None
    return base, couche


def served_value(value: Any, *, origine: bool, sentinelle: bool = False) -> Any:
    """Ce qu'un LECTEUR reçoit pour cette colonne (oto#22 §1-2).

    `unwrap` rend la valeur d'UNE colonne ; celle-ci descend d'un cran quand cette
    valeur est une LISTE DE FICHES. Sans elle, la garantie « le nom nu rend la valeur,
    jamais la structure interne » se romprait au moment précis où les attributs d'un
    item adoptent des couches : `row["contacts"][0]["email"]` rendrait l'enveloppe
    au lieu de l'e-mail, donc tout consommateur casserait — silencieusement, le jour
    où quelqu'un pose une source sur un contact.

    Les couches d'un attribut sont aplaties DANS l'item (`item["email.origine"]`) :
    la règle du premier niveau, appliquée un cran plus bas, plutôt qu'un second
    vocabulaire à apprendre. Qui sait lire `row["email.origine"]` sait lire
    `item["email.origine"]`.

    Un item non-dict traverse tel quel — une liste de scalaires reste une liste de
    scalaires.

    `sentinelle` (`empties=sentinel`, oto#204) : une case au vide ASSUMÉ est servie
    `"@empty"` — à ce niveau comme dans les fiches d'une liste — au lieu du `""` qui la
    confond avec un vide ordinaire. Sans lui, rien ne change.

    `origine` (oto#273) : descend avec la valeur jusqu'à `flat_layers`, à toute
    profondeur — `item["email.origine"]` ne sort que demandée, comme `email.origine`."""
    if sentinelle and vide_assume(value):
        return VIDE_DELIBERE
    v = unwrap(value)
    if isinstance(v, list):
        return [_served_item(item, origine=origine, sentinelle=sentinelle) for item in v]
    return v


def _served_item(item: Any, *, origine: bool, sentinelle: bool = False) -> Any:
    """Un item de liste est une FICHE : chacun de ses attributs est une feuille."""
    if not isinstance(item, dict):
        return item
    out: dict = {}
    for k, v in item.items():
        out[k] = served_value(v, origine=origine, sentinelle=sentinelle)
        out.update(flat_layers(k, v, origine=origine))
    return out


# ── validation d'une ROW à l'écriture ────────────────────────────────────────

def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


#: Alias PUBLIC de `_is_empty`. La notion de « vide » du datastore est UNE : un
#: appelant qui en écrirait une seconde la ferait diverger au premier cas limite
#: — c'est exactement le défaut de #608, où le validateur et le merge lisaient la
#: chaîne vide autrement l'un que l'autre.
est_vide = _is_empty


def vide_assume(cell: Any) -> bool:
    """Cette cellule porte-t-elle le vide ASSUMÉ ? — le marqueur, sur une valeur vide."""
    return (isinstance(cell, dict) and cell.get(VIDE_ASSUME) is True
            and _is_empty(cell.get(VALUE_LAYER)))


def valeur_posee(cell: Any) -> bool:
    """La case porte-t-elle une VALEUR POSÉE ? — UN juge pour deux décisions :
    ce qu'un `null` en écho a à effacer (`columns`), et ce que `readonly` verrouille
    (`champs_reserves`, oto#140 J5).

    Non quand la case est VIDE au sens d'`est_vide` : clé absente, `null`, `""`
    ordinaire, `[]`, `{}`, ou une cellule de couches seules (`{"comment": …}`, sans
    valeur). Oui pour tout le reste — et pour le vide ASSUMÉ : `@empty` (« cherché,
    rien »), stocké `{"valeur": "", "oto.vide_assume": true}`, est un constat posé,
    pas une case à remplir."""
    return vide_assume(cell) or not _is_empty(unwrap(cell))


def sans_cles_internes(value: Any) -> Any:
    """La valeur telle qu'un lecteur de la base BRUTE peut la montrer : sans clé
    interne, à toute profondeur (une liste de fiches porte ses marqueurs un cran plus
    bas). Les lecteurs servis passent par `served_value`, qui ne les expose jamais."""
    if isinstance(value, dict):
        return {k: sans_cles_internes(v) for k, v in value.items()
                if k not in CLES_INTERNES}
    if isinstance(value, list):
        return [sans_cles_internes(v) for v in value]
    return value
