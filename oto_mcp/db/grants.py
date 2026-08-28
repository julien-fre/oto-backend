"""Le modèle d'accès par CHAÎNE de grants — SQL seul (blueprint ADR 0053).

Les tables `grants` / `grant_counters` sont posées par le lot L4 (`_schema.py`) ;
ce module est leur **premier lecteur/écrivain** (lot L5). Il ne porte AUCUNE
politique : qui est bénéficiaire, quelle contrainte s'applique, quel connecteur
est basculé — tout cela vit dans `oto_mcp/grants_chain.py`. Ici, des requêtes.

⚠️ **Ne pas confondre avec `connector_grants.py`** (comptes opérés, #55) ni avec
`resource_grants` (partage de CONTENU, ADR 0030/0048). `grants` porte le droit
d'UTILISER une ressource opérante, par une chaîne matérialisée qui ne fait que
resserrer en descendant.

**Chemin CHAUD, serveur MONO-LOOP.** Les deux lectures d'ici tombent sur chaque
appel d'un connecteur basculé : elles sont servies par l'index NON PARTIEL
`idx_grants_resource_grantee` (mesuré au banc L0 : 0,035 ms avec, 73,8 ms sans —
×2000, parce qu'une clé mutualisée est un *moyeu*). Toute requête ajoutée ici doit
porter `(resource_id, grantee_kind, grantee_id)` en tête, ou être hors chemin chaud.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional, Sequence

from ._conn import _connect

# Les colonnes projetées par les lectures de chaîne. `revoked_at` en fait partie et
# ce n'est pas cosmétique : la résolution DOIT distinguer « aucune arête » (la chaîne
# n'a pas d'avis → repli sur l'ancien chemin) de « arête révoquée » (la chaîne dit
# NON → l'accès est coupé). Les deux rendent zéro ligne vivante.
_GRANT_COLS = (
    "id, resource_kind, resource_id, grantor_kind, grantor_id, grantee_kind, "
    "grantee_id, constraints, parent_id, source, created_by, created_at, revoked_at")


def _grantee_clause(grantees: Sequence[tuple[str, str]]) -> tuple[str, list]:
    """`(kind, id) IN ((…),(…))` — un OR de couples, pas deux IN croisés (qui
    accorderaient à `user:X` ce qui n'est accordé qu'à `org:X`)."""
    parts, params = [], []
    for kind, ident in grantees:
        parts.append("(grantee_kind = %s AND grantee_id = %s)")
        params += [kind, str(ident)]
    return "(" + " OR ".join(parts) + ")", params


def edges_for(resource_id: str, grantees: Sequence[tuple[str, str]]) -> list[dict]:
    """Toutes les arêtes visant `resource_id` pour l'un des `grantees` — **révoquées
    COMPRISES** (l'appelant tranche : vivante ⟹ accès, uniquement révoquées ⟹ refus,
    aucune ⟹ la chaîne n'a pas d'avis). Ordre : vivantes d'abord, plus récentes
    d'abord — la résolution prend la première utile."""
    if not grantees:
        return []
    clause, params = _grantee_clause(grantees)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_GRANT_COLS} FROM grants WHERE resource_id = %s AND {clause} "
            "ORDER BY (revoked_at IS NULL) DESC, created_at DESC, id DESC",
            [resource_id] + params).fetchall()
    return [dict(r) for r in rows]


def edge_exists(resource_id: str, grantee_kind: str, grantee_id: str, conn=None) -> bool:
    """Une arête existe-t-elle DÉJÀ pour ce couple (révoquée comprise) ? Base de
    l'idempotence de la migration de boot : rejouer ne doit ni dupliquer une arête,
    ni **ressusciter** une arête révoquée (ce qui rendrait un accès retiré à la main).
    `conn` = connexion existante (la migration tourne DANS la transaction de schéma)."""
    sql = ("SELECT 1 FROM grants WHERE resource_id = %s AND grantee_kind = %s "
           "AND grantee_id = %s LIMIT 1")
    params = (resource_id, grantee_kind, str(grantee_id))
    if conn is not None:
        return conn.execute(sql, params).fetchone() is not None
    with _connect() as c:
        return c.execute(sql, params).fetchone() is not None


def insert_grant(*, resource_id: str, grantor_kind: str, grantor_id: str,
                 grantee_kind: str, grantee_id: str,
                 constraints: Optional[dict] = None,
                 resource_kind: str = "connector_instance",
                 parent_id: Optional[int] = None, source: str = "manual",
                 created_by: Optional[str] = None, conn=None) -> int:
    """Pose une arête et rend son id. `conn` = connexion existante (la migration de
    boot tourne DANS la transaction de schéma) ; None = connexion du pool."""
    sql = (
        "INSERT INTO grants (resource_kind, resource_id, grantor_kind, grantor_id, "
        "grantee_kind, grantee_id, constraints, parent_id, source, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING id")
    params = (resource_kind, resource_id, grantor_kind, str(grantor_id),
              grantee_kind, str(grantee_id), json.dumps(constraints or {}),
              parent_id, source, created_by)
    if conn is not None:
        return int(conn.execute(sql, params).fetchone()["id"])
    with _connect() as c:
        return int(c.execute(sql, params).fetchone()["id"])


