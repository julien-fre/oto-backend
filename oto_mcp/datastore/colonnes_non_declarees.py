"""Écrire dans une colonne NON DÉCLARÉE ne la créera plus (oto#124) — le préavis daté.

Une écriture qui nommait une colonne que le schéma ne déclare pas la créait à la volée :
le schéma d'un tableau s'étendait au fil des écritures, sans geste dédié. Arbitré le
07/09/2026 (« ça fait n'importe quoi dans les flottes d'agents »), précisé le 05/10 :
**une seule règle, plus de modes**. Une colonne inconnue est REFUSÉE, sur TOUS les
tableaux, quel que soit `unknown_columns` ; pour écrire dans une colonne nouvelle, on la
déclare d'abord (`data_patch_schema`). Un tableau SANS schéma voit ses colonnes FIGÉES à
la bascule : `scripts/figer_colonnes.py` lui déclare celles qu'il porte ce jour-là, puis
il suit la règle commune.

Rejouée sur 40 jours de journal, la garde aurait refusé 3 653 écritures (07/09), et un
refus répété pousse une ligne vers l'abandon : **avertir d'abord, refuser à une date
annoncée** — le mécanisme des autres bascules datées (`upsert_implicite`,
`mots_deprecies`, `vide_remplace`), pas un de plus.

- **Avant la date** : l'écriture passe, la colonne est créée, et la réponse porte dans
  `notices` UNE phrase par geste (union sur un lot) : les colonnes, la date, le geste.
  Ça vaut pour TOUS les réglages, `create` compris, tableau sans schéma compris.
- **À partir d'elle** : `ColonneNonDeclaree` — `400 unknown_column` côté REST et
  réception d'upload, `INVALID_PARAMS` côté MCP —, rien n'est écrit. Un lot (et un
  import découpé en tranches) est jugé ENTIER avant sa première ligne : le lot n'étant
  pas atomique, le juger ligne à ligne laisserait sa première moitié écrite.

Le prédicat est UN : `non_declarees`, sur ce que le geste POSE — jamais sur la ligne
fusionnée. Un patch qui touche une colonne déclarée d'une ligne portant des colonnes
anciennes non déclarées ne se juge pas sur elles (le cran juge le geste, pas le passé
qu'il hérite, #284). Effacer une colonne non déclarée (`null`) reste possible : elle
n'est plus posée.

⚠️ **Ce que ce module ne touche PAS** : `unknown_columns: "report"|"reject"` arme
encore, jusqu'à arbitrage, la validation complète du format (`options` de premier
niveau, structure des types, couches inconnues, fermeture des sous-records, gardes de
pose de `claimable` — `reglages.format_contraignant`). Le refus des colonnes inconnues
en est DÉCOUPLÉ : il ne l'allume pas sur un tableau `create`, il ne l'éteint pas sur un
tableau `report`/`reject`.

**Le refus tombe À LA DATE, dans le code** : rien n'est à déployer le jour J. Le réglage
`OTO_COLONNE_NON_DECLAREE_REFUSEE_LE` déplace la date sans déployer.
"""
from __future__ import annotations

import os
import re
from datetime import date as _date
from typing import Any, Iterable, Optional

from .couches import split_layer
from .declaration import _fields
from .errors import ColonneNonDeclaree

#: La date à partir de laquelle une colonne non déclarée est REFUSÉE. Une seule
#: constante : le texte servi en est DÉRIVÉ, pour que ce qu'on annonce soit ce qu'on
#: refusera. Le même jour que la bascule d'`upsert` (oto#141).
COLONNE_NON_DECLAREE_REFUSEE_LE = _date(2026, 10, 21)

#: Déplace la date sans déployer (`YYYY-MM-DD`). Une valeur illisible LÈVE, elle ne
#: retombe pas sur le défaut (cf. `champs_reserves.date_reglee`).
ENV_COLONNE_NON_DECLAREE_REFUSEE_LE = "OTO_COLONNE_NON_DECLAREE_REFUSEE_LE"

#: Ce qu'un refus ou un avertissement cite avant d'abréger.
_CITEES = 8
_REFERENTIEL_CITE = 15


