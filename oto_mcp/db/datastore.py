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
    """Trouve le `row_id` d'une row par une CLÉ MÉTIER (champ JSONB `data->>key`),
    pour la dédup d'un batch write. Renvoie le plus ancien match (ordre stable) ou
    None. La clé est interpolée en LITTÉRAL SQL (psycopg.sql, jamais un f-string) :
    un paramètre `data->>$1` ne matcherait pas l'index d'expression de clé métier
    (#109 ch.3) — le littéral rend le lookup indexé, O(1)."""
    from psycopg import sql as _sql
    q = _sql.SQL(
        "SELECT row_id FROM datastore_rows WHERE ns_id = %s AND data->>{k} = %s "
        "ORDER BY created_at ASC LIMIT 1"
    ).format(k=_sql.Literal(str(key_field)))
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


def field_value_sql(key: str) -> str:
    """SQL qui rend la VALEUR d'une colonne, qu'elle soit plate ou à couches (#318).

    Une colonne peut porter `{"valeur": …, "source": …, "origine": …}` au lieu d'un
    scalaire. Personne ne réécrira les 43 782 lignes existantes : **la table reste
    mixte pour toujours**, ce n'est pas un état de transition. Tout lecteur adressé
    par champ passe donc par ici — filtres, tri, agrégats, clé métier, contrôles de
    schéma — et **aucun ne recopie l'expression** : c'est le contrat que la bascule
    du modèle de contenu transportera, et il n'existe qu'à un endroit.

    Le `COALESCE` ne se déclenche que sur NULL, donc une `valeur` vide ("") reste
    une valeur et ne retombe pas sur l'objet entier. Un champ `json` légitime qui
    se trouve être un objet sans `valeur` rend son texte, comme avant : l'expression
    ne DEVINE pas — c'est le type déclaré au schéma qui dit ce qui porte des couches,
    jamais la forme observée.

    ⚠️ Le champ est un **littéral** échappé (`psycopg.sql.Literal`), pas un
    paramètre : l'index d'unicité de clé métier est un index d'EXPRESSION, et le
    planner ne le sert au lookup que si le `WHERE` porte la MÊME chaîne. Un écart
    ne casserait rien de visible — la déduplication marcherait, chaque lookup
    partirait en seq scan.
    """
    from psycopg import sql as _sql
    k = _sql.Literal(str(key))
    # Rend un COMPOSABLE, jamais une chaîne : la composition ne quitte pas psycopg.
    # Une chaîne calculée puis re-enveloppée dans `_sql.SQL()` serait CORRECTE ici
    # (le `Literal` double les apostrophes — vérifié sur `x'; DROP TABLE …`), mais
    # la correction reposerait alors sur ce seul échappement, sans filet : une
    # édition future qui retirerait le `Literal` passerait sans que rien ne crie.
    # Signalé par la revue de sécurité automatique, et le durcissement est gratuit.
    return _sql.SQL(
        "COALESCE(data->{k}->>{v}, data->>{k})"
    ).format(k=k, v=_sql.Literal(VALUE_LAYER))


# Même expression, forme PARAMÉTRÉE — le champ passe en `%s` (deux fois) au lieu
# d'être inscrit dans le SQL. C'est la forme des filtres, du tri et des agrégats :
# eux n'ont aucun index d'expression à servir, donc rien n'exige le littéral, et
# l'invariant anti-injection du module (« le champ est TOUJOURS paramétré ») reste
# intact. Seul le chemin CLÉ MÉTIER prend `field_value_sql`, parce que lui doit
# matcher son index à la chaîne près.
FIELD_VALUE_PARAM_SQL = f"COALESCE(data->%s->>'{VALUE_LAYER}', data->>%s)"

# Les COUCHES adressables d'une colonne (#318). `valeur` n'en fait pas partie : elle
# EST la colonne, on l'atteint par son nom nu — c'est ce qui garde le contrat de
# lecture inchangé pour tout l'existant.
# Le vocabulaire vit dans le module PUR du domaine — une seule source, pas deux.
# `source` et `source_link` y sont DEUX couches, et la séparation a une raison
# opérationnelle : une source unique qui mélangerait « registre » et une URL rendrait
# `group_by champ.source` inutile — chaque URL comptant pour une provenance distincte,
# on obtiendrait autant de groupes que de lignes. Or « combien de valeurs déduites ? »
# est précisément la question de pilotage. La NATURE se groupe, la PREUVE se vérifie.

