"""Datastore spine PG (ADR 0016) : namespaces, lignes JSONB, resource grants (ADR 0030).

Extrait de l'ex-monolithe `db.py` (barreau final). Fonctions de domaine — la
plomberie est dans `_conn`. Ré-exporté par `db/__init__`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any, Iterator, Optional

import psycopg

logger = logging.getLogger(__name__)

from ..datastore_schema import LAYER_KEYS, VALUE_LAYER
# Chemins et feuilles : extraits dans `paths` (#325), ré-exportés ici pour que la
# surface plate `db.<fn>` et tous les appelants restent inchangés.
from .paths import (  # noqa: F401
    FIELD_VALUE_PARAM_SQL,
    LAYER_VALUE_PARAM_SQL,
    ROW_VALUES_TEXT_SQL,
    bkey_index_expr,
    field_read_sql,
    field_value_sql,
    leaf_read_sql,
    split_layer,
    split_list_path,
)
from ._conn import _connect, _connect_autocommit
from .users import upsert_user


def create_datastore_namespace(owner_type: str, owner_id: str, namespace: str) -> int:
    """Crée un namespace possédé par `(owner_type, owner_id)` (ADR 0030). `owner_type`
    ∈ {user, org, group} ; `owner_id` = sub | org.id::text | group.id::text. Lève si
    le même propriétaire a déjà ce nom."""
    if owner_type == "user":
        upsert_user(owner_id)
    with _connect() as conn:
        try:
            row = conn.execute(
                "INSERT INTO user_datastores (owner_type, owner_id, namespace) "
                "VALUES (%s, %s, %s) RETURNING id",
                (owner_type, owner_id, namespace),
            ).fetchone()
        except psycopg.errors.UniqueViolation as e:
            raise ValueError(f"namespace `{namespace}` existe déjà") from e
        return int(row["id"])


def get_datastore_namespace(owner_type: str, owner_id: str, namespace: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, owner_type, owner_id, namespace, created_at FROM user_datastores "
            "WHERE owner_type = %s AND owner_id = %s AND namespace = %s",
            (owner_type, owner_id, namespace),
        ).fetchone()
        return dict(row) if row else None


def get_datastore_namespace_by_id(ns_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, owner_type, owner_id, namespace, schema, created_at "
            "FROM user_datastores WHERE id = %s",
            (ns_id,),
        ).fetchone()
        return dict(row) if row else None


def set_datastore_schema(ns_id: int, schema: Optional[dict]) -> None:
    """Pose (ou retire si None) le schéma typé d'un namespace (ADR 0032 §6 / 0029, B6).
    Soft : aucune validation des rows existantes — c'est un schéma de rendu, pas une
    contrainte d'écriture."""
    cfg = json.dumps(schema) if schema is not None else None
    with _connect() as conn:
        conn.execute("UPDATE user_datastores SET schema = %s::jsonb WHERE id = %s",
                     (cfg, ns_id))


def set_datastore_semantic(ns_id: int, enabled: bool) -> int:
    """Active/désactive la recherche SÉMANTIQUE d'un namespace (#67 V2.2, opt-in). À
    l'ACTIVATION, marque toutes ses rows dirty (le worker les indexe) et renvoie leur
    nombre ; à la DÉSACTIVATION, purge les embeddings + lève le dirty (renvoie 0)."""
    with _connect() as conn:
        conn.execute("UPDATE user_datastores SET semantic_search = %s WHERE id = %s",
                     (enabled, ns_id))
        if enabled:
            return conn.execute(
                "UPDATE datastore_rows SET embed_dirty = TRUE WHERE ns_id = %s",
                (ns_id,)).rowcount or 0
        conn.execute("DELETE FROM datastore_row_embeddings WHERE ns_id = %s", (ns_id,))
        conn.execute("UPDATE datastore_rows SET embed_dirty = FALSE "
                     "WHERE ns_id = %s AND embed_dirty", (ns_id,))
        return 0


def list_datastore_namespaces_for_owners(owners: list[tuple[str, str]]) -> list[dict]:
    """Namespaces possédés par l'un des `(owner_type, owner_id)` fournis."""
    if not owners:
        return []
    otypes = [o[0] for o in owners]
    oids = [o[1] for o in owners]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT d.id, d.owner_type, d.owner_id, d.namespace, d.schema, d.created_at "
            "FROM user_datastores d "
            "JOIN unnest(%s::text[], %s::text[]) AS o(t, i) "
            "  ON d.owner_type = o.t AND d.owner_id = o.i "
            "ORDER BY d.namespace",
            (otypes, oids),
        ).fetchall()
        return [dict(r) for r in rows]


def resolve_datastore_ns(
    namespace: str, *, sub: str, org_ids: list[int], group_ids: list[int],
) -> Optional[dict]:
    """Résout un namespace VISIBLE par l'acteur, par NOM **ou par ID** numérique, parmi :
    possédé en perso, possédé par une de ses orgs, ou accordé (grant user/org/group).
    Priorité perso > org > grant. Retourne la ligne `user_datastores` (avec `id`) ou None.
    La décision read/write fine est ensuite faite par `ownership.can_access` sur l'id.

    ⚠️ **id OU nom** : un lien de projet stocke souvent le `target_ref` = **id numérique**
    (le picker dashboard, `EntityPickerDialog`) alors que l'agent lie par **nom** — les deux
    doivent résoudre (sinon l'aperçu tableau tombait en 404 → « Aperçu indisponible »). Le
    prédicat de VISIBILITÉ est identique quelle que soit la clé (aucun IDOR : un id hors de
    la portée de l'acteur ne résout pas). Collision improbable (un namespace nommé « 109 »
    vs un id 109) → le match par NOM est préféré."""
    org_txt = [str(o) for o in org_ids]
    grp_txt = [str(g) for g in group_ids]
    ns_id = int(namespace) if str(namespace).isdigit() else None
    with _connect() as conn:
        row = conn.execute(
            "SELECT d.id, d.owner_type, d.owner_id, d.namespace, d.schema, d.created_at "
            "FROM user_datastores d "
            "WHERE (d.namespace = %(ns)s OR d.id = %(nsid)s) AND ("
            "     (d.owner_type = 'user' AND d.owner_id = %(sub)s)"
            "  OR (d.owner_type = 'org'  AND d.owner_id = ANY(%(org)s))"
            # ADR 0049 (cadrage 10/07) : team-owned = visible dans le contexte de l'org
            # parente (le caller passe mes équipes — ou toutes celles de l'org si admin).
            "  OR (d.owner_type = 'group' AND d.owner_id = ANY(%(grp)s))"
            "  OR EXISTS ("
            "       SELECT 1 FROM resource_grants g"
            "        WHERE g.resource_type = 'datastore_namespace' AND g.resource_id = d.id::text"
            "          AND ( (g.principal_type = 'user'  AND g.principal_id = %(sub)s)"
            "             OR (g.principal_type = 'org'   AND g.principal_id = ANY(%(org)s))"
            "             OR (g.principal_type = 'group' AND g.principal_id = ANY(%(grp)s)) ))"
            ") "
            "ORDER BY CASE WHEN d.namespace = %(ns)s THEN 0 ELSE 1 END, "
            "         CASE WHEN d.owner_type='user' AND d.owner_id=%(sub)s THEN 0 "
            "              WHEN d.owner_type='org' THEN 1 ELSE 2 END "
            "LIMIT 1",
            {"ns": namespace, "nsid": ns_id, "sub": sub, "org": org_txt, "grp": grp_txt},
        ).fetchone()
        return dict(row) if row else None


def list_datastore_namespaces_granted_to(
    sub: str, org_ids: list[int], group_ids: list[int],
) -> list[dict]:
    """Namespaces accordés à l'**org active / groupe actif** via `resource_grants`
    (principal org/group), avec la permission gagnante.

    Volontairement **PAS** les grants `principal_type='user'` : un partage *en propre*
    (cross-org, ex. un namespace de ton org perso partagé à ton compte) ne doit pas
    polluer la vue Données de CHAQUE org — l'org est le contexte (ADR 0023, scope décidé
    avec l'utilisateur le 2026-07-01). La résolution par nom (`resolve_datastore_ns`) est
    elle aussi scopée à l'org active côté appelant (2026-07-03). `sub` ne sert plus qu'à
    exclure les reliques perso possédées (gérées à part)."""
    org_txt = [str(o) for o in org_ids]
    grp_txt = [str(g) for g in group_ids]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT d.id, d.owner_type, d.owner_id, d.namespace, d.created_at, "
            "       max(g.permission) AS permission "
            "FROM resource_grants g "
            "JOIN user_datastores d ON d.id::text = g.resource_id "
            "WHERE g.resource_type = 'datastore_namespace' AND ("
            "     (g.principal_type = 'org'   AND g.principal_id = ANY(%(org)s))"
            "  OR (g.principal_type = 'group' AND g.principal_id = ANY(%(grp)s)) ) "
            "AND NOT (d.owner_type = 'user' AND d.owner_id = %(sub)s) "
            "GROUP BY d.id, d.owner_type, d.owner_id, d.namespace, d.created_at "
            "ORDER BY d.namespace",
            {"sub": sub, "org": org_txt, "grp": grp_txt},
        ).fetchall()
        return [dict(r) for r in rows]


