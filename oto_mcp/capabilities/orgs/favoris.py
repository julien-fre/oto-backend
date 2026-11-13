"""Les favoris PARTAGÉS d'une org : des pages et des tableaux épinglés pour tous ses membres.

Trois routes REST, pour la barre latérale du tableau de bord :

- `GET    /api/orgs/{id}/favorites`                  — membres : les favoris de l'org ;
- `POST   /api/orgs/{id}/favorites`                  — membres : épingler `{kind, id}` ;
- `DELETE /api/orgs/{id}/favorites/{kind}/{ref_id}`  — membres : désépingler.

Chacune rend la liste à jour, `{"favorites": [...]}`, dans l'ordre où ils ont été posés.

**Un favori n'accorde rien.** Il est à l'org, mais chaque membre n'en LIT que ce que ses
droits lui ouvrent déjà : la page par `docs.common.acces_a_la_page` (son projet, ou un
partage de la page), le tableau par `ownership.can_access` — les deux portes de lecture
du produit, jamais recopiées ici. Le tableau perso d'un collègue épinglé par lui reste
invisible aux autres ; la page d'un projet qu'on ne lit pas aussi.

**Épingler exige de lire la cible ET qu'elle soit de l'org** : elle y VIT
(`ownership.org_ou_vit`, ADR 0071 — l'org de son propriétaire, ou de rangement pour un
objet perso), ou elle est partagée à l'org ou à une de ses équipes. Sinon 404
`favorite_target_unknown`, la même réponse pour « n'existe pas » et « ne se lit pas » —
les ids sont des entiers séquentiels globaux (F-016).

Désépingler est ouvert à tout membre (des signets communs), idempotent. Une cible
disparue (page ou tableau supprimé) sort de la liste et sa ligne est retirée ; une page
ARCHIVÉE, ou dont le projet l'est, sort de la liste sans être retirée.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from ... import db, org_origin, ownership
from ...db import favoris as store
from .._authz import ORG_MEMBER_OF
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..docs import common as docs_common
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}

DOC, TABLE = "doc", "table"

#: Ce que rend `_resoudre` pour un objet qui n'existe plus (sa ligne se retire).
DISPARU = "disparu"


class FavoritesInput(BaseModel):
    org_id: int


class FavoriteAddInput(BaseModel):
    org_id: int
    kind: str = Field(description="`doc` (a memory page) or `table` (a datastore).")
    id: int = Field(description="The page id or the table id.")


class FavoriteRemoveInput(BaseModel):
    org_id: int
    kind: str
    ref_id: int


class Favorite(BaseModel):
    kind: str
    id: int
    title: str
    # Le projet dont la mémoire porte la page ; None pour un tableau.
    project_id: Optional[int] = None
    added_by: Optional[str] = None
    added_at: str


class Favorites(BaseModel):
    favorites: list[Favorite]


def _kind(kind: str) -> str:
    if kind not in store.KINDS:
        raise AuthzDenied(400, "invalid_favorite_kind",
                          f"`kind` vaut {' | '.join(store.KINDS)} (reçu {kind!r}).")
    return kind


def _partage_a_l_org(org_id: int, rtype: str, rid: str) -> bool:
    """Un partage VIVANT de la ressource à l'org, ou à une de ses équipes."""
    vivants = [g for g in ownership.list_grants(rtype, rid) if not g.get("expired")]
    if any(g["principal_type"] == "org" and str(g["principal_id"]) == str(org_id)
           for g in vivants):
        return True
    equipes = [("group", g["principal_id"]) for g in vivants
               if g["principal_type"] == "group"]
    return any(int(o) == int(org_id) for o in org_origin.group_orgs(equipes).values())


def _de_l_org(org_id: int, rtype: str, rid: str) -> bool:
    org, connue = ownership.org_ou_vit(rtype, rid)
    if connue and org is not None and int(org) == int(org_id):
        return True
    return _partage_a_l_org(org_id, rtype, rid)


