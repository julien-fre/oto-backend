"""Ouvrir UN nœud : sa fiche, son fil, son corps en blocs — ou le schéma d'un tableau.

Deuxième surface de lecture précoce du modèle de nœuds, après `/shell`. Même régime :
LECTURE seule, forme contractée avec le front (`/nodes/{nodeId}`) et **déclarée
provisoire**.

**404 indistinct, et c'est le point de sécurité de ce module.** Un nœud inexistant et
un nœud interdit rendent la MÊME réponse : un 403 dirait « il existe, mais pas pour
toi » — donc révélerait l'existence d'une page à qui n'y a pas droit, et permettrait
d'énumérer le contenu d'une org en lisant les codes d'état. Le front l'a écrit dans son
propre contrat ; on l'applique parce que c'est juste, pas parce qu'il le demande.

⚠️ **Ouvrir un TABLEAU rend son schéma de colonnes, jamais ses lignes.** Les lignes ont
leur surface, paginée par curseur. Un « ouvrir » qui ramène 43 584 lignes n'est pas une
fiche, c'est un export déguisé — et c'est la panne qui ressemblerait à un problème de
performance plutôt qu'à un problème de modèle.

**Quatre écarts au contrat du front, tous assumés et nommés** (arbitrés le 17/08) :

1. ✅ **Les blocs sont ÉTIQUETÉS depuis le 21/08** (lot ⑦). ⚠️ Ce paragraphe a dit le
   contraire jusque-là — « nos blocs sont trop gros, un titre et trois paragraphes
   partagent un id » — et **c'était faux** : mesuré sur 140 blocs de corps réels, le
   découpage se fait aux lignes vides ET aux titres, zéro bloc mixte, médiane 175 c.
   L'erreur venait d'une lecture partielle du parse (la fonction qui découpe le texte
   n'avait pas été lue), corrigée par la mesure. Ce qui manquait n'était pas le grain
   mais **l'étiquette** : `role` (`heading`/`paragraph`/`list`) et `items[]` sont
   désormais servis, en PROPRIÉTÉ et jamais en `type` (0054-D2).
   **L'interdit d'ancrage sur un `blk_*` est LEVÉ** : l'étiquetage n'a fait tourner
   aucune identité, la clé de rapprochement étant passée sur la SOURCE SEULE.
2. **`modified` est servi en ISO, pas en « jeudi ».** Le front demande la date
   humanisée ; elle n'a pas sa place dans une réponse CACHÉE et versionnée : « jeudi »
   devient faux la semaine suivante sans que `rev` bouge, donc le 304 confirme un cache
   qui ment. Leur propre règle — « le back n'écrit pas les libellés de l'interface » —
   plaide dans le même sens.
3. **`access` et `dependencies` sont ABSENTS, pas vides.** Aucune source de nœud ne les
   porte encore. Un `editors: []` affirmerait « personne d'autre » ; un
   `dependencies: []` autoriserait une suppression. Absent dit « on ne sait pas encore ».
4. **`trail` est servi**, borné en profondeur, avec la fratrie de chaque maillon.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import ownership
from ..db import node_view as db_node
from ..db import shell as db_shell
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, NotModified, ResolvedCtx, RestBinding
from .node_procedure_ref import ProcedureRef, procedure_ref_of
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

_PROFONDEUR_FIL = 12
_FRERES_MAX = 50

_TYPE_PAR_KIND = {"page": "page", "tableau": "table"}
# Même règle qu'au rail : la nature est DÉRIVÉE d'un rôle porté en propriété, jamais
# d'un `kind` de plus. Un rôle inconnu retombe sur le genre.
_TYPE_PAR_ROLE = {"procedure": "agent"}


class NodeInput(BaseModel):
    node_id: str
    rev: Optional[str] = None


class TrailSibling(BaseModel):
    id: str
    name: str
    type: str


class TrailCrumb(BaseModel):
    id: str
    name: str
    type: str
    # Les contenus rangés au même endroit, le maillon lui-même compris — le popover du
    # fil n'a donc rien à redemander. Bornés : un dossier à 4 000 frères ferait du fil
    # la plus grosse partie de la réponse.
    siblings: list[TrailSibling] = []


class ContentBlock(BaseModel):
    id: str
    # Le SUPPORT du bloc — `text` | `code` (0054-D2 : + `image`, `référence` un jour).
    # Ce n'est PAS ce que l'écran rend : ça, c'est `role`.
    type: str
    # Le RÔLE DE PRÉSENTATION, propriété et jamais valeur de `type` : `heading` |
    # `paragraph` | `list`. **Absent quand on ne sait pas classer** (tableau, citation) —
    # un `paragraph` posé par défaut mentirait, et le front rend alors la source comme
    # il l'entend. Servir le rôle À CÔTÉ du type, plutôt qu'à sa place, garde `type`
    # disponible pour ce qu'il désigne : le jour où `image` arrive, il n'entre pas en
    # collision avec `heading`.
    role: Optional[str] = None
    # Les puces d'une liste, déjà extraites. Le front ne peut pas les dériver sans
    # reparser du markdown — ce serait une seconde implémentation du parse, qui
    # divergerait de la nôtre au premier cas limite.
    items: Optional[list[str]] = None
    # La source EXACTE du bloc. Invariant du parse : concaténer les `md` d'un nœud rend
    # son corps au caractère près — c'est ce qui permet au front de rendre ce qu'il sait
    # rendre et de laisser passer le reste, plutôt que de recevoir une forme appauvrie.
    md: Optional[str] = None
    lang: Optional[str] = None


class NodeModified(BaseModel):
    # ISO, jamais une phrase relative : cf. l'écart 2 de l'entête.
    at: Optional[str] = None
    by: Optional[str] = None


class NodeOut(BaseModel):
    id: str
    name: str
    type: Literal["page", "table", "agent", "execution"]
    # Agent : la procédure qu'il exécute (id stable, slug, scope) — le chemin vers sa
    # fiche (#417). `null` sur toute autre nature et sur un agent sans référence
    # lisible : un `null` dit « n'en exécute aucune », un id deviné dirait faux.
    procedure: Optional[ProcedureRef] = None
    trail: list[TrailCrumb] = []
    modified: NodeModified
    # Page : le corps en blocs. Absent sur un tableau.
    body: Optional[list[ContentBlock]] = None
    # Tableau : le schéma de ses colonnes. Absent sur une page — et JAMAIS les lignes.
    columns: Optional[Any] = None
    rev: str
    # Ce que cette v0 ne sait pas encore dire, DIT plutôt que deviné. Sans cette clé,
    # l'absence d'`access` se lirait comme « aucun partage » et celle de `dependencies`
    # comme « rien ne s'en sert » — deux affirmations qu'on ne peut pas tenir.
    non_servi: list[str] = []


def _type_of(kind: str, props: Optional[dict] = None) -> str:
    role = (props or {}).get("role")
    if role in _TYPE_PAR_ROLE:
        return _TYPE_PAR_ROLE[role]
    return _TYPE_PAR_KIND.get(kind or "", "page")


def _introuvable() -> AuthzDenied:
    """LE refus de ce module — un seul, pour les deux causes.

    Inexistant et interdit doivent être indiscernables : un 403 sur l'un des deux
    transforme le code d'état en oracle d'existence.
    """
    return AuthzDenied(404, "not_found", "Aucun nœud de ce nom, ou aucun droit de le voir.")


def _lisible(fiche: dict, principals: set, partages: set) -> bool:
    """Le nœud est-il à portée de cette personne ?

    Deux voies, les mêmes que le rail : son propriétaire est un de mes principals
    (l'org active, une de mes équipes, moi), ou il m'est partagé en direct. On réutilise
    `ownership.active_org_principals` plutôt que de réécrire le scoping — une seconde
    définition de « à portée » divergerait de la première au premier changement.
    """
    if (fiche["owner_type"], str(fiche["owner_id"])) in principals:
        return True
    return fiche["public_id"] in partages


def _fil(fiche: dict) -> list[TrailCrumb]:
    """Le chemin racine→nœud, avec la fratrie de chaque maillon."""
    chaine = db_node.ancestors_of(fiche["id"], max_depth=_PROFONDEUR_FIL)
    if not chaine:
        return []
    freres = db_node.siblings_of([c["parent_id"] for c in chaine],
                                 owner=(fiche["owner_type"], str(fiche["owner_id"])),
                                 cap=_FRERES_MAX)
    out = []
    for c in chaine:
        props = c.get("props") or {}
        out.append(TrailCrumb(
            id=c["public_id"], name=props.get("title") or "", type=_type_of(c["kind"], props),
            siblings=[TrailSibling(id=s["public_id"], name=s.get("title") or "",
                                   type=_type_of(s["kind"], s))
                      for s in freres.get(c["parent_id"], [])]))
    return out


def _rev(corps: dict) -> str:
    """Empreinte du corps canonique — même règle qu'au rail (contenu, pas horodatage)."""
    return hashlib.sha256(
        json.dumps(corps, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:32]


def _compose(ctx: ResolvedCtx, node_id: str) -> dict:
    """Tout le travail SYNCHRONE. Appelé hors boucle (cf. `_node`)."""
    fiche = db_node.node_by_public_id(node_id)
    if not fiche:
        raise _introuvable()

    principals = {(t, str(i)) for t, i in
                  ownership.active_org_principals(ctx.sub, ctx.org_id)}
    partages: set = set()
    if (fiche["owner_type"], str(fiche["owner_id"])) not in principals:
        # Le second chemin ne se paie QUE s'il sert : la voie du propriétaire couvre la
        # quasi-totalité des ouvertures, et lire tous les grants d'une personne pour
        # confirmer ce qu'on sait déjà serait une requête par ouverture de page.
        par_id, _ = db_shell.resolve_grant_nodes(db_shell.direct_grants(ctx.sub))
        partages = set(par_id)
    if not _lisible(fiche, principals, partages):
        raise _introuvable()

    props = fiche.get("props") or {}
    nature = _type_of(fiche["kind"], props)
    ref = procedure_ref_of(nature, fiche.get("owner_type"), props)
    corps: dict = {
        "id": fiche["public_id"],
        "name": props.get("title") or "",
        "type": nature,
        "procedure": ref.model_dump() if ref else None,
        "trail": [c.model_dump() for c in _fil(fiche)],
        "modified": NodeModified(
            at=str(fiche["updated_at"]) if fiche.get("updated_at") else None,
            by=_nom_de(props.get("created_by"))).model_dump(),
        # Ce qu'on ne sait pas encore servir, NOMMÉ. `modified.by` y figure quand le
        # nœud ne porte pas d'auteur : `props.created_by` est le CRÉATEUR, pas le
        # dernier éditeur — le servir comme tel serait une attribution fausse.
        "non_servi": ["access", "dependencies"]
        + ([] if props.get("created_by") else ["modified.by"]),
    }
    if corps["type"] == "table":
        # Le SCHÉMA, jamais les lignes. `child_schema` peut manquer : 29 des 83 tableaux
        # de production sont des tables libres (0054-D4) — `None` dit « libre », `[]`
        # dirait « aucune colonne », ce qui est faux.
        corps["columns"] = props.get("child_schema")
    else:
        corps["body"] = [
            ContentBlock(id=b["public_id"], type=b["type"],
                         role=(b.get("props") or {}).get("role"),
                         items=(b.get("props") or {}).get("items"),
                         md=(b.get("props") or {}).get("md"),
                         lang=(b.get("props") or {}).get("lang")).model_dump(
                             exclude_none=True)
            for b in db_node.blocks_of(fiche["id"])]
    corps["rev"] = _rev({k: v for k, v in corps.items() if k != "rev"})
    return corps


def _nom_de(sub: Optional[str]) -> Optional[str]:
    if not sub:
        return None
    return db_shell.names_of([sub]).get(sub)


async def _node(ctx: ResolvedCtx, inp: NodeInput):
    """Le nœud, ou un 304 si le client porte déjà notre version.

    Hors boucle, comme le rail : plusieurs requêtes DB, sur un serveur mono-loop.
    """
    corps = await run_in_threadpool(_compose, ctx, inp.node_id)
    if inp.rev and inp.rev == corps.get("rev"):
        return NotModified(corps["rev"])
    return corps


CAPABILITIES += [
    Capability(
        key="me.node", handler=_node, Input=NodeInput, Output=NodeOut,
        authz=ORG_MEMBER,
        description=(
            "Open ONE node by its opaque id: name, type, the TRAIL from the root "
            "(each crumb carrying its siblings, so a breadcrumb popover needs no "
            "extra call), and — for a page — its BODY as ordered blocks with STABLE "
            "ids you can cite. Opening a TABLE returns its column schema, never its "
            "rows (rows have their own cursor-paginated surface). Unknown id and "
            "forbidden id answer the SAME 404 on purpose: a 403 would reveal that the "
            "node exists. Pass `rev` for a conditional read (304 / `{not_modified}`). "
            "`non_servi` lists what this version cannot answer yet — read it before "
            "concluding that a node has no sharing or no dependants. PROVISIONAL "
            "surface: shape contracted, not frozen."),
        mcp="oto_node",
        rest=RestBinding("GET", "/api/me/nodes/{node_id}", provisoire=True),
    ),
]