def rename_datastore_namespace_by_id(ns_id: int, new: str) -> bool:
    """Renomme un namespace par id (l'id BIGSERIAL est conservé → URL/deeplink/grants
    stables ; les grants sont keyés par id, donc rien à propager). Lève si le même
    propriétaire a déjà ce nom, ou si l'id est introuvable."""
    new = (new or "").strip()
    if not new:
        raise ValueError("nouveau nom de namespace requis")
    with _connect() as conn:
        with conn.transaction():
            cur = conn.execute(
                "SELECT owner_type, owner_id, namespace FROM user_datastores WHERE id = %s FOR UPDATE",
                (ns_id,),
            ).fetchone()
            if not cur:
                raise ValueError("namespace introuvable")
            if cur["namespace"] == new:
                return True
            if conn.execute(
                "SELECT 1 FROM user_datastores WHERE owner_type = %s AND owner_id = %s AND namespace = %s",
                (cur["owner_type"], cur["owner_id"], new),
            ).fetchone():
                raise ValueError(f"un namespace `{new}` existe déjà")
            conn.execute(
                "UPDATE user_datastores SET namespace = %s WHERE id = %s", (new, ns_id),
            )
    return True


def delete_datastore_namespace_by_id(ns_id: int) -> bool:
    """Supprime un namespace par id (CASCADE sur `datastore_rows`) + ses grants
    (`resource_grants` n'a pas de FK car `resource_id` est générique) + son
    éventuel index de clé métier (#109 ch.3 — orphelin inoffensif sinon, mais
    autant nettoyer)."""
    with _connect() as conn:
        with conn.transaction():
            conn.execute(
                "DELETE FROM resource_grants WHERE resource_type = 'datastore_namespace' AND resource_id = %s",
                (str(ns_id),),
            )
            cur = conn.execute("DELETE FROM user_datastores WHERE id = %s", (ns_id,))
    datastore_drop_key_index(ns_id)
    return cur.rowcount > 0


def reparent_datastore_namespace(ns_id: int, new_owner_type: str, new_owner_id: str) -> None:
    """Re-parente un namespace vers un nouveau propriétaire (cœur du transfert).
    Lève si le destinataire possède déjà un namespace de ce nom."""
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT namespace FROM user_datastores WHERE id = %s FOR UPDATE", (ns_id,),
            ).fetchone()
            if not row:
                raise ValueError("namespace introuvable")
            if conn.execute(
                "SELECT 1 FROM user_datastores WHERE owner_type = %s AND owner_id = %s AND namespace = %s",
                (new_owner_type, new_owner_id, row["namespace"]),
            ).fetchone():
                raise ValueError(f"le destinataire possède déjà un namespace `{row['namespace']}`")
            conn.execute(
                "UPDATE user_datastores SET owner_type = %s, owner_id = %s WHERE id = %s",
                (new_owner_type, new_owner_id, ns_id),
            )


def list_all_datastore_namespaces() -> list[dict]:
    """Tous les namespaces, toutes propriétés confondues — pour l'object-browser
    PLATEFORME (gate super_admin/platform_admin côté capacité)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, owner_type, owner_id, namespace, created_at "
            "FROM user_datastores ORDER BY owner_type, owner_id, namespace",
        ).fetchall()
        return [dict(r) for r in rows]


def count_datastore_rows_for_ns(ns_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM datastore_rows WHERE ns_id = %s", (ns_id,),
        ).fetchone()
        return int(row["n"]) if row else 0


# ADR 0048 — le rôle est la source de vérité ; `permission` (plan CONTENU) en dérive.
_ROLE_TO_PERMISSION = {"viewer": "read", "editor": "write", "manager": "write"}
_PERMISSION_TO_ROLE = {"read": "viewer", "write": "editor"}


def _normalize_role(role: Optional[str], permission: Optional[str]) -> str:
    """Rôle effectif d'un grant. `role` prime ; sinon rétro-compat depuis `permission`
    (read→viewer, write→editor) ; défaut `editor`."""
    if role in _ROLE_TO_PERMISSION:
        return role
    return _PERMISSION_TO_ROLE.get(permission or "", "editor")


def grant_resource(
    resource_type: str, resource_id: str, principal_type: str, principal_id: str,
    permission: Optional[str] = None, granted_by: Optional[str] = None,
    role: Optional[str] = None,
) -> None:
    """Accorde (ou met à jour) un RÔLE à un principal sur une ressource (ADR 0048).
    `role` ∈ {viewer, editor, manager} prime ; à défaut `permission` read/write est mappé
    (rétro-compat). `permission` (plan CONTENU) est TOUJOURS dérivée du rôle (viewer→read,
    editor/manager→write) → tout le SQL du plan contenu reste inchangé. Idempotent :
    ON CONFLICT met à jour rôle + permission."""
    eff_role = _normalize_role(role, permission)
    eff_perm = _ROLE_TO_PERMISSION[eff_role]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO resource_grants "
            "(resource_type, resource_id, principal_type, principal_id, permission, role, granted_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (resource_type, resource_id, principal_type, principal_id) "
            "DO UPDATE SET permission = EXCLUDED.permission, role = EXCLUDED.role, "
            "granted_by = EXCLUDED.granted_by",
            (resource_type, resource_id, principal_type, principal_id,
             eff_perm, eff_role, granted_by),
        )


def revoke_resource_grant(
    resource_type: str, resource_id: str, principal_type: str, principal_id: str,
) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM resource_grants WHERE resource_type = %s AND resource_id = %s "
            "AND principal_type = %s AND principal_id = %s",
            (resource_type, resource_id, principal_type, principal_id),
        )
        return cur.rowcount > 0


def get_resource_grant(
    resource_type: str, resource_id: str, principal_type: str, principal_id: str,
) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT permission, role FROM resource_grants WHERE resource_type = %s AND resource_id = %s "
            "AND principal_type = %s AND principal_id = %s",
            (resource_type, resource_id, principal_type, principal_id),
        ).fetchone()
        return dict(row) if row else None


def list_resource_grants(resource_type: str, resource_id: str) -> list[dict]:
    """Bénéficiaires d'une ressource (principal + permission + email si user), pour
    l'UI de gestion du partage."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT g.principal_type, g.principal_id, g.permission, g.role, g.granted_at, u.email "
            "FROM resource_grants g "
            "LEFT JOIN users u ON g.principal_type = 'user' AND u.sub = g.principal_id "
            "WHERE g.resource_type = %s AND g.resource_id = %s "
            "ORDER BY g.granted_at",
            (resource_type, resource_id),
        ).fetchall()
        return [dict(r) for r in rows]


def datastore_insert_row(ns_id: int, row_id: str, data: dict,
                         created_at: Optional[str] = None,
                         updated_at: Optional[str] = None) -> dict:
    """Insère une row. `created_at`/`updated_at` optionnels (override pour le
    backfill ; sinon NOW())."""
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO datastore_rows (ns_id, row_id, data, created_at, updated_at, embed_dirty) "
            "VALUES (%s, %s, %s::jsonb, COALESCE(%s::timestamptz, NOW()), COALESCE(%s::timestamptz, NOW()), "
            # dirty ⟺ le namespace est opt-in sémantique (#67 V2.2) — sinon jamais embedé.
            "        (SELECT semantic_search FROM user_datastores WHERE id = %s)) "
            "RETURNING row_id, created_at, updated_at, data",
            (ns_id, row_id, json.dumps(data), created_at, updated_at, ns_id),
        ).fetchone()
        from .search import stamp_rank_vector
        stamp_rank_vector(conn, "datastore_rows", "ns_id = %s AND row_id = %s", (ns_id, row_id))
        return dict(row)


def datastore_upsert_row(ns_id: int, row_id: str, data: dict) -> tuple[dict, bool]:
    """Insère OU met à jour une row par sa clé `(ns_id, row_id)`. Idempotent :
    re-poser le même `row_id` remplace `data` au lieu de dupliquer (sert la
    dédup par clé stable, ex. urn LinkedIn). Renvoie `(row, inserted)` où
    `inserted` est True si la row n'existait pas (ON CONFLICT non déclenché)."""
    with _connect() as conn:
        row = conn.execute(
            "INSERT INTO datastore_rows (ns_id, row_id, data, created_at, updated_at, embed_dirty) "
            "VALUES (%s, %s, %s::jsonb, NOW(), NOW(), "
            "        (SELECT semantic_search FROM user_datastores WHERE id = %s)) "
            # data change ⟹ re-dirty ⟺ namespace opt-in sémantique (#67 V2.2).
            "ON CONFLICT (ns_id, row_id) DO UPDATE SET data = EXCLUDED.data, updated_at = NOW(), "
            "  embed_dirty = (SELECT semantic_search FROM user_datastores WHERE id = datastore_rows.ns_id) "
            "RETURNING row_id, created_at, updated_at, data, (xmax = 0) AS inserted",
            (ns_id, row_id, json.dumps(data), ns_id),
        ).fetchone()
        from .search import stamp_rank_vector
        stamp_rank_vector(conn, "datastore_rows", "ns_id = %s AND row_id = %s", (ns_id, row_id))

        inserted = bool(row.pop("inserted"))
        return dict(row), inserted


