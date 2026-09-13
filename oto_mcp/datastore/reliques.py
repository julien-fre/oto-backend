"""Les clés LITTÉRALES pointées — ce qui reste d'avant la garde du 31/08.

Une couche vit imbriquée : `{"contact1_nom": {"link": …}}`. Mais il existe en base des
clés littérales pointées au premier niveau — `{"contact1_nom.link": …}` — écrites avant
que la garde ne les refuse. **Le robinet est fermé** (vérifié : aucune écriture ne peut
plus en créer) ; ce qui a été écrit avant est toujours là. Mesuré le 08/09/2026 : 764
occurrences, 11 clés, sur deux tableaux de production.

⚠️ **Le danger n'est pas leur présence, c'est que trois instruments les ignorent.**

- le FILTRE rendait 0 sur une donnée présente (corrigé) ;
- l'AVERTISSEMENT « colonne inconnue » les déclare inconnues alors qu'on vient de les
  lire ;
- et l'ÉCRITURE — le pire — **accepte un effacement, l'annonce comme un succès, et ne
  détruit rien** : `{"contact1_nom": {"link": "@empty"}}` vise l'imbriqué, la relique
  reste. Un lecteur croit avoir effacé une donnée personnelle.

**Un zéro se met en doute ; un succès ne se met pas en doute.** C'est ce qui rend le
troisième pire que les deux autres, et c'est pourquoi la garde ci-dessous refuse au
lieu d'avertir — mais UNIQUEMENT sur un effacement.

## Pourquoi refuser seulement l'effacement

Refuser toute écriture visant une couche dont une relique existe casserait le geste
normal : sur le tableau des cinq cents, 500 lignes portent `qualification.comment` en
relique, et leurs agents écrivent `{"qualification": {"comment": …}}` tous les jours.
Ce geste-là **fonctionne** — il écrit l'imbriqué, ce qui est le comportement voulu.

Ce qui ne fonctionne pas, c'est de croire DÉTRUIRE. Un effacement qui n'efface rien est
la seule forme où le silence coûte une donnée qu'on pensait retirée — et c'est
exactement le geste d'une attestation de destruction.
"""
from __future__ import annotations

from typing import Any, Optional

from . import couches as dsl

#: Le geste qui atteint réellement une relique — cité par le refus, parce qu'un refus
#: sans issue fait chercher une manœuvre (leçon de #668).
GESTE = "data_drop_column"


def _efface(valeur: Any) -> bool:
    """Ce sous-champ demande-t-il une DESTRUCTION ? — `null`, `@empty` ou `@clear`.

    oto#204 : `@clear` vide sans assumer, `@empty` en assumant — les deux détruisent ce
    qui est en place, donc les deux se refusent sur une relique qu'ils ne toucheraient pas."""
    return valeur is None or valeur in (dsl.VIDE_DELIBERE, dsl.EFFACEMENT)


def effacements_sur_relique(user_data: Optional[dict],
                            avant: Optional[dict]) -> list[str]:
    """Les chemins que cet appel croit EFFACER alors qu'une relique littérale du même
    nom existe sur la ligne — et qu'il ne touchera pas.

    Rend les noms littéraux (`contact1_nom.link`), ceux qu'il faut citer dans le refus
    et passer à `data_drop_column`. Vide quand il n'y a rien à craindre : le chemin
    nominal ne paie rien.
    """
    if not isinstance(avant, dict) or not avant:
        return []
    # ⚠️ **Une relique VIDE n'a rien à protéger** — et c'est le cas de l'immense
    # majorité d'entre elles. Mesuré sur la production le 09/09/2026 : sur les 764
    # clés littérales pointées du parc, **735 valent `None`** — 500
    # `qualification.comment` et 235 `retraitement.comment` sur un seul tableau. Ce
    # sont des coquilles, pas des données : rien ne survivrait à l'effacement, donc
    # rien ne justifie de le refuser.
    #
    # Sans ce filtre la garde refusait un geste qui MARCHE, sur 96 % de ce qu'elle
    # rencontre, avec un message qui affirme deux choses fausses dans ce cas : que la
    # couche imbriquée est vide, et que la donnée resterait lisible. Et elle envoyait
    # vers `data_drop_column`, qui frappe la colonne sur TOUTES les lignes du tableau
    # — 500 lignes de la campagne en cours pour effacer une couche sur une seule.
    #
    # Le geste qu'elle existe pour attraper reste attrapé : 29 reliques portent
    # réellement une donnée, et celles-là refusent toujours. C'est la même règle que
    # partout ailleurs dans le datastore — le vide n'est pas une valeur.
    pointees = {c for c, v in avant.items()
                if isinstance(c, str) and "." in c and not c.startswith("_")
                and not dsl.est_vide(v) and v != dsl.VIDE_DELIBERE}
    if not pointees:
        return []

    vises: list[str] = []
    for cle, valeur in (user_data or {}).items():
        if not isinstance(cle, str):
            continue
        # `{"contact1_nom.link": …}` — le nom littéral écrit tel quel.
        if cle in pointees and _efface(valeur):
            vises.append(cle)
            continue
        # `{"contact1_nom": {"link": …}}` — la forme imbriquée, qui vise à côté.
        if isinstance(valeur, dict):
            for sous, v in valeur.items():
                nom = f"{cle}.{sous}"
                if nom in pointees and _efface(v):
                    vises.append(nom)
    return sorted(set(vises))


def refus(noms: list[str]) -> str:
    """Le refus — il NOMME le geste qui aboutit, jamais seulement ce qui échoue.

    ⚠️ Il dit aussi que rien n'a été écrit : sans ça, l'appelant ne sait pas si son
    appel a fait la moitié du travail, et il rejouerait sur un état qu'il ne connaît
    pas."""
    liste = ", ".join(f"`{n}`" for n in noms[:5]) + (" …" if len(noms) > 5 else "")
    return (
        f"{liste} : cet effacement ne détruirait RIEN — rien n'a été écrit. Ces "
        f"clés sont des reliques stockées au premier niveau de la ligne (avant le "
        f"31/08/2026), pas des couches : ton écriture vise la couche imbriquée, et "
        f"la relique — qui porte une valeur — reste. Elle serait donc encore lisible "
        f"après un succès annoncé. Pour la détruire vraiment : "
        f"`{GESTE}(<tableau>, \"{noms[0]}\", confirm=True)` — avec le nom LITTÉRAL, "
        f"point compris. Le compte `rows` qu'il rend est le seul témoin valable ici : "
        f"il refuse un nom qu'aucune ligne ne porte.")
