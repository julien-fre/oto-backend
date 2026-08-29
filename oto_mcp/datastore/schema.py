"""Datastore v2 — schéma structuré : validation d'écriture + cycle de vie (ADR 0046).

Module PUR (aucun I/O) : le schéma d'un namespace (colonne `user_datastores.schema`)
s'étend au-delà du rendu (0016) avec quatre couches OPT-IN :

- **types imbriqués** : `type: "object"` (+ `fields: [...]`) et `type: "list"`
  (+ `of: <field-def>` — scalaire ou sous-record) décrivent une *fiche* (occupant,
  `contacts[]`, `signaux[]`) que le blob JSONB porte déjà ;
- **validation à l'écriture** : `field.required`, conformité de type,
  `field.required_when: {<champ>: <valeur>}` (le guard-rail : livrables requis
  quand `status = "qualified"`) et `field.max_length` (borne de longueur — un
  intitulé de poste n'est pas un paragraphe de raisonnement) — active si
  `schema.strict` OU si un field déclare required/required_when/max_length ;
- **cycle de vie** : `lifecycle: {states, transitions, terminal?}` sur le field
  `role="status"` — état inconnu ou transition non déclarée = refus ;
- **états terminaux** : `terminal` explicite, sinon dérivés (état sans transition
  sortante) — le store libère le claim de file de travail en y entrant ;
- **plafond de reprises** : `lifecycle.max_claims` + `lifecycle.abandon_state` —
  une ligne réservée N fois sans qu'une écriture n'aboutisse quitte la file.

Défaut (aucune de ces clés) = comportement 0016 inchangé : schéma de rendu SOFT.
Les erreurs sont des *listes de messages actionnables* — le store les joint dans
une ValueError, jamais un refus muet.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache
from datetime import datetime
from typing import Any, Optional

# `url`/`email`/`datetime`/`enum` sont des types de PRÉSENTATION : même donnée (une
# string) qu'un `text`, mais ils disent au client QUEL widget rendre (lien cliquable,
# sélecteur de date, liste de choix) au lieu de le deviner de la valeur. Leur
# validation reste volontairement permissive — le schéma guide le rendu, il ne
# transforme pas le datastore en base contrainte.
SCALAR_TYPES = ("text", "number", "date", "datetime", "bool", "json",
                "url", "email", "enum")
COMPOSITE_TYPES = ("object", "list")

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


FLAT_ALIAS = "flat_alias"
_ALIAS_SLOTS = ("{n}", "{attr}")


def _alias_re(gabarit: str):
    """Le gabarit compilé. `{n}` et `{attr}` sont les seuls trous ; tout le reste est
    littéral et ÉCHAPPÉ — un gabarit est déclaré par un humain, pas une expression."""
    out = []
    for part in re.split(r"(\{n\}|\{attr\})", str(gabarit)):
        if part == "{n}":
            out.append(r"(?P<n>\d+)")
        elif part == "{attr}":
            out.append(r"(?P<attr>.+)")
        else:
            out.append(re.escape(part))
    return re.compile("^" + "".join(out) + "$")


def flat_alias_of(schema: Optional[dict]) -> dict:
    """`{clé de colonne-tableau: gabarit}` — les colonnes en double-service (oto#22 §6).

    Pendant la fenêtre de migration, la colonne-tableau est la VÉRITÉ et les anciens
    noms plats restent servis en lecture, pour que les écrans et réglages qui parlent
    `contact1_nom` ne tombent pas tous le même jour.

    Le gabarit est **DÉCLARÉ**, jamais deviné : résoudre `contact1_nom` vers
    `contacts[0].nom` en le devinant rouvrirait exactement l'interprétation de motif de
    nom que le barreau 1 a fermée. Exécuter une déclaration n'est pas deviner une
    convention. Il n'y a pas de gabarit par défaut non plus — le défaut évident
    (`{key}{n}_{attr}`) rend `contacts1_nom`, pas `contact1_nom` : un défaut qui doit
    singulariser la clé serait une devinette de plus."""
    return {str(f["key"]): str(f[FLAT_ALIAS]) for f in _fields(schema)
            if f.get("key") and f.get(FLAT_ALIAS)}


def flat_name(gabarit: str, rang: int, attr: str) -> str:
    """Le nom projeté d'un attribut. ⚠️ Le `{n}` du gabarit est **1-indexé** — c'est
    l'humain qui le déclare et qui le lit (« contact1 »), alors que l'adressage et
    l'écriture comptent à partir de 0. L'asymétrie est assumée et documentée aux trois
    endroits où elle se rencontre ; c'est ICI qu'une confusion coûterait le plus."""
    return str(gabarit).replace("{n}", str(rang + 1)).replace("{attr}", attr)


def resolve_flat_name(schema: Optional[dict], name: str):
    """`contact1_email.comment` → `("contacts", 0, "email.comment")`, ou None.

    Le suffixe de couche COMPOSE : l'alias mappe le préfixe de chemin, la couche suit.
    Sans ça les marques de provenance disparaîtraient des écrans pendant toute la
    fenêtre de migration, sans message."""
    for key, gabarit in flat_alias_of(schema).items():
        m = _alias_re(gabarit).match(str(name))
        if m:
            return key, int(m.group("n")) - 1, m.group("attr")
    return None


def flat_layers(key: str, value: Any) -> dict:
    """Les couches RENSEIGNÉES d'une colonne, aplaties en `clé.couche`.

    Point unique : le premier niveau d'une ligne et les attributs d'un item de liste
    l'appellent tous les deux. Deux implémentations exposeraient deux formes de la
    même chose — et c'est le consommateur qui paierait la différence."""
    if not isinstance(value, dict) or not any(k in LAYER_KEYS for k in value):
        return {}
    return {f"{key}.{layer}": value[layer] for layer in LAYER_KEYS
            if value.get(layer) not in (None, "")}


def served_value(value: Any) -> Any:
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
    scalaires."""
    v = unwrap(value)
    if isinstance(v, list):
        return [_served_item(item) for item in v]
    return v


def _served_item(item: Any) -> Any:
    """Un item de liste est une FICHE : chacun de ses attributs est une feuille."""
    if not isinstance(item, dict):
        return item
    out: dict = {}
    for k, v in item.items():
        out[k] = served_value(v)
        out.update(flat_layers(k, v))
    return out


_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _fields(schema: Optional[dict]) -> list[dict]:
    return [f for f in (schema or {}).get("fields") or [] if isinstance(f, dict)]


def declares_field(schema: Optional[dict], key: str) -> bool:
    """Le schéma déclare-t-il un field top-level de cette clé ? La reconnaissance
    par DÉCLARATION (#354) : c'est elle qui distingue une colonne de données
    légitime (un CSV importé porte souvent une colonne `id`) d'un identifiant de
    ligne égaré dans le corps — jamais une devinette sur la forme de la valeur."""
    return any(f.get("key") == key for f in _fields(schema))


def _walk_fields(fields: list) -> Iterator[dict]:
    """Tous les fields, sous-records COMPRIS (`object.fields`, `list.of[.fields]`)."""
    for f in fields:
        if not isinstance(f, dict):
            continue
        yield f
        if isinstance(f.get("fields"), list):
            yield from _walk_fields(f["fields"])
        of = f.get("of")
        if isinstance(of, dict):
            yield from _walk_fields([of])


def max_length_of(field: dict) -> Optional[int]:
    """La borne de longueur déclarée sur un field, si elle est exploitable.

    Volontairement muette sur une déclaration mal formée (`max_length: "60"`, 0,
    négative) : c'est `_validate_fields_def` qui la REFUSE à la pose du schéma.
    Ici on ne fait qu'appliquer ce qui est valide — un schéma déjà en base, posé
    quand la clé était encore ignorée, ne doit pas faire exploser une écriture."""
    ml = field.get("max_length")
    if isinstance(ml, bool) or not isinstance(ml, int) or ml <= 0:
        return None
    # Une borne sur un composite n'a pas de sens (longueur de quoi ?) et la
    # définition la refuse ; si elle traîne dans un vieux schéma, on l'ignore.
    return None if field.get("type") in COMPOSITE_TYPES else ml


def pattern_of(field: dict) -> Optional[str]:
    """Le motif déclaré sur un field, S'IL est exploitable en sûreté — sinon None.

    Même parti pris que `max_length_of` : volontairement muette sur une déclaration
    qu'on ne sait pas exécuter, parce qu'un schéma déjà en base — posé quand la clé
    était encore ignorée — ne doit pas faire exploser une écriture. C'est
    `_validate_fields_def` qui REFUSE, à la pose, devant celui qui peut corriger.

    Trois conditions, chacune vérifiée à la pose : une chaîne, sur un champ scalaire,
    et sur un champ BORNÉ. La borne n'est pas un confort — c'est elle qui rend le
    coût du motif majorable (cf. `pattern_refusal`)."""
    src = field.get("pattern")
    if not isinstance(src, str) or not src:
        return None
    if field.get("type") in COMPOSITE_TYPES:
        return None
    ml = max_length_of(field)
    if not ml or ml > PATTERN_MAX_SUBJECT:
        return None
    return None if pattern_refusal(src, ml) else src


def top_level_bounds(schema: Optional[dict]) -> dict[str, int]:
    """`{clé: max_length}` des champs BORNÉS de premier niveau — ceux qu'une requête
    SQL sait mesurer (`data->>clé`). Sert l'avertissement « des lignes existantes
    dépassent déjà » à la pose du schéma.

    ⚠️ Les cibles de COUCHE (#377) en sont exclues : `data->>'q.comment'` mesurerait
    une colonne littérale qui n'existe pas, donc rendrait « aucune ligne hors borne »
    sur un tableau que personne n'a vérifié — un silence qui ferait croire la table
    conforme. La borne, elle, s'applique bien : `validate_row` la fait respecter sur
    la valeur de la couche. Ce qui manque ici est l'avertissement sur l'EXISTANT,
    et il manque franchement plutôt qu'en mentant."""
    out: dict[str, int] = {}
    for f in _fields(schema):
        key, ml = f.get("key"), max_length_of(f)
        if isinstance(key, str) and key and ml and not split_layer(key)[1]:
            out[key] = ml
    return out


def top_level_keys(schema: Optional[dict]) -> set:
    """Colonnes DÉCLARÉES au premier niveau — la réponse à « cette colonne
    existe-t-elle ? ».

    Le schéma est la seule source de vérité là-dessus, et c'est pour ça que ce
    helper existe : dans une row JSONB, **une colonne vide n'existe pas** (il n'y a
    pas de case vide, il n'y a pas de case). Une colonne déclarée mais renseignée
    sur 12 lignes de 500 est donc ABSENTE d'une page où aucune des 12 ne figure —
    et un contrôle qui échantillonne les lignes rendues la déclare inconnue.
    """
    return {str(f["key"]) for f in _fields(schema) if f.get("key")}


def top_level_enum_options(schema: Optional[dict]) -> dict:
    """`{champ: [options]}` des enums DÉCLARÉS au premier niveau, options non vides.

    Restreint au premier niveau comme `top_level_bounds` : c'est ce qu'une requête
    `data->>champ` sait interroger sur l'existant. Un enum sans `options` est un
    enum LIBRE (le client rend un select vide) — il ne condamne rien."""
    out: dict = {}
    for f in _fields(schema):
        key = f.get("key")
        # Même raison que `top_level_bounds` : une cible de couche n'est pas
        # interrogeable par `data->>champ`, l'annoncer ferait porter le réglage
        # d'un écran sur une colonne qui n'existe pas.
        if not key or f.get("type") != "enum" or split_layer(key)[1]:
            continue
        opts = [str(o) for o in (f.get("options") or [])]
        if opts:
            out[str(key)] = opts
    return out


def order_spec(schema: Optional[dict], key) -> tuple:
    """`(type, options)` qui rend le TRI typé pour ce champ — `(None, None)` sinon.

    Le tri honore le type DÉCLARÉ (#336) : `number` → cast numérique, `enum` →
    rang d'option, `date`/`datetime` → texte (ISO trie juste par l'alphabet) mais
    vides-en-queue. Tout le reste — text, non déclaré, composite, chemin
    `col[0].attr`, couche `champ.source` — garde le tri textuel historique : ce
    helper ne matche que la CLÉ EXACTE d'un champ de premier niveau, comme
    `top_level_enum_options`, parce que c'est ce que `data->>champ` sait trier.
    Un enum sans `options` est un enum LIBRE : rien à ranger, tri textuel."""
    if not isinstance(key, str):
        return (None, None)
    for f in _fields(schema):
        if str(f.get("key") or "") != key:
            continue
        ftype = f.get("type")
        if ftype == "number":
            return ("number", None)
        if ftype in ("date", "datetime"):
            return ("date", None)
        if ftype == "enum":
            opts = [str(o) for o in (f.get("options") or [])]
            return ("enum", opts) if opts else (None, None)
        return (None, None)
    return (None, None)


def field_by_role(schema: Optional[dict], role: str) -> Optional[dict]:
    """Le premier field déclarant ce `role` (`status`, `title`…), ou None."""
    for f in _fields(schema):
        if f.get("role") == role:
            return f
    return None


def status_field(schema: Optional[dict]) -> Optional[dict]:
    """Le field déclaré `role="status"` (premier trouvé), ou None."""
    return field_by_role(schema, "status")


# La PRÉSENTATION d'une colonne — ce que sa valeur sert à l'écran, par opposition à
# `type` qui dit ce qu'elle EST (#317, « voie Notion »). Les deux dimensions sont
# ORTHOGONALES, et c'est une mesure qui l'a établi : sur les 57 titres de production,
# **six ne sont pas du texte** (cinq `url`, une `date`). En faire une valeur de `type`
# aurait forcé à choisir — un titre qui est une URL aurait cessé d'être rendu en lien.
#
# Un champ, une présentation ; un tableau, un titre.
DISPLAY_TITLE = "title"


def title_field(schema: Optional[dict]) -> Optional[dict]:
    """La colonne qui NOMME une ligne — ce qu'un humain reconnaît dans un journal, à
    la place d'un uuid.

    ⚠️ **Plus de repli sur `role="title"`** (#317 étape C) : il a vécu le temps de la
    conversion des schémas en base, et il meurt ici. Un repli qui survit à sa raison
    devient le canal par lequel ce qu'on retire revient — un schéma neuf déclarant un
    rôle continuerait de marcher, et le rôle ne serait jamais parti.

    La conversion au boot est passée sur les 57 tableaux (additive, idempotente) :
    un schéma qui n'aurait QUE le rôle ne nomme plus sa ligne, et retombe sur la clé
    métier puis l'identifiant, comme un tableau sans titre."""
    for f in _fields(schema):
        if f.get("display") == DISPLAY_TITLE and f.get("key"):
            return f
    return None


def validation_active(schema: Optional[dict]) -> bool:
    """La validation d'écriture est OPT-IN : `schema.strict` truthy, OU au moins un
    field déclarant `required`/`required_when`/`max_length`. Sans ça, écriture
    soft (0016).

    `max_length` compte au même titre que `required` — sans quoi une borne posée
    sur un schéma qui n'a aucun requis serait INERTE, silencieusement (signal
    #383). Elle est cherchée en PROFONDEUR (sous-records inclus), là où
    required/required_when sont lus sur les seules entrées DÉCLARÉES ICI : élargir
    ces deux-là activerait rétroactivement la validation de schémas déjà posés,
    alors que déclarer une borne EST la demande de la faire respecter.

    ⚠️ « entrée déclarée ici » ≠ « colonne » depuis #377 : une cible de COUCHE
    (`qualification.comment`) est une entrée de cette liste comme une autre, et
    active donc bien la validation. Ce qui reste hors de portée, c'est la
    profondeur — un requis enfoui dans un sous-record."""
    if not isinstance(schema, dict):
        return False
    if schema.get("strict"):
        return True
    if any(f.get("required") or f.get("required_when") for f in _fields(schema)):
        return True
    return any(max_length_of(f) for f in _walk_fields(_fields(schema)))


def key_required_of(schema: Optional[dict]) -> bool:
    """Ce tableau n'accepte-t-il QUE des écritures visant une ligne existante ?

    `schema.key_required` (#516) — opt-in, à côté de la clé métier qu'il durcit. Sur
    un tableau qui le porte, une écriture qui ne désigne aucune ligne (ni par son
    identifiant, ni par une valeur de `key` que le tableau porte déjà) est REFUSÉE au
    lieu d'en créer une. Le défaut reste la création, signalée par un `notices`
    (#390) : un tableau se remplit souvent avant d'avoir sa clé, et le cran est une
    déclaration de son propriétaire, jamais une politique de plateforme.

    ⚠️ **Sans `key` déclarée, il ne s'arme pas.** La combinaison se refuse à la POSE
    (`validate_schema_def`) — mais un schéma déjà en base qui la porterait rendrait
    le tableau inécrivable, et un vieux schéma ne doit pas faire exploser une
    écriture (même parti pris que `max_length_of`/`pattern_of`)."""
    if not isinstance(schema, dict):
        return False
    cle = schema.get("key")
    if not (isinstance(cle, str) and cle):
        return False
    return bool(schema.get("key_required"))


def lifecycle_of(schema: Optional[dict]) -> Optional[dict]:
    sf = status_field(schema)
    lc = (sf or {}).get("lifecycle")
    return lc if isinstance(lc, dict) else None


def terminal_states(schema: Optional[dict]) -> set:
    """États terminaux du cycle de vie : `lifecycle.terminal` explicite, sinon
    dérivés = états sans transition sortante déclarée. Vide si pas de lifecycle."""
    lc = lifecycle_of(schema)
    if not lc:
        return set()
    explicit = lc.get("terminal")
    if isinstance(explicit, list):
        return {str(s) for s in explicit}
    states = {str(s) for s in lc.get("states") or []}
    transitions = lc.get("transitions") or {}
    outgoing = {str(k) for k, v in transitions.items() if v}
    return states - outgoing if states else set()


def is_terminal_status(schema: Optional[dict], value: Any) -> bool:
    return value is not None and str(value) in terminal_states(schema)


def max_claims_of(schema: Optional[dict]) -> Optional[int]:
    """Plafond de réservations SANS écriture (`lifecycle.max_claims`), ou None =
    garde inactive — le comportement historique, qu'aucune déclaration n'arme.

    Une valeur présente mais inutilisable LÈVE : la déclaration est refusée à la
    pose (`validate_schema_def`), donc un plafond illisible ici ne peut venir que
    d'une écriture hors surface. L'ignorer rendrait la garde inerte en silence —
    exactement le défaut que ce plafond existe pour fermer."""
    lc = lifecycle_of(schema) or {}
    if "max_claims" not in lc:
        return None
    v = lc.get("max_claims")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(f"lifecycle.max_claims doit être un entier >= 1 (déclaré : {v!r})")
    return v


def abandon_state_of(schema: Optional[dict]) -> Optional[str]:
    """L'état où verser une ligne qui a atteint son plafond de reprises
    (`lifecycle.abandon_state`). None = non déclaré."""
    v = (lifecycle_of(schema) or {}).get("abandon_state")
    return str(v) if v is not None else None


def merge_fields(current: list, patch: list) -> tuple[list, list[str], list[str]]:
    """Fusionne `patch` dans `current` PAR CLÉ → `(fields, ajoutés, modifiés)`.

    Un field déjà présent est COMPLÉTÉ (les propriétés fournies écrasent, les autres
    sont préservées) ; un field inconnu est ajouté À LA FIN. L'ordre existant ne
    bouge pas : il pilote le rendu (ADR 0032 §6), le déplacer serait un effet de
    bord invisible dans un geste qui prétend ne toucher qu'aux propriétés nommées.

    La fusion descend dans les composites DÉCLARÉS (`object.fields`, `list.of` et
    ses `fields`) : sans ça, patcher un sous-record détruirait ses sous-champs —
    le trou qu'on ferme, un cran plus bas."""
    out = [dict(f) for f in current if isinstance(f, dict)]
    by_key = {f.get("key"): f for f in out if f.get("key")}
    added: list[str] = []
    updated: list[str] = []
    for p in patch:
        if not isinstance(p, dict):
            continue
        key = p.get("key")
        if not isinstance(key, str) or not key:
            continue
        target = by_key.get(key)
        if target is None:
            new = dict(p)
            out.append(new)
            by_key[key] = new
            added.append(key)
            continue
        updated.append(key)
        sub_patch = p.get("fields")
        of_patch = p.get("of")
        for k, v in p.items():
            if k in ("fields", "of"):
                continue
            target[k] = v
        if isinstance(sub_patch, list) and isinstance(target.get("fields"), list):
            target["fields"] = merge_fields(target["fields"], sub_patch)[0]
        elif isinstance(sub_patch, list):
            target["fields"] = [dict(f) for f in sub_patch if isinstance(f, dict)]
        if isinstance(of_patch, dict):
            of_cur = dict(target.get("of") or {})
            of_sub = of_patch.get("fields")
            for k, v in of_patch.items():
                if k != "fields":
                    of_cur[k] = v
            if isinstance(of_sub, list):
                of_cur["fields"] = (merge_fields(of_cur["fields"], of_sub)[0]
                                    if isinstance(of_cur.get("fields"), list)
                                    else [dict(f) for f in of_sub if isinstance(f, dict)])
            target["of"] = of_cur
    return out, added, updated


def remove_fields(current: list, keys: list) -> tuple[list, list[str]]:
    """Retire les fields nommés → `(fields, clés inconnues)`.

    Le retrait est le pendant OBLIGÉ de la fusion : un patch qui ne sait qu'ajouter
    et compléter rend le nettoyage délibéré impossible, et on aurait troqué la
    destruction accidentelle contre l'impossibilité de supprimer. Les deux gestes
    servent à une heure d'intervalle sur un format qui bouge.

    Les clés inconnues sont RENDUES, pas ignorées : un `remove` silencieux sur une
    faute de frappe ferait croire au nettoyage."""
    wanted = {str(k) for k in keys or []}
    kept = [f for f in current
            if not (isinstance(f, dict) and f.get("key") in wanted)]
    present = {f.get("key") for f in current if isinstance(f, dict)}
    return kept, sorted(wanted - present)


def off_schema_keys(schema: Optional[dict], data: dict) -> list[str]:
    """Clés de la row ÉCRITE qu'aucun field du schéma ne déclare (chemins pointés
    pour les sous-records : `contacts[].email_pro`) — le signal de l'issue #294.

    Un nom hors schéma n'est PAS refusé et n'est pas perdu : il crée une colonne
    libre et la valeur persiste (contrat 0016, « tout autre champ s'affiche, il ne
    débloque rien »). Mais cette colonne est **hors du format** — l'interface et
    les consommateurs du schéma ne la lisent pas. Sur un renommage de champs (cas
    ordinaire : le format évolue, les agents ne sont pas tous relancés ensemble),
    le travail atterrit dans une colonne que personne ne regarde, et rien ne le
    signale : un agent écrit, reçoit un accusé de réception, passe à la ligne.
    D'où ce relevé, rendu à l'appelant qui peut le vérifier.

    Vide hors mode `strict` : un champ libre y est un droit explicite du contrat
    (c'est ce qui permet d'explorer un tableau avant de le typer), pas une anomalie.
    Vide aussi si le schéma strict ne déclare AUCUN field — sans référentiel, tout
    serait « hors schéma », ce qui n'informe personne."""
    if not isinstance(schema, dict) or not schema.get("strict"):
        return []
    fields = _fields(schema)
    if not fields or not isinstance(data, dict):
        return []
    return sorted(_off_schema(fields, data, ""))


def _off_schema(fields: list, data: dict, prefix: str) -> set:
    """Clés de `data` absentes de `fields`, en descendant dans les composites
    DÉCLARÉS (un champ déjà hors schéma n'est pas exploré : on ne sait pas ce
    qu'il devrait contenir). Les items d'une liste sont agrégés sur un chemin
    unique `clé[].sous_clé` — un lot de 300 contacts ne rend pas 300 lignes."""
    declared = {f["key"]: f for f in fields
                if isinstance(f.get("key"), str) and f["key"]}
    out: set = set()
    for key, value in data.items():
        f = declared.get(key)
        if f is None:
            out.add(f"{prefix}{key}")
            continue
        ftype, sub = f.get("type"), None
        if ftype == "object" and isinstance(value, dict):
            sub = f.get("fields")
            if isinstance(sub, list):
                out |= _off_schema([x for x in sub if isinstance(x, dict)],
                                   value, f"{prefix}{key}.")
        elif ftype == "list" and isinstance(value, list):
            of = f.get("of")
            sub = of.get("fields") if isinstance(of, dict) else None
            if isinstance(sub, list):
                declared_sub = [x for x in sub if isinstance(x, dict)]
                for item in value:
                    if isinstance(item, dict):
                        out |= _off_schema(declared_sub, item, f"{prefix}{key}[].")
    return out


def off_schema_warning(keys: list) -> Optional[str]:
    """La phrase actionnable qui accompagne `off_schema_keys` — une liste nue ne
    dit pas ce qu'elle implique. None si rien n'est hors schéma."""
    if not keys:
        return None
    noms = ", ".join(f"`{k}`" for k in keys)
    return (f"écrit HORS SCHÉMA : {noms} — le tableau déclare un format strict, ces "
            "colonnes en sortent : elles sont stockées et lisibles, mais l'interface "
            "et tout ce qui s'appuie sur le schéma les ignorent. Si c'est une faute de "
            "nom (champ renommé depuis), relis le format avec `data_get_schema` et "
            "réécris sous le bon nom ; si le champ est voulu, déclare-le au schéma.")


def queue_release_warning(schema: Optional[dict]) -> Optional[str]:
    """Le namespace se donne un STATUT mais aucun état TERMINAL : dit-le, sinon le
    silence se paie en file de travail (signal #360).

    L'auto-release du bail (`_release_if_terminal`) ne se déclenche que sur un état
    terminal ; sans `lifecycle`, `terminal_states` est vide, donc l'écriture du
    verdict ne libère RIEN et chaque ligne traitée reste réservée jusqu'à expiration
    du bail. Un `role="status"` avec ses `options` ressemble pourtant à un cycle de
    vie — c'est exactement la configuration où l'agent croit tenir la garantie qu'il
    n'a pas. None = rien à signaler (pas de statut, ou terminaux dérivables)."""
    sf = status_field(schema)
    if not sf or terminal_states(schema):
        return None
    key = sf.get("key") or "status"
    cause = ("aucun `lifecycle`" if not lifecycle_of(schema)
             else "un `lifecycle` sans état terminal dérivable "
                  "(tout état a une transition sortante)")
    return (f"champ `{key}` (role=status) : {cause} → la file de travail ne libérera "
            "AUCUN bail à l'écriture du verdict (les lignes traitées restent "
            "réservées jusqu'à expiration). Déclare "
            f"`lifecycle: {{states: [...], terminal: [...]}}` sur `{key}`, ou appelle "
            "`data_release` après chaque verdict. Cf. guide `work-queue`.")


# ── `pattern` : la FORME d'une valeur, quand la taille ne suffit pas (#387) ───
#
# Jumeau de `max_length`, et il répond à ce que la borne ne sait PAS dire. Cas
# mesuré : un champ qui doit porter une énumération de catégories séparées par des
# points-virgules, pas une phrase de positionnement. Les longueurs des deux formes
# se recouvrent (20 à 207 caractères) — borner à 150 tue les deux, borner à 250
# n'attrape rien. Ce qui les sépare est la STRUCTURE.
#
# ⚠️ **Une expression fournie par un appelant est une arme.** Elle s'exécute à
# chaque écriture, DANS la boucle unique du serveur : un motif à explosion
# combinatoire n'y coûte pas une requête, il coûte le serveur entier — même famille
# que la bombe de décompression (docs/conventions.md, 13/08).
#
# Un garde purement SYNTAXIQUE (« pas de groupe quantifié ») ne suffit pas, et c'est
# une mesure, pas une intuition — sans un seul groupe ni une seule alternance :
#     `.*.*.*.*.*z`       sur  80 caractères ....   0,75 s
#     `.*.*.*.*.*.*.*z`   sur  60 caractères ....  14,8  s
# Ce qui explose est le nombre de FAÇONS de découper le sujet, pas la forme du motif.
#
# D'où un BUDGET calculé sur l'arbre du motif : le produit, quantificateur par
# quantificateur, du nombre de longueurs qu'il peut prendre — une majoration de
# l'espace de recherche du moteur. Il se calcule CONTRE la longueur du sujet, ce qui
# exige `max_length` sur le même champ : sans sujet borné il n'y a pas de budget,
# donc pas de garantie, et un motif dont on ne sait pas majorer le coût est refusé
# en le disant. Tout ce que l'analyse ne reconnaît pas est refusé de la même façon —
# fail-closed : un motif accepté par ignorance est exactement le défaut à éviter.

PATTERN_MAX_SRC = 200          # longueur du MOTIF
PATTERN_MAX_SUBJECT = 1000     # borne maximale d'un champ qui porte un motif
PATTERN_BUDGET = 100_000       # explorations majorées tolérées


def _re_parser():
    """Le parseur d'expressions de la stdlib — `re._parser` (3.11+) ou `sre_parse`
    (3.10, la version de la box). Aucun des deux : on ne sait plus majorer, donc on
    ne laisse plus passer un motif (le refus vit dans `pattern_refusal`)."""
    try:
        from re import _parser as p          # 3.11+
        return p
    except ImportError:
        pass
    try:
        import sre_parse as p                # 3.10
        return p
    except ImportError:                      # pragma: no cover — stdlib amputée
        return None


# Ce qu'on refuse en le NOMMANT, plutôt qu'en rendant « motif invalide » : chacune
# de ces constructions sort du modèle de coût, aucune n'a de majoration simple.
_PATTERN_REFUSES = {
    "GROUPREF": "une référence arrière",
    "GROUPREF_EXISTS": "un groupe conditionnel",
    "ASSERT": "une assertion avant/arrière",
    "ASSERT_NOT": "une assertion négative",
}

# Feuilles : elles consomment un caractère (ou zéro pour une ancre), sans choix.
_PATTERN_FEUILLES = {"LITERAL", "NOT_LITERAL", "IN", "ANY", "AT", "RANGE",
                     "CATEGORY", "NEGATE", "ANY_ALL"}


class _MotifTropCher(Exception):
    """Le motif sort du budget, ou de ce que l'analyse sait majorer."""


def _op_name(op) -> str:
    return getattr(op, "name", None) or str(op)


def _sub_budget(sub, cap: int) -> float:
    """Le budget d'une séquence — PRODUIT des budgets de ses termes.

    Le produit, pas la somme : le moteur revient en arrière, donc il explore le
    produit cartésien des découpages que ses termes autorisent. C'est exactement ce
    que la mesure de `.*.*.*z` montre, et c'est pourquoi une majoration additive
    laisserait passer la famille polynomiale."""
    total = 1.0
    for op, av in sub:
        total *= _node_budget(op, av, cap)
        if total > PATTERN_BUDGET:
            raise _MotifTropCher(
                f"il autorise plus de {PATTERN_BUDGET} découpages d'une valeur de "
                f"{cap} caractères")
    return total


def _node_budget(op, av, cap: int) -> float:
    nom = _op_name(op)
    if nom in _PATTERN_FEUILLES:
        return 1.0
    if nom in _PATTERN_REFUSES:
        raise _MotifTropCher(
            _PATTERN_REFUSES[nom] + " : cette construction n'a pas de coût "
            "majorable, oto ne l'exécute pas")
    if nom in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
        lo, hi, corps = av
        # `MAXREPEAT` (le sentinelle de `*`/`+`) vaut 2**32-1 : le sujet étant borné,
        # c'est la borne du champ qui plafonne le nombre de répétitions réelles.
        hi = cap if hi > cap else hi
        lo = cap if lo > cap else lo
        longueurs = float(max(hi - lo, 0) + 1)
        interne = _sub_budget(corps, cap)
        if interne > 1.0:
            # Un corps qui offre DÉJÀ un choix, répété : l'espace de recherche est
            # `interne ** répétitions`. C'est la famille `(a+)+` / `(a|aa)*`, celle
            # dont le coût est exponentiel — on ne la borne pas, on la refuse.
            raise _MotifTropCher(
                "un groupe qui offre déjà plusieurs découpages y est répété "
                "(`(a+)+`, `(a|aa)*`) — l'exploration y est exponentielle")
        return longueurs
    if nom == "SUBPATTERN":
        return _sub_budget(av[3], cap)
    if nom == "ATOMIC_GROUP":
        return _sub_budget(av, cap)
    if nom == "BRANCH":
        # Une alternance NON répétée coûte la somme de ses branches — `^(oui|non)$`
        # reste bon marché. Répétée, elle tombe dans le cas ci-dessus.
        return float(sum(_sub_budget(b, cap) for b in av[1])) or 1.0
    raise _MotifTropCher(
        f"il emploie une construction que l'analyse de coût ne reconnaît pas "
        f"({nom}) — oto n'exécute que ce dont elle sait majorer le prix")


def pattern_refusal(src: str, max_length: int) -> Optional[str]:
    """La RAISON de refuser ce motif sur un champ borné à `max_length`, ou None.

    Rendue en clair et adressée à l'auteur : un refus qui dit « motif invalide » ne
    laisse rien à corriger, et c'est ici — à la pose — qu'il reste corrigible."""
    if not isinstance(src, str) or not src:
        return "un motif est une chaîne non vide"
    if len(src) > PATTERN_MAX_SRC:
        return (f"{len(src)} caractères, maximum {PATTERN_MAX_SRC} — au-delà, le "
                "coût d'exécution n'est plus majorable de façon utile")
    try:
        re.compile(src)
    except re.error as e:
        return f"expression invalide ({e})"
    parseur = _re_parser()
    if parseur is None:                      # pragma: no cover
        return ("le parseur d'expressions de la stdlib est introuvable : oto ne "
                "peut pas majorer le coût de ce motif, donc ne l'exécute pas")
    try:
        arbre = parseur.parse(src)
    except Exception as e:                   # noqa: SILENT — traduit en refus nommé
        return f"expression illisible par l'analyse de coût ({e})"
    try:
        _sub_budget(arbre, int(max_length))
    except _MotifTropCher as e:
        return str(e)
    return None


@lru_cache(maxsize=256)
def _pattern_re(src: str):
    """Le motif compilé, mémorisé — il s'exécute à chaque écriture de ligne."""
    return re.compile(src)


def top_level_patterns(schema: Optional[dict]) -> dict:
    """`{clé: motif}` des champs de premier niveau porteurs d'un motif EXPLOITABLE.

    Même restriction que `top_level_bounds` et `top_level_enum_options` : ce que
    `data->>clé` sait relire sur l'existant. Sert l'avertissement « des lignes
    existantes ne suivent déjà pas ce motif » à la pose du schéma."""
    out: dict = {}
    for f in _fields(schema):
        cle, motif = f.get("key"), pattern_of(f)
        if isinstance(cle, str) and cle and motif and not split_layer(cle)[1]:
            out[cle] = motif
    return out


# ── validation de la DÉFINITION du schéma ────────────────────────────────────

def validate_schema_def(schema: Optional[dict]) -> list[str]:
    """Erreurs de structure de la définition elle-même (posée par data_set_schema).
    Un schéma 0016 plat reste valide tel quel."""
    if schema is None:
        return []
    if not isinstance(schema, dict):
        return ["schema doit être un objet {fields:[...]} ou null"]
    errors: list[str] = []
    _validate_fields_def(_fields(schema), "fields", errors)
    # Une colonne titre par tableau (#317) : deux candidats, et le nom d'une ligne
    # dépendrait de l'ordre de déclaration — une inférence silencieuse, exactement ce
    # que le retrait des rôles supprime. Zéro conflit en production au moment de la
    # bascule : le refus ne casse personne.
    titres = [str(f.get("key")) for f in _fields(schema)
              if f.get("display") == DISPLAY_TITLE and f.get("key")]
    if len(titres) > 1:
        errors.append(
            f"display=\"title\" déclaré sur {len(titres)} colonnes ({', '.join(titres)}) "
            "— une seule nomme la ligne")
    # Une clé métier n'est JAMAIS un sous-tableau ni un sous-record (oto#22 §4). Elle
    # identifie la ligne : les écritures par lot dédupliquent dessus, et un index
    # d'unicité d'expression la compare. Une liste ne se réduit pas à une valeur —
    # l'unicité porterait sur le TEXTE d'un objet JSON, donc deux listes équivalentes
    # d'ordre différent ne collisionneraient pas. Refusé à la DÉCLARATION plutôt qu'à
    # la première écriture : le tableau serait déjà peuplé de doublons.
    cle = schema.get("key")
    if cle:
        porteur = next((f for f in _fields(schema) if f.get("key") == cle), None)
        if porteur and porteur.get("type") in COMPOSITE_TYPES:
            errors.append(
                f"key=\"{cle}\" désigne un champ de type \"{porteur.get('type')}\" — "
                "une clé métier identifie la ligne, elle doit être une valeur simple "
                "(une liste ne se réduit pas à une valeur, l'unicité serait fausse)")
    # `key_required` DURCIT la clé métier : sans elle, il n'y a plus aucun moyen de
    # désigner une ligne autrement que par son identifiant, et le tableau deviendrait
    # inécrivable pour tout agent qui ne relit pas d'abord. Refusé à la POSE, là où le
    # tableau se déclare — pas à la première écriture d'une campagne déjà lancée (même
    # parti que `max_claims` sans `abandon_state`).
    if schema.get("key_required") and not cle:
        errors.append(
            "key_required exige une clé métier : déclare `key` (la colonne qui "
            "identifie une ligne), sinon aucune écriture ne pourrait viser une ligne "
            "existante et le tableau serait inécrivable")
    for f in _fields(schema):
        gabarit = f.get(FLAT_ALIAS)
        if not gabarit:
            continue
        nom = f.get("key")
        if f.get("type") != "list":
            errors.append(
                f"{nom}: `{FLAT_ALIAS}` ne vaut que sur une colonne de type \"list\" "
                "— c'est le service des anciens noms plats pendant une migration")
        for trou in _ALIAS_SLOTS:
            if str(gabarit).count(trou) != 1:
                errors.append(
                    f"{nom}: le gabarit {gabarit!r} doit contenir `{trou}` "
                    f"exactement une fois (ex. \"contact{{n}}_{{attr}}\")")
    lc = lifecycle_of(schema)
    if lc is not None:
        states = lc.get("states")
        if not isinstance(states, list) or not states:
            errors.append("lifecycle.states doit être une liste non vide")
        else:
            known = {str(s) for s in states}
            for frm, tos in (lc.get("transitions") or {}).items():
                if str(frm) not in known:
                    errors.append(f"lifecycle.transitions: état source inconnu {frm!r}")
                for to in tos if isinstance(tos, list) else [tos]:
                    if str(to) not in known:
                        errors.append(f"lifecycle.transitions: état cible inconnu {to!r}")
            for t in lc.get("terminal") or []:
                if str(t) not in known:
                    errors.append(f"lifecycle.terminal: état inconnu {t!r}")
        # Le plafond de reprises (#433) et son état d'abandon vont ENSEMBLE : un
        # plafond sans état où verser la ligne serait une garde qui ne peut pas
        # s'appliquer, et un état non terminal la remettrait dans la file qu'elle
        # vient de quitter. Les deux se refusent à la pose, là où le tableau se
        # déclare — pas au premier claim d'une campagne déjà lancée.
        plafond = lc.get("max_claims")
        if plafond is not None and (isinstance(plafond, bool)
                                    or not isinstance(plafond, int) or plafond < 1):
            errors.append(
                f"lifecycle.max_claims doit être un entier >= 1 (reçu {plafond!r}) — "
                "c'est le nombre de réservations SANS écriture qu'une ligne supporte "
                "avant de quitter la file")
        abandon = lc.get("abandon_state")
        if plafond is not None and abandon is None:
            errors.append(
                "lifecycle.max_claims exige lifecycle.abandon_state — l'état terminal "
                "où verser une ligne réservée N fois sans écriture")
        if abandon is not None and str(abandon) not in terminal_states(schema):
            errors.append(
                f"lifecycle.abandon_state: {abandon!r} n'est pas un état terminal déclaré "
                "(ajoute-le à lifecycle.terminal) — une ligne abandonnée reviendrait "
                "sinon dans la file qu'elle vient de quitter")
    else:
        # lifecycle posé sur un field non-status = erreur de placement (silencieux sinon)
        for f in _fields(schema):
            if isinstance(f.get("lifecycle"), dict) and f.get("role") != "status":
                errors.append(
                    f"field {f.get('key')!r}: lifecycle exige role=\"status\"")
    return errors


# Ce qu'une COLONNE seule peut déclarer — donc ce qu'une cible de couche ne peut pas.
# Chacune désigne la colonne en tant que telle : nommer la ligne (`display`), porter
# son statut (`role`), se subdiviser (`fields`/`of`), répondre à un ancien nom plat
# (`flat_alias`). Posées sur une couche, elles ne seraient lues nulle part — la forme
# acceptée-inerte que #347 a fermée.
_COLUMN_ONLY_KEYS = ("display", "fields", "of", FLAT_ALIAS, "role")


def _validate_fields_def(fields: list, path: str, errors: list[str]) -> None:
    for f in fields:
        key = f.get("key")
        fpath = f"{path}.{key or '?'}"
        if not isinstance(key, str) or not key:
            errors.append(f"{fpath}: key manquante")
            continue
        # Cible de COUCHE (#377) : `qualification.comment` contraint la couche du
        # même nom SUR la colonne `qualification` — ce n'est pas une colonne de
        # plus. Toute autre forme pointée se refuse ICI plutôt que d'être stockée :
        # elle ne désignerait rien, et une contrainte qui ne désigne rien n'est pas
        # inerte, elle est INSATISFIABLE — c'est le défaut de #377, où la pose
        # passait et toute écriture déclenchante était ensuite refusée, y compris
        # celle qui portait bien la justification.
        base, layer = split_layer(key)
        if layer:
            if not any(str(x.get("key") or "") == base
                       for x in fields if isinstance(x, dict)):
                errors.append(
                    f"{fpath}: la couche `{layer}` porte sur la colonne `{base}`, "
                    f"qui n'est pas déclarée ici — déclare-la, ou corrige le nom. "
                    f"Une contrainte sur une colonne absente ne pourrait jamais "
                    f"être satisfaite.")
            interdites = [k for k in _COLUMN_ONLY_KEYS if k in f]
            if interdites:
                errors.append(
                    f"{fpath}: {', '.join(repr(k) for k in interdites)} ne se "
                    f"déclare que sur une COLONNE, pas sur une couche — une couche "
                    f"ne nomme pas la ligne, ne porte pas son statut et ne se "
                    f"subdivise pas. Sur `{key}` ces clés ne seraient lues nulle "
                    f"part : déplace-les sur `{base}`.")
        elif "." in key:
            errors.append(
                f"{fpath}: `{key}` n'est pas un nom de colonne — un point ne "
                f"désigne qu'une couche, et les couches sont "
                f"{', '.join(LAYER_KEYS)}. La valeur, elle, se désigne par le nom "
                f"NU (`{key.rpartition('.')[0]}`). Une colonne littérale portant un "
                f"point serait invisible au filtre et au tri du même nom.")
        ftype = f.get("type")
        if ftype is not None and ftype not in SCALAR_TYPES + COMPOSITE_TYPES:
            errors.append(f"{fpath}: type inconnu {ftype!r}")
        if ftype == "object":
            sub = f.get("fields")
            if not isinstance(sub, list) or not sub:
                errors.append(f"{fpath}: type=object exige fields:[...]")
            else:
                _validate_fields_def([x for x in sub if isinstance(x, dict)],
                                     fpath, errors)
        if ftype == "list":
            of = f.get("of")
            if of is None:
                errors.append(f"{fpath}: type=list exige of:<field-def>")
            elif isinstance(of, dict):
                if isinstance(of.get("fields"), list):
                    _validate_fields_def(
                        [x for x in of["fields"] if isinstance(x, dict)], fpath, errors)
                elif of.get("type") is not None and \
                        of["type"] not in SCALAR_TYPES + COMPOSITE_TYPES:
                    errors.append(f"{fpath}.of: type inconnu {of.get('type')!r}")
            else:
                errors.append(f"{fpath}: of doit être un objet field-def")
        rw = f.get("required_when")
        if rw is not None and (not isinstance(rw, dict) or not rw):
            errors.append(f"{fpath}: required_when doit être un objet {{champ: valeur}}")
        elif isinstance(rw, dict):
            # La règle de la famille #329/#331 : une forme non interprétée se
            # REFUSE à la pose en nommant l'attendu — jamais stockée-inerte
            # (vécu #347 : une condition en liste était acceptée et désarmait
            # la contrainte pour TOUTES les valeurs, scalaires comprises).
            for ck, cv in rw.items():
                ok_scalaire = isinstance(cv, (str, int, float, bool))
                ok_liste = (isinstance(cv, (list, tuple)) and len(cv) > 0
                            and all(isinstance(x, (str, int, float, bool)) for x in cv))
                if not (ok_scalaire or ok_liste):
                    errors.append(
                        f"{fpath}: required_when — la condition de `{ck}` doit être "
                        f"une valeur ou une liste non vide de valeurs (requis quand "
                        f"la valeur du champ est / est parmi) ; reçu {cv!r}")
        ml = f.get("max_length")
        if ml is not None:
            if isinstance(ml, bool) or not isinstance(ml, int) or ml <= 0:
                errors.append(f"{fpath}: max_length doit être un entier > 0, reçu {ml!r}")
            elif ftype in COMPOSITE_TYPES:
                errors.append(
                    f"{fpath}: max_length ne borne qu'un champ scalaire "
                    f"(type={ftype} — borne le sous-champ concerné)")
        # #387 : le motif se refuse ICI, devant celui qui le pose — jamais à
        # l'écriture d'une ligne trois semaines plus tard. Un motif fautif accepté
        # puis inerte est le pire des deux mondes : son auteur croit avoir posé un
        # contrat. Trois refus, chacun nommant sa raison.
        motif = f.get("pattern")
        if motif is not None:
            bornes = max_length_of(f)
            if not isinstance(motif, str) or not motif:
                errors.append(
                    f"{fpath}: pattern doit être une expression régulière (une "
                    f"chaîne non vide), reçu {motif!r}")
            elif ftype in COMPOSITE_TYPES:
                errors.append(
                    f"{fpath}: pattern ne contraint qu'un champ scalaire "
                    f"(type={ftype} — pose-le sur le sous-champ concerné)")
            elif not bornes:
                # La borne n'est pas un confort : c'est elle qui rend le coût du
                # motif majorable. Sans sujet borné, aucune garantie — et le motif
                # tourne dans la boucle UNIQUE du serveur, à chaque écriture.
                errors.append(
                    f"{fpath}: pattern exige max_length sur le même champ — le coût "
                    f"d'un motif se majore contre la longueur de ce qu'il lit, et "
                    f"oto n'exécute pas ce dont elle ne sait pas majorer le prix "
                    f"(borne le champ, puis repose le motif)")
            elif bornes > PATTERN_MAX_SUBJECT:
                errors.append(
                    f"{fpath}: pattern sur un champ borné à {bornes} caractères — "
                    f"maximum {PATTERN_MAX_SUBJECT} : au-delà, contraindre la FORME "
                    f"d'une valeur n'a plus de sens et son coût n'est plus majorable")
            else:
                raison = pattern_refusal(motif, bornes)
                if raison:
                    errors.append(f"{fpath}: pattern {motif!r} refusé — {raison}")


# ── validation d'une ROW à l'écriture ────────────────────────────────────────

def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _type_error(value: Any, ftype: str, path: str,
                fields: Optional[list] = None, of: Optional[dict] = None,
                options: Optional[list] = None) -> list[str]:
    """Erreurs de conformité d'UNE valeur à un type déclaré (récursif)."""
    if ftype == "text":
        return [] if isinstance(value, str) else [f"{path}: attendu text, reçu {type(value).__name__}"]
    if ftype == "number":
        if isinstance(value, bool):
            return [f"{path}: attendu number, reçu bool"]
        if isinstance(value, (int, float)):
            return []
        if isinstance(value, str) and _NUM_RE.match(value.strip()):
            return []  # coercible — l'agent écrit souvent "42"
        return [f"{path}: attendu number, reçu {value!r}"]
    if ftype == "bool":
        return [] if isinstance(value, bool) else [f"{path}: attendu bool, reçu {value!r}"]
    if ftype in ("date", "datetime"):
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return []
            except ValueError:
                pass
        return [f"{path}: attendu {ftype} ISO, reçu {value!r}"]
    if ftype == "url":
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return []
        return [f"{path}: attendu une URL http(s), reçu {value!r}"]
    if ftype == "email":
        if isinstance(value, str) and "@" in value and " " not in value.strip():
            return []
        return [f"{path}: attendu un e-mail, reçu {value!r}"]
    if ftype == "enum":
        # `options` absentes ⇒ enum libre (le client rend un select vide, pas d'erreur).
        if not isinstance(value, str):
            return [f"{path}: attendu une valeur d'énumération, reçu {value!r}"]
        allowed = [str(o) for o in (options or [])]
        if allowed and value not in allowed:
            return [f"{path}: valeur {value!r} hors options ({', '.join(allowed)})"]
        return []
    if ftype == "object":
        if not isinstance(value, dict):
            return [f"{path}: attendu object, reçu {type(value).__name__}"]
        return _row_errors(fields or [], value, path)
    if ftype == "list":
        if not isinstance(value, list):
            return [f"{path}: attendu list, reçu {type(value).__name__}"]
        errors: list[str] = []
        of = of or {}
        sub_fields = of.get("fields")
        for i, item in enumerate(value):
            ipath = f"{path}[{i}]"
            if isinstance(sub_fields, list):
                if not isinstance(item, dict):
                    errors.append(f"{ipath}: attendu object, reçu {type(item).__name__}")
                else:
                    errors.extend(_row_errors(
                        [x for x in sub_fields if isinstance(x, dict)], item, ipath))
            elif of.get("type"):
                errors.extend(_type_error(item, of["type"], ipath,
                                          of.get("fields"), of.get("of"),
                                          of.get("options")))
        return errors
    return []  # json / type absent : tout passe


def _row_errors(fields: list, data: dict, path: str,
                written: Optional[set] = None) -> list[str]:
    """Erreurs d'un (sous-)record. `written` = clés effectivement RÉÉCRITES par ce
    geste (None = toutes) : seule la borne de longueur s'y restreint, cf.
    `validate_row`. La récursion dans un sous-record repart à None — remplacer une
    clé de premier niveau réécrit tout ce qu'elle contient."""
    errors: list[str] = []
    for f in fields:
        key = f.get("key")
        if not key:
            continue
        fpath = f"{path}.{key}" if path else key
        inconnues = unknown_layers(data.get(key))
        if inconnues:
            errors.append(
                f"{fpath}: sous-champ(s) inconnu(s) {', '.join(repr(k) for k in inconnues)}"
                f" — disponibles : {', '.join(LAYER_KEYS)}. Une couche stockée sans "
                "être lue donnerait l'illusion d'une provenance renseignée.")
        # Déballer avant de juger : c'est la VALEUR qui doit respecter le type, la
        # borne et les options — pas son enveloppe. Sans ça un schéma strict refuse
        # toute écriture en couches, donc la primitive est inutilisable là où elle
        # sert le plus.
        #
        # Une clé POINTÉE (#377) désigne une couche : `qualification.comment` est la
        # justification portée par la colonne `qualification`, pas une colonne du
        # même nom. Le contrôle lisait `data["qualification.comment"]` — un nom que
        # `_refuse_dotted_names` interdit précisément d'ÉCRIRE : la contrainte ne
        # pouvait donc jamais être satisfaite, et refusait jusqu'aux écritures qui
        # portaient bien le commentaire. Accepté à la pose, bloquant à l'écriture :
        # un cran plus grave qu'inerte, parce que la déclaration avait l'air d'avoir
        # pris.
        base, layer = split_layer(key)
        value = (layer_value(data.get(base), layer) if layer
                 else unwrap(data.get(key)))
        required = bool(f.get("required"))
        rw = f.get("required_when")
        if not required and isinstance(rw, dict) and rw:
            # Une condition en LISTE = requis quand la valeur ∈ liste (#347).
            # Avant, str(liste) ne matchait jamais : la déclaration qui semblait
            # ÉLARGIR la garde la rendait inerte, sans un mot.
            # ⚠️ La valeur de condition se DÉBALLE (unwrap) comme toute valeur
            # jugée — une qualification écrite en couches ({"valeur": …}) est un
            # dict brut qui ne matche rien : la garde était désarmée par le
            # geste NORMAL des agents (justifier en couches), et par tout merge
            # sur une ligne portant déjà une couche (prouvé en re-validation :
            # 5 fiches écartées sans motif, aucun refus).
            required = all(
                str(unwrap(data.get(k))) in {str(x) for x in v}
                if isinstance(v, (list, tuple))
                else str(unwrap(data.get(k))) == str(v)
                for k, v in rw.items())
        if _is_empty(value):
            if required:
                cause = f" (requis quand {rw})" if not f.get("required") and rw else ""
                errors.append(f"{fpath}: champ requis manquant{cause}")
            continue
        if f.get("type"):
            errors.extend(_type_error(value, f["type"], fpath,
                                      f.get("fields"), f.get("of"),
                                      f.get("options")))
        mi = f.get("max_items")
        if (isinstance(mi, int) and not isinstance(mi, bool) and mi > 0
                and isinstance(value, list) and len(value) > mi):
            # Même forme que la borne de longueur : le CONSTATÉ autant que la borne,
            # sinon le refus fait deviner de combien on dépasse.
            errors.append(f"{fpath}: {len(value)} éléments, maximum {mi}")
        ml = max_length_of(f)
        pose = written is None or key in written
        trop_long = False
        if ml and pose:
            n = len(value) if isinstance(value, str) else len(str(value))
            if n > ml:
                # La longueur CONSTATÉE autant que la borne : un refus qui ne dit
                # pas de combien on dépasse fait deviner (signal #383).
                errors.append(f"{fpath}: {n} caractères, maximum {ml}")
                trop_long = True
        # #387 : la FORME, là où la taille ne sépare rien. Restreint aux clés que le
        # geste ÉCRIT, comme la borne et pour la même raison : la validation porte
        # sur le résultat MERGÉ, donc sans cette restriction une ligne déjà non
        # conforme deviendrait inécritable pour n'importe quel patch, y compris sur
        # un champ sans rapport (23 lignes gelées chez un client, oto-backend#284).
        # Sauté quand la valeur dépasse déjà la borne : c'est ELLE qui garantit que
        # le motif s'exécute sur un sujet de taille connue — et le refus est déjà
        # posé, l'ajouter en double ne dirait rien de plus.
        motif = pattern_of(f)
        if motif and pose and not trop_long:
            texte = value if isinstance(value, str) else str(value)
            if not _pattern_re(motif).search(texte):
                # La valeur CONSTATÉE autant que le motif attendu : sans le motif, le
                # refus ne laisse rien à corriger ; sans la valeur, il fait relire la
                # ligne pour savoir ce qui coince.
                errors.append(
                    f"{fpath}: {texte!r} ne suit pas le motif `{motif}`")
    return errors


def validate_row(schema: Optional[dict], merged: dict, *,
                 prev_status: Any = None,
                 written: Optional[set] = None) -> list[str]:
    """Erreurs d'une row TELLE QU'ELLE SERA ÉCRITE (le résultat mergé, pas le
    patch) : required / required_when / types / structure imbriquée — si la
    validation est active — plus le cycle de vie (états + transitions) dès qu'un
    `lifecycle` est déclaré, même hors mode strict. Liste vide = OK.

    `written` = les clés que ce geste réécrit (None = la row entière, cas d'un
    insert ou d'un remplacement). La borne `max_length` — et elle seule — s'y
    restreint : c'est une propriété de la valeur qu'on POSE, pas de l'état final.
    Sans ça, une valeur trop longue déjà en base ferait échouer tout patch
    ultérieur de la ligne, même portant sur un champ sans rapport (signal #383).
    Le reste continue de se juger sur le mergé : un requis manquant est un défaut
    de la row, quel que soit le geste qui l'y laisse."""
    errors: list[str] = []
    if validation_active(schema):
        # required_when se juge sur la row finale (le statut mergé, pas l'ancien)
        errors.extend(_row_errors(_fields(schema), merged, "", written))
    lc = lifecycle_of(schema)
    if lc:
        sf = status_field(schema)
        key = sf.get("key") if sf else None
        new = merged.get(key) if key else None
        if new is not None:
            states = {str(s) for s in lc.get("states") or []}
            if states and str(new) not in states:
                errors.append(
                    f"{key}: état inconnu {new!r} (états: {sorted(states)})")
            elif prev_status is not None and str(prev_status) != str(new):
                transitions = lc.get("transitions")
                if isinstance(transitions, dict):
                    allowed = {str(t) for t in transitions.get(str(prev_status)) or []}
                    if str(new) not in allowed:
                        errors.append(
                            f"{key}: transition {prev_status!r} → {new!r} interdite"
                            + (f" (autorisées: {sorted(allowed)})" if allowed
                               else " (état terminal)"))
    return errors


# ── Ce qu'une POSE de schéma efface (#388) ───────────────────────────────────
#
# `set_schema` pose le schéma ENTIER, sans fusion. Le geste qui ne PEUT pas détruire
# existe (`patch_schema`, fusion par clé), mais `set_schema` reste la bonne façon de
# POSER un format — et rien dans sa réponse ne disait ce qu'il venait d'emporter.
#
# ⚠️ Le point qui fait le signal : **le mode d'écriture était indétectable côté
# appelant**. Sur le même tableau, le même jour, la même session a fait les deux — sa
# migration a PRÉSERVÉ 78 notes de champ (elle patchait le schéma relu en mémoire),
# son remappage en a DÉTRUIT deux (il rebâtissait la liste). Même méthode, même
# succès, réponse identique : il fallait connaître son propre code pour savoir ce
# qu'on venait de perdre, ce qui est hors de portée d'un agent qui exécute une
# procédure écrite par un autre. Deux incidents en deux jours, dont 52 notes.
#
# C'est la forme exacte du défaut corrigé le 27/08 sur les LIGNES (`valeurs_effacees`,
# #407/#408/#409) : une écriture qui efface sans le dire. D'où le même parti, jusqu'au
# nom — on n'empêche rien (retirer un champ est légitime), on NOMME. Et on rend les
# VALEURS : après la pose, la réponse est la seule copie qui reste.

# Ce qui décrit la STRUCTURE plutôt qu'un réglage : `key` identifie l'entrée,
# `fields`/`of` portent les sous-champs — ceux-là sont relevés à leur propre chemin,
# les compter deux fois ferait un relevé qui s'auto-amplifie.
_DECL_STRUCTURELLES = ("key", "fields", "of")
_DECL_NOMMEES = 20
_DECL_VALEUR_MAX = 300


def _field_index(schema: Optional[dict]) -> dict:
    """`{chemin: field-def}` — même convention de chemin que
    `unknown_declaration_keys` (`occupant.nom`, `contacts[].email`), pour qu'un agent
    lise le même nom dans les deux relevés."""
    out: dict = {}

    def _visiter(fields: list, prefixe: str = "") -> None:
        for f in fields:
            if not isinstance(f, dict) or not f.get("key"):
                continue
            nom = f"{prefixe}{f['key']}"
            out[nom] = f
            if isinstance(f.get("fields"), list):
                _visiter(f["fields"], f"{nom}.")
            of = f.get("of")
            if isinstance(of, dict) and isinstance(of.get("fields"), list):
                _visiter(of["fields"], f"{nom}[].")

    _visiter(_fields(schema))
    return out


def _sous_un_retire(chemin: str, retires: set) -> bool:
    """Le champ vit-il SOUS un champ déjà relevé comme retiré ? Alors sa perte est
    déjà dite par celle de son parent — la répéter gonflerait le compte sans rien
    apprendre."""
    return any(chemin != r and chemin.startswith(r) and chemin[len(r)] in ".["
               for r in retires)


def declarations_effacees(ancien: Optional[dict], nouveau: Optional[dict],
                          annonces: Optional[list] = None) -> list[dict]:
    """Ce que poser `nouveau` retire de `ancien` — `[{champ, retire, declarations}]`.

    `champ = None` désigne la TÊTE du schéma (`key`, `strict`) : ce qu'on perd en
    premier est `schema.key`, la clé métier, qui porte un index UNIQUE partiel — la
    re-poster absente lève la contrainte sans que rien ne le signale.

    `annonces` = les champs dont le retrait est DÉJÀ dit par l'appelant (le `remove`
    d'un patch). Les taire n'affaiblit pas le filet : tout ce qui se perd en plus
    reste relevé — c'est même le seul moyen de voir une fusion qui laisserait
    échapper quelque chose. Et un avertissement qui crie sur un geste explicite est
    celui qu'on apprend à ignorer, donc celui qui ruine les vrais.

    Seules les DISPARITIONS comptent, jamais les changements de valeur : réécrire une
    note est un geste qui se nomme lui-même ; la faire disparaître, non."""
    if not isinstance(ancien, dict):
        return []
    tus = {str(a) for a in (annonces or [])}
    sortie: list[dict] = []

    tete_av = {k: v for k, v in ancien.items() if k != "fields"}
    tete_ap = ({k: v for k, v in nouveau.items() if k != "fields"}
               if isinstance(nouveau, dict) else {})
    perdu = {k: v for k, v in tete_av.items() if k not in tete_ap}
    if perdu:
        sortie.append({"champ": None, "retire": False, "declarations": perdu})

    av, ap = _field_index(ancien), _field_index(nouveau)
    retires: set = set()
    for chemin in sorted(av, key=len):            # les parents avant les enfants
        fav = av[chemin]
        if chemin not in ap:
            retires.add(chemin)
            if chemin in tus or _sous_un_retire(chemin, retires):
                continue
            sortie.append({
                "champ": chemin, "retire": True,
                "declarations": {k: v for k, v in fav.items()
                                 if k not in _DECL_STRUCTURELLES}})
            continue
        if chemin in tus:
            continue
        fap = ap[chemin]
        manquantes = {k: v for k, v in fav.items()
                      if k not in _DECL_STRUCTURELLES and k not in fap}
        if manquantes:
            sortie.append({"champ": chemin, "retire": False,
                           "declarations": manquantes})
    return sortie


def _decl_rendue(valeur: Any) -> Any:
    """La déclaration perdue, ou sa TAILLE quand la rendre coûterait la réponse.

    La taille, jamais un extrait : projeter n'est pas tronquer — un début de valeur
    ferait croire qu'on l'a lue, alors qu'elle n'est plus nulle part."""
    n = len(valeur) if isinstance(valeur, str) else len(str(valeur))
    if n <= _DECL_VALEUR_MAX:
        return valeur
    return (f"<{n} caractères — la valeur complète n'est plus lisible ici, "
            "elle n'est plus en base non plus>")


def declarations_effacees_report(entrees: list) -> dict:
    """Le relevé prêt à fusionner dans la réponse d'une pose de schéma.

    `{}` quand rien n'a été retiré — le cas normal ne porte pas de clé parasite."""
    if not entrees:
        return {}
    nommees = [{**e, "declarations": {k: _decl_rendue(v)
                                      for k, v in e["declarations"].items()}}
               for e in entrees[:_DECL_NOMMEES]]
    hint = ("cette écriture RETIRE des déclarations que le schéma portait — poser un "
            "schéma le REMPLACE, il ne le fusionne pas, donc tout réglage absent du "
            "corps envoyé disparaît (une note de champ, une borne, des options, une "
            "clé métier et son index UNIQUE). Si ce n'est pas voulu, repose les "
            "valeurs ci-dessus : elles ne sont plus en base. Pour ÉDITER un format "
            "sans risquer d'en perdre une part, `data_patch_schema` fusionne par clé "
            "et ne peut pas détruire ce qu'il ne nomme pas.")
    if len(entrees) > len(nommees):
        hint += (f" {len(entrees)} entrées effacées au total, "
                 f"{len(nommees)} nommées ici.")
    return {"declarations_effacees": nommees,
            "declarations_effacees_hint": hint}


# ── Ce que CETTE version fait respecter (#389) ───────────────────────────────
#
# Le signal qui rendait les autres dangereux : il ne demandait pas une contrainte de
# plus, il demandait de savoir lesquelles MORDENT. Deux cas vécus le même jour, et le
# second est le vrai sujet — l'écart n'était pas dans le vocabulaire mais dans le
# DÉPLOIEMENT. `max_length: 60` posé sur quatre colonnes d'un tableau de production,
# code de validation écrit le jour même, version déployée qui ne l'exécutait pas
# encore : un PATCH idempotent rendait 200, et avec le code à jour 75 lignes sur 600
# devenaient inécritables. Effet DIFFÉRÉ au prochain déploiement, MASSIF, SIMULTANÉ,
# et de cause vieille de plusieurs semaines — personne ne relie « les agents
# n'écrivent plus sur ces lignes » à « quelqu'un a posé une borne un mardi ».
#
# `unknown_declaration_keys` (#316) dit déjà la moitié NÉGATIVE — « cette clé, je ne
# la lis pas ». Il manquait la moitié POSITIVE, la seule qu'un client puisse vérifier
# contre le serveur qui lui répond plutôt que contre une documentation.
#
# ⚠️ **Le relevé s'établit en FAISANT TOURNER le validateur**, jamais en recopiant une
# liste. Une liste parallèle diverge le jour où quelqu'un exécute une clé de plus (ou
# cesse d'en exécuter une), et elle se met alors à mentir dans les deux sens — ce que
# le signal reproche au silence. Chaque sonde est un schéma minimal + une ligne qui le
# viole : la clé est annoncée si, et seulement si, `validate_row` refuse ici et
# maintenant. C'est le même parti que `interpreted_keys` (dérivé du code), poussé d'un
# cran : dérivé du COMPORTEMENT, donc insensible à la façon dont le code est écrit.

# `(clé, schéma qui doit REFUSER, ligne fautive, témoin qui doit PASSER ou None)`.
# Le témoin ne sert qu'aux clés dont l'effet est d'ARMER autre chose : `strict`
# n'interdit rien par lui-même, il rend la conformité de type opposable. Sans le
# témoin, on l'annoncerait dès que le type est vérifié, ce qui serait vrai par
# accident.
_ENFORCEMENT_PROBES = (
    ("required",
     {"fields": [{"key": "x", "required": True}]}, {}, None),
    ("required_when",
     {"fields": [{"key": "x", "required_when": {"y": "1"}}, {"key": "y"}]},
     {"y": "1"}, None),
    ("max_length",
     {"fields": [{"key": "x", "max_length": 1}]}, {"x": "ab"}, None),
    ("pattern",
     {"fields": [{"key": "x", "max_length": 8, "pattern": "^ok$"}]},
     {"x": "non"}, None),
    ("max_items",
     {"strict": True,
      "fields": [{"key": "x", "type": "list", "of": {"type": "text"},
                  "max_items": 1}]},
     {"x": ["a", "b"]}, None),
    ("options",
     {"strict": True,
      "fields": [{"key": "x", "type": "enum", "options": ["a"]}]},
     {"x": "b"}, None),
    ("type",
     {"strict": True, "fields": [{"key": "x", "type": "number"}]},
     {"x": "abc"}, None),
    ("strict",
     {"strict": True, "fields": [{"key": "x", "type": "number"}]}, {"x": "abc"},
     ({"fields": [{"key": "x", "type": "number"}]}, {"x": "abc"})),
    ("lifecycle",
     {"fields": [{"key": "s", "role": "status",
                  "lifecycle": {"states": ["a", "b"]}}]},
     {"s": "z"}, None),
)

_ENFORCED: Optional[tuple] = None


def reset_enforced_keys() -> None:
    """Oublie le relevé mémorisé — pour un banc qui désarme une règle et vérifie que
    l'annonce tombe avec elle."""
    global _ENFORCED
    _ENFORCED = None


def enforced_keys() -> list[str]:
    """Les clés de validation que CETTE version EXÉCUTE, triées.

    Rendue à la pose ET à la lecture d'un schéma : un client peut donc vérifier que ce
    qu'il déclare sera appliqué par le serveur qui lui répond — c'est la seule parade
    au décalage entre le code écrit et la version servie."""
    global _ENFORCED
    if _ENFORCED is None:
        vues = []
        for cle, schema, row, temoin in _ENFORCEMENT_PROBES:
            if not validate_row(schema, row):
                continue                      # la règle n'existe pas ici
            if temoin and validate_row(temoin[0], temoin[1]):
                continue                      # elle refuse même sans la clé : pas elle
            vues.append(cle)
        # `key_required` (#516) ne se prouve pas sur une ROW : il se juge contre le
        # CONTENU du tableau (cette clé désigne-t-elle une ligne ?), que `validate_row`
        # ne voit pas. Sa sonde interroge donc la fonction qui DÉCIDE — dérivée du
        # code comme les autres, jamais une ligne de liste : le jour où le cran
        # disparaît, l'annonce tombe avec lui.
        if key_required_of({"key": "x", "key_required": True}):
            vues.append("key_required")
        _ENFORCED = tuple(sorted(vues))
    return list(_ENFORCED)


# ── Clés de déclaration non interprétées (#316) ──────────────────────────────
#
# Le cas réel : trois champs posés avec `enum: [...]` au lieu d'`options: [...]`.
# La clé a été stockée, rendue fidèlement, affichée — et jamais lue. Les trois
# énumérations étaient LIBRES sans que rien ne le dise, et 504 valeurs sont entrées
# sur un tableau qui se croyait contraint. Comportement conforme au contrat, et
# indistinguable d'un enum contraint À L'USAGE.
#
# ⚠️ **On ne ferme PAS le vocabulaire**, et c'est doctrinal : les consommateurs posent
# leurs propres déclarations (`role: qualif`, `dated_by`, `compare_by`, `initial_of`)
# que le datastore transporte sans les interpréter. Refuser l'inconnu casserait ce
# contrat. On SIGNALE — même patron que `hors_schema` à l'écriture d'une ligne : on
# n'empêche rien, on rend la chose visible et actionnable.


def _read_keys() -> frozenset:
    """Les clés que le code LIT réellement, dérivées de son source.

    ⚠️ **Dérivées, pas listées** — et ce n'est pas du zèle : une liste parallèle du
    vocabulaire diverge le jour où quelqu'un lit une clé de plus (ou cesse d'en lire
    une), et le signal se met alors à mentir dans les deux sens — taire une vraie
    faute de frappe, ou accuser une clé parfaitement lue. C'est exactement ce que
    `lifecycle` et `role` s'apprêtent à faire : ils sont en cours de recadrage
    (#315/#317), et les figer ici en dur les laisserait dans le vocabulaire après
    que le code aura cessé de les lire.

    La dérivation surestime (elle ramasse aussi des clés de ligne ou de namespace,
    `data`, `owner_id`…) et c'est le BON côté de l'erreur : on signale moins, jamais
    à tort. Un faux positif — accuser une clé qui marche — est ce qui ferait ignorer
    l'avertissement, donc le rendrait inutile.
    """
    import ast
    import pathlib

    keys: set = set()
    ici = pathlib.Path(__file__).parent
    for nom in ("schema.py", "core.py"):
        try:
            arbre = ast.parse((ici / nom).read_text(encoding="utf-8"))
        # noqa: SILENT — clés de schéma illisibles ⇒ ensemble vide, la lecture continue
        except Exception:      # source illisible (zip, .pyc seul) : on n'invente pas
            return frozenset()
        for n in ast.walk(arbre):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get" and n.args
                    and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                keys.add(n.args[0].value)
    return frozenset(keys)


_READ_KEYS: Optional[frozenset] = None


def interpreted_keys() -> frozenset:
    """Le vocabulaire effectivement interprété — calculé une fois, dérivé du code."""
    global _READ_KEYS
    if _READ_KEYS is None:
        _READ_KEYS = _read_keys()
    return _READ_KEYS


# Fautes de frappe qui MÉRITENT d'être nommées : une clé inconnue proche d'une clé
# lue n'est presque jamais une déclaration tierce délibérée. Dérivé lui aussi — les
# variantes pointent vers la clé réelle, qui doit exister dans le vocabulaire lu.
_NEAR_MISS = {
    "enum": "options", "enums": "options", "option": "options",
    "choices": "options", "choix": "options", "values": "options",
    "valeurs": "options", "allowed": "options",
    "maxlength": "max_length", "max_len": "max_length", "maxLength": "max_length",
    "requiredWhen": "required_when", "required_if": "required_when",
    "mandatory": "required", "obligatoire": "required",
    "champs": "fields", "columns": "fields",
    "cle": "key", "name": "key", "nom": "key",
}


def unknown_declaration_keys(schema: Optional[dict]) -> list[dict]:
    """Par champ, les clés de déclaration qu'oto n'interprète pas.

    Rend `[{field, keys: [...], near_miss: {clé: clé_réelle}}]` — vide quand tout est
    lu. Le near-miss est ce qui rend l'avertissement ACTIONNABLE : « `enum` n'est pas
    lue par oto ; si tu voulais contraindre les valeurs, la clé est `options` » vaut
    infiniment mieux que « clé inconnue ».
    """
    if not isinstance(schema, dict):
        return []
    lues = interpreted_keys()
    if not lues:                       # dérivation indisponible : ne rien affirmer
        return []
    out: list[dict] = []

    def _visiter(fields: list, prefixe: str = "") -> None:
        for f in fields:
            if not isinstance(f, dict):
                continue
            nom = f"{prefixe}{f.get('key') or '?'}"
            inconnues = sorted(k for k in f if k not in lues)
            if inconnues:
                near = {k: _NEAR_MISS[k] for k in inconnues
                        if k in _NEAR_MISS and _NEAR_MISS[k] in lues}
                out.append({"field": nom, "keys": inconnues, "near_miss": near})
            if isinstance(f.get("fields"), list):
                _visiter(f["fields"], f"{nom}.")
            of = f.get("of")
            if isinstance(of, dict) and isinstance(of.get("fields"), list):
                _visiter(of["fields"], f"{nom}[].")

    _visiter(_fields(schema))
    return out


def unknown_keys_warning(inconnues: list[dict]) -> str:
    """Le message rendu à l'appelant — une phrase, pas un dump.

    Il dit la CONSÉQUENCE (« stockée et rendue, mais jamais lue ») avant la
    correction : sans elle, un lecteur pressé prend l'avertissement pour un détail de
    style, alors qu'il signale une contrainte qui n'existe pas."""
    if not inconnues:
        return ""
    corrections = [f"{k} → {v}" for e in inconnues
                   for k, v in (e.get("near_miss") or {}).items()]
    champs = ", ".join(f"{e['field']} ({', '.join(e['keys'])})" for e in inconnues[:5])
    msg = (f"Clés non interprétées par oto : {champs}"
           + (" …" if len(inconnues) > 5 else "")
           + ". Elles sont stockées et rendues telles quelles, mais AUCUNE ne "
             "contraint quoi que ce soit.")
    if corrections:
        msg += " Vouliez-vous écrire : " + ", ".join(sorted(set(corrections))) + " ?"
    return msg


def unknown_keys_read_warning(inconnues: list[dict]) -> str:
    """Le MÊME relevé, dit au LECTEUR d'un schéma plutôt qu'à son auteur (#416).

    ⚠️ Ce n'est pas une variante de style : l'avertissement de pose demande « vouliez-
    vous écrire `options` ? », question qui n'a aucun sens pour qui lit le schéma d'un
    tableau qu'il n'a pas déclaré — il n'a rien voulu écrire, il cherche à savoir à
    quoi s'en tenir. Ce qu'il lui faut, c'est **laquelle des deux clés fait foi**.

    Le défaut mesuré : un champ portant à la fois `enum` (jamais lue) et `options`
    (qui contraint) donne deux réponses contradictoires à « quelles valeurs sont
    admises ». Un agent se fie au plus court, `enum` — qui a l'air le plus officiel —
    et se restreint à tort, ou attend un rejet qui n'arrivera jamais.

    La liste vient de `unknown_declaration_keys`, comme à la pose : une seule
    dérivation, deux formulations. Le jour où une clé entre dans le vocabulaire lu,
    les deux messages s'éteignent ensemble."""
    if not inconnues:
        return ""
    champs = ", ".join(f"{e['field']} ({', '.join(e['keys'])})" for e in inconnues[:5])
    # Ce qui FAIT FOI, quand la clé morte a une cousine vivante : c'est la seule
    # information qui permette d'écrire juste sans reposer le schéma.
    autorite = sorted({v for e in inconnues for v in (e.get("near_miss") or {}).values()})
    msg = (f"Ce schéma porte des clés qu'oto NE LIT PAS : {champs}"
           + (" …" if len(inconnues) > 5 else "")
           + ". Elles sont stockées et rendues fidèlement, mais ne contraignent RIEN "
             "— ne t'y fie pas pour savoir ce qui est admis.")
    if autorite:
        msg += (" Ce qui fait foi : " + ", ".join(f"`{k}`" for k in autorite)
                + ". En cas de contradiction entre les deux, c'est cette clé-là qui "
                  "décide, et l'autre est un résidu.")
    return msg


# ── Options déclarées mais non appliquées (#319) ─────────────────────────────
#
# `validation_active` ne s'arme que sur `strict` / `required` / `required_when` /
# `max_length` — **`options` n'y est pas**. Un tableau qui déclare
# `options: ["oui","non","inconnu"]` et rien d'autre accepte « Peut-être » sans un mot.
#
# Le défaut a été signalé sur pièce par une mission, et il est aggravé par #316 : cet
# avertissement-là dirige vers `options` (« si tu voulais contraindre les valeurs, la
# clé est `options` ») — donc vers une clé qui, hors strict, ne contraint rien. Le
# correctif précédent avait déplacé le mensonge d'un cran.
#
# ⚠️ **On AVERTIT, on ne refuse pas.** Un tableau non-strict est en régime souple PAR
# DÉCLARATION : y refuser changerait son contrat rétroactivement. Mesuré en production
# le 13/08 — 23 tableaux sur 57 sont dans ce cas, et les 118 valeurs réellement hors
# liste sont TOUTES sur un seul, dont les écritures deviendraient des erreurs du jour
# au lendemain sans qu'il ait rien demandé. Le régime strict, lui, refuse déjà.
#
# ⚠️ **Tout est DÉRIVÉ des fonctions qui décident** (`validation_active`,
# `top_level_enum_options`), jamais d'une copie de leur logique : le jour où `options`
# entrera dans `validation_active`, ces avertissements s'éteindront d'eux-mêmes. Ce
# lot existe précisément parce qu'une liste avait divergé de ce que le code lit.


def _options_already_enforced(schema: Optional[dict]) -> set:
    """Les champs dont les valeurs sont DÉJÀ contraintes autrement que par `options`.

    ⚠️ Aujourd'hui il n'y en a qu'un : le champ `role="status"` porteur d'un
    `lifecycle`, dont les états sont refusés hors liste MÊME quand `validation_active`
    est faux (vérifié : un état inconnu lève, sans `strict`). L'avertir serait un FAUX
    POSITIF — et un avertissement qui crie à tort est celui qu'on apprend à ignorer,
    donc celui qui ruine les deux autres.

    Dérivé de `lifecycle_of`/`status_field`, jamais d'un nom en dur : le mécanisme de
    cycle de vie est en cours de retrait (#317) et cette exclusion s'éteindra d'
    elle-même le jour où il partira."""
    if lifecycle_of(schema) is None:
        return set()
    sf = status_field(schema) or {}
    key = sf.get("key")
    return {str(key)} if key else set()


def unenforced_options(schema: Optional[dict], data: dict) -> dict:
    """`{champ: valeur hors liste}` — et SEULEMENT quand rien ne les fait respecter.

    Vide dès que la validation est armée : là, une valeur hors options est REFUSÉE, et
    signaler en plus serait un doublon bavard sur un chemin qui ne peut pas passer.
    """
    if validation_active(schema) or not isinstance(data, dict):
        return {}
    deja = _options_already_enforced(schema)
    out: dict = {}
    for champ, opts in top_level_enum_options(schema).items():
        if champ in deja:
            continue
        v = data.get(champ)
        if v is not None and str(v) not in opts:
            out[champ] = str(v)
    return out


def unenforced_options_warning(hors: dict) -> Optional[str]:
    """La phrase qui accompagne le relevé — elle dit la CONSÉQUENCE avant le remède.

    Sans ça on lit « valeur inhabituelle » là où il faut lire « ce champ n'est pas la
    liste fermée que le schéma laisse croire »."""
    if not hors:
        return None
    detail = ", ".join(f"`{k}` = {v!r}" for k, v in sorted(hors.items()))
    return (f"valeur hors des options déclarées : {detail} — elle est ÉCRITE quand "
            "même. Ce tableau n'étant pas en format strict, les `options` de son "
            "schéma décrivent des choix proposés, elles ne les imposent pas. Pour "
            "qu'elles contraignent vraiment, pose `strict: true` sur le tableau "
            "(`data_set_schema`) — les écritures hors liste seront alors refusées.")


def options_not_enforced(schema: Optional[dict]) -> list[str]:
    """Les champs dont les `options` sont déclarées mais inertes — à la POSE.

    Pendant de #316, au moment qui compte : quand on écrit le schéma, pas six semaines
    plus tard en constatant les valeurs libres."""
    if validation_active(schema):
        return []
    deja = _options_already_enforced(schema)
    return sorted(c for c in top_level_enum_options(schema) if c not in deja)


def options_not_enforced_warning(champs: list[str]) -> Optional[str]:
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    return (f"options déclarées mais NON appliquées : {noms} — ce tableau n'est pas "
            "en format strict, donc ces listes sont indicatives : une valeur hors "
            "liste sera acceptée. Ajoute `strict: true` au schéma pour qu'elles "
            "contraignent.")


def json_fields_depth(schema: Optional[dict]) -> list[str]:
    """Les champs `type: json` — dont le contenu n'est pas interrogeable en profondeur.

    Le fait est documenté, mais invisible AU MOMENT où on déclare le champ : une
    mission y a mis toute sa traçabilité par champ avant de découvrir qu'elle n'était
    ni filtrable ni agrégeable."""
    return sorted(str(f.get("key")) for f in _walk_fields(_fields(schema))
                  if f.get("type") == "json" and f.get("key"))


def json_depth_warning(champs: list[str]) -> Optional[str]:
    """⚠️ Énonce le FAIT, sans prescrire de contournement : la provenance native est
    en cours de conception, et recommander une structure aujourd'hui reviendrait à
    conseiller ce qui sera obsolète demain."""
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    return (f"champ(s) `json` : {noms} — leur contenu est stocké et rendu tel quel, "
            "mais il n'est ni filtrable ni agrégeable au-delà du premier niveau : "
            "`data_rows` ne sait pas interroger une clé imbriquée, et l'export ne la "
            "déplie pas.")