def datastore_find_row_id_by_key(ns_id: int, key_field: str, key_value) -> Optional[str]:
    """Trouve le `row_id` d'une row par une CLÉ MÉTIER, pour la dédup d'un batch
    write. Renvoie le plus ancien match (ordre stable) ou None.

    ⚠️ L'expression vient de `bkey_index_expr` — LA MÊME que celle de l'index, à la
    chaîne près. Le planner ne sert un index d'EXPRESSION que si le `WHERE` porte
    exactement la sienne : un écart ne casserait rien de visible (la déduplication
    marcherait) et ferait simplement partir chaque lookup en seq scan. C'est la panne
    qu'on ne voit qu'au moment où le namespace est assez gros pour qu'elle coûte."""
    from psycopg import sql as _sql
    q = _sql.SQL(
        "SELECT row_id FROM datastore_rows WHERE ns_id = %s AND {e} = %s "
        "ORDER BY created_at ASC LIMIT 1"
    ).format(e=bkey_index_expr(key_field))
    with _connect() as conn:
        row = conn.execute(q, (ns_id, str(key_value))).fetchone()
        return row["row_id"] if row else None


# ── Clé métier = contrainte (#109 ch.3) ──────────────────────────────────────
# Quand `schema.key` est déclarée, elle cesse d'être purement applicative : un
# index UNIQUE PARTIEL par namespace (`ds_bkey_<ns_id>`, expression `data->>key`,
# prédicat ns_id + clé non nulle) rend la dédup concurrent-safe (deux writes
# parallèles du même member_id ⇒ le perdant prend une UniqueViolation, convertie
# en update par le store) et le lookup indexé. Cycle de vie : posé/déposé par
# `set_schema` (source unique de schema.key) + migration boot pour l'existant.

def _bkey_index_name(ns_id: int) -> str:
    return f"ds_bkey_{int(ns_id)}"


def datastore_key_dup_groups(ns_id: int, key: str, limit: int = 10) -> list[dict]:
    """Valeurs de clé métier en DOUBLON dans les rows existantes — `[{value, n}]`,
    plus gros groupes d'abord. Sert le refus actionnable de `set_schema` (on ne
    pose pas un UNIQUE sur des données sales sans le dire)."""
    from psycopg import sql as _sql
    q = _sql.SQL(
        "SELECT data->>{k} AS value, COUNT(*) AS n FROM datastore_rows "
        "WHERE ns_id = %s AND data->>{k} IS NOT NULL "
        "GROUP BY 1 HAVING COUNT(*) > 1 ORDER BY n DESC, 1 LIMIT %s"
    ).format(k=_sql.Literal(str(key)))
    with _connect() as conn:
        return [dict(r) for r in conn.execute(q, (ns_id, limit)).fetchall()]


def datastore_overlong_fields(ns_id: int, bounds: dict) -> list[dict]:
    """Champs dont des rows DÉJÀ EN BASE dépassent la borne `max_length` posée —
    `[{field, max_length, rows, longest}]`, pire dépassement d'abord.

    Sert l'avertissement (pas le refus) de `set_schema` : borner un champ après
    coup est légitime, mais celui qui pose la borne doit savoir ce que l'historique
    contient — ces lignes-là ne seront refusées qu'au geste qui les réécrit."""
    from psycopg import sql as _sql
    out: list[dict] = []
    with _connect() as conn:
        for field, ml in (bounds or {}).items():
            q = _sql.SQL(
                "SELECT COUNT(*) AS rows, MAX(length({v})) AS longest "
                "FROM datastore_rows WHERE ns_id = %s AND length({v}) > %s"
            ).format(v=field_value_sql(field))
            r = conn.execute(q, (ns_id, int(ml))).fetchone()
            if r and (r["rows"] or 0) > 0:
                out.append({"field": field, "max_length": int(ml),
                            "rows": int(r["rows"]), "longest": int(r["longest"])})
    return sorted(out, key=lambda d: d["longest"] - d["max_length"], reverse=True)



def datastore_offending_enum_values(ns_id: int, options: dict,
                                    per_field: int = 5) -> list[dict]:
    """Valeurs DÉJÀ EN BASE qu'un enum fraîchement déclaré condamne —
    `[{field, values: [{value, rows}], rows}]`, le plus atteint d'abord.

    Un schéma ne vaut que pour l'AVENIR : le poser ne revalide pas l'existant. Une
    colonne peut donc être pleine de valeurs que le format refuse désormais, sans
    que rien ne le dise — et le tableau *a l'air* conforme puisqu'il a un schéma.
    Vécu : 504 lignes en « Oui »/« Non » sur un enum `oui`/`non`/`inconnu`, valeurs
    présentes à l'écran et invisibles au filtrage comme aux facettes.

    On rend les valeurs FAUTIVES avec leur compte, pas un simple total : c'est ce
    qui permet de trancher tout de suite entre corriger la donnée et élargir les
    options. Les vides (`NULL`, chaîne vide) sont écartés — une case non remplie
    n'est pas une valeur hors options, c'est l'affaire de `required`."""
    from psycopg import sql as _sql
    out: list[dict] = []
    with _connect() as conn:
        for field, allowed in (options or {}).items():
            vals = [str(o) for o in (allowed or [])]
            if not vals:
                continue  # enum libre : aucune option déclarée, rien à condamner
            q = _sql.SQL(
                "SELECT {v} AS value, COUNT(*) AS rows "
                "FROM datastore_rows WHERE ns_id = %s "
                "AND {v} IS NOT NULL AND {v} <> '' "
                "AND NOT ({v} = ANY(%s)) "
                "GROUP BY 1 ORDER BY 2 DESC"
            ).format(v=field_value_sql(field))
            rows = conn.execute(q, (ns_id, vals)).fetchall()
            if not rows:
                continue
            out.append({
                "field": field,
                "rows": sum(int(r["rows"]) for r in rows),
                "distinct": len(rows),
                "values": [{"value": r["value"], "rows": int(r["rows"])}
                           for r in rows[:per_field]],
            })
    return sorted(out, key=lambda d: d["rows"], reverse=True)


def datastore_drop_column(ns_id: int, key: str) -> int:
    """Retire la clé `key` du blob `data` de TOUTES les rows du namespace. Renvoie le
    nombre de rows modifiées (0 = la colonne n'existait dans aucune).

    L'opérateur JSONB `-` retire la clé, là où l'écrire à `null` la CONSERVE (une
    clé de valeur nulle reste une clé : elle continue de se rendre, et de tromper).
    Le `WHERE data ? key` borne l'UPDATE aux rows concernées — sur un namespace où
    la colonne est rare, on ne réécrit pas les autres pour rien.

    ⚠️ NON sérialisé avec les écritures applicatives : celles-ci font un
    read-merge-write du blob entier (`_merge_into_row`), donc un write dont le
    SELECT précède cette purge et l'UPDATE la suit REMET la clé sur sa ligne.
    Fenêtre étroite, effet re-purgeable — purger hors drainage, ou repasser."""
    from psycopg import sql as _sql
    q = _sql.SQL(
        "UPDATE datastore_rows SET data = data - {k} "
        " WHERE ns_id = %s AND data ? {k}"
    ).format(k=_sql.Literal(str(key)))
    with _connect() as conn:
        return conn.execute(q, (ns_id,)).rowcount or 0


