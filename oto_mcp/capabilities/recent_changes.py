"""`GET /api/me/recent-changes` — les dernières pages et procédures modifiées (oto#191).

La source de l'îlot « Dernières modifications » de l'accueil, qui a remplacé l'inbox
(retirée, oto#191).
**Dérivée, jamais dupliquée** : deux `updated_at` existants, une requête
(`db/recent_changes.py`), aucun journal ni table nouvelle.

Le périmètre est celui de la LECTURE, et pas plus large :
- les pages des projets lisibles dans l'org active — `ownership.accessible_project_ids`,
  le même ensemble que `oto_project op=list` et que la recherche (« cherchable ⇔
  lisible », tripwire `test_search_scope_tripwire`). Plus étroit que `can_access`
  par-id (qui traverse toutes mes orgs) : on ne montre jamais ici ce qu'une
  ouverture montrerait ailleurs ;
- les procédures des paliers que `oto_procedure op=get` accepte : les miennes
  (`scope='user'`), celles de l'org active, celles de mes équipes — ou de TOUTES les
  équipes pour un admin d'org, exactement `roles.can_read_group`. C'est
  `ownership.project_scope_owners` + la personne, la règle du rail.

**Sans org active : 200 et une liste vide**, jamais un 400 — l'accueil charge cet
îlot d'office, un refus casserait l'écran (le contrat qu'avait l'inbox remplacée).

**Pas de face MCP** dans ce lot : la question est celle d'un écran, et un agent qui
veut « ce qui a bougé » a `oto_search` et les listes de projet. L'ouvrir plus tard
est un binding de plus, pas une autre capacité.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import org_store, ownership
from ..db import recent_changes as db_recent
from ..db import shell as db_shell
from ._authz import SUB_ONLY
from ._types import Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

LIMIT_DEFAUT = 20
LIMIT_MAX = 50


class RecentChangesInput(BaseModel):
    # Hors bornes = `400 invalid_input` (l'adaptateur), jamais un plafond appliqué en
    # silence : un client qui demande 500 doit apprendre qu'il n'en aura que 50.
    limit: int = Field(default=LIMIT_DEFAUT, ge=1, le=LIMIT_MAX, description=(
        f"Nombre maximal d'éléments rendus, {LIMIT_DEFAUT} par défaut, {LIMIT_MAX} au plus."))


class RecentChangeProject(BaseModel):
    id: int
    name: str


class RecentChangeAuthor(BaseModel):
    sub: str
    # Nom affichable (`users.name`, repli email puis sub — `db/shell.names_of`).
    name: str


class RecentChange(BaseModel):
    type: Literal["doc", "procedure"]
    # `doc` : la poignée `doc_id` de `POST /api/me/docs` ; `procedure` : l'id stable
    # que `GET /api/me/guides/{guide_id}` ouvre (et `oto_procedure`).
    id: int
    title: str
    # Le projet d'une PAGE. `null` pour une procédure : elle appartient à un palier
    # (personne, org, équipe), pas à un projet.
    project: Optional[RecentChangeProject] = None
    # Procédure seulement : sa référence lisible et son palier (0059-D3 : les deux).
    slug: Optional[str] = None
    scope: Optional[Literal["user", "org", "group"]] = None
    # Qui a fait CETTE modification, quand la donnée existe (révision appariée à la
    # microseconde, cf. `db/recent_changes.py`). `null` = inconnu, jamais deviné.
    author: Optional[RecentChangeAuthor] = None
    updated_at: str


class RecentChangesView(BaseModel):
    items: list[RecentChange]
    limit: int


def _recent_changes(ctx: ResolvedCtx, inp: RecentChangesInput) -> dict:
    sub, org_id = ctx.sub, ctx.org_id
    if sub is None or org_id is None:
        return {"items": [], "limit": inp.limit}
    project_ids = ownership.accessible_project_ids(sub, org_id, want="read")
    owners = [("user", sub)] + ownership.project_scope_owners(sub, org_id)
    rows = db_recent.recent_changes(project_ids, owners, limit=inp.limit,
                                    base_slug=org_store.BASE_SLUG)
    noms = db_shell.names_of(r["author_sub"] for r in rows)
    items = []
    for r in rows:
        auteur = r.get("author_sub")
        items.append({
            "type": r["type"],
            "id": int(r["id"]),
            "title": r["title"],
            "project": ({"id": int(r["project_id"]), "name": r["project_name"]}
                        if r.get("project_id") is not None else None),
            "slug": r.get("slug"),
            "scope": r.get("owner_type"),
            "author": ({"sub": auteur, "name": noms.get(auteur, auteur)}
                       if auteur else None),
            "updated_at": r["updated_at"],
        })
    return {"items": items, "limit": inp.limit}


_DOC = (
    "Les dernières pages et procédures modifiées dans ce que je peux lire (projets de "
    "l'org active, procédures de mes paliers), les plus récentes d'abord, fusionnées "
    f"par date de modification. `limit` : {LIMIT_DEFAUT} par défaut, {LIMIT_MAX} au "
    "plus. L'auteur est rendu quand la donnée existe, `null` sinon — jamais déduit. "
    "Sans org active : liste vide."
)

CAPABILITIES += [
    Capability(
        key="me.recent_changes", handler=_recent_changes,
        Input=RecentChangesInput, authz=SUB_ONLY,
        Output=RecentChangesView, description=_DOC,
        mcp=None,   # la question est celle d'un écran (oto#191) ; un binding de plus suffira
        rest=RestBinding("GET", "/api/me/recent-changes"),
    ),
]
