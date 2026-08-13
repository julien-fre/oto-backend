"""Le TABLEAU lui-même : son existence, son nom, et qui y a droit.

Extrait de `db/datastore.py` sans un changement de comportement (#325). La couture
sépare le CONTENANT (un namespace, son propriétaire, ses partages) de son CONTENU (les
lignes) — deux préoccupations qui n'ont jamais évolué ensemble.

Les partages (`resource_grants`, ADR 0030/0048) vivent ici parce qu'ils répondent à la
même question que le namespace : à qui cette ressource appartient-elle, et qui d'autre
y accède. Le rôle porté par un grant est la source, la permission lecture/écriture en
est la PROJECTION — jamais l'inverse.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import psycopg

from ._conn import _connect
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
    # Import LOCAL : l'index de clé métier vit avec les lignes, pas avec le tableau.
    # Le faire en tête créerait un cycle (les lignes connaissent déjà le tableau).
    from .datastore import datastore_drop_key_index
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