def datastore_row_keys(ns_id: int, sample: int = 1000) -> list[str]:
    """Clés présentes dans les DONNÉES d'un namespace, triées.

    Bornée à un ÉCHANTILLON (`sample` rows les plus récentes) : l'usage est de
    signaler des colonnes que le schéma ne déclare plus, et celles-là sont sur
    toutes les lignes ou presque. Scanner un namespace de 500 000 rows pour un
    geste de confort (poser un schéma) coûterait plus que ça ne rapporte — au prix
    assumé qu'une clé présente sur une poignée de lignes anciennes puisse échapper
    au relevé."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT jsonb_object_keys(data) AS k FROM ("
            "  SELECT data FROM datastore_rows WHERE ns_id = %s "
            "   ORDER BY created_at DESC, row_id DESC LIMIT %s) t",
            (ns_id, int(sample)),
        ).fetchall()
    return sorted(r["k"] for r in rows)


def datastore_merge_key_duplicates(ns_id: int, key: str) -> int:
    """Résorbe les doublons de clé métier en reconstituant la sémantique upsert :
    pour chaque valeur en doublon, MERGE les `data` dans l'ordre chronologique dans
    la row la plus ANCIENNE (celle que `find_row_id_by_key` aurait servie à chaque
    write), puis supprime les plus récentes. Renvoie le nombre de rows supprimées.
    Une transaction par groupe (échec isolé, jamais de demi-merge)."""
    from psycopg import sql as _sql
    key = str(key)
    removed = 0
    dup_q = _sql.SQL(
        "SELECT data->>{k} AS value FROM datastore_rows "
        "WHERE ns_id = %s AND data->>{k} IS NOT NULL GROUP BY 1 HAVING COUNT(*) > 1"
    ).format(k=_sql.Literal(key))
    rows_q = _sql.SQL(
        "SELECT row_id, data FROM datastore_rows WHERE ns_id = %s AND data->>{k} = %s "
        "ORDER BY created_at ASC, row_id ASC"
    ).format(k=_sql.Literal(key))
    with _connect() as conn:
        values = [r["value"] for r in conn.execute(dup_q, (ns_id,)).fetchall()]
    for value in values:
        with _connect() as conn:
            group = conn.execute(rows_q, (ns_id, value)).fetchall()
            if len(group) < 2:
                continue  # résorbé entre-temps
            merged: dict = {}
            for r in group:
                d = r["data"]
                merged.update(d if isinstance(d, dict) else json.loads(d))
            keeper = group[0]["row_id"]
            losers = [r["row_id"] for r in group[1:]]
            conn.execute(
                "UPDATE datastore_rows SET data = %s::jsonb, updated_at = NOW() "
                "WHERE ns_id = %s AND row_id = %s",
                (json.dumps(merged), ns_id, keeper))
            conn.execute(
                "DELETE FROM datastore_rows WHERE ns_id = %s AND row_id = ANY(%s)",
                (ns_id, losers))
            removed += len(losers)
    return removed


def datastore_ensure_key_index(ns_id: int, key: str) -> None:
    """Pose l'index UNIQUE partiel de clé métier du namespace (dépose l'ancien —
    la clé a pu changer). Nom déterministe `ds_bkey_<ns_id>` (int → sûr) ; la clé
    est un LITTÉRAL composé via psycopg.sql (le DDL ne se paramètre pas)."""
    from psycopg import sql as _sql
    name = _bkey_index_name(ns_id)
    expr = bkey_index_expr(key)
    # CRÉER AVANT DE DÉPOSER, et jamais l'inverse : un DROP suivi d'un CREATE laisse
    # une fenêtre où RIEN n'impose l'unicité, et un batch concurrent y insère des
    # doublons que l'index neuf ne pourra plus se créer par-dessus. Les deux
    # coexistent sans conflit — sur une ligne plate, les deux expressions rendent la
    # même valeur.
    #
    # CONCURRENTLY ne bloque pas les écritures pendant la construction, et REFUSE de
    # tourner dans une transaction (vérifié) — d'où la connexion autocommit. Mesuré :
    # 40 ms sur 50 000 lignes, contre 32 ms pour l'ancienne forme plate.
    tmp = _sql.Identifier(name + "_v2")
    with _connect_autocommit() as conn:
        conn.execute(_sql.SQL("DROP INDEX IF EXISTS {t}").format(t=tmp))
        conn.execute(_sql.SQL(
            "CREATE UNIQUE INDEX CONCURRENTLY {t} ON datastore_rows (({e})) "
            "WHERE ns_id = {ns} AND {e} IS NOT NULL"
        ).format(t=tmp, e=expr, ns=_sql.Literal(int(ns_id))))
        conn.execute(_sql.SQL("DROP INDEX IF EXISTS {n}").format(n=_sql.Identifier(name)))
        conn.execute(_sql.SQL("ALTER INDEX {t} RENAME TO {n}").format(
            t=tmp, n=_sql.Identifier(name)))


def datastore_drop_key_index(ns_id: int) -> None:
    from psycopg import sql as _sql
    with _connect() as conn:
        conn.execute(_sql.SQL("DROP INDEX IF EXISTS {n}").format(
            n=_sql.Identifier(_bkey_index_name(ns_id))))


def datastore_has_key_index(ns_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s",
                           (_bkey_index_name(ns_id),)).fetchone()
        return row is not None


def datastore_namespaces_with_key() -> list[dict]:
    """Namespaces dont le schéma déclare une clé métier — `[{id, key}]` (migration
    boot #109 ch.3 : matérialiser la clé en contrainte sur l'existant)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, schema->>'key' AS key FROM user_datastores "
            "WHERE schema->>'key' IS NOT NULL AND schema->>'key' <> ''"
        ).fetchall()
        return [dict(r) for r in rows]


_DS_MAX_ROWS_BY_IDS = 200


def datastore_rows_by_ids(ns_id: int, row_ids: list) -> dict:
    """Contenu d'un LOT de lignes, en UNE requête : `{row_id: data}`.

    Sert à libeller des références (le journal d'activité cite des `row_id`, l'UI
    veut le champ `role="title"`). Les ids inconnus — ligne supprimée depuis —
    sont simplement absents du résultat, jamais une erreur. Lot borné.
    """
    ids = [str(r) for r in (row_ids or []) if r][:_DS_MAX_ROWS_BY_IDS]
    if not ids:
        return {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT row_id, data FROM datastore_rows WHERE ns_id = %s AND row_id = ANY(%s)",
            (ns_id, ids),
        ).fetchall()
        return {r["row_id"]: (r["data"] or {}) for r in rows}


def datastore_get_row(ns_id: int, row_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT row_id, created_at, updated_at, data, claimed_by, claimed_until "
            "FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id),
        ).fetchone()
        return dict(row) if row else None


# Filtres par colonne (vue tableau dashboard, oto-dashboard#18). Chaque filtre =
# {field, op, value}. Le champ est TOUJOURS paramétré (`data ->> %s`) et l'op tiré
# d'une whitelist → fragment SQL fixe, zéro interpolation de valeur = pas d'injection.
_DS_FILTER_OPS = {"contains", "eq", "ne", "in", "gt", "gte", "lt", "lte", "empty", "not_empty"}


_DS_CMP_SQL = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


_DS_NUM_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")  # numérique strict (pas de nan/1e5)


def _ds_text(val: Any) -> str:
    """Valeur de filtre → sa forme TEXTE telle que `data ->> champ` la rendrait (#306).

    `->>` extrait le JSON **en texte**, avec les conventions du JSON : un booléen y
    ressort `"true"`/`"false"` en minuscules. `str(True)` rend `"True"` — majuscule,
    convention Python — donc la comparaison était `"true" = "True"`, fausse pour
    chaque ligne. Zéro résultat, **sans erreur** : SQL compare deux chaînes valides
    qui ne coïncident jamais, et « aucune correspondance » est une réponse honnête à
    une question qui n'était pas celle qu'on posait. Mesuré : 0 ligne contre 29.

    Même famille pour un flottant entier — `str(1.0)` rend `"1.0"` là où un entier
    stocké ressort `"1"`.

    ⚠️ Une CHAÎNE passe telle quelle, et c'est délibéré : des appelants contournent
    aujourd'hui en envoyant `"true"` (le seul moyen d'obtenir le bon résultat). Le
    correctif ne doit pas transformer un piège silencieux en régression chez ceux
    qui avaient trouvé la parade — les deux formes matchent le même booléen stocké.
    """
    if isinstance(val, bool):        # AVANT le test int : en Python, bool ⊂ int
        return "true" if val else "false"
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    if val is None:
        # `data ->> champ` rend SQL NULL aussi bien pour un JSON `null` que pour une
        # clé absente : aucune comparaison textuelle ne peut les distinguer. On le
        # dit plutôt que de rendre un zéro que l'appelant lirait comme « aucune
        # ligne ne correspond ».
        raise ValueError(
            "valeur de filtre `null` : `data ->> champ` ne distingue pas un JSON "
            "`null` d'une clé absente, donc `eq`/`ne` ne peuvent pas y répondre — "
            "utiliser l'opérateur `empty` (ou `not_empty`).")
    return str(val)


# Colonnes MÉTA filtrables. Elles ne vivent PAS dans `data` : sans ce routage, un
# filtre « modifié depuis le 1er » partait en `data ->> '_updated_at'` = NULL et
# rendait ZÉRO ligne, sans la moindre erreur — un filtre muet est pire qu'un filtre
# absent. `order_by` les connaissait déjà (cf. `datastore_list_rows`), pas le WHERE.
_DS_META_TS_COLS = {"_updated_at": "updated_at", "_created_at": "created_at"}
_DS_META_TEXT_COLS = {"_id": "row_id"}
# Ops qui ont un sens sur une colonne NOT NULL : ni `empty`/`not_empty` (réponse
# connue d'avance), ni `contains` sur un instant. On REFUSE plutôt que de servir un
# résultat vide inexplicable.
_DS_META_TS_OPS = {"eq", "ne", "gt", "gte", "lt", "lte"}
_DS_META_TEXT_OPS = {"eq", "ne", "contains", "in"}
# Date seule (`2026-08-05`) vs instant (`2026-08-05T14:30`, suffixe tz optionnel).
_DS_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DS_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


_DS_MAX_FILTERS = 30


