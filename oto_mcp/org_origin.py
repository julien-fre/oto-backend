"""L'org où VIT un objet — l'axe « org » de l'ADR 0071, jamais son chemin d'accès.

L'ADR 0071 sépare deux questions que le seul champ `owner_type`/`owner_id` portait
ensemble : **où vit l'objet** (son org) et **qui le voit** (son audience). Ce module
répond à la première, et à elle seule. Il ne regarde pas PAR OÙ l'acteur atteint
l'objet : le chemin d'accès (possédé par le contexte, membre, partage) dit l'audience,
pas l'org. Déduire l'org du chemin se trompe dans les deux sens — le tableau
personnel du visiteur, né dans une autre org, passe pour « d'ici » parce qu'il est à
lui ; le projet d'un collègue de la même org, partagé nommément, passe pour
« d'ailleurs » parce qu'il arrive par un partage (et la réponse changeait selon que
l'acteur était admin d'org ou non).

Règle, par propriétaire :

- `org` → cette org ;
- `group` → l'org PARENTE de l'équipe (ADR 0049) ;
- `user` → l'org de rangement quand elle est enregistrée (`projects.context_org_id`) ;
  sinon **inconnue** — un tableau ou un nœud personnel ne porte pas encore son org
  (ADR 0071 §3). On le DIT, on ne la devine pas : une org devinée est exactement la
  confusion que ce champ existe pour empêcher ;
- `platform` → aucune org, et c'est su : la bibliothèque de la plateforme.
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import group_store

#: (org où vit l'objet, cette org est-elle connue ?). `(None, True)` = l'objet ne vit
#: dans aucune org (plateforme) ; `(None, False)` = elle n'est pas enregistrée.
Origine = tuple[Optional[int], bool]


def group_orgs(owners: Iterable[tuple]) -> dict[int, int]:
    """Org parente de chaque équipe propriétaire parmi `owners` — UNE requête groupée,
    aucune si aucun propriétaire n'est une équipe."""
    gids = sorted({int(oid) for otype, oid in owners if otype == "group"})
    return group_store.org_ids_of_groups(gids) if gids else {}


def org_of(owner_type: str, owner_id: str, *, context_org_id: Optional[int] = None,
           groups: dict[int, int]) -> Origine:
    """L'org où vit un objet possédé par `(owner_type, owner_id)`. `groups` = sortie de
    `group_orgs` sur le lot. Un type de propriétaire inconnu lève : c'est une donnée
    incohérente, pas un cas à classer."""
    if owner_type == "org":
        return int(owner_id), True
    if owner_type == "group":
        org = groups.get(int(owner_id))
        return org, org is not None
    if owner_type == "user":
        return (int(context_org_id), True) if context_org_id is not None else (None, False)
    if owner_type == "platform":
        return None, True
    raise ValueError(f"type de propriétaire inconnu : {owner_type!r}")


def other_org(origine: Origine, active_org_id: int) -> Optional[bool]:
    """L'objet vit-il dans une AUTRE org que l'org active ? `None` = on ne sait pas."""
    org, connue = origine
    if not connue:
        return None
    return org is not None and org != int(active_org_id)
