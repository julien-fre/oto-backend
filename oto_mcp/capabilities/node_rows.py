"""Les LIGNES d'un nœud-tableau — paginées par CURSEUR opaque.

Troisième surface de lecture précoce du modèle de nœuds. Elle n'invente aucun stockage :
c'est un habillage nœud du store existant (`DatastorePg.cursor_rows`), qui sait déjà
pousser filtre, recherche et tri en SQL, et qui porte les deux régimes de curseur.
Réécrire ce chemin aurait produit une seconde vérité sur le tri typé et les couches
pointées — celle qui diverge au premier correctif appliqué d'un seul côté.

**Curseur opaque, jamais d'offset dans le contrat** (0059-D5). L'offset/limit de l'API
actuelle est l'héritage, pas le modèle : toute surface NEUVE naît curseur. Le front s'y
attendait — c'est même la seule de ses divergences déclarées qui se résout sans que
personne ne bouge.

⚠️ **Le 404 couvre TROIS causes, et c'est délibéré** : nœud inexistant, nœud interdit,
et nœud qui n'est pas un tableau. Distinguer la troisième dirait « il existe, et c'est
une page » — donc renseignerait sur un contenu qu'on n'a pas le droit de voir.

⚠️ **La garde d'identité qui n'était pas demandée.** Un nœud-tableau désigne son
namespace par son TITRE, et le store résout un namespace par NOM dans le scope de
l'appelant. Deux tableaux homonymes dans deux scopes atteignables suffiraient donc à
servir les lignes d'un autre tableau que celui qu'on a ouvert — sans erreur, avec les
bonnes colonnes, et personne ne le verrait. On vérifie donc que le namespace résolu est
bien CELUI que le nœud désigne (`props.legacy_id`), et on refuse sinon.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel

from starlette.concurrency import run_in_threadpool

from .. import datastore as ds
from ..db import datastore as db_ds
from ..db import node_view as db_node
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding, cap_limit
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

_LIMITE_MAX = 200
_LIMITE_DEFAUT = 50


class NodeRowsInput(BaseModel):
    node_id: str
    q: Optional[str] = None
    sort: Optional[str] = None
    direction: Optional[Literal["asc", "desc"]] = None
    # `<clé>:<valeur>` par entrée — la forme du contrat front. Une chaîne unique est
    # acceptée aussi : une query string à un seul `?filter=` n'arrive pas en liste.
    filter: Optional[list[str] | str] = None
    cursor: Optional[str] = None
    limit: Optional[int] = None


class TableColumn(BaseModel):
    key: str
    title: str
    # Le SEUL indice d'affichage qu'un front ne peut pas deviner : une colonne de
    # nombres s'aligne à droite. Tout le reste (largeur, couleur, ordre) lui appartient.
    numeric: Optional[bool] = None


class TableRow(BaseModel):
    id: str
    # Valeurs indexées par clé de colonne, TOUTES en chaînes déjà rendues : le front ne
    # formate pas une donnée dont il ne connaît ni le type déclaré ni les sous-champs.
    cells: dict[str, str]


class RowsPage(BaseModel):
    columns: list[TableColumn]
    # Compte FILTRE APPLIQUÉ — c'est ce que le pied du tableau annonce. Un total non
    # filtré ferait dire « 3 sur 12 000 » à un écran qui en montre 3 sur 3.
    total: int
    items: list[TableRow]
    # Opaque, et `null` quand il n'y a plus rien. Le client le renvoie tel quel : sa
    # composition est à nous, et elle change selon le régime de tri.
    nextCursor: Optional[str] = None


def _introuvable() -> AuthzDenied:
    return AuthzDenied(404, "not_found",
                       "Aucun tableau de ce nom, ou aucun droit de le voir.")


def _filtres(valeur) -> list[dict]:
    """`["statut:clos", …]` → la forme du store. Une entrée sans `:` est IGNORÉE.

    Ignorer plutôt que refuser : le filtre vient d'une query string tapée par un
    humain autant que par le front, et faire échouer toute la page pour une entrée
    malformée coûte plus que de ne pas l'appliquer. Ce qui est appliqué reste visible
    dans la requête du client — il n'y a rien à deviner.
    """
    if not valeur:
        return []
    brut = [valeur] if isinstance(valeur, str) else list(valeur)
    out = []
    for entree in brut:
        cle, sep, val = str(entree).partition(":")
        if sep and cle.strip():
            out.append({"field": cle.strip(), "op": "eq", "value": val})
    return out


def _colonnes(schema: Optional[dict]) -> list[TableColumn]:
    """Les colonnes DANS L'ORDRE du schéma. Une table libre n'en déclare aucune.

    Elles voyagent avec CHAQUE page parce qu'elles décrivent la liste entière, pas la
    page : un front qui ne les recevrait qu'à la première page ne saurait plus rendre
    la seconde après un rechargement.
    """
    champs = ((schema or {}).get("fields") or []) if isinstance(schema, dict) else []
    out = []
    for f in champs:
        if not isinstance(f, dict) or not f.get("key"):
            continue
        out.append(TableColumn(
            key=str(f["key"]),
            title=str(f.get("label") or f.get("title") or f["key"]),
            numeric=True if f.get("type") in ("number", "integer", "float") else None))
    return out


def _cellules(ligne: dict, colonnes: list[TableColumn]) -> dict[str, str]:
    """Les valeurs d'une ligne, en CHAÎNES rendues, restreintes aux colonnes déclarées.

    Une table libre (aucune colonne déclarée) rend tous ses champs utilisateur : sinon
    l'écran serait vide pour 29 des 83 tableaux de production.
    """
    from ..share_ui import _cell

    if colonnes:
        cles = [c.key for c in colonnes]
    else:
        cles = [k for k in ligne if not k.startswith("_")]
    return {k: _cell(ligne.get(k)) for k in cles}


def _compose(ctx: ResolvedCtx, inp: NodeRowsInput) -> dict:
    fiche = db_node.node_by_public_id(inp.node_id)
    # Les trois causes, un seul refus (cf. l'entête).
    if not fiche or fiche.get("kind") != "tableau":
        raise _introuvable()

    props = fiche.get("props") or {}
    namespace = props.get("title") or ""
    store = ds.make_store(ctx.sub)
    try:
        # La résolution du store EST le contrôle d'accès : visible dans l'org active,
        # possédé ou accordé. On ne réécrit pas cette règle — une seconde définition
        # de « à portée » divergerait de la première.
        ns_id = store._resolve(namespace)
    except Exception:
        raise _introuvable()

    legacy = props.get("legacy_id")
    if legacy is not None and int(ns_id) != int(legacy):
        # Le nom a résolu ailleurs que le nœud ne désigne : deux homonymes dans deux
        # scopes atteignables. Servir cette page rendrait les lignes d'un AUTRE tableau,
        # avec les bonnes colonnes et sans la moindre erreur.
        logger.warning("node_rows: %s désigne ns %s, le nom a résolu ns %s",
                       inp.node_id, legacy, ns_id)
        raise _introuvable()

    filtres = _filtres(inp.filter)
    limite = cap_limit(inp.limit, _LIMITE_MAX, default=_LIMITE_DEFAUT)
    page = store.cursor_rows(namespace, q=inp.q, order_by=inp.sort,
                             order_dir=(inp.direction or "desc"),
                             filters=filtres or None, cursor=inp.cursor, limit=limite)
    colonnes = _colonnes(props.get("child_schema"))
    return {
        "columns": [c.model_dump(exclude_none=True) for c in colonnes],
        "total": db_ds.datastore_count_rows(ns_id, inp.q, filtres or None),
        "items": [TableRow(id=str(r.get("_id")),
                           cells=_cellules(r, colonnes)).model_dump()
                  for r in page.get("rows") or []],
        "nextCursor": page.get("next_cursor"),
    }


async def _node_rows(ctx: ResolvedCtx, inp: NodeRowsInput):
    """Hors boucle : requêtes DB en série sur un serveur mono-loop."""
    return await run_in_threadpool(_compose, ctx, inp)


CAPABILITIES += [
    Capability(
        key="me.node.rows", handler=_node_rows, Input=NodeRowsInput, Output=RowsPage,
        authz=ORG_MEMBER,
        description=(
            "One PAGE of rows of a table node, by OPAQUE cursor — never an offset. "
            "Send `cursor` back exactly as received; `nextCursor: null` means there is "
            "nothing left. `columns` travel with every page (they describe the whole "
            "list, not the page) and `total` is the count WITH filters applied — that "
            "is what a footer shows. Cells are strings already rendered by the server. "
            "Optional `q` (full text), `sort` + `direction` (a column KEY, never its "
            "title), `filter` as `key:value` entries. A node that does not exist, that "
            "you cannot see, or that is not a table all answer the SAME 404 — telling "
            "them apart would leak what a node is. PROVISIONAL surface."),
        mcp="oto_node_rows",
        rest=RestBinding("GET", "/api/me/nodes/{node_id}/rows", provisoire=True),
    ),
]