def date_du_refus() -> _date:
    """La date en vigueur : le réglage s'il est posé, le défaut du code sinon."""
    from .champs_reserves import date_reglee

    return date_reglee(ENV_COLONNE_NON_DECLAREE_REFUSEE_LE,
                       os.environ.get(ENV_COLONNE_NON_DECLAREE_REFUSEE_LE),
                       COLONNE_NON_DECLAREE_REFUSEE_LE)


def _aujourdhui() -> _date:
    """Le jour qui juge le refus (UTC). Un point d'appui à part, pour que les bancs
    fixent le jour de CETTE bascule sans toucher à l'horloge ni aux autres."""
    from .champs_reserves import jour_utc

    return jour_utc()


def refus_arme(aujourdhui: Optional[_date] = None) -> bool:
    """Une colonne non déclarée est-elle refusée, AUJOURD'HUI (jour UTC) ?"""
    return (aujourdhui or _aujourdhui()) >= date_du_refus()


def _quand() -> str:
    from .champs_reserves import _en_francais

    return _en_francais(date_du_refus())


# ── le prédicat ──────────────────────────────────────────────────────────────

def declarees(schema: Optional[dict]) -> set:
    """Les colonnes de PREMIER niveau que le schéma déclare (vide sans schéma)."""
    return {f["key"] for f in _fields(schema)
            if isinstance(f.get("key"), str) and f["key"]}


def non_declarees(schema: Optional[dict], posees: Optional[dict]) -> list[str]:
    """Les colonnes que le geste POSE et que le schéma ne déclare pas, triées.

    Sur un tableau sans schéma — ou un schéma sans colonne —, TOUTE colonne posée l'est :
    c'est la règle commune, et ce qui la rend vivable est le gel
    (`scripts/figer_colonnes.py`). `<colonne déclarée>.<couche>` n'est pas une colonne
    (même règle que `hors_schema._unknown_subkeys`). Un `null` EFFACE, il ne crée rien :
    vider une colonne non déclarée reste possible — c'est le geste qui la retire."""
    if not isinstance(posees, dict):
        return []
    connues = declarees(schema)
    out = []
    for cle, valeur in posees.items():
        if not isinstance(cle, str) or cle in connues or valeur is None:
            continue
        base, couche = split_layer(cle)
        if couche and base in connues:
            continue
        out.append(cle)
    return sorted(out)


_BASE = re.compile(r"[.\[]")


def non_declarees_du_lot(schema: Optional[dict], rows: Iterable[Any]) -> list[str]:
    """Les colonnes non déclarées qu'un LOT poserait, jugées sur les clés BRUTES des
    lignes avant leur premier écrit — `{colonne: None}` exclus.

    Une clé pointée ou par rang (`site_web.comment`, `contacts[0].email`) vise la
    colonne de sa BASE : elle compte comme non déclarée si sa base l'est. Les clés
    techniques (`_id`, `_revision`…) ne sont pas des colonnes ; leurs refus propres
    viennent ensuite, ligne par ligne. Un `null` sur une colonne non déclarée l'EFFACE,
    il n'en crée pas, et un `{}` n'est pas une valeur (oto#165, écarté avant d'écrire) :
    ni l'un ni l'autre n'est compté (même règle que `non_declarees`)."""
    connues = declarees(schema)
    out: set = set()
    for data in rows:
        if not isinstance(data, dict):
            continue
        for cle, valeur in data.items():
            if not isinstance(cle, str) or cle.startswith("_") or cle in connues:
                continue
            base = _BASE.split(cle, 1)[0]
            if base in connues or valeur is None or valeur == {}:
                continue
            out.add(base or cle)
    return sorted(out)


# ── ce que la réponse en dit, et le refus ────────────────────────────────────

def _noms(colonnes: list[str]) -> str:
    cite = ", ".join(f"`{c}`" for c in colonnes[:_CITEES])
    reste = len(colonnes) - _CITEES
    return cite + (f" (+{reste} autres)" if reste > 0 else "")


def _geste(colonnes: list[str]) -> str:
    champs = ", ".join(f'{{"key": "{c}"}}' for c in colonnes[:3])
    return (f"déclare-la d'abord au schéma — `data_patch_schema(datastore=…, "
            f"fields=[{champs}])` (REST : `PATCH /api/datastores/{{datastore}}/schema`, "
            f"corps `{{\"fields\": [{champs}]}}`), avec son `type` si tu le connais —, "
            f"puis écris")