def _ds_meta_ts_clause(col: str, op: str, val: str) -> tuple[str, list]:
    """Fragment WHERE d'un filtre sur une colonne timestamptz méta.

    Une valeur DATE SEULE désigne la journée entière : « jusqu'au 5 » inclut le 5
    (sinon `<= '2026-08-05'` = minuit, et la journée saisie disparaît du résultat —
    le piège classique d'un filtre de date sur un timestamp). Une valeur avec heure
    se compare telle quelle. `col` vient de nos propres dicts (jamais de la saisie),
    la valeur reste paramétrée.

    ⚠️ « La journée » est celle du fuseau de la session PG (UTC en prod) : une ligne
    touchée à 23h30 à Paris compte pour le lendemain. Assumé tant qu'aucun fuseau
    utilisateur n'est déclaré nulle part — le jour où il l'est, c'est ICI que ça se
    règle (une borne, pas un décalage éparpillé dans les appelants)."""
    day = bool(_DS_DATE_ONLY_RE.match(val))
    if not day and not _DS_TS_RE.match(val):
        raise ValueError(
            f"valeur de date invalide `{val}` — attendu `AAAA-MM-JJ` "
            f"ou `AAAA-MM-JJTHH:MM`")
    if not day:
        sym = {"eq": "=", "ne": "<>"}.get(op) or _DS_CMP_SQL[op]
        return f"{col} {sym} %s::timestamptz", [val]
    lo, hi = "%s::timestamptz", "(%s::date + 1)::timestamptz"
    if op == "gte":
        return f"{col} >= {lo}", [val]
    if op == "lt":
        return f"{col} < {lo}", [val]
    if op == "gt":                       # après CE jour = à partir du lendemain
        return f"{col} >= {hi}", [val]
    if op == "lte":                      # jusqu'à CE jour inclus
        return f"{col} < {hi}", [val]
    window = f"({col} >= {lo} AND {col} < {hi})"
    return (window if op == "eq" else f"NOT {window}"), [val, val]


_DS_MAX_FIELDS_PER_FILTER = 50
_DS_MATCHES = ("any", "all")


def _ds_named_fields(value, quoi: str) -> list[str]:
    """Valide une liste de colonnes DÉCLARÉES par l'appelant (oto#22 barreau 1).

    Une notion vit souvent sur des colonnes numérotées (`contact1_fonction`,
    `contact2_fonction`…) : les interroger ensemble suppose de savoir lesquelles.
    L'appelant les NOMME — le serveur ne reconnaît aucune « famille » à l'orthographe
    d'un nom. Deviner `contact*` réintroduirait la convention de nommage qu'on vient
    de sortir des rôles, et ferait dépendre un résultat de l'orthographe des colonnes :
    une colonne renommée changerait un chiffre, sans que rien ne le signale.

    Une liste VIDE est refusée plutôt qu'ignorée : elle ne porterait sur rien, donc
    rendrait toutes les lignes — une réponse qui a l'air d'en être une."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(
            f"`{quoi}` doit être une LISTE NON VIDE de noms de colonnes — vide, "
            f"le filtre ne porterait sur rien et rendrait toutes les lignes")
    if len(value) > _DS_MAX_FIELDS_PER_FILTER:
        raise ValueError(
            f"`{quoi}` : {len(value)} colonnes déclarées, maximum "
            f"{_DS_MAX_FIELDS_PER_FILTER}")
    for k in value:
        if not isinstance(k, str) or not k:
            raise ValueError(
                f"`{quoi}` contient une entrée non textuelle ({k!r}) — chaque "
                f"membre est un nom de colonne")
    return list(value)


def _ds_filter_targets(f: dict) -> list[str]:
    """Les colonnes qu'un filtre VISE : une (`field`) ou plusieurs (`fields`)."""
    field, fields = f.get("field"), f.get("fields")
    if fields is not None and field is not None:
        raise ValueError(
            "filtre : `field` et `fields` sont exclusifs — nommer UNE colonne, ou "
            "déclarer la liste des colonnes membres, jamais les deux")
    if fields is not None:
        return _ds_named_fields(fields, "fields")
    if not isinstance(field, str) or not field:
        raise ValueError("invalid filter: `field` manquant ou non textuel")
    return [field]


def _ds_filter_joiner(f: dict) -> str:
    """`any` (défaut) : une colonne qui satisfait suffit. `all` : toutes.

    Les deux sont nécessaires, et `all` n'est pas la négation d'`any` : « aucun des
    trois rangs n'a de contact » (`empty`+`all`) ne s'obtient pas en niant « au moins
    un rang a un contact ». Sans `all`, le complément d'une mesure serait inexprimable
    — et c'est en général la moitié qu'on cherche."""
    m = f.get("match") or "any"
    if m not in _DS_MATCHES:
        raise ValueError(
            f"filtre : `match` inconnu `{m}` — `any` (une colonne suffit, défaut) "
            f"ou `all` (toutes les colonnes déclarées)")
    return " OR " if m == "any" else " AND "


def _ds_filter_clauses(filters: Optional[list]) -> tuple[list[str], list]:
    """Construit les fragments WHERE (combinés en AND) pour des filtres par colonne
    JSONB — **ou par colonne méta** (`_updated_at`/`_created_at`, dates système ;
    `_id`), routées vers la vraie colonne au lieu de `data ->>` (cf.
    `_DS_META_TS_COLS`). Champ paramétré + op whitelisté → pas d'injection. Les comparaisons
    ordonnées (`gt/gte/lt/lte`) sont numériques si la valeur EST numérique (cast
    gardé `::numeric`, les rows non numériques sont écartées), sinon textuelles
    (l'ISO `YYYY-MM-DD` se compare correctement en lexicographique). Lève
    `ValueError` sur un filtre malformé (→ 400 côté route).

    Un filtre vise UNE colonne (`field`) ou PLUSIEURS déclarées (`fields` + `match`,
    oto#22) : le prédicat est alors évalué sur chaque membre et les résultats joints
    en OR (`any`) ou en AND (`all`). Le filtre reste UN filtre — il se croise en AND
    avec les autres, comme n'importe lequel."""
    clauses: list[str] = []
    params: list = []
    if not filters:
        return clauses, params
    if len(filters) > _DS_MAX_FILTERS:
        raise ValueError("too many filters")
    for f in filters:
        if not isinstance(f, dict):
            raise ValueError("invalid filter")
        targets = _ds_filter_targets(f)
        op, val = f.get("op"), f.get("value")
        if op not in _DS_FILTER_OPS:
            # Dire QUELS opérateurs existent : « invalid filter » nu obligeait à
            # deviner (ou à renoncer et tout rapatrier pour filtrer en local).
            raise ValueError(
                f"opérateur de filtre inconnu `{op}` sur la colonne "
                f"`{'`, `'.join(targets)}` — "
                f"disponibles : {', '.join(sorted(_DS_FILTER_OPS))}")
        joiner = _ds_filter_joiner(f)
        subs: list[str] = []
        subparams: list = []
        for field in targets:
            clause, cparams = _ds_one_field_clause(field, op, val)
            if clause is None:      # filtre inerte (ex. `in` sur une liste vide)
                continue
            subs.append(clause)
            subparams.extend(cparams)
        if not subs:
            continue
        # Une cible unique ne se parenthèse pas : la forme `field` porte tout
        # l'existant, et son fragment doit rester identique au caractère près.
        clauses.append(subs[0] if len(subs) == 1 else "(" + joiner.join(subs) + ")")
        params.extend(subparams)
    return clauses, params


def _ds_one_field_clause(field: str, op: str, val) -> tuple[Optional[str], list]:
    """Le prédicat sur UNE colonne — `(fragment, params)`, ou `(None, [])` s'il est
    inerte. Point unique : `fields` boucle dessus, il n'en existe pas de copie."""
    if field in _DS_META_TS_COLS:
        if op not in _DS_META_TS_OPS:
            raise ValueError(
                f"opérateur `{op}` non applicable à `{field}` (date système, "
                f"toujours renseignée) — disponibles : "
                f"{', '.join(sorted(_DS_META_TS_OPS))}")
        return _ds_meta_ts_clause(_DS_META_TS_COLS[field], op, str(val))
    if field in _DS_META_TEXT_COLS:
        col = _DS_META_TEXT_COLS[field]
        if op not in _DS_META_TEXT_OPS:
            raise ValueError(
                f"opérateur `{op}` non applicable à `{field}` — disponibles : "
                f"{', '.join(sorted(_DS_META_TEXT_OPS))}")
        if op == "in":
            vals = [str(v) for v in (val if isinstance(val, list) else [val])
                    if v is not None and str(v) != ""]
            if not vals:
                return None, []
            return f"{col} = ANY(%s)", [vals]
        if op == "contains":
            return f"{col} ILIKE %s", [f"%{val}%"]
        return f"{col} {'=' if op == 'eq' else '<>'} %s", [str(val)]
    # À partir d'ici, la colonne est lue par l'expression POLYMORPHE (#318) : elle
    # rend la valeur qu'elle soit plate ou à couches. Le champ y passe DEUX fois
    # (un `%s` par branche du COALESCE) — d'où `fp` plutôt que `field` répété à
    # la main, qui est l'endroit exact où un décalage de paramètres se glisse.
    #
    # Nom nu → la valeur ; `champ.source` → la couche. Une seule décision, ici, dont
    # toutes les branches héritent.
    chemin = split_list_path(field)
    if chemin is not None and chemin[1] is None:
        # `contacts[].email` — l'EXISTENCE est intrinsèque à la notation (oto#22 §12) :
        # « il existe un contact dont l'e-mail… ». `match` ne descend jamais ici, il
        # joint les cibles déclarées. C'est le SEUL chemin qui ne rend pas une valeur
        # scalaire, donc le seul qui ne peut pas passer par `field_read_sql`.
        colonne, _, reste = chemin
        V, fp = leaf_read_sql("_i.v", [], reste)
        clause, cparams = _ds_leaf_predicate(V, fp, op, val)
        if clause is None:
            return None, []
        # La garde de type est OBLIGATOIRE : `jsonb_array_elements` LÈVE sur une
        # valeur qui n'est pas un tableau, et pendant une conversion une partie
        # des lignes ne l'est pas encore — l'état NORMAL, pas un cas limite.
        return (f"EXISTS (SELECT 1 FROM jsonb_array_elements("
                f"CASE WHEN jsonb_typeof(data->%s) = 'array' THEN data->%s "
                f"ELSE '[]'::jsonb END) AS _i(v) WHERE {clause})",
                [colonne, colonne] + cparams)
    V, fp = field_read_sql(field)
    return _ds_leaf_predicate(V, fp, op, val)


