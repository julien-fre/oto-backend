"""`""` et `[]` REMPLACERONT la valeur en place (oto#140, jalon J2) — le préavis daté.

Le contrat d'écriture d'une case (arbitré le 23/09/2026) : `null` efface, `@empty` dit
« cherché, rien », l'omission ne touche à rien — et `""` et `[]` sont des VALEURS, qui
remplacent ce qui est en place comme n'importe quelle autre.

Aujourd'hui, un vide non-`null` sur une valeur en place est écarté (#608) et, quand il
est tout le geste, refusé comme « écriture sans effet » (#724). La bascule sera livrée à
la date ci-dessous ; d'ici là rien ne change à ce que fait l'écriture, et la réponse dit,
datée, ce qui va changer et comment garder la valeur.

`{}` n'est pas concerné : ce n'est pas une valeur, il n'est jamais stocké (oto#165).

**La bascule se fait À LA DATE, dans le code** : rien n'est à déployer le jour J.
`bascule_faite()` la juge à chaque écriture (jour UTC) ; avant, le préavis et les gardes
#608/#724 restent tels quels ; à partir d'elle, `arbitrer_les_vides` pose `""` et `[]`
comme n'importe quelle valeur, et la valeur remplacée revient dans `valeurs_effacees`.
Le réglage `OTO_VIDE_REMPLACE_LE` déplace la date sans déployer.
"""
from __future__ import annotations

import os
from datetime import date as _date
from typing import Iterable, Optional

from . import couches as dsl

#: La date à partir de laquelle `""` et `[]` REMPLACENT la valeur en place. Une seule
#: constante : le texte servi en est DÉRIVÉ, pour que ce qu'on annonce soit ce qu'on livrera.
VIDE_REMPLACE_LE = _date(2026, 10, 6)

#: Les vides qui deviendront des valeurs — `{}` n'en est pas un.
_VIDES_VALEURS = ("", [])

#: Déplace la date sans déployer (`YYYY-MM-DD`). Une valeur illisible LÈVE, elle ne
#: retombe pas sur le défaut (cf. `champs_reserves.date_reglee`).
ENV_VIDE_REMPLACE_LE = "OTO_VIDE_REMPLACE_LE"


def date_de_bascule() -> _date:
    """La date en vigueur : le réglage s'il est posé, le défaut du code sinon."""
    from .champs_reserves import date_reglee

    return date_reglee(ENV_VIDE_REMPLACE_LE, os.environ.get(ENV_VIDE_REMPLACE_LE),
                       VIDE_REMPLACE_LE)


def _aujourdhui() -> _date:
    """Le jour qui juge la bascule (UTC). Un point d'appui à part, pour que les bancs
    fixent le jour de CETTE bascule sans toucher à l'horloge ni aux autres."""
    from .champs_reserves import jour_utc

    return jour_utc()


def bascule_faite(aujourdhui: Optional[_date] = None) -> bool:
    """`""` et `[]` remplacent-ils la valeur en place, AUJOURD'HUI (jour UTC) ?"""
    return (aujourdhui or _aujourdhui()) >= date_de_bascule()


def description_ecriture() -> str:
    """L'annonce servie dans la description de `data_write`, DÉRIVÉE de la date.

    La description se compose au démarrage : avant la date, le préavis — qui reste
    VRAI si le process franchit la date sans redémarrer (« from … on » et « until
    then » décrivent chacun leur côté) ; démarré après, la règle au présent."""
    if bascule_faite():
        return ("`\"\"` and `[]` are values: they REPLACE the value in place, like any "
                "other value, and the replaced value comes back in `valeurs_effacees`. "
                "To keep a value, leave the field out; to erase it, write `null`.")
    return (f"⚠️ From {date_de_bascule().isoformat()} on, `\"\"` and `[]` REPLACE the "
            "value in place, like any other value. Until then they are ignored on a cell "
            "that holds a value (an empty that is the whole write is refused as a "
            "no-op), and the response warns in `notices`. To keep a value, leave the "
            "field out; to erase it, write `null`.")


#: Composée une fois, au démarrage (cf. `description_ecriture`).
DESCRIPTION_ECRITURE = description_ecriture()


def vides_ecartables() -> str:
    """Les vides non-`null` que l'arbitrage écarte ENCORE sur une valeur en place (#608)
    — nommés dans le relevé `valeurs_ignorees` et dans le refus #724. Après la
    bascule, il ne reste que `{}`, qui n'est pas une valeur (oto#165)."""
    if bascule_faite():
        return "objet vide `{}`, qui n'est pas une valeur"
    return "chaîne vide, liste vide, objet vide"


def vide_valeur(valeur) -> bool:
    """`""` ou `[]`, au TYPE près — `0` et `False` ne sont pas des vides."""
    return any(type(valeur) is type(v) and valeur == v for v in _VIDES_VALEURS)


def colonnes_annoncees(user_data: Optional[dict], ecartes: Iterable[dict]) -> list[str]:
    """Les colonnes ÉCARTÉES par l'arbitrage (#608) dont la bascule changera le sort :
    celles qui portaient `""` ou `[]`, nus ou en couches (`{"valeur": ""}`)."""
    champs = {str(r.get("champ")) for r in ecartes}
    return sorted(c for c in champs
                  if vide_valeur(dsl.unwrap((user_data or {}).get(c))))


def avertissement(colonnes: list[str]) -> Optional[str]:
    """L'avertissement servi : la DATE, les colonnes, le geste qui garde la valeur."""
    if not colonnes:
        return None
    from .champs_reserves import _en_francais

    cite = ", ".join(f"`{c}`" for c in colonnes[:5]) + (" …" if len(colonnes) > 5 else "")
    return (f"À partir du {_en_francais(date_de_bascule())}, `\"\"` / `[]` REMPLACERA la "
            f"valeur en place de {cite} ; pour la garder, omets la colonne. Aujourd'hui "
            "ce vide est encore ignoré et la valeur reste ; pour l'effacer, écris `null`.")


def annonce(user_data: Optional[dict], ecartes: Iterable[dict]) -> Optional[str]:
    """L'avertissement de CET appel, ou `None` — le cas normal ne coûte rien de plus."""
    ecartes = list(ecartes)
    if not ecartes:
        return None
    return avertissement(colonnes_annoncees(user_data, ecartes))
