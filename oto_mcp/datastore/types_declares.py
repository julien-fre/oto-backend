"""Le `type` déclaré s'applique parce qu'il est déclaré — plus besoin d'opter.

**Le contrôle existait déjà et il est bon** : `_type_error` sait juger `number`, `bool`,
`date`, `datetime`, `email`. Ce qui manquait n'était pas la règle, c'était son
ARMEMENT — elle ne tournait que sous `validation_active`, c'est-à-dire sur les tableaux
ayant posé `strict` ou une borne. Le reste du parc déclarait des types que personne ne
lisait.

**Mesuré sur le parc entier le 08/09/2026, et le résultat ne laisse pas de doute :**

    88 tableaux avec validation active    →   0 violation
   248 tableaux sans validation active    → 118 violations

Zéro contre cent dix-huit. La règle est juste partout où elle tourne ; là où elle ne
tourne pas, la donnée dérive. Ce n'est pas un contrôle à écrire, c'est un contrôle à
brancher.

**Pourquoi hors de `validation_active`, comme `required_layers` et le cycle de vie.**
Le docstring de `validation_active` porte déjà l'argument, à propos de `max_length` :
*« déclarer une borne EST la demande de la faire respecter »*. Un `type` déclaré est de
la même nature — personne n'écrit `"type": "number"` pour décorer. La colonne qui le
porte demande qu'on le respecte.

⚠️ **Ce que ça refusera, et ce n'est pas ce qu'on croit.** Sur les 118, cent douze
n'ont même pas d'arobase dans une colonne `email` : ce sont des PHRASES écrites dans la
case valeur — `"non trouvé"`, `"dropcontact: not found; lusha: not found"`,
`"e.gonot@neovia-tp.fr — ATTENTION : déduit du pattern"`. Des agents qui écrivent
l'absence comme une donnée, ou qui collent la valeur et son explication faute de se
servir de la couche `comment`. Le refus ne détruit donc pas des données légitimes : il
attrape des cases qui mélangent la donnée et ce qu'on en sait. **D'où la forme du
message : il ne dit pas seulement « mauvais type », il dit où mettre quoi.**

⚠️ **`text` et `url` sont VOLONTAIREMENT absents.** `text` refuserait un nombre écrit
sans guillemets dans une colonne libre, pour rien. Et pour `url`, la règle disponible
exige `http://` — elle refuserait `www.editions-x.fr`, qu'aucune décision n'a déclaré
fautif. Une garde qui refuse ce qu'on n'a pas jugé fautif ne protège personne : elle
apprend à être contournée.
"""
from __future__ import annotations

from typing import Any, Optional

from . import couches as dsl
from .couches import unwrap
from . import charge_a_renvoyer as car
from .declaration import _fields
from .phrases_de_refus import gabarit

#: Les types dont la déclaration ARME le contrôle. Chacun a été mesuré sur le parc
#: avant d'entrer ici — on ne branche pas une règle sans savoir ce qu'elle refusera.
#: `phone` (oto#103, 24/09/2026) entre armé d'emblée : type neuf, aucune colonne du parc
#: ne le porte encore, donc rien d'existant à refuser.
TYPES_ARMES = ("number", "bool", "date", "datetime", "email", "phone")

#: Ce qu'il faut faire, par type — la DESTINATION du refus, pas seulement sa cause.
_OU_METTRE_QUOI = {
    "email": ("écris l'adresse seule dans la valeur ; ce que tu en sais — la source, "
              "le doute — va dans `<colonne>.comment`"),
    "phone": ("écris le numéro seul, de préférence au format international (`+`, "
              "indicatif, puis le numéro — espaces et tirets acceptés) ; le poste, le "
              "contexte ou la source vont dans `<colonne>.comment`"),
    "number": ("écris le nombre seul ; l'unité, l'exercice ou la tranche vont dans "
               "`<colonne>.comment`"),
    "date": "écris la date au format `AAAA-MM-JJ` ; la précision va dans `.comment`",
    "datetime": "écris l'horodatage au format `AAAA-MM-JJ` (ou ISO complet)",
    "bool": "écris `true` ou `false`, pas leur texte",
}


def _rien_trouve(cle: str) -> str:
    """La clause commune à tout refus de type : « cherché, rien » a son mot (oto#140).

    Cent douze des cent dix-huit violations mesurées étaient des PHRASES d'absence
    (`"non trouvé"`) : c'est le cas que le refus doit aiguiller, quel que soit le type.
    Il disait « omets la colonne » — or omettre veut dire « je n'y touche pas » : la
    recherche vaine ne laissait aucune trace, et une valeur en place survivait à la
    recherche qui l'avait démentie. `@empty` dit l'absence, sa raison va dans `comment`."""
    return (f"si rien n'a été trouvé, écris `{dsl.VIDE_DELIBERE}` avec la raison dans "
            f"`{cle}.comment` ; pour ne pas toucher à la case, omets la colonne")


def _juge(valeur: Any, ftype: str) -> bool:
    """Vrai si la valeur trahit son type déclaré. Volontairement identique aux règles
    de `_type_error` : deux jugements du même attribut qui divergeraient seraient pires
    qu'un seul un peu large."""
    from .validation import _type_error
    return bool(_type_error(valeur, ftype, "x", None, None, None))


def types_trahis(schema: Optional[dict], merged: dict, *,
                 written: Optional[set] = None,
                 gelees: Optional[list] = None,
                 charge: Optional[dict] = None) -> list[str]:
    """Les colonnes dont la valeur ne tient pas le type que leur schéma déclare.

    Rend des phrases prêtes à servir : la faute, puis le geste. La valeur est DÉBALLÉE
    avant jugement (`unwrap`) — une cellule vaut `{"valeur": …, "comment": …}` en base,
    et juger la structure au lieu de son contenu est le défaut qui a arrêté une
    campagne entière le 29/08 sur la colonne d'état.

    `written` restreint le REFUS aux colonnes que CE geste écrit — même partage
    `posé / gelé` que `etats_trahis`, pour la même raison (07-08/09/2026, signal
    oto-backend#923) : une valeur déjà en base, hors type AVANT ce geste, ne doit
    pas geler un patch qui ne la touche pas — elle serait sinon refusée pour un
    défaut qu'elle n'a pas causé, avec un message qui prétend à tort qu'elle
    l'a envoyée. `written=None` (insert/remplacement, toute la row est écrite)
    garde l'ancien comportement : tout est jugé.

    `charge` = la charge à renvoyer (oto#135) : la colonne refusée y reçoit son
    gabarit (`charge_a_renvoyer`).
    """
    if not isinstance(merged, dict):
        return []
    out: list[str] = []
    for f in _fields(schema):
        cle, ftype = f.get("key"), f.get("type")
        if not isinstance(cle, str) or ftype not in TYPES_ARMES:
            continue
        if cle not in merged:
            continue
        valeur = unwrap(merged.get(cle))
        if valeur in (None, "", [], {}):
            continue
        if not _juge(valeur, ftype):
            continue
        quoi = " ; ".join(q for q in (_OU_METTRE_QUOI.get(ftype, ""), _rien_trouve(cle)) if q)
        refus = f"`{cle}` est déclarée `{ftype}` et reçoit {valeur!r} — refusé. Pour écrire : {quoi}"
        if written is None or cle in written:
            out.append(refus)
            car.noter(charge, car.champ(car.RACINE, cle), gabarit(f))
        elif gelees is not None:
            gelees.append({"champ": str(cle), "refus": refus})
    return out