def _ds_leaf_predicate(V: str, fp: list, op: str, val) -> tuple:
    """Le prédicat sur une FEUILLE déjà résolue — `(fragment, params)`.

    Séparé de la résolution du chemin pour que les deux vivent au même endroit quel
    que soit le niveau : une colonne, une couche, l'attribut d'un item de liste. Sans
    cette séparation, interroger une liste aurait demandé une seconde copie de toute
    la logique d'opérateurs, et les deux auraient divergé au premier ajout."""
    if op in ("empty", "not_empty"):
        # ⚠️ La VALEUR compte. `{"empty": false}` se lit « pas vide » — c'est
        # la seule lecture possible d'un booléen, et un agent l'écrira. Elle
        # était JETÉE : les deux sens rendaient le même jeu de lignes, donc
        # « quelles valeurs n'ont pas de provenance ? » et son contraire
        # répondaient pareil, sans erreur. Défaut antérieur aux couches — il
        # valait déjà sur une colonne plate.
        veut_vide = (op == "empty") == (val is not False)
        return (f"({V} IS NULL OR {V} = '')" if veut_vide
                else f"({V} IS NOT NULL AND {V} <> '')"), fp + fp
    if op == "in":
        vals = [_ds_text(v) for v in (val if isinstance(val, list) else [val])
                if v is not None and str(v) != ""]
        if not vals:
            return None, []
        return f"{V} = ANY(%s)", fp + [vals]
    if op == "contains":
        return f"{V} ILIKE %s", fp + [f"%{_ds_text(val)}%"]
    if op == "eq":
        return f"{V} = %s", fp + [_ds_text(val)]
    if op == "ne":
        return f"({V} IS DISTINCT FROM %s)", fp + [_ds_text(val)]
    # gt/gte/lt/lte
    sym = _DS_CMP_SQL[op]
    sval = _ds_text(val)
    if _DS_NUM_RE.match(sval):
        return (f"({V} ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                f"AND ({V})::numeric {sym} %s::numeric)"), fp + fp + [sval]
    return f"{V} {sym} %s", fp + [sval]


def _ds_where(ns_id: int, q: Optional[str], filters: Optional[list]) -> tuple[str, list]:
    """Clause WHERE partagée par list/count (même filtrage → total cohérent)."""
    where = "WHERE ns_id = %s"
    params: list = [ns_id]
    if q:
        # Recherche plein-texte sur tout le JSON. ACCENT-INSENSIBLE (#67 V2.3) :
        # même repli d'accents `_fold` qu'`oto_search` → « café » trouve « cafe » et
        # inversement (fin de la divergence « sans accents repliés »). Reste un substring
        # (matching partiel conservé, choix de la file feed) — l'alignement en tsquery
        # tokenisée est un arbitrage distinct.
        from .projects import _fold  # lazy : projects importe datastore (évite le cycle)
        where += (f" AND {_fold(ROW_VALUES_TEXT_SQL)} ILIKE"
                  f" '%%' || {_fold('%s')} || '%%'")
        params.append(q)
    fclauses, fparams = _ds_filter_clauses(filters)
    for c in fclauses:
        where += f" AND {c}"
    params.extend(fparams)
    return where, params


def datastore_list_rows(ns_id: int, *, offset: int = 0, limit: Optional[int] = None,
                        order_by: Optional[str] = None, order_dir: str = "desc",
                        q: Optional[str] = None, filters: Optional[list] = None) -> list[dict]:
    """Page de rows d'un namespace. `order_by` : `_created_at`/`_updated_at`/`_id`
    (colonnes méta) ou un nom de champ user → `data->>field`. `q` : recherche
    plein-texte sur tout le JSON (substring ACCENT-INSENSIBLE, aligné sur oto_search).
    `filters` : filtres par
    colonne (liste `{field, op, value}`, combinés AND — cf. `_ds_filter_clauses`).
    Tri/pagination/recherche/filtres côté SQL (server-side, ADR 0016). `limit=None`
    = toutes les rows (compat `store.list_rows` / MCP `data_rows`)."""
    direction = "ASC" if str(order_dir).lower() == "asc" else "DESC"
    where, params = _ds_where(ns_id, q, filters)
    if order_by in (None, "", "_created_at"):
        order_sql = f"created_at {direction}, row_id {direction}"
    elif order_by == "_updated_at":
        order_sql = f"updated_at {direction}, row_id {direction}"
    elif order_by == "_id":
        order_sql = f"row_id {direction}"
    else:
        _v, _vp = field_read_sql(order_by)
        order_sql = f"{_v} {direction}, row_id {direction}"
        # ⚠️ TOUS les paramètres de l'expression, pas le nom une fois : depuis #318 la
        # lecture d'une colonne est un COALESCE à DEUX emplacements (plate ou à
        # couches), et un chemin de liste en compte quatre. N'en fournir qu'un faisait
        # échouer la requête — tout tri par colonne user, en production. Le banc de tri
        # stubbait le SQL : il vérifiait quel CHEMIN de code est pris, jamais que la
        # requête s'exécute. La sonde qui l'attrape est donc contre un vrai PostgreSQL.
        params.extend(_vp)
    tail = ""
    if limit is not None:
        tail = " LIMIT %s OFFSET %s"
        params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(
            "SELECT row_id, created_at, updated_at, data, claimed_by, claimed_until "
            f"FROM datastore_rows {where} ORDER BY {order_sql}{tail}",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]


def datastore_list_rows_after(ns_id: int, *, after_row_id: Optional[str] = None,
                              limit: int = 100, q: Optional[str] = None,
                              filters: Optional[list] = None) -> list[dict]:
    """Page **keyset** (curseur stable) triée par `row_id`. `row_id` est un uuid7 —
    monotone dans le temps de création — donc `ORDER BY row_id ASC` = ordre de
    création et `WHERE row_id > after_row_id` (borne EXCLUSIVE) enchaîne les pages
    sans dérive sous écritures concurrentes (contrairement à OFFSET, décalé par toute
    insertion). `after_row_id=None` = première page. `q`/`filters` = même filtrage
    SQL que `datastore_list_rows`. La clé est exacte (pas de troncature de timestamp,
    contrairement à un keyset sur `created_at` rendu à la seconde)."""
    where, params = _ds_where(ns_id, q, filters)
    if after_row_id:
        where += " AND row_id > %s"
        params.append(after_row_id)
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT row_id, created_at, updated_at, data, claimed_by, claimed_until "
            f"FROM datastore_rows {where} ORDER BY row_id ASC LIMIT %s",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]


def datastore_count_rows(ns_id: int, q: Optional[str] = None,
                         filters: Optional[list] = None) -> int:
    """Nombre total de rows d'un namespace (pour la pagination), filtré par `q` et
    les filtres par colonne — même clause que `datastore_list_rows` → total cohérent
    avec la page affichée."""
    where, params = _ds_where(ns_id, q, filters)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM datastore_rows {where}", tuple(params)
        ).fetchone()
        return int(row["n"]) if row else 0


_NUMERIC_RE = r'^\s*-?[0-9]+(\.[0-9]+)?\s*$'


