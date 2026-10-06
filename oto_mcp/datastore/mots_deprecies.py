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
    dsl.EFFACEMENT: "`null` (il efface la case)",
    dsl.GARDE: ("omets le sous-champ ; s'il doit survivre à une valeur qui change "
                "(un `comment`, un `link`), renvoie sa valeur telle quelle"),
}

#: La règle servie dans la description de `data_write`.
DESCRIPTION_ECRITURE = (
    f"⚠️ `\"{dsl.GARDE}\"` and `\"{dsl.EFFACEMENT}\"` are not accepted: a write "
    f"carrying them anywhere is REFUSED whole. Instead of `{dsl.EFFACEMENT}`, write "
    f"`null`. Instead of `{dsl.GARDE}`, leave the sub-field out — or, for a `comment` "
    "or `link` that must survive a value that changes, send it back as it is.")


def _porte(valeur: Any, mot: str) -> bool:
    """Ce mot est-il écrit quelque part dans cette valeur — au mot ENTIER, en couche
    comme dans un élément de liste ?"""
    if isinstance(valeur, dict):
        return any(_porte(v, mot) for v in valeur.values())
    if isinstance(valeur, list):
        return any(_porte(v, mot) for v in valeur)
    return isinstance(valeur, str) and valeur == mot


def mots_nommes(user_data: Optional[dict]) -> dict[str, list[str]]:
    """Les colonnes de CET appel qui portent un mot retiré, par mot.

    `{}` quand l'appel n'en porte aucun — le cas normal ne coûte rien de plus."""
    trouves: dict[str, list[str]] = {}
    for mot in REMPLACEMENTS:
        cols = sorted(str(c) for c, v in (user_data or {}).items() if _porte(v, mot))
        if cols:
            trouves[mot] = cols
    return trouves


def _cite(cols: list[str]) -> str:
    return ", ".join(f"`{c}`" for c in cols[:5]) + (" …" if len(cols) > 5 else "")


def refus(trouves: dict[str, list[str]]) -> str:
    """Le refus nommé : chaque mot, ses colonnes, et le geste qui le remplace."""
    phrases = [f"`{mot}` ({_cite(cols)}) : à la place, {REMPLACEMENTS[mot]}."
               for mot, cols in trouves.items()]
    return (f"écriture refusée : `{dsl.GARDE}` et `{dsl.EFFACEMENT}` ne sont pas "
            "acceptés, et rien n'a été écrit. " + " ".join(phrases)
            + f" Pour dire « cherché, rien », écris `{dsl.VIDE_DELIBERE}`, la raison "
            "dans `comment`.")


def controler(*corps: Optional[dict]) -> None:
    """Le geste de CET appel (une écriture, ou toutes les lignes d'un lot) porte-t-il un
    mot retiré ? → REFUS de l'écriture entière — jamais une traduction silencieuse.

    Appelé AVANT toute écriture : sur un lot, on juge toutes les lignes d'abord, pour
    qu'un refus ne laisse pas la moitié du lot écrite."""
    trouves: dict[str, list[str]] = {}
    for c in corps:
        for mot, cols in mots_nommes(c).items():
            trouves[mot] = sorted(set(trouves.get(mot, [])) | set(cols))
    if trouves:
        raise RowValidationError([refus(trouves)])
