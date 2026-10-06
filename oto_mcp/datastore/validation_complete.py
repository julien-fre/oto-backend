"""Le format déclaré fait contrat sur TOUS les tableaux (oto#124) — le préavis daté.

Jusqu'ici, la validation COMPLÈTE d'une écriture était un réglage : `unknown_columns`
autre que `create` (`reglages.format_contraignant`) faisait respecter, en plus de ce qui
s'arme seul (`required`, bornes, motif, types armés, couches exigées, cycle de vie) :

- les `options` de premier niveau ;
- la FORME des valeurs `text`, `url`, `object`, `list` et `enum` ;
- les couches inconnues d'une valeur (`{"valeur": …, "commentaire": …}`) ;
- la fermeture des sous-records déclarés (un attribut que `fields` ne nomme pas) ;
- à la pose, la garde de `lifecycle.claimable` (une colonne non déclarée).

Mesuré le 05/10/2026 : elle était active sur 100 tableaux, éteinte sur 382. Arbitré le
même jour : **toujours refuser, plus aucun réglage**. Une liste d'options ou une forme
déclarée est respectée partout, et `unknown_columns` disparaît
(`schema_keys.CLES_RETIREES`, `scripts/retirer_unknown_columns.py`).

Sur les 382 tableaux où elle était éteinte, 596 lignes sont déjà en faute (25 tableaux).
On ne corrige PAS leurs données — et on ne les bloque pas : **on juge ce que le geste
ÉCRIT, jamais ce que la ligne porte déjà** (`validation._row_errors`, `pose`). Une ligne
en faute s'écrit sur ses autres colonnes ; la faute qu'elle porte se DIT (`hors_type`),
elle ne refuse rien.

Mécanisme : celui des autres bascules datées (`colonnes_non_declarees`,
`upsert_implicite`), pas un de plus.

- **Avant la date**, sur un tableau qui n'était pas réglé pour faire contrat :
  l'écriture qu'aurait refusée la validation complète PASSE, et la réponse porte dans
  `notices` UNE phrase pour le geste (union sur un lot) — chaque faute (la colonne, la
  valeur, la règle : options permises ou forme attendue), la date, et le geste à faire
  (corriger la valeur, ou étendre les `options` par `data_patch_schema`). Toutes les
  faces passent par le même seam (`controles._check_row`) : MCP, REST, lots, upload,
  import.
- **À partir d'elle**, la validation complète s'applique à tout tableau à schéma : le
  refus est celui des tableaux qui la portaient déjà (`row_invalid`, ou la valeur hors
  options ÉCARTÉE quand elle est la seule faute, #667).

Le tableau encore réglé `report`/`reject` d'ici là garde sa validation complète : le
réglage stocké est LU jusqu'à la date (`reglages.format_contraignant`), et n'est plus
lu à partir d'elle — c'est pourquoi le script qui le retire des schémas se lance APRÈS
la date.

**La bascule tombe À LA DATE, dans le code** : rien n'est à déployer le jour J. Le
réglage `OTO_VALIDATION_COMPLETE_LE` déplace la date sans déployer.
"""
from __future__ import annotations

import os
from datetime import date as _date
from typing import Any, Iterable, Optional

from . import reglages

#: La date à partir de laquelle la validation complète s'applique à TOUS les tableaux à
#: schéma. Une seule constante : le texte servi en est DÉRIVÉ. Le même jour que le refus
#: des colonnes non déclarées (oto#124) et de l'`upsert` implicite (oto#141).
VALIDATION_COMPLETE_LE = _date(2026, 10, 21)

#: Déplace la date sans déployer (`YYYY-MM-DD`). Une valeur illisible LÈVE, elle ne
#: retombe pas sur le défaut (cf. `champs_reserves.date_reglee`).
ENV_VALIDATION_COMPLETE_LE = "OTO_VALIDATION_COMPLETE_LE"

#: Ce qu'un avertissement cite avant d'abréger.
_CITEES = 8


def date_de_bascule() -> _date:
    """La date en vigueur : le réglage s'il est posé, le défaut du code sinon."""
    from .champs_reserves import date_reglee

    return date_reglee(ENV_VALIDATION_COMPLETE_LE,
                       os.environ.get(ENV_VALIDATION_COMPLETE_LE),
                       VALIDATION_COMPLETE_LE)


def _aujourdhui() -> _date:
    """Le jour qui juge la bascule (UTC). Un point d'appui à part, pour que les bancs
    fixent le jour de CETTE bascule sans toucher à l'horloge ni aux autres."""
    from .champs_reserves import jour_utc

    return jour_utc()


def bascule_faite(aujourdhui: Optional[_date] = None) -> bool:
    """La validation complète s'applique-t-elle partout, AUJOURD'HUI (jour UTC) ?"""
    return (aujourdhui or _aujourdhui()) >= date_de_bascule()


def _quand() -> str:
    from .champs_reserves import _en_francais

    return _en_francais(date_de_bascule())


def _a_des_colonnes(schema: Any) -> bool:
    champs = schema.get("fields") if isinstance(schema, dict) else None
    return isinstance(champs, list) and any(
        isinstance(f, dict) and isinstance(f.get("key"), str) and f["key"]
        for f in champs)