def _metric_filter_sql(m: dict) -> tuple[str, list]:
    """Le `FILTER (WHERE …)` d'une métrique conditionnelle (oto#22 barreau 1).

    Une métrique porte sa propre condition, dans la MÊME grammaire que `filters`
    (`fields` multi-champs compris). C'est ce qui permet de compter DEUX populations
    dans une seule requête — le total et le sous-ensemble — donc d'obtenir un taux
    sans recouper deux appels dont les périmètres peuvent diverger sans le dire."""
    spec = m.get("where")
    if not spec:
        return "", []
    if not isinstance(spec, (list, tuple)):
        raise ValueError(
            "agrégat : `where` d'une métrique = une LISTE de filtres, même "
            "grammaire que `filters`")
    fc, fp = _ds_filter_clauses(list(spec))
    if not fc:
        return "", []
    return " FILTER (WHERE " + " AND ".join(fc) + ")", fp


def _metric_label(m: dict, defaut: str, pris: set) -> str:
    """Le nom sous lequel la métrique ressort. `label` explicite, sinon dérivé.

    Le dérivé est DÉDOUBLONNÉ : deux `count` (le total et le conditionnel) portaient
    sinon la même clé, et la seconde écrasait la première à la construction du dict —
    un résultat qui a l'air complet, avec une métrique en moins."""
    nom = m.get("label") or defaut
    if not isinstance(nom, str) or not nom:
        raise ValueError("agrégat : `label` doit être un nom non vide")
    base, out, i = nom, nom, 2
    while out in pris:
        out = f"{base}_{i}"
        i += 1
    pris.add(out)
    return out


def _group_fields(group_by) -> Optional[list]:
    """Les colonnes d'un regroupement en UNION, ou None pour un group_by ordinaire."""
    if isinstance(group_by, (list, tuple)):
        return _ds_named_fields(group_by, "group_by")
    return None


def group_key(group_by) -> Optional[str]:
    """La clé sous laquelle la valeur du groupe ressort. Pour une union, elle nomme
    les colonnes mises en commun — la valeur vient de l'une d'elles, et laquelle n'a
    pas de sens : c'est le principe même du « tous rangs confondus »."""
    if isinstance(group_by, (list, tuple)):
        return "|".join(group_by)
    return group_by


def _build_aggregate(ns_id: int, group_by, metrics: Optional[list],
                     q: Optional[str], filters: Optional[list],
                     limit: int) -> tuple[str, list, list]:
    """Construit `(sql, params, names)` de l'agrégat — PUR (aucun I/O), testable sans PG.
    `names` = `[(alias_sql, nom_lisible)]`. Ordre des `%s` : colonnes SELECT (group +
    métriques, filtres de métrique inclus) puis LATERAL puis WHERE puis LIMIT —
    l'ordre de `params` doit suivre EXACTEMENT.
    Les noms de champs passent en PARAMÈTRES, jamais interpolés (anti-injection) —
    DEUX fois chacun depuis #318, un par branche du COALESCE qui lit une colonne
    plate ou à couches. L'ordre de `sparams` en dépend.

    `group_by` accepte une LISTE de colonnes (oto#22) : leurs valeurs sont alors mises
    en commun, une ligne contribuant une occurrence par colonne renseignée — la
    « répartition tous rangs confondus ». Le dégroupement passe par un `LATERAL
    (VALUES …)`, dont les paramètres s'insèrent entre ceux du SELECT et ceux du WHERE."""
    metrics = metrics or [{"op": "count"}]
    pooled = _group_fields(group_by)
    select, sparams, names = [], [], []  # noms lisibles alignés sur les alias mN
    lateral, lparams = "", []
    if pooled:
        vals = []
        for k in pooled:
            _v, _vp = field_read_sql(k)
            vals.append(f"({_v})")
            lparams.extend(_vp)
        lateral = " , LATERAL (VALUES " + ", ".join(vals) + ") AS _u(v)"
        select.append("_u.v AS grp")
    elif group_by:
        _v, _vp = field_read_sql(group_by)
        select.append(f"{_v} AS grp")
        sparams.extend(_vp)
    pris: set = set()
    for i, m in enumerate(metrics):
        op = str(m.get("op", "")).lower()
        field = m.get("field")
        alias = f"m{i}"
        fsql, fparams = _metric_filter_sql(m)
        if op == "count" and not field:
            select.append(f"COUNT(*){fsql} AS {alias}")
            sparams.extend(fparams)
            names.append((alias, _metric_label(m, "count", pris)))
        elif op == "count_rows":
            # Sous une union, `count` compte les OCCURRENCES (deux contacts sur la
            # même fiche font deux) : le nombre de FICHES est une autre question, et
            # les confondre donne un chiffre plausible et faux. Hors union, les deux
            # coïncident — `row_id` est unique par ligne.
            select.append(f"COUNT(DISTINCT row_id){fsql} AS {alias}")
            sparams.extend(fparams)
            names.append((alias, _metric_label(m, "count_rows", pris)))
        elif op == "count":
            _v, _vp = field_read_sql(field)
            select.append(f"COUNT({_v}){fsql} AS {alias}")
            sparams.extend(_vp + fparams)
            names.append((alias, _metric_label(m, f"count_{field}", pris)))
        elif op in ("sum", "avg", "min", "max"):
            if not field:
                raise ValueError(f"agrégat: op '{op}' exige un `field`")
            _v, _vp = field_read_sql(field)
            select.append(
                f"{op.upper()}(CASE WHEN {_v} ~ %s "
                f"THEN ({_v})::numeric END){fsql} AS {alias}")
            sparams.extend(_vp + [_NUMERIC_RE] + _vp + fparams)
            names.append((alias, _metric_label(m, f"{op}_{field}", pris)))
        else:
            raise ValueError(
                f"agrégat: op inconnu {op!r} (count|count_rows|sum|avg|min|max)")
    where, wparams = _ds_where(ns_id, q, filters)
    if pooled:
        # Un rang vide ou absent n'est pas un contact : il ne fabrique pas un groupe.
        where += " AND _u.v IS NOT NULL AND _u.v <> ''"
    sql = f"SELECT {', '.join(select)} FROM datastore_rows{lateral} {where}"
    params = sparams + lparams + wparams
    if group_by:
        sql += " GROUP BY grp ORDER BY m0 DESC NULLS LAST, grp ASC"
    sql += " LIMIT %s"
    params.append(limit)
    return sql, params, names


def datastore_aggregate(ns_id: int, *, group_by: Optional[str] = None,
                        metrics: Optional[list] = None, q: Optional[str] = None,
                        filters: Optional[list] = None, limit: int = 1000) -> list[dict]:
    """Agrégat serveur d'un namespace (feedback #191) : `COUNT/SUM/AVG/MIN/MAX` sur des
    champs JSONB, avec `group_by` optionnel — stats d'un gros vivier sans rapatrier les
    lignes. `group_by` = champ `data->>field` (None = agrégat global, une ligne).
    `metrics` = liste `{op, field?}`, op ∈ count|sum|avg|min|max (défaut `[{op:count}]`) ;
    `count` sans field = COUNT(*). sum/avg/min/max ne comptent que les valeurs
    NUMÉRIQUES (les non-numériques sont ignorées via un garde regex, jamais d'erreur de
    cast). Filtré par `q`/`filters` (même clause que list/count). Trié par la 1re métrique
    décroissante (« top … ») quand `group_by`. Renvoie `[{<group_by>: val, <metric>: n}]`
    (clés lisibles : `count`, `sum_<field>`, `avg_<field>`…)."""
    from decimal import Decimal
    sql, params, names = _build_aggregate(ns_id, group_by, metrics, q, filters, limit)
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    out = []
    cle = group_key(group_by)
    for r in rows:
        d: dict = {}
        if group_by:
            d[cle] = r["grp"]
        for alias, name in names:
            v = r[alias]
            d[name] = float(v) if isinstance(v, Decimal) else v
        out.append(d)
    return out