def avertissement(colonnes: list[str]) -> Optional[str]:
    """L'avertissement servi AVANT la date : les colonnes, la date, le geste."""
    if not colonnes:
        return None
    pluriel = len(colonnes) > 1
    return (f"{_noms(colonnes)} : colonne{'s' if pluriel else ''} non "
            f"déclarée{'s' if pluriel else ''} au schéma de ce tableau — "
            f"{'elles ont' if pluriel else 'elle a'} été "
            f"créée{'s' if pluriel else ''}. À partir du {_quand()}, écrire dans une "
            f"colonne non déclarée sera REFUSÉ (`unknown_column`), sur tous les "
            f"tableaux, au lieu de la créer. Pour écrire dans une colonne nouvelle, "
            f"{_geste(colonnes)}.")


def refus(schema: Optional[dict], colonnes: list[str], *,
          lot: bool = False) -> ColonneNonDeclaree:
    """Le refus À PARTIR de la date : les colonnes, le référentiel, le geste.

    ⚠️ Aucune destination n'est DEVINÉE (#678) : pointer la colonne « la plus proche »
    enverrait la valeur dans une colonne juste. Seule exception, qui ne devine rien
    (oto#63) : `<colonne déclarée>_<couche>` est une couche écrite avec un souligné au
    lieu d'un point — la destination se LIT dans la clé (`couche_mal_ecrite`)."""
    from .hors_schema import couche_mal_ecrite

    connues = [f["key"] for f in _fields(schema)
               if isinstance(f.get("key"), str) and f["key"]]
    details: dict = {"colonnes": list(colonnes)}
    couches = [(k, *c) for k in colonnes if (c := couche_mal_ecrite(k, set(connues)))]
    if couches:
        k, col, couche = couches[0]
        details["expected_column"] = f"{col}.{couche}"
        return ColonneNonDeclaree(
            [f"{_noms(colonnes)} : rien n'a été écrit. ⚠️ Ce n'est pas une colonne "
             f"inconnue, c'est une COUCHE dont le nom s'écrit avec un POINT — "
             + " ; ".join(f"`{a}` → `{b}.{c}`" for a, b, c in couches)
             + f". `{{\"{col}\": {{\"{couche}\": …}}}}` écrit la même chose, et c'est "
             f"la forme que `layers=\"nested\"` te rend à la lecture."],
            details=details)
    if connues:
        cite = ", ".join(f"`{k}`" for k in connues[:_REFERENTIEL_CITE])
        if len(connues) > _REFERENTIEL_CITE:
            cite += f" (+{len(connues) - _REFERENTIEL_CITE} autres, `data_get_schema`)"
        referentiel = f"Colonnes du tableau : {cite}."
    else:
        referentiel = "Ce tableau ne déclare encore aucune colonne."
    pluriel = len(colonnes) > 1
    portee = "ce lot est refusé ENTIER, " if lot else ""
    return ColonneNonDeclaree(
        [f"{_noms(colonnes)} : colonne{'s' if pluriel else ''} non "
         f"déclarée{'s' if pluriel else ''} — {portee}rien n'a été écrit. Depuis le "
         f"{_quand()}, écrire dans une colonne ne la crée plus, sur aucun tableau. "
         f"{referentiel} Écris sous un nom déclaré ; ou, si la colonne est voulue, "
         f"{_geste(colonnes)}. ⚠️ Ne réessaie pas sous une variante du même nom : "
         f"elle serait refusée pareil."],
        details=details)


def controler(schema: Optional[dict], posees: Optional[dict],
              releve: set) -> None:
    """Le geste pose-t-il une colonne non déclarée ? À partir de la date, REFUS ; avant,
    la colonne rejoint `releve` (l'avertissement se compose une fois par geste, union
    sur un lot — `avertissement`)."""
    colonnes = non_declarees(schema, posees)
    if not colonnes:
        return
    if refus_arme():
        raise refus(schema, colonnes)
    releve.update(colonnes)


