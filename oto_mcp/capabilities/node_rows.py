"""Les LIGNES d'un nœud-tableau — paginées par CURSEUR opaque.

Troisième surface de lecture précoce du modèle de nœuds. **Deux provenances, deux
chemins**, et c'est la marque de la recopie qui les sépare :

- un tableau **né ici** n'a aucun namespace à résoudre : ses lignes sont ses enfants,
  lues dans `nodes`. Le faire passer par le store chercherait un nom qui n'y existe
  pas et refuserait la lecture d'un tableau parfaitement lisible ;
- un tableau **recopié** de l'ancien monde reste servi par le store existant
  (`DatastorePg.cursor_rows`), qui sait déjà pousser filtre, recherche et tri en SQL.
  Réécrire ce chemin aurait produit une seconde vérité sur le tri typé et les couches
  pointées — celle qui diverge au premier correctif appliqué d'un seul côté. Il meurt
  avec le résidu de la recopie.

⚠️ Sur un tableau natif, filtre, recherche et tri sont **REFUSÉS, pas ignorés** : les
servir demanderait de fouiller la donnée métier, donc de l'interpréter — la frontière
qu'oto ne franchit pas. Les accepter en silence ferait croire à un filtre appliqué.

⚠️ **Et la même règle vaut sur le chemin recopié depuis le 2026-09-01 (#621)** : une
entrée de `filter` sans `:` y était ignorée, un curseur illisible y sortait en 500, et
le `total` s'y comptait sur les noms NON résolus alors que la page les résout. Trois
formes d'une seule faute — *répondre juste sur ce qu'on n'a pas regardé*. Les deux
provenances rendent désormais les MÊMES codes (`invalid_filter`, `invalid_cursor`),
parce que la provenance d'un tableau n'est servie à personne.

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

from ..datastore import core as ds
from ..datastore.errors import InvalidCursor
from ..db import node_tables as db_node_tables
from ..db import node_view as db_node
from ._authz import ORG_MEMBER
from ._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding,
                     cap_limit)
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


# UNE phrase et UN code pour les deux provenances (#621). Le chemin natif rendait
# `curseur_invalide` et le chemin recopié ne rattrapait rien : un front qui apprend à
# traiter l'un tombait sur l'autre au premier tableau de l'autre provenance — et la
# provenance ne lui est pas servie, il n'a donc aucun moyen de prévoir lequel il aura.
_CURSEUR_ILLISIBLE = (
    "`cursor` illisible — tronqué, périmé, ou repassé d'un régime de tri dans "
    "l'autre. Reprends la liste sans `cursor`.")


def _filtres(valeur) -> list[dict]:
    """`["statut:clos", …]` → la forme du store. Une entrée sans `:` est REFUSÉE.

    ⚠️ **Elle était IGNORÉE, et c'est corrigé le 2026-09-01 (#621).** Le motif écrit
    ici disait : « faire échouer toute la page pour une entrée malformée coûte plus que
    de ne pas l'appliquer ». Il compare le mauvais couple. Ce qui partait n'était pas
    une page en moins, c'était une page **non filtrée servie en 200** à un appelant qui
    avait demandé un filtre — il lisait un tableau entier en croyant lire un extrait, et
    rien dans la réponse ne le détrompait. Le refus, lui, se voit et se corrige.

    C'est le même geste que sur un tableau natif (`_natif`), où filtre, recherche et
    tri sont refusés plutôt qu'avalés : une seule règle sur toute la route.
    """
    if not valeur:
        return []
    brut = [valeur] if isinstance(valeur, str) else list(valeur)
    out = []
    for entree in brut:
        cle, sep, val = str(entree).partition(":")
        if not sep or not cle.strip():
            # Le refus NOMME la forme attendue ET l'entrée fautive : sans les deux,
            # l'appelant doit deviner laquelle de ses entrées corriger, et vers quoi.
            raise AuthzDenied(
                400, "invalid_filter",
                f"`filter` attend des entrées `colonne:valeur` — reçu {str(entree)!r}, "
                "qui ne désigne aucune colonne. Refusé plutôt qu'ignoré : la page "
                "servie ne serait pas filtrée, et rien ne le dirait.")
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


def _natif(inp: NodeRowsInput, fiche: dict, props: dict) -> dict:
    """Un tableau NÉ ICI : ses lignes sont ses enfants, aucun store n'est consulté.

    ⚠️ **Le filtre, la recherche et le tri sont REFUSÉS, pas ignorés.** Le store de
    l'ancien monde les pousse en SQL ; ici ils demanderaient de fouiller la donnée
    métier, donc de l'interpréter — la frontière qu'oto ne franchit pas. Les accepter
    en silence serait pire que les refuser : l'appelant croirait avoir filtré et
    lirait une page complète en pensant qu'elle est le résultat.

    Le curseur est une POSITION, pas un décalage : intercaler une ligne pendant
    qu'on pagine ne fait ni sauter ni répéter de ligne.
    """
    refuses = [n for n, v in (("q", inp.q), ("sort", inp.sort),
                              ("filter", inp.filter)) if v]
    if refuses:
        raise AuthzDenied(
            400, "non_supporte_sur_tableau_natif",
            "Sur un tableau né dans le nouvel univers, " + ", ".join(refuses) +
            " n'est pas encore servi — les lignes sortent dans l'ordre du tableau.")
    limite = cap_limit(inp.limit, _LIMITE_MAX, default=_LIMITE_DEFAUT)
    depuis = None
    if inp.cursor:
        try:
            depuis = int(inp.cursor)
        except ValueError:
            raise AuthzDenied(400, "invalid_cursor", _CURSEUR_ILLISIBLE)
    lignes, suivant = db_node_tables.list_rows(fiche["id"], limit=limite,
                                               after_position=depuis)
    colonnes = _colonnes(props.get("child_schema"))
    return {
        "columns": [c.model_dump(exclude_none=True) for c in colonnes],
        "total": db_node_tables.count_rows(fiche["id"]),
        "items": [TableRow(id=str(l["public_id"]),
                           cells=_cellules(l.get("data") or {}, colonnes)).model_dump()
                  for l in lignes],
        "nextCursor": str(suivant) if suivant is not None else None,
    }


def _compose(ctx: ResolvedCtx, inp: NodeRowsInput) -> dict:
    fiche = db_node.node_by_public_id(inp.node_id)
    # Les trois causes, un seul refus (cf. l'entête).
    if not fiche or fiche.get("kind") != "tableau":
        raise _introuvable()

    props = fiche.get("props") or {}
    # Deux provenances, deux chemins, et la marque de la recopie est ce qui les
    # sépare. Un tableau né ici n'a AUCUN namespace à résoudre : le faire passer par
    # le store chercherait un nom qui n'y existe pas, et refuserait la lecture d'un
    # tableau parfaitement lisible. Le second chemin meurt avec le résidu de la
    # recopie ; celui-ci reste.
    if props.get("legacy_id") is None:
        return _natif(inp, fiche, props)
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
    try:
        page = store.cursor_rows(namespace, q=inp.q, order_by=inp.sort,
                                 order_dir=(inp.direction or "desc"),
                                 filters=filtres or None, cursor=inp.cursor,
                                 limit=limite)
    except InvalidCursor:
        # ⚠️ Rattrapé ICI, sinon 500 (#621). L'adaptateur REST ne traduit que
        # `AuthzDenied` : tout le reste ressort en panne de serveur. Un curseur
        # tronqué par un copier-coller n'est pas une panne — c'est une demande
        # malformée, et le client n'a qu'une chose à faire, que le 500 ne dit pas.
        raise AuthzDenied(400, "invalid_cursor", _CURSEUR_ILLISIBLE)
    colonnes = _colonnes(props.get("child_schema"))
    return {
        "columns": [c.model_dump(exclude_none=True) for c in colonnes],
        # ⚠️ Le compte passe par le STORE, pas par la table (#621). `count_rows`
        # résout les noms plats comme la page le fait — `datastore_count_rows` bâtit
        # son `WHERE` sur les noms qu'on lui donne, et on lui donnait les noms NON
        # résolus. Sur un schéma à double service (`contact1_nom` servi en lecture
        # pour `contacts[0].nom`), le pied du tableau comptait donc un autre jeu que
        # celui qu'il coiffe, sans que rien n'échoue. Le store porte déjà la règle
        # (« le compte doit décrire le MÊME jeu que la page ») : la redire ici en
        # ferait une seconde vérité, qui divergerait au premier correctif appliqué
        # d'un seul côté.
        "total": store.count_rows(namespace, q=inp.q, filters=filtres or None),
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
        # Les trois refus que cette route rend en propre. Déclarés parce qu'un
        # consommateur qui reçoit un 400 doit savoir LEQUEL avant de l'écrire :
        # `invalid_filter` se corrige dans la requête, `invalid_cursor` se corrige en
        # repartant du début, et le troisième ne se corrige pas du tout sur ce
        # tableau-là. Trois gestes différents derrière un même statut.
        errors=(
            DeclaredError(400, "invalid_filter",
                          "une entrée de `filter` n'a pas la forme `colonne:valeur`"),
            DeclaredError(400, "invalid_cursor",
                          "`cursor` illisible, périmé, ou d'un autre régime de tri"),
            DeclaredError(400, "non_supporte_sur_tableau_natif",
                          "`q`, `sort` ou `filter` sur un tableau né dans la nouvelle "
                          "surface — refusés, jamais ignorés"),
        ),
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