def _resoudre(sub: str, org_id: int, kind: str, ref_id: int) -> Any:
    """Ce que `sub` voit du favori `(kind, ref_id)` dans l'org : `{title, project_id}`,
    `None` s'il ne le lit pas (ou si l'objet n'est pas de l'org), `DISPARU` si l'objet
    n'existe plus."""
    if kind == DOC:
        page = db.get_doc_by_id(int(ref_id))
        if page is None:
            return DISPARU
        projet = db.get_project_by_id(int(page["project_id"]))
        if projet is None:
            return DISPARU
        if page.get("archived_at") or projet.get("archived_at"):
            return None
        pid = str(page["project_id"])
        if not (_de_l_org(org_id, "project", pid)
                or _partage_a_l_org(org_id, docs_common.DOC_RTYPE, str(ref_id))):
            return None
        if docs_common.acces_a_la_page(sub, page) is None:
            return None
        return {"title": page.get("title") or "", "project_id": int(page["project_id"])}
    tableau = db.get_datastore_by_id(int(ref_id))
    if tableau is None:
        return DISPARU
    tds, rid = ownership.TYPE_RESSOURCE_DATASTORE, str(ref_id)
    if not _de_l_org(org_id, tds, rid):
        return None
    if not ownership.can_access(sub, tds, rid, "read"):
        return None
    return {"title": tableau.get("datastore") or "", "project_id": None}


def _iso(v) -> str:
    """La date de pose en ISO 8601 AVEC son fuseau : le store rend un texte sans fuseau,
    en UTC (`db._conn._str_dict_row`, session `TimeZone=UTC`) — même règle que
    `service_commerce._iso` (#1073)."""
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


def _vue(sub: str, org_id: int) -> dict:
    out: list[dict] = []
    morts: dict[str, list[int]] = {}
    for f in store.lister(org_id):
        vu = _resoudre(sub, org_id, f["kind"], f["ref_id"])
        if vu == DISPARU:
            morts.setdefault(f["kind"], []).append(int(f["ref_id"]))
            continue
        if vu is None:
            continue
        out.append({"kind": f["kind"], "id": int(f["ref_id"]), **vu,
                    "added_by": f.get("added_by"),
                    "added_at": _iso(f["added_at"])})
    for kind, ids in morts.items():
        store.retirer_morts(kind, ids)
    return {"favorites": out}


def _lister(ctx: ResolvedCtx, inp: FavoritesInput) -> dict:
    return _vue(ctx.sub, inp.org_id)


def _ajouter(ctx: ResolvedCtx, inp: FavoriteAddInput) -> dict:
    kind = _kind(inp.kind)
    if not isinstance(_resoudre(ctx.sub, inp.org_id, kind, inp.id), dict):
        raise AuthzDenied(404, "favorite_target_unknown",
                          f"Aucun{'e page' if kind == DOC else ' tableau'} #{inp.id} "
                          "lisible dans cette org.")
    store.poser(inp.org_id, kind, inp.id, ctx.sub)
    return _vue(ctx.sub, inp.org_id)


def _retirer(ctx: ResolvedCtx, inp: FavoriteRemoveInput) -> dict:
    store.retirer(inp.org_id, _kind(inp.kind), inp.ref_id)
    return _vue(ctx.sub, inp.org_id)


CAPABILITIES += [
    Capability(key="org.favorites.list", handler=_lister, Input=FavoritesInput,
               Output=Favorites, authz=ORG_MEMBER_OF("org_id"), mcp=None,
               rest=RestBinding("GET", "/api/orgs/{id}/favorites", _ID)),
    Capability(key="org.favorites.add", handler=_ajouter, Input=FavoriteAddInput,
               Output=Favorites, authz=ORG_MEMBER_OF("org_id"), mcp=None,
               rest=RestBinding("POST", "/api/orgs/{id}/favorites", _ID)),
    Capability(key="org.favorites.remove", handler=_retirer, Input=FavoriteRemoveInput,
               Output=Favorites, authz=ORG_MEMBER_OF("org_id"), mcp=None,
               rest=RestBinding("DELETE", "/api/orgs/{id}/favorites/{kind}/{ref_id}",
                                _ID)),
]