def complete(schema: Any) -> bool:
    """LA décision : la validation complète s'applique-t-elle à ce tableau ?

    À partir de la date, sur tout tableau qui déclare au moins une colonne. Avant elle,
    seulement sur ceux que leur réglage stocké `unknown_columns` faisait déjà contracter
    (`reglages.format_contraignant`, lu jusqu'à la date et plus après)."""
    if not isinstance(schema, dict):
        return False
    if bascule_faite():
        return _a_des_colonnes(schema)
    return reglages.format_contraignant(schema)


def en_preavis(schema: Any) -> bool:
    """Ce tableau est-il dans la fenêtre du préavis ? — il déclare des colonnes, la
    validation complète ne s'y applique pas encore, et elle s'y appliquera à la date."""
    return _a_des_colonnes(schema) and not complete(schema)


# ── ce que la réponse en dit ─────────────────────────────────────────────────

def avertissement(fautes: Iterable[str]) -> Optional[str]:
    """L'avertissement servi AVANT la date : chaque faute telle que le refus la dira
    (la colonne, la valeur, la règle), la date, et le geste. `None` sans faute."""
    fautes = sorted(set(fautes))
    if not fautes:
        return None
    cite = " ; ".join(fautes[:_CITEES])
    reste = len(fautes) - _CITEES
    if reste > 0:
        cite += f" (+{reste} autres)"
    pluriel = len(fautes) > 1
    return (f"format déclaré non respecté — {cite}. L'écriture est passée telle quelle. "
            f"À partir du {_quand()}, le format déclaré fait contrat sur TOUS les "
            f"tableaux : {'ces valeurs seront REFUSÉES' if pluriel else 'cette valeur sera REFUSÉE'} "
            f"(`row_invalid` ; une valeur hors options seule dans sa faute est écartée et "
            f"le reste de la ligne s'écrit). Corrige la valeur — ou, si elle est juste, "
            f"étends le format : `data_patch_schema(datastore=…, fields=[{{\"key\": "
            f"\"<colonne>\", \"options\": [… toutes les valeurs permises …]}}])` (REST : "
            f"`PATCH /api/datastores/{{datastore}}/schema`). Les lignes déjà en base ne "
            f"sont pas touchées, et une écriture d'une AUTRE colonne n'est jamais jugée "
            f"sur elles.")


def avertissement_de_pose(fautes: list[str]) -> Optional[str]:
    """À la POSE d'un schéma, avant la date : les gardes de pose que la validation
    complète ajoutera (aujourd'hui, `lifecycle.claimable` sur une colonne non
    déclarée) — dites, pas refusées."""
    if not fautes:
        return None
    return (f"{' ; '.join(fautes)}. Accepté jusqu'au {_quand()} ; à partir de cette "
            f"date, ce schéma sera REFUSÉ à la pose tant que la faute demeure.")


# ── le texte servi ───────────────────────────────────────────────────────────

_REGLE_EN = ("top-level `options` are enforced, the SHAPE of `text`/`url`/`object`/"
             "`list`/`enum` values is checked, an unknown layer is refused and a "
             "declared sub-record refuses an attribute it does not declare")
_GESTE_EN = ("fix the value — or, if it is right, extend the format "
             "(`data_patch_schema(fields=[{\"key\": …, \"options\": [...]}])`)")
_JUGE_EN = ("Only what a write SETS is judged: a row already off-format still accepts "
            "a write to its OTHER columns (the stale value is reported in `hors_type`).")


def description_ecriture() -> str:
    """L'annonce servie dans la description de `data_write` et des faces REST, DÉRIVÉE
    de la date. Composée par ses appelants à LEUR chargement (jamais ici : `declaration`
    importe ce module, et la date se lit par `champs_reserves`, qui importe
    `declaration`) : avant la date, le préavis ; démarré après, la règle au présent."""
    if bascule_faite():
        return (f"⚠️ **The declared format is a CONTRACT on every table**: {_REGLE_EN}. "
                f"A violation is refused (`row_invalid`; an off-options value alone in "
                f"its fault is set aside and the rest of the row is written). "
                f"{_JUGE_EN} No head setting changes this.")
    return (f"⚠️ **From {date_de_bascule().isoformat()} on, the declared format is a "
            f"CONTRACT on every table**: {_REGLE_EN} — a violation is refused "
            f"(`row_invalid`). Until then, on a table that did not enforce it, the "
            f"write passes and `notices` names each fault (column, value, rule), the "
            f"date and what to do: {_GESTE_EN}. {_JUGE_EN}")



def description_schema() -> str:
    """Ce que `data_set_schema` et `data_patch_schema` disent de la règle — et de la fin
    du réglage `unknown_columns`."""
    quand = ("" if bascule_faite() else
             f" from {date_de_bascule().isoformat()} on (until then a violation passes "
             "with a dated warning in `notices`)")
    return (f"⚠️ The declared format is a CONTRACT on every table{quand}: {_REGLE_EN}. "
            "There is NO head setting for it any more: `unknown_columns` was REMOVED "
            "on 2026-10-05 and is refused — columns and values are always checked.")
