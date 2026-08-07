"""Datastore v2 — schéma structuré : validation d'écriture + cycle de vie (ADR 0046).

Module PUR (aucun I/O) : le schéma d'un namespace (colonne `user_datastores.schema`)
s'étend au-delà du rendu (0016) avec quatre couches OPT-IN :

- **types imbriqués** : `type: "object"` (+ `fields: [...]`) et `type: "list"`
  (+ `of: <field-def>` — scalaire ou sous-record) décrivent une *fiche* (occupant,
  `contacts[]`, `signaux[]`) que le blob JSONB porte déjà ;
- **validation à l'écriture** : `field.required`, conformité de type, et
  `field.required_when: {<champ>: <valeur>}` (le guard-rail : livrables requis
  quand `status = "qualified"`) — active si `schema.strict` OU si un field déclare
  required/required_when ;
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
    field déclarant `required`/`required_when`. Sans ça, écriture soft (0016)."""
    if not isinstance(schema, dict):
        return False
    if schema.get("strict"):
        return True
    return any(f.get("required") or f.get("required_when") for f in _fields(schema))


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


def _row_errors(fields: list, data: dict, path: str) -> list[str]:
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
    return errors


def validate_row(schema: Optional[dict], merged: dict, *,
                 prev_status: Any = None) -> list[str]:
    """Erreurs d'une row TELLE QU'ELLE SERA ÉCRITE (le résultat mergé, pas le
    patch) : required / required_when / types / structure imbriquée — si la
    validation est active — plus le cycle de vie (états + transitions) dès qu'un
    `lifecycle` est déclaré, même hors mode strict. Liste vide = OK."""
    errors: list[str] = []
    if validation_active(schema):
        # required_when se juge sur la row finale (le statut mergé, pas l'ancien)
        errors.extend(_row_errors(_fields(schema), merged, ""))
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