# Chemin GÉNÉRIQUE vers une sous-clé : `data->%s->>%s` (colonne, sous-clé) — les deux
# en paramètres, rien de figé. Il sert les couches aujourd'hui ; il servira tel quel le
# jour où l'on voudra filtrer la sous-clé d'un champ `json` ordinaire (oto#20), qui est
# la même question posée sur un autre vocabulaire.
#
# Pas de COALESCE ici : une sous-clé n'a pas de forme plate à laquelle retomber. Sur
# une colonne scalaire elle est NULL, et c'est la BONNE réponse — « cette valeur n'a
# pas de source » est justement la question qu'on veut pouvoir poser.
LAYER_VALUE_PARAM_SQL = "data->%s->>%s"


# Le blob RECONSTRUIT avec les valeurs à la place des enveloppes — pour tout ce qui
# lit la ligne entière en texte : recherche plein-texte, extrait, embedding sémantique.
#
# Sans ça, une colonne à couches ferait entrer sa provenance dans le texte cherché :
# `q=hunter` matcherait toute ligne dont un email VIENT de Hunter, et l'embedding
# porterait la source au même titre que le contenu. Ce n'est pas une casse — c'est
# une pollution, et elle est indétectable depuis le résultat.
#
# ⚠️ On reconstruit un JSONB puis on le sérialise, plutôt que de concaténer les
# valeurs : le texte produit est alors IDENTIQUE À L'OCTET à `data::text` sur une
# ligne plate — c'est-à-dire sur les 43 782 lignes existantes et sur tout ce qui
# n'aura jamais de couches. Une concaténation aurait changé la forme (ponctuation
# JSON perdue), donc le résultat de recherches en sous-chaîne, pour tout le monde.
_ROW_VALUES_REBUILD_SQL = (
    "COALESCE((SELECT jsonb_object_agg(k, CASE"
    " WHEN jsonb_typeof(v) = 'object' AND v ? '" + VALUE_LAYER + "'"
    " THEN v->'" + VALUE_LAYER + "' ELSE v END)"
    " FROM jsonb_each(data) AS _e(k, v)), data)::text"
)

# ⚠️ GARDÉ, et la garde vient d'une mesure, pas d'une intuition. Reconstruire le blob
# pour chaque ligne scannée coûte ×6,4 ; le faire seulement quand la ligne PORTE une
# couche ramène à ×1,5 sur une table sans couches — c'est-à-dire sur la totalité de
# l'existant. Le coût suit donc l'usage : il n'arrive qu'avec la fonctionnalité.
#
# Mesuré sur 50 000 lignes, 7 colonnes (PG 17) :
#     data::text nu ............  78 ms    projection systématique ...  498 ms  ×6,4
#     garde jsonpath ..........  113 ms    garde par sous-chaîne .....  150 ms  ×1,9
# Le pire cas (toutes les lignes à couches) revient à ×7 quelle que soit la variante —
# c'est le prix du service rendu, pas un défaut de la garde.
ROW_VALUES_TEXT_SQL = (
    "CASE WHEN jsonb_path_exists(data, '$.*." + VALUE_LAYER + "')"
    " THEN " + _ROW_VALUES_REBUILD_SQL + " ELSE data::text END"
)


def split_layer(field: str) -> tuple:
    """`email.source` → `("email", "source")` ; `email` → `("email", None)`.

    Ne coupe qu'au DERNIER point, et seulement si le suffixe est une couche connue :
    un champ légitimement nommé `taux.2024` reste un nom de colonne entier. Le
    vocabulaire est FERMÉ, donc l'ambiguïté est décidable — pas de devinette."""
    base, sep, last = str(field).rpartition(".")
    if sep and base and last in LAYER_KEYS:
        return base, last
    return str(field), None


def field_read_sql(field: str) -> tuple:
    """`(fragment SQL, paramètres)` pour lire ce que l'appelant a désigné.

    Un nom nu lit la VALEUR (plate ou à couches) ; `champ.source` lit la couche.
    Les deux se filtrent, se trient et s'agrègent pareil — c'est ce qui rend
    « toutes les lignes dont l'email n'a pas de source » exprimable, et donc la
    provenance vérifiable au lieu de décorative."""
    base, layer = split_layer(field)
    if layer:
        return LAYER_VALUE_PARAM_SQL, [base, layer]
    return FIELD_VALUE_PARAM_SQL, [base, base]


