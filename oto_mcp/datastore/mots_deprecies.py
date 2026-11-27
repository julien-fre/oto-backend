"""`@keep` et `@clear` sont retirés (oto#140) — le refus qui nomme le geste à la place.

Le contrat d'écriture d'une case tient en deux gestes :

| ce que l'agent envoie | ce que ça veut dire |
|---|---|
| `null` | j'efface la case |
| `@empty` (et sa raison dans `comment`) | cherché, rien |
| l'omission | je n'y touche pas |

`@clear` doublait `null` et `@keep` doublait l'omission : deux mots de plus pour deux
gestes qui existaient déjà. Une écriture qui les porte, où que ce soit, est REFUSÉE
entière, lot compris.

⚠️ **Refusé, jamais stocké ni traduit en silence** : sans ce refus, `"@keep"` serait
écrit comme une valeur littérale. Et le refus dit le geste de remplacement, que l'agent
choisit — un `@keep` traduit d'office ne sait pas si la valeur change autour de lui.
"""
from __future__ import annotations

from typing import Any, Optional

from . import couches as dsl
from .errors import RowValidationError

#: Le mot retiré → le geste qui le remplace, tel qu'on le sert.
REMPLACEMENTS = {
    dsl.EFFACEMENT: "`null` (it clears the cell)",
    dsl.GARDE: ("leave the sub-field out; if it must survive a value that changes "
                "(a `comment`, a `link`), send its value back as it is"),
}

#: La règle servie dans la description de `data_write`.
DESCRIPTION_ECRITURE = (
    f"⚠️ `\"{dsl.GARDE}\"` and `\"{dsl.EFFACEMENT}\"` are not accepted: a write "
    f"carrying them anywhere is REFUSED whole. Instead of `{dsl.EFFACEMENT}`, write "
    f"`null`. Instead of `{dsl.GARDE}`, leave the sub-field out — or, for a `comment` "
    "or `link` that must survive a value that changes, send it back as it is.")


def _cases(valeur: Any, chemin: str, mot: str) -> list[str]:
    """Les CASES de cette valeur qui portent ce mot, au mot ENTIER : la colonne nue
    (`site`), une couche (`site.valeur`, `site.comment`), un élément de liste
    (`contacts[0].fonction`)."""
    if isinstance(valeur, dict):
        return [c for k, v in valeur.items() for c in _cases(v, f"{chemin}.{k}", mot)]
    if isinstance(valeur, list):
        return [c for i, v in enumerate(valeur) for c in _cases(v, f"{chemin}[{i}]", mot)]
    return [chemin] if isinstance(valeur, str) and valeur == mot else []


def _porte(valeur: Any, mot: str) -> bool:
    """Ce mot est-il écrit quelque part dans cette valeur — au mot ENTIER, en couche
    comme dans un élément de liste ?"""
    return bool(_cases(valeur, "", mot))


def mots_nommes(user_data: Optional[dict]) -> dict[str, list[str]]:
    """Les colonnes de CET appel qui portent un mot retiré, par mot.

    `{}` quand l'appel n'en porte aucun — le cas normal ne coûte rien de plus."""
    trouves: dict[str, list[str]] = {}
    for mot in REMPLACEMENTS:
        cols = sorted(str(c) for c, v in (user_data or {}).items() if _porte(v, mot))
        if cols:
            trouves[mot] = cols
    return trouves


def cases_nommees(user_data: Optional[dict]) -> dict[str, list[str]]:
    """Comme `mots_nommes`, mais à la CASE près : la couche ou l'élément qui porte le
    mot (signal 1481). « `@keep` (`statut_connexion`) » ne disait pas que le mot était
    dans `valeur` — précisément la couche qu'il suffit d'omettre pour réécrire les
    seules `comment`/`link` d'une case."""
    trouves: dict[str, list[str]] = {}
    for mot in REMPLACEMENTS:
        cases = sorted(c for col, v in (user_data or {}).items()
                       for c in _cases(v, str(col), mot))
        if cases:
            trouves[mot] = cases
    return trouves


def _cite(cols: list[str]) -> str:
    return ", ".join(f"`{c}`" for c in cols[:5]) + (" …" if len(cols) > 5 else "")


#: Le geste exact quand `@keep` est posé sur la VALEUR d'une case à couches : c'est le
#: cas du signal 1481 (un lot qui ne voulait réécrire que `comment`/`link`).
_GARDE_SUR_LA_VALEUR = (
    "To rewrite only a cell's `comment`/`link` and keep its value, send those layers "
    "WITHOUT `valeur` (`{\"comment\": …, \"link\": …}`): the stored value stays.")


