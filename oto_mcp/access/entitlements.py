"""The SINGLE read point for declared entitlements (ADR 0070 §7).

The core asks "does this person, in this org, have this right, and at what value?"
and knows nothing more: neither who set it nor whether it is paid. It does not import
`billing` — commerce is a producer of entitlements, which writes into `org_entitlements`.

`value_for` applies the whole rule, re-read on every use (never cached):

1. the rows VALID at the instant (start inclusive, end exclusive, null bound = unbounded),
   all sources combined;
2. **the org's value**: the maximum of its org rows, and with no valid org row at all
   **the default declared by the instance** (`OTO_ENTITLEMENT_DEFAULTS`), never a
   default from the code. A catalogue key without a declared default makes startup fail
   (`verifier_defauts`, called by `server.main`);
3. **a per-person row only adds**: the most generous of the org's value and the
   person's rows (in the org and everywhere). The most generous is the
   LARGEST for every catalogue key: `0` there means "no" or "no access", and
   "unlimited" is written `SANS_PLAFOND`, the largest integer (`entitlements_catalogue`).

⚠️ An ORG row counts even when it is LESS generous than the default: it
replaces it (a quota of 5 set on the org wins over a default of 10). A PERSON
row, on the other hand, never removes anything: lower than the org's value, it does not
count. Without a person (`sub` None), the read is exactly the org's.
"""
from __future__ import annotations

import functools
import json
import os
from datetime import datetime
from typing import NamedTuple, Optional

from .. import entitlements_catalogue as catalogue
from .. import providers
from ..db import entitlements as db_entitlements

# The names the rest of the code imports from here (the catalogue is the source).
PLATFORM_UNMETERED = catalogue.PLATFORM_UNMETERED
MEMBERS_MAX = catalogue.MEMBERS_MAX

_VAR_DEFAUTS_DROITS = "OTO_ENTITLEMENT_DEFAULTS"
# In the declaration, "unlimited" is spelled out in full.
_SANS_PLAFOND_DECLARE = "unlimited"


def _declaration_defauts() -> str:
    brut = os.environ.get("OTO_ENTITLEMENT_DEFAULTS")
    if not brut:
        raise RuntimeError(
            f"{_VAR_DEFAUTS_DROITS} is missing: the instance must declare the value of each catalogue "
            "right for whoever has none set (JSON, e.g. "
            '{"unipile": 0, "platform_unmetered": 0, "unipile_seats": 5, '
            '"members_max": "unlimited", "platform_key:*": 0}). '
            "`scripts/defauts_des_droits.py` prints those of the historical instance.")
    return brut


@functools.lru_cache(maxsize=4)
def _analyser_defauts(brut: str) -> dict[str, int]:
    """The declaration, verified IN FULL: a JSON object, every key from the catalogue (or the
    `platform_key:*` wildcard), every value matching its key's kind, and every fixed
    catalogue key covered. Otherwise `RuntimeError`, naming everything that is missing."""
    try:
        donnees = json.loads(brut)
    except ValueError as e:
        raise RuntimeError(f"{_VAR_DEFAUTS_DROITS} is not valid JSON: {e}") from e
    if not isinstance(donnees, dict):
        raise RuntimeError(f"{_VAR_DEFAUTS_DROITS} must be a JSON object {{key: value}}")
    defauts: dict[str, int] = {}
    fautes: list[str] = []
    for cle, valeur in donnees.items():
        if valeur == _SANS_PLAFOND_DECLARE:
            valeur = catalogue.SANS_PLAFOND
        try:
            defauts[cle] = catalogue.valeur_de_defaut(cle, valeur)
        except ValueError as e:
            fautes.append(str(e))
    manquantes = sorted(set(catalogue.FIXES) - set(defauts))
    if catalogue.PLATFORM_KEY_JOKER not in defauts:
        sans = sorted(c for c in providers.REGISTRY
                      if catalogue.platform_key(c) not in defauts)
        if sans:
            manquantes.append(f"{catalogue.PLATFORM_KEY_JOKER} (or an override for: "
                              f"{', '.join(sans)})")
    if manquantes:
        fautes.append(f"default not declared for: {', '.join(manquantes)}")
    if fautes:
        raise RuntimeError(f"{_VAR_DEFAUTS_DROITS} rejected — " + " ; ".join(fautes))
    return defauts


def defauts_de_l_instance() -> dict[str, int]:
    """The defaults declared by the instance, verified. Raises if the declaration is missing or
    does not cover the catalogue."""
    return _analyser_defauts(_declaration_defauts())


def verifier_defauts() -> None:
    """The startup guard: a catalogue key without a declared default is an error
    AT STARTUP, never a silent 0 on first use."""
    defauts_de_l_instance()


def defaut_du_droit(key: str) -> int:
    """The declared default of `key` (a catalogue key, otherwise `ValueError`)."""
    catalogue.droit(key)
    defauts = defauts_de_l_instance()
    if key in defauts:
        return defauts[key]
    return defauts[catalogue.PLATFORM_KEY_JOKER]


class ValeurExpliquee(NamedTuple):
    """The value of an entitlement AND its provenance, as computed by `value_for`:
    `valeur` = what the usage points read; `par` = the valid rows read
    (scope, source, value), from which the value is drawn; `defaut` = true if the
    instance default supplied the org's value (no valid org row, or
    no org)."""
    valeur: int
    par: tuple[db_entitlements.LigneValide, ...]
    defaut: bool


def valeur_expliquee(sub: Optional[str], org_id: Optional[int], key: str,
                     now: Optional[datetime] = None) -> ValeurExpliquee:
    """The WHOLE rule, with its provenance — the only code that computes it; `value_for`
    returns only the value. See `value_for` for the rule."""
    catalogue.droit(key)
    defaut = defaut_du_droit(key)
    if org_id is None and sub is None:
        return ValeurExpliquee(defaut, (), True)
    posees = db_entitlements.valeurs_posees(None if org_id is None else int(org_id),
                                            sub, key, now)
    de_l_org = defaut if posees.org is None else posees.org
    valeur = de_l_org if posees.personne is None else max(de_l_org, posees.personne)
    return ValeurExpliquee(valeur, posees.lignes, posees.org is None)


def value_for(sub: Optional[str], org_id: Optional[int], key: str,
              now: Optional[datetime] = None) -> int:
    """The value of entitlement `key` for person `sub` in org `org_id`, at `now`
    (default: now, database clock): the org's value (its rows, otherwise the
    default declared by the instance), raised by the person's rows in the org and
    everywhere if they are more generous. `sub` None = the org alone; `org_id` None =
    the default, raised by the person's rows everywhere; both None = the default.

    The value of `valeur_expliquee`, which also returns where it comes from."""
    return valeur_expliquee(sub, org_id, key, now).valeur


def has_right(sub: Optional[str], org_id: Optional[int], right_key: str) -> bool:
    """True if person `sub`, acting in `org_id`, has this yes/no right (or a
    non-zero value): `value_for`, including the person's rows and the instance default.
    This is the question of a USAGE point, which knows the person."""
    return value_for(sub, org_id, right_key) >= 1


def org_has(org_id: int, right_key: str) -> bool:
    """True if the ORG has this yes/no right (or a non-zero value), without a person:
    its org rows, otherwise the instance default. For reads that concern
    no person (is the org subscribed?)."""
    return has_right(None, org_id, right_key)