def bkey_index_expr(key: str) -> str:
    """Expression indexée pour la clé métier — LA MÊME que celle du lookup.

    Délègue plutôt que de recopier : la dérive entre les deux est impossible par
    construction, et le test qui compare les deux chaînes garde l'invariant si
    quelqu'un rompt un jour cette délégation."""
    return field_value_sql(key)


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
    with _connect() as conn:
        conn.execute(_sql.SQL("DROP INDEX IF EXISTS {n}").format(n=_sql.Identifier(name)))
        conn.execute(_sql.SQL(
            "CREATE UNIQUE INDEX {n} ON datastore_rows ((data->>{k})) "
            "WHERE ns_id = {ns} AND data->>{k} IS NOT NULL"
        ).format(n=_sql.Identifier(name), k=_sql.Literal(str(key)),
                 ns=_sql.Literal(int(ns_id))))


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


def _ds_filter_clauses(filters: Optional[list]) -> tuple[list[str], list]:
    """Construit les fragments WHERE (combinés en AND) pour des filtres par colonne
    JSONB — **ou par colonne méta** (`_updated_at`/`_created_at`, dates système ;
    `_id`), routées vers la vraie colonne au lieu de `data ->>` (cf.
    `_DS_META_TS_COLS`). Champ paramétré + op whitelisté → pas d'injection. Les comparaisons
    ordonnées (`gt/gte/lt/lte`) sont numériques si la valeur EST numérique (cast
    gardé `::numeric`, les rows non numériques sont écartées), sinon textuelles
    (l'ISO `YYYY-MM-DD` se compare correctement en lexicographique). Lève
    `ValueError` sur un filtre malformé (→ 400 côté route)."""
    clauses: list[str] = []
    params: list = []
    if not filters:
        return clauses, params
    if len(filters) > _DS_MAX_FILTERS:
        raise ValueError("too many filters")
    for f in filters:
        if not isinstance(f, dict):
            raise ValueError("invalid filter")
        field, op, val = f.get("field"), f.get("op"), f.get("value")
        if not isinstance(field, str) or not field:
            raise ValueError("invalid filter: `field` manquant ou non textuel")
        if op not in _DS_FILTER_OPS:
            # Dire QUELS opérateurs existent : « invalid filter » nu obligeait à
            # deviner (ou à renoncer et tout rapatrier pour filtrer en local).
            raise ValueError(
                f"opérateur de filtre inconnu `{op}` sur la colonne `{field}` — "
                f"disponibles : {', '.join(sorted(_DS_FILTER_OPS))}")
        if field in _DS_META_TS_COLS:
            if op not in _DS_META_TS_OPS:
                raise ValueError(
                    f"opérateur `{op}` non applicable à `{field}` (date système, "
                    f"toujours renseignée) — disponibles : "
                    f"{', '.join(sorted(_DS_META_TS_OPS))}")
            clause, cparams = _ds_meta_ts_clause(_DS_META_TS_COLS[field], op, str(val))
            clauses.append(clause)
            params.extend(cparams)
        elif field in _DS_META_TEXT_COLS:
            col = _DS_META_TEXT_COLS[field]
            if op not in _DS_META_TEXT_OPS:
                raise ValueError(
                    f"opérateur `{op}` non applicable à `{field}` — disponibles : "
                    f"{', '.join(sorted(_DS_META_TEXT_OPS))}")
            if op == "in":
                vals = [str(v) for v in (val if isinstance(val, list) else [val])
                        if v is not None and str(v) != ""]
                if not vals:
                    continue
                clauses.append(f"{col} = ANY(%s)")
                params.append(vals)
            elif op == "contains":
                clauses.append(f"{col} ILIKE %s")
                params.append(f"%{val}%")
            else:
                clauses.append(f"{col} {'=' if op == 'eq' else '<>'} %s")
                params.append(str(val))
        # À partir d'ici, la colonne est lue par l'expression POLYMORPHE (#318) : elle
        # rend la valeur qu'elle soit plate ou à couches. Le champ y passe DEUX fois
        # (un `%s` par branche du COALESCE) — d'où `fp` plutôt que `field` répété à
        # la main, qui est l'endroit exact où un décalage de paramètres se glisse.
        else:
            # Nom nu → la valeur ; `champ.source` → la couche. Une seule décision,
            # ici, dont toutes les branches héritent.
            V, fp = field_read_sql(field)
            if op == "empty":
                clauses.append(f"({V} IS NULL OR {V} = '')")
                params.extend(fp + fp)
            elif op == "not_empty":
                clauses.append(f"({V} IS NOT NULL AND {V} <> '')")
                params.extend(fp + fp)
            elif op == "in":
                vals = [_ds_text(v) for v in (val if isinstance(val, list) else [val])
                        if v is not None and str(v) != ""]
                if not vals:
                    continue
                clauses.append(f"{V} = ANY(%s)")
                params.extend(fp + [vals])
            elif op == "contains":
                clauses.append(f"{V} ILIKE %s")
                params.extend(fp + [f"%{_ds_text(val)}%"])
            elif op == "eq":
                clauses.append(f"{V} = %s")
                params.extend(fp + [_ds_text(val)])
            elif op == "ne":
                clauses.append(f"({V} IS DISTINCT FROM %s)")
                params.extend(fp + [_ds_text(val)])
            else:  # gt/gte/lt/lte
                sym = _DS_CMP_SQL[op]
                sval = _ds_text(val)
                if _DS_NUM_RE.match(sval):
                    clauses.append(
                        f"({V} ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                        f"AND ({V})::numeric {sym} %s::numeric)")
                    params.extend(fp + fp + [sval])
                else:
                    clauses.append(f"{V} {sym} %s")
                    params.extend(fp + [sval])
    return clauses, params


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
        params.append(order_by)  # valeur paramétrée → pas d'injection
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


