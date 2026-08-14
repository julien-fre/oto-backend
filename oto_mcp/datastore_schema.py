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
  sortante) — le store libère le claim de file de travail en y entrant.

Défaut (aucune de ces clés) = comportement 0016 inchangé : schéma de rendu SOFT.
Les erreurs sont des *listes de messages actionnables* — le store les joint dans
une ValueError, jamais un refus muet.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
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


def top_level_bounds(schema: Optional[dict]) -> dict[str, int]:
    """`{clé: max_length}` des champs BORNÉS de premier niveau — ceux qu'une requête
    SQL sait mesurer (`data->>clé`). Sert l'avertissement « des lignes existantes
    dépassent déjà » à la pose du schéma."""
    out: dict[str, int] = {}
    for f in _fields(schema):
        key, ml = f.get("key"), max_length_of(f)
        if isinstance(key, str) and key and ml:
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
        if not key or f.get("type") != "enum":
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
    required/required_when restent au premier niveau : élargir ces deux-là
    activerait rétroactivement la validation de schémas déjà posés, alors que
    déclarer une borne EST la demande de la faire respecter."""
    if not isinstance(schema, dict):
        return False
    if schema.get("strict"):
        return True
    if any(f.get("required") or f.get("required_when") for f in _fields(schema)):
        return True
    return any(max_length_of(f) for f in _walk_fields(_fields(schema)))


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
    else:
        # lifecycle posé sur un field non-status = erreur de placement (silencieux sinon)
        for f in _fields(schema):
            if isinstance(f.get("lifecycle"), dict) and f.get("role") != "status":
                errors.append(
                    f"field {f.get('key')!r}: lifecycle exige role=\"status\"")
    return errors


def _validate_fields_def(fields: list, path: str, errors: list[str]) -> None:
    for f in fields:
        key = f.get("key")
        fpath = f"{path}.{key or '?'}"
        if not isinstance(key, str) or not key:
            errors.append(f"{fpath}: key manquante")
            continue
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
        value = unwrap(data.get(key))
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
        if ml and (written is None or key in written):
            n = len(value) if isinstance(value, str) else len(str(value))
            if n > ml:
                # La longueur CONSTATÉE autant que la borne : un refus qui ne dit
                # pas de combien on dépasse fait deviner (signal #383).
                errors.append(f"{fpath}: {n} caractères, maximum {ml}")
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
    for nom in ("datastore_schema.py", "datastore.py"):
        try:
            arbre = ast.parse((ici / nom).read_text(encoding="utf-8"))
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