def datastore_update_row(ns_id: int, row_id: str, data: dict, updated_at: str) -> Optional[dict]:
    """Remplace `data` (le store a déjà fusionné le patch) + `updated_at`."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE datastore_rows SET data = %s::jsonb, updated_at = %s::timestamptz "
            "WHERE ns_id = %s AND row_id = %s "
            "RETURNING row_id, created_at, updated_at, data",
            (json.dumps(data), updated_at, ns_id, row_id),
        ).fetchone()
        from .search import stamp_rank_vector
        stamp_rank_vector(conn, "datastore_rows", "ns_id = %s AND row_id = %s", (ns_id, row_id))
        return dict(row) if row else None


def datastore_merge_row_locked(ns_id: int, row_id: str, apply_fn, updated_at: str,
                               lease_guard=None):
    """MERGE ATOMIQUE d'une row par son `row_id`, sous verrou de ligne (#197).

    Dans UNE transaction : verrouille la row (`SELECT … FOR UPDATE`), applique
    `apply_fn(current_data) -> merged` SOUS le verrou, puis écrit `merged`. Deux
    writes concurrents de la MÊME row (deux upserts de la même clé métier
    résolvent le même row_id via find_row_id_by_key) se **sérialisent** → plus de
    merge perdu : l'ancien `get_row` + merge Python + `update_row` sur deux
    connexions autocommit séparées était last-writer-wins (~30-35 % des merges
    écrasés sous forte concurrence). Renvoie `(row, merged)` ou `None` si la row
    n'existe plus (course de suppression). `apply_fn` peut lever (validation) →
    la transaction rollback, l'exception est propagée.

    `lease_guard` (#317) = contrôle du BAIL, appelé sous le verrou avec la ligne
    verrouillée (`claimed_by`/`claimed_until`/`claimed_run` inclus). Il lève pour
    refuser l'écriture — la transaction rollback, rien n'est écrit. Passé en
    paramètre plutôt que codé ici parce que « qui a le droit d'écrire » est une règle
    du STORE, pas du SQL : ce module ne connaît ni le run courant ni le worker.
    """
    with _connect() as conn:
        with conn.transaction():
            # Le bail est lu DANS le même verrou que la donnée (#317) : le lire
            # avant, sur une autre connexion, laisserait la fenêtre où un claim
            # s'intercale entre le contrôle et l'écriture — le défaut exact que
            # `FOR UPDATE` a été posé pour fermer sur `data` (#197).
            locked = conn.execute(
                "SELECT data, claimed_by, claimed_until, claimed_run "
                "FROM datastore_rows WHERE ns_id = %s AND row_id = %s FOR UPDATE",
                (ns_id, row_id),
            ).fetchone()
            if locked is None:
                return None
            if lease_guard is not None:
                lease_guard(locked)
            current = locked["data"]
            if not isinstance(current, dict):
                current = json.loads(current) if current else {}
            merged = apply_fn(current)
            row = conn.execute(
                "UPDATE datastore_rows SET data = %s::jsonb, updated_at = %s::timestamptz "
                "WHERE ns_id = %s AND row_id = %s "
                "RETURNING row_id, created_at, updated_at, data",
                (json.dumps(merged), updated_at, ns_id, row_id),
            ).fetchone()
            return dict(row), merged


def datastore_delete_row(ns_id: int, row_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id),
        )
        return (cur.rowcount or 0) > 0


# ── File de travail (ADR 0046 D) ─────────────────────────────────────────────
# Une row se « claim » avec un BAIL (claimed_by/claimed_until) : pick atomique de
# la prochaine row libre (bail NULL ou expiré) via FOR UPDATE SKIP LOCKED — deux
# workers concurrents ne prennent jamais la même row, sans sérialiser la table.
# Le bail expiré rend la row recyclable (worker mort ≠ row perdue). Libération :
# explicite (release, gardée par worker) ou automatique à l'entrée dans un état
# terminal du cycle de vie (côté store).

def datastore_claim_next(ns_id: int, *, worker: str, lease_seconds: int = 900,
                         filters: Optional[list] = None,
                         run_id: Optional[str] = None) -> Optional[dict]:
    """Claim atomique de la prochaine row claimable du namespace (ordre de
    création — row_id uuid7 monotone). `filters` = mêmes filtres whitelistés que
    la lecture (`_ds_filter_clauses`), typiquement `[{field:'status',op:'eq',…}]`.
    Renvoie la row (avec bail posé) ou None si plus rien à traiter."""
    fclauses, fparams = _ds_filter_clauses(filters)
    where = "WHERE ns_id = %s AND (claimed_until IS NULL OR claimed_until < NOW())"
    params: list = [ns_id, *fparams]
    for c in fclauses:
        where += f" AND {c}"
    with _connect() as conn:
        picked = conn.execute(
            f"SELECT row_id FROM datastore_rows {where} "
            "ORDER BY row_id ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
            tuple(params),
        ).fetchone()
        if not picked:
            return None
        row = conn.execute(
            "UPDATE datastore_rows SET claimed_by = %s, "
            "claimed_until = NOW() + (%s || ' seconds')::interval, claimed_run = %s "
            "WHERE ns_id = %s AND row_id = %s "
            "RETURNING row_id, created_at, updated_at, data, claimed_by, claimed_until",
            (str(worker), int(lease_seconds), run_id, ns_id, picked["row_id"]),
        ).fetchone()
        return dict(row) if row else None


def datastore_claim_row(ns_id: int, row_id: str, *, worker: str,
                        lease_seconds: int = 900,
                        run_id: Optional[str] = None) -> Optional[dict]:
    """Claim d'une row **nommée** (≠ pick de la suivante) — la file pilotée par un
    humain, qui choisit la ligne qu'il traite et à qui le serveur la réserve.

    Même condition d'éligibilité que `datastore_claim_next` (bail NULL ou expiré),
    plus le RENOUVELLEMENT par le même worker : rafraîchir son écran ne doit pas
    coûter sa propre ligne. L'UPDATE conditionnel EST l'atomicité — deux appels
    concurrents sur la même row, un seul repart avec le bail.

    None = row absente OU sous bail actif d'un AUTRE worker ; les distinguer coûte
    une relecture, laissée à l'appelant (chemin d'échec seulement)."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE datastore_rows SET claimed_by = %s, "
            "claimed_until = NOW() + (%s || ' seconds')::interval, claimed_run = %s "
            "WHERE ns_id = %s AND row_id = %s AND (claimed_until IS NULL "
            "OR claimed_until < NOW() OR claimed_by = %s) "
            "RETURNING row_id, created_at, updated_at, data, claimed_by, claimed_until",
            (str(worker), int(lease_seconds), run_id, ns_id, row_id, str(worker)),
        ).fetchone()
        return dict(row) if row else None


def datastore_release_by_run(run_id: str) -> int:
    """Libère toutes les lignes réservées sous ce run — la TROISIÈME voie du verrou.

    Appelée à la fermeture d'un run, quel que soit son issue (`done`, `failed`,
    `blocked`) : un run qui se termine ne travaille plus, donc ne tient plus rien.

    ⚠️ **Ce qu'elle NE couvre PAS, contrairement à ce que ce commentaire affirmait :
    l'agent qui MEURT.** Un agent mort n'appelle pas `run_finish` — c'est la
    définition. `stale` est par ailleurs DÉRIVÉ (`run_status.is_stale`), jamais posé :
    rien ne ferme un run abandonné, donc rien ne libère ses lignes. Le seul filet pour
    ce cas reste l'expiration du bail, celui qui a mis **18 jours** à jouer sur la
    seule ligne réservée qu'ait portée la production. Le ramassage des runs abandonnés
    est une décision à part (#324).

    Ce qu'elle couvre réellement : l'agent qui TERMINE son run en oubliant de relâcher
    ses lignes. Plus petit que promis, et probablement le cas fréquent.

    ⚠️ Aucune garde de worker ici, et c'est voulu : la garde du release protège d'un
    agent qui libérerait la ligne d'un AUTRE. Ici c'est le run lui-même qui se ferme —
    il ne peut libérer que ce qu'il tenait, la clause `claimed_run = %s` s'en charge.

    Rend le nombre de lignes libérées (0 = le cas normal, un run qui n'a rien
    réservé)."""
    if not run_id:
        return 0
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE datastore_rows SET claimed_by = NULL, claimed_until = NULL, "
            "claimed_run = NULL WHERE claimed_run = %s", (str(run_id),))
        return cur.rowcount or 0


def datastore_active_lease(ns_id: int, row_id: str) -> Optional[dict]:
    """Le bail ACTIF d'une ligne, ou None — expiré compte pour libre.

    ⚠️ « Actif » est la nuance qui empêche la protection en écriture de devenir un
    mur : un bail expiré ne protège rien (son titulaire est mort), sinon le zombie de
    18 jours mesuré en production aurait bloqué cette ligne pendant 18 jours."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT claimed_by, claimed_until, claimed_run FROM datastore_rows "
            "WHERE ns_id = %s AND row_id = %s AND claimed_by IS NOT NULL "
            "  AND claimed_until IS NOT NULL AND claimed_until > NOW()",
            (ns_id, row_id)).fetchone()
        return dict(row) if row else None


def datastore_claimed_rows(ns_id: int) -> list[dict]:
    """Rows sous bail de file de travail (ADR 0046 D) — la vue « en cours » du
    dashboard. Bail actif OU expiré confondus (le consommateur tranche sur
    `claimed_until`), plus ancien bail d'abord."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT row_id, created_at, updated_at, data, claimed_by, claimed_until "
            "FROM datastore_rows WHERE ns_id = %s AND claimed_by IS NOT NULL "
            "ORDER BY claimed_until ASC",
            (ns_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def datastore_release_claim(ns_id: int, row_id: str, worker: Optional[str]) -> bool:
    """Libère le bail d'une row. `worker` non-None = gardé (on ne libère pas le
    claim d'un autre) ; None = libération inconditionnelle (chemin interne : entrée
    en état terminal). Renvoie False si rien n'a été libéré (pas de bail, ou bail
    d'un autre worker)."""
    guard = "" if worker is None else " AND claimed_by = %s"
    params: tuple = (ns_id, row_id) if worker is None else (ns_id, row_id, str(worker))
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE datastore_rows SET claimed_by = NULL, claimed_until = NULL "
            f"WHERE ns_id = %s AND row_id = %s AND claimed_by IS NOT NULL{guard}",
            params,
        )
        return (cur.rowcount or 0) > 0
