"""Tableaux et lignes NATIFS — la face d'écriture du nouvel univers de contenu.

Un tableau natif ne délègue à aucun store : c'est un nœud `kind='tableau'` dont les
lignes sont ses enfants `kind='ligne'`. Le schéma de colonnes vit dans
`props.child_schema`, exactement comme sur les tableaux que la recopie produisait —
c'est ce qui permet à la face de lecture de servir les deux sans deux vocabulaires.

**Où va quoi, et c'est tout le lot** : les valeurs métier d'une ligne vont dans la
colonne `data`, jamais dans `props`. `props` porte ce qu'oto interprète (titre,
position, schéma d'enfants), `data` ce que l'utilisateur y met. Mêlés, une colonne de
tableau nommée `title` écraserait le titre du nœud — et la lecture devrait connaître
la liste des clés réservées pour faire le tri.

⚠️ **Ce module n'interprète JAMAIS une valeur.** Il range, il ordonne, il rend. Le
type d'une colonne, sa validation, son format : c'est la frontière du datastore
(« oto gère les types standards, jamais l'interprétation métier d'une valeur »), et la
franchir ici en produirait une seconde version, qui divergerait de la première au
premier correctif appliqué d'un seul côté.

⚠️ **Aucune écriture ici ne touche un nœud RECOPIÉ.** Les nœuds marqués
`props.legacy` sont l'image d'un autre monde, où leur source fait foi ; les écrire des
deux côtés ferait diverger les deux. Le refus est porté par la surface, et la garde de
dernier recours est ici : chaque écriture porte `props->>'legacy' IS NULL`.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Optional

from ._conn import _connect
from .nodes import POSITION_GAP

_KIND_TABLE = "tableau"
_KIND_ROW = "ligne"


def _new_node_id(prefixe: str = "nod") -> str:
    """L'identifiant public d'un nœud natif est un TIRAGE (0059-D3), pas une dérivée
    d'un identifiant d'ailleurs : rien de l'ancien monde n'entre dans cette clé."""
    return prefixe + "_" + secrets.token_hex(12)


def create_table(*, owner_type: str, owner_id: str, title: str,
                 columns: Optional[list[dict]] = None,
                 parent_id: Optional[int] = None) -> dict:
    """Un nœud-tableau neuf, avec son schéma de colonnes s'il en déclare un.

    Le schéma est FACULTATIF : 29 des 83 tableaux de production n'en déclaraient
    aucun (table libre). C'est d'ailleurs pourquoi le genre `tableau` existe — la
    présence d'un schéma ne peut pas discriminer ce qu'un objet est.

    ⚠️ **La clé de stockage est `fields`, pas `columns`.** C'est la forme que la face
    de lecture sait lire et celle du datastore ; écrire `columns` produirait un
    tableau qui s'affiche SANS AUCUNE COLONNE, sans la moindre erreur. La surface,
    elle, parle de colonnes — c'est le mot du contrat front, et la traduction se fait
    ici, à un seul endroit.
    """
    props: dict[str, Any] = {"title": title}
    if columns:
        props["child_schema"] = {"fields": columns}
    with _connect() as conn:
        with conn.transaction():
            position = _fin_de_fratrie(conn, parent_id, owner_type, owner_id)
            row = conn.execute(
                "INSERT INTO nodes (public_id, parent_id, position, kind, "
                "                   owner_type, owner_id, props) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) "
                "RETURNING id, public_id",
                (_new_node_id(), parent_id, position, _KIND_TABLE,
                 owner_type, owner_id, json.dumps(props))).fetchone()
    return dict(row)


def set_columns(table_id: int, columns: list[dict]) -> bool:
    """Repose le schéma de colonnes du tableau.

    ⚠️ **Une pose REMPLACE**, comme au datastore : c'est le geste que la surface
    expose, et le connaître évite d'y voir une fusion. Éditer une colonne sans
    toucher aux autres se fait en reposant la liste complète — la surface rend le
    schéma courant pour ça.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE nodes SET props = jsonb_set(props, '{child_schema}', %s::jsonb), "
            "                 updated_at = NOW() "
            " WHERE id = %s AND kind = %s AND props->>'legacy' IS NULL",
            (json.dumps({"fields": columns}), table_id, _KIND_TABLE))
        return cur.rowcount > 0