def revoke_edges(resource_id: str, grantee_kind: str, grantee_id: str) -> int:
    """ARCHIVE (jamais ne supprime, 0053-D7) les arêtes vivantes de ce couple.
    Rend le nombre d'arêtes révoquées. La révocation se voit à la lecture suivante,
    sans rien propager : c'est l'argument du banc L0 contre tout cache."""
    with _connect() as conn:
        rows = conn.execute(
            "UPDATE grants SET revoked_at = NOW() WHERE resource_id = %s "
            "AND grantee_kind = %s AND grantee_id = %s AND revoked_at IS NULL "
            "RETURNING id",
            (resource_id, grantee_kind, str(grantee_id))).fetchall()
    return len(rows)


def bump_counter(grant_id: int, calls: int = 1) -> None:
    """Débite l'ARÊTE (0053-D7 : « l'arête porte la règle et les incréments »).
    Fenêtre = le jour (`window_start DATE`). Un seul UPSERT par PK — l'écriture la
    plus légère possible sur un chemin chaud mono-loop."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO grant_counters (grant_id, window_start, calls) "
            "VALUES (%s, CURRENT_DATE, %s) "
            "ON CONFLICT (grant_id, window_start) "
            "DO UPDATE SET calls = grant_counters.calls + EXCLUDED.calls",
            (grant_id, calls))


def counter_sum_today(resource_id: str, grantee_kind: str, grantee_id: str) -> int:
    """La lecture de quota de D7 : **sommer les arêtes** de la même (instance,
    bénéficiaire, fenêtre) — **archivées comprises**. Sans quoi une bascule de plan
    (D6 remplace le grant) remettrait la consommation à zéro sans que personne ne le
    voie, et multiplier les chemins d'accès doublerait le quota.

    ⚠️ C'est CETTE requête que l'index non partiel sert. Ne pas y ajouter
    `revoked_at IS NULL` : ce serait exactement l'erreur que le §4 de l'ADR
    interdit — et elle coûterait 74 ms par appel compté, en silence."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(c.calls), 0) AS calls FROM grants g "
            "JOIN grant_counters c ON c.grant_id = g.id "
            "WHERE g.resource_id = %s AND g.grantee_kind = %s AND g.grantee_id = %s "
            "AND c.window_start = CURRENT_DATE",
            (resource_id, grantee_kind, str(grantee_id))).fetchone()
    return int(row["calls"]) if row else 0


def live_edges_for_grantee(grantee_kind: str, grantee_id: str,
                           resource_prefix: Optional[str] = None) -> list[dict]:
    """Les arêtes VIVANTES d'un bénéficiaire (surface d'affichage : « quelles
    instances me sont accordées »). Part du bénéficiaire ⟹ servie par
    `idx_grants_grantee` (partiel `revoked_at IS NULL`) — le sens bon marché."""
    sql = (f"SELECT {_GRANT_COLS} FROM grants WHERE grantee_kind = %s AND grantee_id = %s "
           "AND revoked_at IS NULL")
    params: list = [grantee_kind, str(grantee_id)]
    if resource_prefix:
        sql += " AND resource_id LIKE %s"
        params.append(resource_prefix + "%")
    sql += " ORDER BY created_at DESC, id DESC"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def live_grantees_for_resource(resource_id: str) -> list[str]:
    """Les scopes (`user:<sub>` / `org:<id>` / …) que les arêtes VIVANTES visant cette
    ressource accordent — le sens INVERSE de `live_edges_for_grantee`.

    Sert la visibilité dérivée (R9) : « qui peut la résoudre » se lit en partant de
    l'instance, pas du bénéficiaire. Servi par `idx_grants_resource_grantee` — l'index
    NON PARTIEL du comptage, qui porte `resource_id` en tête ; c'est le même parcours
    qu'une lecture de quota, avec un prédicat de plus. Hors chemin chaud (une
    projection de listing), et jamais appelé pour un connecteur non basculé.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT grantee_kind, grantee_id FROM grants "
            "WHERE resource_id = %s AND revoked_at IS NULL "
            "ORDER BY grantee_kind, grantee_id", (resource_id,)).fetchall()
    return [f"{r['grantee_kind']}:{r['grantee_id']}" for r in rows]


def resource_ids_with_edges(resource_ids: Iterable[str]) -> set[str]:
    """Parmi `resource_ids`, ceux qui portent au moins une arête (révoquée comprise).
    Sert les surfaces d'inventaire ; hors chemin chaud."""
    ids = [r for r in resource_ids]
    if not ids:
        return set()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT resource_id FROM grants WHERE resource_id = ANY(%s)",
            (ids,)).fetchall()
    return {r["resource_id"] for r in rows}
