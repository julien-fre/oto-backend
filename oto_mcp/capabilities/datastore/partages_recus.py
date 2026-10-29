"""« Partagés avec moi » : les tableaux partagés à UNE PERSONNE — l'appelant — et à elle seule.

Arbitrage d'Alexis sur otomata-tech/oto#160 (10/09/2026) : le partage à une personne
reste, les trois audiences (personne, équipe, organisation) coexistent — l'ADR 0048 telle
quelle. Le défaut à corriger était la visibilité côté DESTINATAIRE : le partage
réussissait, la lecture du contenu l'honorait, et aucune liste ne le rendait.

- `GET /api/datastores` l'exclut dans toute org de travail (commit du 01/07/2026, après
  l'incident du 30/06) : un partage à une personne n'appartient à aucune org, et le faire
  entrer dans la liste d'une org le ferait lire comme un tableau de cette org. Depuis le
  28/09/2026, il l'inclut dans l'org PERSO.
- La section « Partagé » du chrome (`me.shell`) n'en rend aucun : elle résout chaque droit
  vers une copie du tableau dans `nodes`, et ces copies ont été retirées (0 en base au
  10/09/2026, pour 414 tableaux). Son identifiant (`nod_…`) n'est d'ailleurs pas celui des
  routes `/api/datastores/{datastore}`.

D'où cette route, À CÔTÉ de la liste : même forme d'entrée
que `DatastoreEntry` (le tableau de bord la peint avec le même composant), plus
`shared_by`. Trois garanties, éprouvées sur une vraie base
(`tests/datastore/test_partages_recus.py`) :

1. **Seulement ce qui a été donné à l'appelant.** La requête ne connaît qu'une clé,
   `principal_type='user' AND principal_id=<sub>` (`db.list_datastores_shared_to_user`) :
   un tiers de la même org ne voit rien, un droit d'org ou d'équipe n'entre pas ici.
2. **Dans toute org** (décision d'Alexis du 29/09/2026 : une org perso est une org
   comme une autre, « perso » n'est qu'une étiquette). Un partage à une personne
   n'appartient à aucune org : la lentille « moi » rend la même chose partout.
3. **Tous les partages faits à l'appelant.** La garantie « sans doublon avec la liste
   de l'org » est retirée le 29/09/2026 : dans l'org perso, `GET /api/datastores` rend
   aussi ces tableaux (`shared: true`), et la dédoublonner la viderait toujours. Cette
   route en est le sous-ensemble « partagés à moi », avec `shared_by`.

`mcp=None` : le besoin est celui du tableau de bord. Un jeton porté n'atteint pas cette
route — `auth/token_scopes` est deny-by-default, et elle n'y figure pas.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ... import db
from ...datastore.core import make_store
from ...db import shell as db_shell
from .._authz import SUB_ONLY
from .._types import Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES
from .datastores import DatastoreEntry


class SharedWithMeInput(BaseModel):
    """Aucun paramètre : le périmètre est l'appelant, jamais un argument."""


class SharedDatastoreEntry(DatastoreEntry):
    # QUI a partagé — un NOM (repli e-mail, puis identifiant : `db.shell.names_of`).
    # C'est la seule information qui dise au destinataire d'où vient un tableau qu'il
    # n'a pas créé.
    shared_by: Optional[str] = Field(
        default=None, description="Nom de la personne qui a partagé ce tableau à l'appelant.")


class SharedWithMe(BaseModel):
    datastores: list[SharedDatastoreEntry]


def _shared_with_me(ctx: ResolvedCtx, inp: SharedWithMeInput) -> dict:
    store = make_store(ctx.sub)
    recus = db.list_datastores_shared_to_user(ctx.sub)
    noms = db_shell.names_of(r.get("granted_by") for r in recus)
    gouverne = store._gouvernance(recus)
    out = []
    for r in recus:
        entree = store._entry(r, shared=True, permission=r.get("permission"),
                              can_govern=gouverne[int(r["id"])])
        entree["shared_by"] = noms.get(r.get("granted_by"))
        out.append(entree)
    return {"datastores": out}


CAPABILITIES += [
    Capability(
        key="me.datastore.shared_with_me",
        handler=_shared_with_me,
        Input=SharedWithMeInput,
        Output=SharedWithMe,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="GET", path="/api/me/datastores/shared"),
        description=(
            "Les tableaux partagés NOMINATIVEMENT à l'appelant (partage à une personne), "
            "et à lui seul — jamais un droit d'org ou d'équipe, qui se lisent dans "
            "`GET /api/datastores`. Servie dans toute org, avec le même contenu : un "
            "partage à une personne n'appartient à aucune org. Dans l'org perso, "
            "`GET /api/datastores` rend aussi ces tableaux. Même forme que les entrées de `GET /api/datastores`, plus "
            "`shared_by` (le nom de qui a partagé)."),
    ),
]