def _build_aggregate(ns_id: int, group_by: Optional[str], metrics: Optional[list],
                     q: Optional[str], filters: Optional[list],
                     limit: int) -> tuple[str, list, list]:
    """Construit `(sql, params, names)` de l'agrégat — PUR (aucun I/O), testable sans PG.
    `names` = `[(alias_sql, nom_lisible)]`. Ordre des `%s` : colonnes SELECT (group +
    métriques) puis WHERE puis LIMIT — l'ordre de `params` doit suivre EXACTEMENT.
    Les noms de champs passent en PARAMÈTRES, jamais interpolés (anti-injection) —
    DEUX fois chacun depuis #318, un par branche du COALESCE qui lit une colonne
    plate ou à couches. L'ordre de `sparams` en dépend."""
    metrics = metrics or [{"op": "count"}]
    select, sparams, names = [], [], []  # noms lisibles alignés sur les alias mN
    if group_by:
        _v, _vp = field_read_sql(group_by)
        select.append(f"{_v} AS grp")
        sparams.extend(_vp)
    for i, m in enumerate(metrics):
        op = str(m.get("op", "")).lower()
        field = m.get("field")
        alias = f"m{i}"
        if op == "count" and not field:
            select.append(f"COUNT(*) AS {alias}")
            names.append((alias, "count"))
        elif op == "count":
            _v, _vp = field_read_sql(field)
            select.append(f"COUNT({_v}) AS {alias}")
            sparams.extend(_vp)
            names.append((alias, f"count_{field}"))
        elif op in ("sum", "avg", "min", "max"):
            if not field:
                raise ValueError(f"agrégat: op '{op}' exige un `field`")
            _v, _vp = field_read_sql(field)
            select.append(
                f"{op.upper()}(CASE WHEN {_v} ~ %s "
                f"THEN ({_v})::numeric END) AS {alias}")
            sparams.extend(_vp + [_NUMERIC_RE] + _vp)
            names.append((alias, f"{op}_{field}"))
        else:
            raise ValueError(f"agrégat: op inconnu {op!r} (count|sum|avg|min|max)")
    where, wparams = _ds_where(ns_id, q, filters)
    sql = f"SELECT {', '.join(select)} FROM datastore_rows {where}"
    params = sparams + wparams
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
    for r in rows:
        d: dict = {}
        if group_by:
            d[group_by] = r["grp"]
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


def datastore_merge_row_locked(ns_id: int, row_id: str, apply_fn, updated_at: str):
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
    """
    with _connect() as conn:
        with conn.transaction():
            locked = conn.execute(
                "SELECT data FROM datastore_rows WHERE ns_id = %s AND row_id = %s FOR UPDATE",
                (ns_id, row_id),
            ).fetchone()
            if locked is None:
                return None
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
                         filters: Optional[list] = None) -> Optional[dict]:
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
            "claimed_until = NOW() + (%s || ' seconds')::interval "
            "WHERE ns_id = %s AND row_id = %s "
            "RETURNING row_id, created_at, updated_at, data, claimed_by, claimed_until",
            (str(worker), int(lease_seconds), ns_id, picked["row_id"]),
        ).fetchone()
        return dict(row) if row else None


def datastore_claim_row(ns_id: int, row_id: str, *, worker: str,
                        lease_seconds: int = 900) -> Optional[dict]:
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
            "claimed_until = NOW() + (%s || ' seconds')::interval "
            "WHERE ns_id = %s AND row_id = %s AND (claimed_until IS NULL "
            "OR claimed_until < NOW() OR claimed_by = %s) "
            "RETURNING row_id, created_at, updated_at, data, claimed_by, claimed_until",
            (str(worker), int(lease_seconds), ns_id, row_id, str(worker)),
        ).fetchone()
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
