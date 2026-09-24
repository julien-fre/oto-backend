"""La CHARGE À RENVOYER d'un refus de validation (oto#135, J4 d'oto#140).

Un refus qui nomme la faute sans donner la forme fait rejouer l'appel à l'identique :
mesuré sur une soirée de campagne, un agent qui ne sait pas quoi changer rejoue ou
abandonne la ligne. Un refus qui porte la charge exacte est suivi dans la minute. Ce
module calcule cette charge, **depuis la déclaration**, jamais depuis la donnée :

- un fragment de `row` (`details["a_renvoyer"]`), qui ne porte QUE ce qu'il faut
  corriger, le champ marqué par un gabarit (`"<texte>"`, `"<nombre>"`…), avec
  `@empty` en alternative là où ce geste est permis ;
- dans une liste, l'élément en cause SEUL — désigné par son identité `of.key` quand
  elle existe, sinon par son rang (`details["a_renvoyer_elements"]`). Jamais la
  liste entière : ce serait recopier des données que l'agent a déjà ;
- la clause que la face MCP ajoute en fin de message (`clause`) ; la face REST rend
  `details` tel quel.

Le validateur (`validation.py`, `couches_exigees.py`) note au fil de sa descente un
CHEMIN structuré — jamais une chaîne à reparser — et une feuille ; `rendre` assemble.

Ce qu'il ne tient pas : la décision de refuser (`validation.py`), la prose du refus
(`phrases_de_refus.py`), dont le gabarit est la forme courte.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .couches import VIDE_DELIBERE

#: Un chemin est un tuple de segments : `("k", clé)` descend dans un objet (ligne,
#: sous-record, cellule en couches), `("e", rang, identité)` dans l'élément `rang`
#: d'une liste — `identité` = `(clé of.key, valeur)` ou `None`.
RACINE: tuple = ()


def champ(chemin: tuple, cle: str) -> tuple:
    return chemin + (("k", cle),)


def element(chemin: tuple, rang: int, identite: Optional[tuple]) -> tuple:
    return chemin + (("e", rang, identite),)


def vide_permis(chemin: tuple, cle: str, cle_d_identite: Optional[str] = None) -> bool:
    """`@empty` peut-il remplir ce champ ? Oui sur une colonne et sur l'attribut d'un
    élément de liste ; non sur l'identité d'un élément (`of.key`) ni dans un objet —
    là, il est refusé (cf. le guide `datastore-semantics`, § 4 quinquies)."""
    if not chemin:
        return True
    return chemin[-1][0] == "e" and cle != cle_d_identite


def avec_vide(gabarit: Any) -> Any:
    """Le gabarit, `@empty` en alternative — sur une feuille texte seulement."""
    return f"{gabarit} | {VIDE_DELIBERE}" if isinstance(gabarit, str) else gabarit


def noter(charge: Optional[dict], chemin: tuple, feuille: Any) -> None:
    """Retient la feuille à ce chemin. `charge` absente = personne ne la lira (un
    élément non écrit, une colonne gelée) : on ne note rien. La PREMIÈRE feuille d'un
    chemin gagne, comme `expected_column`."""
    if charge is not None and chemin:
        charge.setdefault(chemin, feuille)


class _Liste(dict):
    """Une liste en construction : `{rang: élément}`."""


def _poser(noeud: dict, chemin: tuple, feuille: Any) -> None:
    seg, reste = chemin[0], chemin[1:]
    if seg[0] == "e":
        rang, identite = seg[1], seg[2]
        if not reste:
            noeud.setdefault(rang, feuille)
            return
        elt = noeud.get(rang)
        if not isinstance(elt, dict):
            elt = noeud[rang] = dict([identite]) if identite else {}
        _poser(elt, reste, feuille)
        return
    cle = seg[1]
    if not reste:
        deja = noeud.get(cle)
        if isinstance(deja, dict) and not isinstance(deja, _Liste) and isinstance(feuille, dict):
            for k, v in feuille.items():
                deja.setdefault(k, v)
        else:
            noeud.setdefault(cle, feuille)
        return
    sorte = _Liste if reste[0][0] == "e" else dict
    sous = noeud.get(cle)
    if type(sous) is not sorte:
        sous = noeud[cle] = sorte()
    _poser(sous, reste, feuille)


def _finir(valeur: Any, chemin: str, elements: list, identites: dict) -> Any:
    if isinstance(valeur, _Liste):
        out = []
        for rang in sorted(valeur):
            ici = f"{chemin}[{rang}]"
            identite = identites.get((chemin, rang))
            elements.append(f"{chemin}[{identite[0]}={identite[1]}]" if identite else ici)
            out.append(_finir(valeur[rang], ici, elements, identites))
        return out
    if isinstance(valeur, dict):
        return {k: _finir(v, f"{chemin}.{k}" if chemin else k, elements, identites)
                for k, v in valeur.items()}
    return valeur


def rendre(charge: dict) -> dict:
    """`{"a_renvoyer": fragment, "a_renvoyer_elements": [désignations]}` — la seconde
    clé seulement quand le fragment touche une liste. `{}` sans charge."""
    if not charge:
        return {}
    racine: dict = {}
    identites: dict = {}
    for chemin, feuille in charge.items():
        _poser(racine, chemin, feuille)
        texte = ""
        for seg in chemin:
            if seg[0] == "e":
                if seg[2]:
                    identites[(texte, seg[1])] = seg[2]
                texte = f"{texte}[{seg[1]}]"
            else:
                texte = f"{texte}.{seg[1]}" if texte else seg[1]
    elements: list = []
    out: dict = {"a_renvoyer": _finir(racine, "", elements, identites)}
    if elements:
        out["a_renvoyer_elements"] = list(dict.fromkeys(elements))
    return out


def clause(details: Optional[dict]) -> str:
    """Ce que la face MCP ajoute en fin de message : elle n'a pas d'enveloppe
    structurée, le message doit porter la charge seul."""
    charge = (details or {}).get("a_renvoyer")
    if not charge:
        return ""
    texte = (" — À renvoyer, les gabarits `<…>` remplacés : "
             + json.dumps(charge, ensure_ascii=False))
    elements = (details or {}).get("a_renvoyer_elements")
    if elements:
        texte += (f" (élément visé : {', '.join(elements)}). Une liste se renvoie "
                  "ENTIÈRE : cet élément corrigé à sa place, les autres tels quels — "
                  "un élément omis est retiré")
    return texte + "."