def juger_le_lot(schema: Optional[dict], rows: Iterable[Any]) -> None:
    """À partir de la date, un lot qui poserait une colonne non déclarée est refusé
    ENTIER, avant sa première écriture. Sans effet avant la date : chaque ligne relève
    ses colonnes au passage (`controler`)."""
    if not refus_arme():
        return
    colonnes = non_declarees_du_lot(schema, rows)
    if colonnes:
        raise refus(schema, colonnes, lot=True)


# ── le texte servi ───────────────────────────────────────────────────────────

_GESTE_EN = ("To write a NEW column, declare it first — `data_patch_schema(datastore=…, "
             "fields=[{\"key\": \"<column>\", \"type\": …}])` — then write.")


def description_ecriture() -> str:
    """L'annonce servie dans la description de `data_write` et des faces REST, DÉRIVÉE
    de la date. Composée au démarrage : avant la date, le préavis — qui reste VRAI si
    le process franchit la date sans redémarrer ; démarré après, la règle au présent."""
    if refus_arme():
        return ("⚠️ **A write into a column the schema does NOT declare is REFUSED** "
                "(`unknown_column`), on every table and whatever `unknown_columns` says "
                f"— nothing is written, a batch is refused WHOLE. {_GESTE_EN}")
    return (f"⚠️ **From {date_du_refus().isoformat()} on, a write into a column the "
            "schema does NOT declare is REFUSED** (`unknown_column`) instead of "
            "creating it — on every table, tables without a schema included (their "
            "existing columns are declared for them before that date), whatever "
            "`unknown_columns` says; a batch is refused WHOLE. Until then the column "
            f"is still created and the response warns in `notices`. {_GESTE_EN}")


#: Composée une fois, au démarrage (cf. `description_ecriture`).
DESCRIPTION_ECRITURE = description_ecriture()


def description_schema() -> str:
    """Ce que `data_set_schema` et `data_patch_schema` disent de la règle et du réglage
    `unknown_columns` — qui ne décide plus du sort d'une colonne inconnue à partir de la
    date, et garde d'ici à l'arbitrage son autre effet (le format fait contrat)."""
    quand = ("" if refus_arme() else
             f" from {date_du_refus().isoformat()} on (until then it is still created, "
             "with a warning in `notices`)")
    return ("⚠️ A column the schema does NOT declare is REFUSED at write"
            f"{quand}, on EVERY table whatever `unknown_columns` says: declaring a "
            "column (`data_patch_schema(fields=[{\"key\": …}])`) is the ONLY way to "
            "add one. `unknown_columns` (`\"create\"` default | `\"report\"` | "
            "`\"reject\"`) still decides, until that date, whether an undeclared column "
            "is created silently, created and named back in `hors_schema`, or refused; "
            "and `\"report\"`/`\"reject\"` still make the declared format a CONTRACT "
            "(top-level `options` enforced, value structure and unknown layers judged, "
            "declared sub-records closed).")


def description_creation() -> str:
    """Ce que `data_create_datastore` et `POST /api/datastores` disent du paramètre
    `schema` : un tableau NAÎT sans colonne, donc, à partir de la date, sa première
    écriture est refusée tant qu'on ne lui en a pas déclaré. Le déclarer à la création
    est le geste normal pour un tableau qu'on va remplir — DÉRIVÉ de la date, comme les
    autres annonces de ce module."""
    if refus_arme():
        regle = ("⚠️ **A new table is born with NO column, and a write into a column the "
                 "schema does not declare is REFUSED** (`unknown_column`)")
    else:
        regle = (f"⚠️ **From {date_du_refus().isoformat()} on, a write into a column the "
                 "schema does not declare is REFUSED** (`unknown_column`) — and a new "
                 "table is born with NO column")
    return (f"{regle}: its first write is refused until columns are declared. To "
            "create a table you are going to FILL, pass `schema` — the SAME object as "
            "`data_set_schema` (`{\"fields\": [{\"key\": …, \"type\": …}], \"key\"?: …}`, "
            "same closed vocabulary, same validation): the table is born with it, in ONE "
            "atomic call. If the schema is refused, the table is NOT created. That is the "
            "normal way to create a table you fill; without `schema` it is born free, "
            "and `data_patch_schema(fields=[…])` declares columns later.")
