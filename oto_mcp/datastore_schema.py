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


def field_by_role(schema: Optional[dict], role: str) -> Optional[dict]:
    """Le premier field déclarant ce `role` (`status`, `title`…), ou None."""
    for f in _fields(schema):
        if f.get("role") == role:
            return f
    return None


def status_field(schema: Optional[dict]) -> Optional[dict]:
    """Le field déclaré `role="status"` (premier trouvé), ou None."""
    return field_by_role(schema, "status")


def title_field(schema: Optional[dict]) -> Optional[dict]:
    """Le field déclaré `role="title"` (premier trouvé), ou None — le LIBELLÉ d'une
    ligne (ce qu'un humain reconnaît dans un journal, à la place d'un uuid)."""
    return field_by_role(schema, "title")


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
        value = data.get(key)
        required = bool(f.get("required"))
        rw = f.get("required_when")
        if not required and isinstance(rw, dict) and rw:
            required = all(str(data.get(k)) == str(v) for k, v in rw.items())
        if _is_empty(value):
            if required:
                cause = f" (requis quand {rw})" if not f.get("required") and rw else ""
                errors.append(f"{fpath}: champ requis manquant{cause}")
            continue
        if f.get("type"):
            errors.extend(_type_error(value, f["type"], fpath,
                                      f.get("fields"), f.get("of"),
                                      f.get("options")))
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
