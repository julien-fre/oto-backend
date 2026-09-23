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

⚠️ **Nommé, jamais traduit en silence au refus** : le refus dit le geste de
remplacement et l'agent le choisit — un `@keep` traduit d'office ne sait pas si la
valeur change autour de lui.

**Le refus tombe À LA DATE, dans le code** (jalon J3) : rien n'est à déployer le jour J.
`controler` le juge à chaque écriture (jour UTC) — avant, l'avertissement ; à partir
d'elle, l'écriture ENTIÈRE est refusée, lot compris. Le réglage
`OTO_MOTS_DEPRECIES_REFUSES_LE` déplace la date sans déployer.
"""
from __future__ import annotations

import os
from datetime import date as _date
from typing import Any, Optional

from . import couches as dsl
from .errors import RowValidationError

#: La date à partir de laquelle `@keep` et `@clear` sont REFUSÉS. Une seule constante :
#: le texte servi en est DÉRIVÉ, pour que ce qu'on annonce soit ce qu'on refusera.
MOTS_DEPRECIES_REFUSES_LE = _date(2026, 10, 8)

#: Le mot déprécié → le geste qui le remplace, tel qu'on le sert.
REMPLACEMENTS = {
    dsl.EFFACEMENT: "`null` (il efface la case)",
    dsl.GARDE: ("omets le sous-champ ; s'il doit survivre à une valeur qui change "
                "(un `comment`, un `link`), renvoie sa valeur telle quelle"),
}

#: Déplace la date sans déployer (`YYYY-MM-DD`). Une valeur illisible LÈVE, elle ne
#: retombe pas sur le défaut (cf. `champs_reserves.date_reglee`).
ENV_MOTS_DEPRECIES_REFUSES_LE = "OTO_MOTS_DEPRECIES_REFUSES_LE"


def date_du_refus() -> _date:
    """La date en vigueur : le réglage s'il est posé, le défaut du code sinon."""
    from .champs_reserves import date_reglee

    return date_reglee(ENV_MOTS_DEPRECIES_REFUSES_LE,
                       os.environ.get(ENV_MOTS_DEPRECIES_REFUSES_LE),
                       MOTS_DEPRECIES_REFUSES_LE)


def _aujourdhui() -> _date:
    """Le jour qui juge le refus (UTC). Un point d'appui à part, pour que les bancs
    fixent le jour de CETTE bascule sans toucher à l'horloge ni aux autres."""
    from .champs_reserves import jour_utc

    return jour_utc()


def refus_arme(aujourdhui: Optional[_date] = None) -> bool:
    """Le refus est-il tombé, AUJOURD'HUI (jour UTC) ?"""
    return (aujourdhui or _aujourdhui()) >= date_du_refus()


_GESTES_EN = (f"Instead of `{dsl.EFFACEMENT}`, write `null`. Instead of `{dsl.GARDE}`, "
              "leave the sub-field out — or, for a `comment` or `link` that must survive "
              "a value that changes, send it back as it is.")


def description_ecriture() -> str:
    """L'annonce servie dans la description de `data_write`, DÉRIVÉE de la date.

    La description se compose au démarrage : avant la date, le préavis — qui reste
    VRAI si le process franchit la date sans redémarrer (« from … on » et « until
    then » décrivent chacun leur côté) ; démarré après, la règle au présent."""
    if refus_arme():
        return (f"⚠️ `\"{dsl.GARDE}\"` and `\"{dsl.EFFACEMENT}\"` are no longer "
                f"accepted: a write carrying them anywhere is REFUSED whole. {_GESTES_EN}")
    return (f"⚠️ `\"{dsl.GARDE}\"` and `\"{dsl.EFFACEMENT}\"` are DEPRECATED and "
            f"REFUSED from {date_du_refus().isoformat()} on: until then a write "
            f"carrying them still works, and the response warns in `notices`. "
            f"{_GESTES_EN}")


#: Composée une fois, au démarrage (cf. `description_ecriture`).
DESCRIPTION_ECRITURE = description_ecriture()


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


def _cite(cols: list[str]) -> str:
    return ", ".join(f"`{c}`" for c in cols[:5]) + (" …" if len(cols) > 5 else "")


def avertissement(trouves: dict[str, list[str]]) -> Optional[str]:
    """L'avertissement servi : le mot, les colonnes, la DATE du refus, le remplacement."""
    if not trouves:
        return None
    from .champs_reserves import _en_francais

    quand = _en_francais(date_du_refus())
    phrases = []
    for mot, cols in trouves.items():
        phrases.append(f"`{mot}` ({_cite(cols)}) a fonctionné, mais il est déprécié et sera "
                       f"REFUSÉ à partir du {quand}. À la place : {REMPLACEMENTS[mot]}.")
    return " ".join(phrases) + (f" Pour dire « cherché, rien », écris "
                                f"`{dsl.VIDE_DELIBERE}`, la raison dans `comment`.")


def refus(trouves: dict[str, list[str]]) -> str:
    """Le refus nommé : chaque mot, ses colonnes, et le geste qui le remplace."""
    from .champs_reserves import _en_francais

    phrases = [f"`{mot}` ({_cite(cols)}) : à la place, {REMPLACEMENTS[mot]}."
               for mot, cols in trouves.items()]
    return (f"écriture refusée : `{dsl.GARDE}` et `{dsl.EFFACEMENT}` ne sont plus "
            f"acceptés depuis le {_en_francais(date_du_refus())}, et rien n'a été "
            "écrit. " + " ".join(phrases) + f" Pour dire « cherché, rien », écris "
            f"`{dsl.VIDE_DELIBERE}`, la raison dans `comment`.")


def controler(notices: set, *corps: Optional[dict]) -> None:
    """Le geste de CET appel (une écriture, ou toutes les lignes d'un lot) porte-t-il un
    mot déprécié ? Avant la date : l'avertissement, dans `notices`. À partir d'elle :
    REFUS de l'écriture entière — jamais une traduction silencieuse.

    Appelé AVANT toute écriture : sur un lot, on juge toutes les lignes d'abord, pour
    qu'un refus ne laisse pas la moitié du lot écrite."""
    trouves: dict[str, list[str]] = {}
    for c in corps:
        for mot, cols in mots_nommes(c).items():
            trouves[mot] = sorted(set(trouves.get(mot, [])) | set(cols))
    if not trouves:
        return
    if refus_arme():
        raise RowValidationError([refus(trouves)])
    notices.add(avertissement(trouves))
