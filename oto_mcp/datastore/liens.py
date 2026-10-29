"""Le type `ref` : une cellule de LIEN porte l'`_id` d'une ligne d'un autre tableau.

Un front peint déjà des colonnes de lien (des puces qui ouvrent la ligne cible, et de
l'autre côté un cumul des lignes qui pointent vers elle). Sans type côté serveur, la
colonne n'était qu'un `text` : mesuré le 27/10/2026, une écriture qui y pose le NOM de
la ligne cible (« Acme ») au lieu de son `_id` passe sans un mot — l'écran montre une
puce morte, et un agent n'a aucun moyen d'apprendre qu'il s'est trompé.

`ref` se déclare avec sa CIBLE — `{"type": "ref", "target": <numéro du tableau>}`, ou
une liste de liens `{"type": "list", "of": {"type": "ref", "target": <numéro>}}` — la
forme même que le front lit. Ce module dit trois choses, et seulement elles :

- `est_un_id_de_ligne` — la FORME d'un `_id` (`outils._new_id`) ; c'est ce que juge
  l'écriture, armée par la déclaration comme `email` et `phone`
  (`types_declares.TYPES_ARMES`) ;
- `cible_valide` — ce qu'est une `target` bien formée, pour la pose ;
- `ou_trouver` — la DESTINATION du refus : où lire l'`_id` qu'il fallait écrire.

⚠️ **L'écriture juge la forme, pas l'existence.** Le validateur est pur (il ne lit pas
la base) et la règle qui manquait est celle d'un NOM écrit à la place d'un `_id` — la
forme suffit à la tenir. Vérifier que l'`_id` existe dans la cible ajouterait une
lecture à chaque écriture et une question de droits (celui qui écrit ne lit pas
forcément la cible : lui répondre « n'existe pas » lui apprendrait ce qu'il ne voit
pas), pour une garantie que la suppression d'une ligne cible défait de toute façon.
La pose, elle, vérifie que la cible existe et que l'auteur la lit
(`schema_ops._poser_schema`).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

#: La forme d'un `_id` de ligne : un UUID en minuscules (`outils._new_id`, uuid7-like).
_ID_DE_LIGNE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def est_un_id_de_ligne(valeur: Any) -> bool:
    """Vrai si la valeur a la forme d'un `_id` de ligne — jamais un nom, un domaine ou
    un numéro de tableau."""
    return isinstance(valeur, str) and bool(_ID_DE_LIGNE.match(valeur.strip()))


def cible_valide(target: Any) -> Optional[int]:
    """Le numéro (`ns_id`) d'une `target` bien formée — un entier > 0, ou ses chiffres
    en texte —, sinon `None`. Le nom d'un tableau n'est PAS une cible : il se renomme,
    et un tableau s'adresse par son numéro."""
    if isinstance(target, bool):
        return None
    if isinstance(target, int):
        return target if target > 0 else None
    if isinstance(target, str) and target.strip().isdigit():
        n = int(target.strip())
        return n if n > 0 else None
    return None


def ou_trouver(target: Any) -> str:
    """Ce qu'il faut écrire, et où le lire : la phrase qu'un refus de lien sert."""
    cible = cible_valide(target)
    ou = (f"`data_rows(datastore={cible}, q=\"<its name>\")`" if cible
          else "`data_rows` on the linked table")
    return (f"write the `_id` of a row of the linked table, never its name — find it "
            f"with {ou} and copy that row's `_id`")


def cibles(fields: Iterable[Any]) -> set[int]:
    """Toutes les cibles qu'une liste de colonnes déclare, à toute profondeur (colonne,
    élément de liste, sous-champ d'objet)."""
    out: set[int] = set()
    for f in fields or []:
        if not isinstance(f, dict):
            continue
        if f.get("type") == "ref" and (n := cible_valide(f.get("target"))):
            out.add(n)
        of = f.get("of")
        if isinstance(of, dict):
            if of.get("type") == "ref" and (n := cible_valide(of.get("target"))):
                out.add(n)
            out |= cibles(of.get("fields") or [])
        out |= cibles(f.get("fields") or [])
    return out
