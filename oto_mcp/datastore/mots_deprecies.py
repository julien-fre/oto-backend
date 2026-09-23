"""`@keep` et `@clear` sont dépréciés (oto#140) — l'avertissement daté qui l'annonce.

Le contrat d'écriture d'une case tient en deux gestes :

| ce que l'agent envoie | ce que ça veut dire |
|---|---|
| `null` | j'efface la case |
| `@empty` (et sa raison dans `comment`) | cherché, rien |
| l'omission | je n'y touche pas |

`@clear` doublait `null` et `@keep` doublait l'omission : deux mots de plus pour deux
gestes qui existaient déjà. Ils seront REFUSÉS à la date ci-dessous ; d'ici là,
une écriture qui les porte réussit et la réponse dit, datée, par quoi les remplacer.

⚠️ **Nommé, jamais traduit en silence au refus** : le refus, quand il tombera, dira le
geste de remplacement et l'agent le choisira — un `@keep` traduit d'office ne sait pas
si la valeur change autour de lui.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any, Optional

from . import couches as dsl

#: La date à partir de laquelle `@keep` et `@clear` sont REFUSÉS. Une seule constante :
#: le texte servi en est DÉRIVÉ, pour que ce qu'on annonce soit ce qu'on refusera.
MOTS_DEPRECIES_REFUSES_LE = _date(2026, 10, 8)

#: Le mot déprécié → le geste qui le remplace, tel qu'on le sert.
REMPLACEMENTS = {
    dsl.EFFACEMENT: "`null` (il efface la case)",
    dsl.GARDE: ("omets le sous-champ ; s'il doit survivre à une valeur qui change "
                "(un `comment`, un `link`), renvoie sa valeur telle quelle"),
}

#: L'annonce servie dans la description de `data_write`, DÉRIVÉE de la date.
DESCRIPTION_ECRITURE = (
    f"⚠️ `\"{dsl.GARDE}\"` and `\"{dsl.EFFACEMENT}\"` are DEPRECATED and REFUSED from "
    f"{MOTS_DEPRECIES_REFUSES_LE.isoformat()} on: until then a write carrying them "
    f"still works, and the response warns in `notices`. Instead of `{dsl.EFFACEMENT}`, "
    f"write `null`. Instead of `{dsl.GARDE}`, leave the sub-field out — or, for a "
    f"`comment` or `link` that must survive a value that changes, send it back as it is."
)


def _porte(valeur: Any, mot: str) -> bool:
    """Ce mot est-il écrit quelque part dans cette valeur — au mot ENTIER, en couche
    comme dans un élément de liste ?"""
    if isinstance(valeur, dict):
        return any(_porte(v, mot) for v in valeur.values())
    if isinstance(valeur, list):
        return any(_porte(v, mot) for v in valeur)
    return isinstance(valeur, str) and valeur == mot


def mots_nommes(user_data: Optional[dict]) -> dict[str, list[str]]:
    """Les colonnes de CET appel qui portent un mot déprécié, par mot.

    `{}` quand l'appel n'en porte aucun — le cas normal ne coûte rien de plus."""
    trouves: dict[str, list[str]] = {}
    for mot in REMPLACEMENTS:
        cols = sorted(str(c) for c, v in (user_data or {}).items() if _porte(v, mot))
        if cols:
            trouves[mot] = cols
    return trouves


def avertissement(trouves: dict[str, list[str]]) -> Optional[str]:
    """L'avertissement servi : le mot, les colonnes, la DATE du refus, le remplacement."""
    if not trouves:
        return None
    from .champs_reserves import _en_francais

    quand = _en_francais(MOTS_DEPRECIES_REFUSES_LE)
    phrases = []
    for mot, cols in trouves.items():
        cite = ", ".join(f"`{c}`" for c in cols[:5]) + (" …" if len(cols) > 5 else "")
        phrases.append(f"`{mot}` ({cite}) a fonctionné, mais il est déprécié et sera "
                       f"REFUSÉ à partir du {quand}. À la place : {REMPLACEMENTS[mot]}.")
    return " ".join(phrases) + (f" Pour dire « cherché, rien », écris "
                                f"`{dsl.VIDE_DELIBERE}`, la raison dans `comment`.")
