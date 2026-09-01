"""Écrire un nœud — la face d'écriture du nouvel univers de contenu.

Le nouvel univers **ne projette pas** : ce qu'on écrit ici naît dans `nodes` et n'a
aucune source dans l'ancien monde, comme les couches de contexte depuis M1. Les deux
surfaces vivent côte à côte le temps de la transition, et l'ancienne continue de
servir l'ancien monde sans rien savoir de celle-ci (arbitrage d'Alexis, 31/08/2026 :
« on ne migre pas, on arrête la recopie, la surface nœud vit à côté et part de vide »).

⚠️ **Trois genres, et rien d'autre** : `page`, `tableau`, `ligne`. Projet, guide et
procédure ne sont PAS des genres — ce sont des rôles portés en propriété par une page
(ADR 0054-D5 : « le genre dit ce que l'objet EST, et ce qu'il JOUE est un rôle porté
en propriété, jamais un `kind` de plus »). Cette surface n'en introduira pas.

⚠️ **Aucun nœud écrit ici ne porte `delivery`.** La recherche discrimine une couche de
contexte par cette propriété, pas par le genre : la poser sur une page ordinaire la
ferait remonter dans le périmètre des couches, celui qu'on injecte au handshake.

L'autorisation n'est pas réécrite : le palier d'écriture est celui des guides
(`guides._owner_for_write` — plateforme / org / chef d'équipe / soi), qui existe,
complet, une couche en dessous. En écrire une seconde version la ferait diverger de
la première au premier changement.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..db import node_tables as db_node_tables, nodes as db_nodes, node_view as db_node
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class NodeEditInput(BaseModel):
    op: Literal["create", "update", "move", "delete"]
    # create
    scope: Optional[str] = None          # platform | org | group | user (défaut : org)
    owner_id: Optional[str] = None       # cible explicite du scope
    # Le GENRE, et il n'y en a que trois (0054-D5). Une ligne se crée sous son
    # tableau : `kind="ligne"` + `parent_id` du tableau + `data`.
    kind: Optional[Literal["page", "tableau", "ligne"]] = None
    title: Optional[str] = None
    description: Optional[str] = None
    body_md: Optional[str] = None
    # tableau : le schéma de ses enfants. Facultatif — une table libre est un
    # tableau valide (29 des 83 tableaux de production n'en déclaraient aucun).
    columns: Optional[list[dict]] = None
    # ligne : les valeurs métier. Elles vont dans la colonne `data`, JAMAIS dans
    # `props` — une cellule nommée `title` ne doit pas écraser le titre du nœud.
    data: Optional[dict] = None
    # update / move / delete
    node_id: Optional[str] = None        # l'id opaque, jamais l'id interne
    parent_id: Optional[str] = None      # move : le nouveau parent (id opaque), ou null
    after_id: Optional[str] = None       # move : le frère après lequel se placer


class NodeEditOut(BaseModel):
    ok: bool
    id: Optional[str] = None
    op: str


def _interne(node_id: str) -> dict:
    """La ligne d'un nœud depuis son id PUBLIC, ou le même 404 qu'à la lecture.

    Inexistant et interdit restent indiscernables : un 403 sur l'un des deux ferait
    du code d'état un oracle d'existence — la règle que la face de lecture tient déjà.
    """
    fiche = db_node.node_by_public_id(node_id)
    if not fiche:
        raise AuthzDenied(404, "not_found",
                          "Aucun nœud de ce nom, ou aucun droit de le voir.")
    return fiche


def _mien(ctx: ResolvedCtx, fiche: dict) -> None:
    """Écrire suppose le palier du PROPRIÉTAIRE, pas la simple lecture.

    On repasse par `_owner_for_write` avec le propriétaire réel du nœud : c'est la
    même règle que pour créer, donc personne ne peut modifier ce qu'il n'aurait pas pu
    écrire. Un nœud CONVERTI est refusé ici — il appartient à l'ancien monde, et sa
    source y est la vérité ; l'écrire des deux côtés ferait diverger les deux.
    """
    if (fiche.get("props") or {}).get("legacy"):
        raise AuthzDenied(
            409, "node_projete",
            "Ce nœud est une copie de l'ancien monde : il s'édite sur sa surface "
            "d'origine. Seuls les nœuds nés ici s'écrivent ici.")
    from .guides import _owner_for_write
    _owner_for_write(ctx, str(fiche["owner_type"]), str(fiche["owner_id"]))


def _create(ctx: ResolvedCtx, inp: NodeEditInput) -> dict:
    """Les trois genres passent par le MÊME verbe, et c'est le modèle qui le veut.

    Un tableau et une ligne ne sont pas des objets à part : ce sont des nœuds, avec
    un genre et une place dans l'arbre. Leur donner chacun son verbe créerait trois
    vocabulaires pour une seule notion — et trois endroits où l'autorisation, la
    place dans la fratrie et le refus d'écrire une copie devraient rester d'accord.
    """
    from .guides import _owner_for_write
    genre = (inp.kind or "page").strip()
    parent = _interne(inp.parent_id)["id"] if inp.parent_id else None

    if genre == "ligne":
        # Une ligne n'a PAS de propriétaire propre (0054-D4) : elle prend celui de
        # son tableau. Le scope demandé n'a donc pas de sens ici — c'est le palier
        # d'écriture SUR LE TABLEAU qui décide, et rien d'autre.
        if parent is None:
            raise AuthzDenied(400, "missing_parent_id",
                              "Une ligne se crée sous son tableau : `parent_id` requis.")
        _mien(ctx, _interne(inp.parent_id))
        row = db_node_tables.add_row(parent, inp.data or {})
        if row is None:
            raise AuthzDenied(400, "parent_pas_un_tableau",
                              "`parent_id` doit désigner un tableau né ici.")
        return {"ok": True, "id": row["public_id"], "op": "create"}

    scope = (inp.scope or "org").strip()
    owner_id = _owner_for_write(ctx, scope, inp.owner_id)
    if not (inp.title or "").strip():
        raise AuthzDenied(400, "missing_title",
                          f"`title` requis pour créer un nœud de genre {genre}.")
    if genre == "tableau":
        row = db_node_tables.create_table(owner_type=scope, owner_id=owner_id,
                                          title=inp.title, columns=inp.columns,
                                          parent_id=parent)
        return {"ok": True, "id": row["public_id"], "op": "create"}

    row = db_nodes.create_page(owner_type=scope, owner_id=owner_id,
                               title=inp.title, body_md=inp.body_md or "",
                               description=inp.description or "", parent_id=parent)
    return {"ok": True, "id": row["public_id"], "op": "create"}


def _update(ctx: ResolvedCtx, inp: NodeEditInput) -> dict:
    """Ce qu'on peut écrire dépend du GENRE — c'est le nœud qui le dit, pas l'appel.

    On ne demande pas à l'appelant de redéclarer le genre : il est déjà stocké, et
    le lui faire répéter ouvrirait un écart entre ce qu'il croit modifier et ce
    qu'il modifie vraiment.
    """
    fiche = _interne(_besoin(inp.node_id, "update"))
    _mien(ctx, fiche)
    genre = fiche.get("kind")

    if genre == "ligne":
        if not db_node_tables.update_row(fiche["id"], inp.data or {}):
            raise AuthzDenied(400, "rien_a_ecrire",
                              "Aucune cellule fournie — `data` attendu pour une ligne.")
        return {"ok": True, "id": fiche["public_id"], "op": "update"}

    fait = False
    if genre == "tableau" and inp.columns is not None:
        fait = db_node_tables.set_columns(fiche["id"], inp.columns)
    # Le titre et la description valent pour une page comme pour un tableau ; le
    # corps n'a de sens que pour une page, et `update_page` l'ignore s'il est absent.
    fait = db_nodes.update_page(fiche["id"], title=inp.title,
                                description=inp.description,
                                body_md=inp.body_md) or fait
    if not fait:
        raise AuthzDenied(400, "rien_a_ecrire",
                          "Aucun champ fourni — `title`, `description`, `body_md` "
                          "(page) ou `columns` (tableau).")
    return {"ok": True, "id": fiche["public_id"], "op": "update"}


def _move(ctx: ResolvedCtx, inp: NodeEditInput) -> dict:
    fiche = _interne(_besoin(inp.node_id, "move"))
    _mien(ctx, fiche)
    parent = None
    if inp.parent_id:
        cible = _interne(inp.parent_id)
        _mien(ctx, cible)                 # on ne range pas chez quelqu'un d'autre
        parent = cible["id"]
    after = _interne(inp.after_id)["id"] if inp.after_id else None
    db_nodes.move_page(fiche["id"], parent_id=parent, after_id=after)
    return {"ok": True, "id": fiche["public_id"], "op": "move"}


def _delete(ctx: ResolvedCtx, inp: NodeEditInput) -> dict:
    fiche = _interne(_besoin(inp.node_id, "delete"))
    _mien(ctx, fiche)
    db_nodes.delete_page(fiche["id"])
    return {"ok": True, "id": fiche["public_id"], "op": "delete"}


def _besoin(node_id: Optional[str], op: str) -> str:
    if not (node_id or "").strip():
        raise AuthzDenied(400, "missing_node_id", f"`node_id` requis pour {op}.")
    return node_id


_OPS = {"create": _create, "update": _update, "move": _move, "delete": _delete}


async def edit(ctx: ResolvedCtx, inp: NodeEditInput) -> dict:
    """Hors boucle : plusieurs requêtes DB par appel, sur un serveur mono-loop."""
    return await run_in_threadpool(_OPS[inp.op], ctx, inp)


CAPABILITIES += [
    Capability(
        key="me.node.edit", handler=edit, Input=NodeEditInput, Output=NodeEditOut,
        # Plancher : être membre de l'org active. Le PALIER réel (plateforme, org,
        # équipe, soi) est tranché dans le handler par `guides._owner_for_write` —
        # le même modèle que les guides, jamais une seconde version.
        authz=ORG_MEMBER,
        description=(
            "Write a node in the NEW content universe (op=create | update | move | "
            "delete). THREE kinds, and only three: `page`, `tableau`, `ligne` "
            "(default page). A table row is a node too: create it with "
            "kind='ligne', `parent_id` of its table, and `data` holding the cell "
            "values — `data` is where user values live, never `props`, so a column "
            "named `title` cannot overwrite the node's own title. A table's column "
            "schema is `columns`, optional (a free table is a valid table), and "
            "re-posting it REPLACES it. Nodes written here are NATIVE: they have no "
            "source in the old world, nothing refreshes them, and the old surfaces "
            "(`oto_doc`, `oto_project`) do not see them — the two universes live "
            "side by side during the transition. A page BODY is stored as ordered "
            "blocks with STABLE ids, so editing a title never re-identifies the "
            "paragraphs and citations survive. `scope` picks the owner (platform | "
            "org | group | user, default org) and follows the SAME write ladder as "
            "guides; a row has no owner of its own, it takes its table's. `move` "
            "changes parent and rank WITHOUT changing identity — that is what makes "
            "children, blocks and inbound references survive. Editing a node that is "
            "a COPY of the old world is refused (409): it is edited on its own "
            "surface. PROVISIONAL surface, like its read side."),
        mcp="oto_node_edit",
        rest=RestBinding("POST", "/api/me/nodes/edit", provisoire=True),
    ),
]