def _fin_de_fratrie(conn, parent_id: Optional[int], owner_type: str,
                    owner_id: str) -> int:
    """La position juste après le dernier frère — un intervalle, pas un compteur.

    Renuméroter 45 000 frères coûte 20 s, insérer dans l'intervalle 1,4 ms : l'ordre
    est un BIGINT espacé, et l'insertion prend le dernier plus un écart.
    """
    if parent_id is None:
        cur = conn.execute(
            "SELECT max(position) AS m FROM nodes "
            " WHERE parent_id IS NULL AND owner_type = %s AND owner_id = %s",
            (owner_type, owner_id))
    else:
        cur = conn.execute(
            "SELECT max(position) AS m FROM nodes WHERE parent_id = %s", (parent_id,))
    dernier = (cur.fetchone() or {}).get("m")
    return int(dernier or 0) + POSITION_GAP


def add_row(table_id: int, data: dict) -> Optional[dict]:
    """Une ligne au bout du tableau. Rend `None` si la cible n'est pas un tableau
    natif — inexistant, recopié ou d'un autre genre, la surface n'en dit qu'une chose.

    La ligne n'a **pas de propriétaire propre** (0054-D4) : elle a celui de son
    tableau, qu'elle recopie pour que les requêtes d'ownership n'aient pas à
    remonter l'arbre. C'est aussi pourquoi l'index d'ownership les exclut.
    """
    with _connect() as conn:
        with conn.transaction():
            table = conn.execute(
                "SELECT id, owner_type, owner_id FROM nodes "
                " WHERE id = %s AND kind = %s AND props->>'legacy' IS NULL",
                (table_id, _KIND_TABLE)).fetchone()
            if table is None:
                return None
            position = _fin_de_fratrie(conn, table_id, str(table["owner_type"]),
                                       str(table["owner_id"]))
            row = conn.execute(
                "INSERT INTO nodes (public_id, parent_id, position, kind, "
                "                   owner_type, owner_id, props, data) "
                "VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb, %s::jsonb) "
                "RETURNING id, public_id",
                (_new_node_id("row"), table_id, position, _KIND_ROW,
                 table["owner_type"], table["owner_id"],
                 json.dumps(data or {}))).fetchone()
    return dict(row)


def update_row(row_id: int, data: dict) -> bool:
    """Fusionne les clés fournies dans la donnée de la ligne.

    **Fusion et non remplacement** : le formulaire qui ne poste qu'une cellule ne doit
    pas effacer les autres. C'est la règle déjà tenue par l'écriture d'un credential,
    et pour la même raison — lecture partielle plus remplacement total est un piège à
    perte de données, vécu sur un pont client. Une clé présente et vide EFFACE,
    puisque c'est la seule façon d'exprimer « vide cette cellule ».
    """
    if not data:
        return False
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE nodes SET data = data || %s::jsonb, updated_at = NOW() "
            " WHERE id = %s AND kind = %s AND props->>'legacy' IS NULL",
            (json.dumps(data), row_id, _KIND_ROW))
        return cur.rowcount > 0


def delete_row(row_id: int) -> bool:
    """Une ligne n'a pas de descendance ni de corps : rien ne pend après elle."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM nodes WHERE id = %s AND kind = %s "
            "  AND props->>'legacy' IS NULL", (row_id, _KIND_ROW))
        return cur.rowcount > 0


def list_rows(table_id: int, *, limit: int, after_position: Optional[int] = None
              ) -> tuple[list[dict], Optional[int]]:
    """Les lignes d'un tableau natif, dans l'ordre de la fratrie.

    **Le curseur est une POSITION, pas un décalage** : intercaler une ligne pendant
    qu'on pagine ne décale pas la page suivante, alors qu'un `OFFSET` ferait sauter
    ou répéter une ligne. C'est la même règle qu'à la lecture des tableaux de
    l'ancien monde — le contrat de surface reste « curseur opaque ».

    Rend `(lignes, position_suivante)`. La position suivante est `None` quand la
    dernière page est atteinte.
    """
    params: list[Any] = [table_id]
    borne = ""
    if after_position is not None:
        borne = " AND position > %s"
        params.append(after_position)
    params.append(limit + 1)              # une de plus : elle DIT s'il y a une suite
    with _connect() as conn:
        lignes = [dict(r) for r in conn.execute(
            "SELECT id, public_id, position, data, created_at, updated_at "
            "  FROM nodes WHERE parent_id = %s AND kind = 'ligne'" + borne +
            " ORDER BY position ASC LIMIT %s", tuple(params)).fetchall()]
    if len(lignes) > limit:
        return lignes[:limit], int(lignes[limit - 1]["position"])
    return lignes, None


def count_rows(table_id: int) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM nodes WHERE parent_id = %s AND kind = 'ligne'",
            (table_id,)).fetchone()["n"]
