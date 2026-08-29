"""La RÉFÉRENCE de procédure d'un nœud « agent » — une définition, deux surfaces (#417).

Un nœud de nature `agent` porte un `nod_*` DÉRIVÉ (`md5('prc:' || id)`, `db/nodes`
lot ⑧). Jusqu'au 29/08, rien côté serveur ne menait de ce nœud à sa fiche : le rail ne
lisait pas `props.legacy_id`, la fiche de nœud ne le rendait pas, et la fiche de guide
refuse un `nod_*`. Un front devait recalculer un md5 côté client ou apparier par titre
(non unique) — tous ses liens rail → fiche d'agent tombaient en 404.

Le nœud porte donc sa référence, LUE dans ses propriétés — posées par la conversion,
jamais reconstruites, et sans jointure (`legacy_id` et `slug` sont déjà dans `props`) :

- `id` : l'identifiant STABLE du guide, celui que `GET /api/me/guides/{guide_id}` et
  `oto_procedure` acceptent ;
- `slug` : la référence lisible, à côté (0059-D3 : l'API rend toujours les deux) ;
- `scope` : le PROPRIÉTAIRE du nœud (`owner_type` — `org` ou `group`), parce que le rail
  sert aussi les procédures d'ÉQUIPE et qu'un front qui ne le sait pas frappe la
  mauvaise route de fiche.

**Une seule définition pour le rail ET la fiche.** Deux surfaces qui décriraient la même
référence divergeraient au premier correctif — c'est la raison d'être de ce module, la
même que pour `_type_of` (figée par `test_node_natures`).

**`None`, jamais une invention.** Un nœud qui n'est pas un agent, un agent dont les
propriétés ne portent pas de référence de guide (nœud natif, famille inconnue, id
illisible) ne reçoit rien : le rail l'omet (`exclude_none`), la fiche sert `null`. Servir
un id deviné produirait exactement le 404 qu'on retire.
"""
from __future__ import annotations

from typing import Mapping, Optional

from pydantic import BaseModel

# La famille de conversion des guides — miroir de `db/nodes._FAMILY_GUIDE` et de
# `db/shell._FAMILLE_PAR_GRANT`, comparés par test plutôt qu'importés : la lecture
# d'une surface n'a pas à dépendre du module de conversion.
FAMILLE_GUIDE = "prc"
# La nature servie qui porte une référence — la seule (cf. `_TYPE_PAR_ROLE`).
NATURE_AGENT = "agent"


class ProcedureRef(BaseModel):
    """The procedure an agent node runs: `id` is the stable guide id accepted by
    `GET /api/me/guides/{guide_id}` and `oto_procedure`; `slug` its readable
    reference; `scope` the owner of the node (`org` | `group`)."""
    id: int
    slug: Optional[str] = None
    scope: str


def procedure_ref_of(nature: str, owner_type: Optional[str],
                     source: Optional[Mapping]) -> Optional[ProcedureRef]:
    """La référence d'un nœud, ou `None`.

    `source` est ce qui porte `legacy`, `legacy_id` et `slug` : les `props` d'une
    fiche, ou la LIGNE du rail (qui les extrait en colonnes, `db/shell._COLS`). Les
    deux surfaces passent par ici — pas par une lecture chacune.
    """
    if nature != NATURE_AGENT or not source:
        return None
    if source.get("legacy") != FAMILLE_GUIDE:
        return None
    try:
        ident = int(source.get("legacy_id"))
    except (TypeError, ValueError):
        return None
    if not owner_type:
        return None
    return ProcedureRef(id=ident, slug=source.get("slug") or None, scope=str(owner_type))
