"""Déclarer une colonne d'après les valeurs qu'elle PORTE (oto#124).

Une colonne non déclarée est refusée à l'écriture à partir de la date de
`colonnes_non_declarees` : deux gestes déclarent pour celui qui écrit — le gel des
colonnes existantes (`scripts/figer_colonnes.py`) et l'import NDJSON
(`upload_tokens.parse_import`, comme l'import CSV déclare ses en-têtes). Les deux
déduisent le `type` des valeurs, par CETTE règle, une seule.

**La règle : l'homogénéité stricte, ou rien.** Un type n'est déclaré que si TOUTES les
valeurs non vides de la colonne (déballées de leurs couches, `couches.unwrap`) ont la
même forme JSON :

| toutes les valeurs sont… | type déclaré |
|---|---|
| des booléens JSON | `bool` |
| des nombres JSON (pas des chaînes de chiffres) | `number` |
| des chaînes `AAAA-MM-JJ` | `date` |
| des chaînes | `text` |
| des listes | `list`, `of: {}` (liste libre) |
| des objets | `json` |
| mélangées, ou aucune valeur | **pas de `type`** |

⚠️ **Pourquoi pas de type plutôt que `text` quand c'est hétérogène** : un `type` déclaré
s'arme seul pour `number`, `bool`, `date` (`types_declares.TYPES_ARMES`), et `text`
refuse un non-texte sous un format qui fait contrat (partout au 21/10/2026, oto#124). Une
colonne sans type n'a aucune forme à tenir (`validation._conformite_scalaire`) : la
déclarer ne refuse RIEN de plus qu'aujourd'hui. Le type n'est déduit que là où TOUTES
les valeurs en place le tiennent déjà — aucune ligne existante ne devient hors format.

⚠️ **Ce qu'un type déduit change pour la suite** : `number`, `bool` et `date` s'arment
dès leur déclaration — une écriture future qui ne les tient pas sera refusée, et une
date s'écrira sous sa forme `AAAA-MM-JJ`. C'est pourquoi `number` exige des NOMBRES
JSON (un code postal ou un SIREN écrit en chaîne reste `text`) et `date` la forme ISO
déjà normalisée.

Ni `options`, ni `required`, ni rien d'autre : on déclare la COLONNE, pas son contrat.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Optional

from .couches import unwrap

_DATE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _vide(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _forme(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "date" if _DATE_ISO.match(v) else "text"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "json"
    return "?"


def inferer(valeurs: Iterable[Any]) -> Optional[str]:
    """Le type que TOUTES les valeurs non vides tiennent, ou `None` (cf. l'en-tête).
    Les valeurs sont celles STOCKÉES : déballées ici."""
    formes = {_forme(v) for v in (unwrap(x) for x in valeurs) if not _vide(v)}
    if formes == {"date", "text"}:
        return "text"
    return formes.pop() if len(formes) == 1 and "?" not in formes else None


def declarable(cle: Any) -> bool:
    """Un nom que la pose d'un schéma accepte comme COLONNE : non vide, ni technique
    (`_…`), ni pointé (une couche ou une relique littérale, `definition` le refuse),
    ni par rang."""
    return (isinstance(cle, str) and bool(cle.strip()) and not cle.startswith("_")
            and "." not in cle and "[" not in cle)


def colonnes_a_declarer(valeurs_par_colonne: dict, declarees: set) -> list[dict]:
    """`[{"key", "type"?}]` des colonnes DÉCLARABLES absentes de `declarees`, de la plus
    portée à la moins portée (puis par nom) — l'ordre des `fields` est celui de la
    fiche. `valeurs_par_colonne` = `{colonne: [valeurs stockées]}`."""
    compte = Counter({c: sum(1 for v in vs if not _vide(unwrap(v)))
                      for c, vs in valeurs_par_colonne.items()})
    out = []
    for cle in sorted(valeurs_par_colonne, key=lambda c: (-compte[c], c)):
        if cle in declarees or not declarable(cle):
            continue
        ftype = inferer(valeurs_par_colonne[cle])
        # Une liste se déclare avec son élément (`definition` l'exige) : `of: {}` est
        # la liste LIBRE — aucun élément n'est jugé, aucun n'est identifié.
        decl = {"key": cle, **({"type": ftype} if ftype else {})}
        if ftype == "list":
            decl["of"] = {}
        out.append(decl)
    return out