#: Les lignes d'un lot citées dans `details` : assez pour les retrouver, jamais un lot
#: entier recopié dans le refus.
_LIGNES_CITEES = 20


class _Releve:
    """Ce que le refus cite, par mot : colonnes, cases, et lignes du lot."""

    def __init__(self) -> None:
        self.cols: dict[str, set] = {}
        self.cases: dict[str, set] = {}
        self.lignes: dict[str, list[str]] = {}

    def noter(self, corps: Optional[dict], ligne: Optional[str] = None) -> None:
        for mot, cols in mots_nommes(corps).items():
            self.cols.setdefault(mot, set()).update(cols)
        for mot, cases in cases_nommees(corps).items():
            self.cases.setdefault(mot, set()).update(cases)
            if ligne is not None:
                self.lignes.setdefault(mot, []).append(ligne)

    def details(self) -> dict:
        return {"code": "retired_word", "words": [
            {"word": mot, "columns": sorted(self.cols[mot]),
             "cells": sorted(self.cases.get(mot, ())),
             **({"rows": self.lignes[mot][:_LIGNES_CITEES],
                 "row_count": len(self.lignes[mot])} if mot in self.lignes else {})}
            for mot in self.cols]}


def _phrase(mot: str, cols: list[str], cases: list[str], lignes: list[str],
            total: Optional[int]) -> str:
    ou = f" at {_cite(cases)}" if cases and cases != cols else ""
    sur = ""
    if lignes and total and total > 1:
        sur = (f", on {len(lignes)} of the {total} rows of the batch: "
               + ", ".join(lignes[:5]) + (" …" if len(lignes) > 5 else ""))
    return f"`{mot}` ({_cite(cols)}){ou}{sur}: instead, {REMPLACEMENTS[mot]}."


def refus(trouves: dict[str, list[str]], cases: Optional[dict[str, list[str]]] = None,
          lignes: Optional[dict[str, list[str]]] = None,
          total: Optional[int] = None) -> str:
    """Le refus nommé : chaque mot, ses colonnes, la case exacte (couche, élément), les
    lignes d'un lot qui le portent, et le geste qui le remplace."""
    cases = cases or {}
    lignes = lignes or {}
    phrases = []
    for mot, cols in trouves.items():
        phrases.append(_phrase(mot, cols, cases.get(mot, []), lignes.get(mot, []), total))
        if mot == dsl.GARDE and any(c.endswith(f".{dsl.VALUE_LAYER}")
                                    for c in cases.get(mot, [])):
            phrases.append(_GARDE_SUR_LA_VALEUR)
    return (f"write refused: `{dsl.GARDE}` and `{dsl.EFFACEMENT}` are not "
            "accepted, and nothing was written. " + " ".join(phrases)
            + f" To say \"searched, nothing\", write `{dsl.VIDE_DELIBERE}`, with the reason "
            "in `comment`.")


def _designe(rang: int, ligne: dict, key: Optional[str]) -> str:
    """`row 3 (linkedin_url=…)` — le rang dans le geste, et la clé quand le lot en a une."""
    kv = dsl.unwrap(ligne.get(key)) if key else None
    return f"row {rang}" + (f" ({key}={kv})" if kv is not None and kv != "" else "")


def controler(*corps: Optional[dict], lot: bool = False, key: Optional[str] = None,
              premier_rang: int = 1) -> None:
    """Le geste de CET appel (une écriture, ou toutes les lignes d'un lot) porte-t-il un
    mot retiré ? → REFUS de l'écriture entière — jamais une traduction silencieuse.

    Appelé AVANT toute écriture : sur un lot, on juge toutes les lignes d'abord, pour
    qu'un refus ne laisse pas la moitié du lot écrite.

    `lot=True` : chaque corps est une LIGNE du lot (rang `premier_rang` + sa position,
    `key` = la clé qui la désigne) — le refus nomme les lignes fautives. Sinon, les
    corps sont des formes d'UNE même écriture (brute et rangée)."""
    releve = _Releve()
    for i, c in enumerate(corps):
        if not isinstance(c, dict):
            continue
        releve.noter(c, _designe(premier_rang + i, c, key) if lot else None)
    if releve.cols:
        trouves = {m: sorted(cols) for m, cols in releve.cols.items()}
        cases = {m: sorted(v) for m, v in releve.cases.items()}
        raise RowValidationError(
            [refus(trouves, cases, releve.lignes, len(corps) if lot else None)],
            details=releve.details())
