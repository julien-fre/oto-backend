"""Autorisations de compte connecteur partagé (otomata-private#55).

Le propriétaire d'un compte Unipile accorde à un membre nommé le droit d'OPÉRER
son compte sur un canal (`connector_account_grants`), et le grantee pointe le
compte qu'il opère (`unipile_operated_accounts`). Deux plans distincts : le grant
= le DROIT (deny-by-default, révocable, audité) ; le pointeur = le CHOIX courant,
jamais un droit (revalidé contre les grants vivants à chaque appel).

⚠️ Pas de fail-open ici (≠ RBAC ADR 0025) : ce chemin est le backstop d'identité
— une erreur infra doit lever, pas laisser passer une usurpation.

⚠️ **Corrigé le 2026-09-02** (trouvé en écrivant les tests DB réels de l'extension
groupe, `tests/test_account_group_grants_db.py`) : toutes les jointures vers
`unipile_accounts` omettaient `disconnected_at IS NULL`. Le SOFT-disconnect
(`clear_unipile_account`) laisse la ligne vivre (`docs/unipile.md`), donc sans cette
condition un canal déconnecté restait « vivant » aux yeux de ces requêtes — la
docstring de `granted_accounts_for` promettait déjà « déconnexion = disparition
immédiate », promesse qu'aucun test n'exerçait et que le SQL ne tenait pas. Jamais
constaté en prod (aucun signal, aucun ticket) mais jamais prouvé faux non plus —
latent depuis la table d'origine (#55), pas introduit par l'extension groupe.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect


def set_account_grant(owner_sub: str, provider: str, account_id: str,
                      grantee_sub: str, granted_by: str) -> None:
    """Accorde (upsert) à `grantee_sub` le droit d'opérer le compte `provider` de
    `owner_sub`. `account_id` = snapshot d'audit (la résolution relit le live)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO connector_account_grants "
            "(owner_sub, provider, account_id, grantee_sub, granted_by) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (owner_sub, provider, grantee_sub) DO UPDATE SET "
            "account_id = EXCLUDED.account_id, granted_by = EXCLUDED.granted_by, "
            "granted_at = NOW()",
            (owner_sub, provider, account_id, grantee_sub, granted_by),
        )


def clear_account_grant(owner_sub: str, provider: str, grantee_sub: str) -> bool:
    """Révoque le grant. True si une ligne a été supprimée (idempotent sinon)."""
    with _connect() as conn:
        n = conn.execute(
            "DELETE FROM connector_account_grants "
            "WHERE owner_sub = %s AND provider = %s AND grantee_sub = %s",
            (owner_sub, provider, grantee_sub),
        ).rowcount
    return n > 0


def list_account_grants_by_owner(owner_sub: str) -> list[dict]:
    """Grants accordés PAR ce propriétaire (face « qui opère mes comptes »).
    `account_id`/`account_name` = état LIVE du compte (LEFT JOIN, condition posée
    dans le ON pour garder la ligne à l'affichage — None si le canal a été
    déconnecté depuis, le grant est alors inerte)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT g.provider, ua.account_id, ua.account_name, "
            "g.grantee_sub, u.email AS grantee_email, u.name AS grantee_name, "
            "g.granted_by, g.granted_at, (ua.account_id IS NOT NULL) AS active "
            "FROM connector_account_grants g "
            "LEFT JOIN users u ON u.sub = g.grantee_sub "
            "LEFT JOIN unipile_accounts ua "
            "  ON ua.sub = g.owner_sub AND ua.provider = g.provider "
            "  AND ua.disconnected_at IS NULL "
            "WHERE g.owner_sub = %s ORDER BY g.provider, g.granted_at",
            (owner_sub,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_account_grants_to(grantee_sub: str) -> list[dict]:
    """Grants reçus PAR ce user (face « comptes que je peux opérer ») — nominatifs
    ET de groupe (UNION, `org_group_members` rejoint EN LIVE : un départ de groupe
    retire la ligne au prochain appel, sans rien à nettoyer côté grant).
    `active=False` si le owner a déconnecté le canal (grant inerte).
    `owner_org_id`/`owner_org_name` = l'org sous laquelle le owner a connecté ce
    compte (`unipile_accounts.org_id`) — pour que l'UI dise D'OÙ vient le partage
    (le grant lui-même n'est pas scopé à une org). `via_group_id`/`via_group_name`
    = None sur un grant nominatif, le groupe qui porte l'accès sinon."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT g.provider, g.owner_sub, u.email AS owner_email, "
            "u.name AS owner_name, ua.account_id, ua.account_name, "
            "ua.org_id AS owner_org_id, o.name AS owner_org_name, "
            "g.granted_at, (ua.account_id IS NOT NULL) AS active, "
            "NULL::BIGINT AS via_group_id, NULL::TEXT AS via_group_name "
            "FROM connector_account_grants g "
            "LEFT JOIN users u ON u.sub = g.owner_sub "
            "LEFT JOIN unipile_accounts ua "
            "  ON ua.sub = g.owner_sub AND ua.provider = g.provider "
            "  AND ua.disconnected_at IS NULL "
            "LEFT JOIN orgs o ON o.id = ua.org_id "
            "WHERE g.grantee_sub = %s "
            "UNION ALL "
            "SELECT gg.provider, gg.owner_sub, u.email AS owner_email, "
            "u.name AS owner_name, ua.account_id, ua.account_name, "
            "ua.org_id AS owner_org_id, o.name AS owner_org_name, "
            "gg.granted_at, (ua.account_id IS NOT NULL) AS active, "
            "gg.grantee_group_id AS via_group_id, grp.name AS via_group_name "
            "FROM connector_account_group_grants gg "
            "JOIN org_group_members m ON m.group_id = gg.grantee_group_id "
            "LEFT JOIN org_groups grp ON grp.id = gg.grantee_group_id "
            "LEFT JOIN users u ON u.sub = gg.owner_sub "
            "LEFT JOIN unipile_accounts ua "
            "  ON ua.sub = gg.owner_sub AND ua.provider = gg.provider "
            "  AND ua.disconnected_at IS NULL "
            "LEFT JOIN orgs o ON o.id = ua.org_id "
            "WHERE m.sub = %s "
            "ORDER BY provider, granted_at",
            (grantee_sub, grantee_sub),
        ).fetchall()
    return [dict(r) for r in rows]


def granted_accounts_for(grantee_sub: str, provider: str) -> dict[str, dict]:
    """LE check dur par appel : comptes que `grantee_sub` est autorisé à opérer
    sur ce canal, `{account_id_live: {owner_sub, owner_email}}` — nominatifs ET
    reçus via un groupe dont il est membre EN CE MOMENT (JOIN live, pas un
    snapshot). INNER JOIN sur le compte NON DÉCONNECTÉ du owner ⇒ révocation,
    départ du groupe OU déconnexion = disparition immédiate."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ua.account_id, g.owner_sub, u.email AS owner_email "
            "FROM connector_account_grants g "
            "JOIN unipile_accounts ua "
            "  ON ua.sub = g.owner_sub AND ua.provider = g.provider "
            "  AND ua.disconnected_at IS NULL "
            "LEFT JOIN users u ON u.sub = g.owner_sub "
            "WHERE g.grantee_sub = %s AND g.provider = %s "
            "UNION ALL "
            "SELECT ua.account_id, gg.owner_sub, u.email AS owner_email "
            "FROM connector_account_group_grants gg "
            "JOIN org_group_members m ON m.group_id = gg.grantee_group_id "
            "JOIN unipile_accounts ua "
            "  ON ua.sub = gg.owner_sub AND ua.provider = gg.provider "
            "  AND ua.disconnected_at IS NULL "
            "LEFT JOIN users u ON u.sub = gg.owner_sub "
            "WHERE m.sub = %s AND gg.provider = %s",
            (grantee_sub, provider, grantee_sub, provider),
        ).fetchall()
    return {r["account_id"]: {"owner_sub": r["owner_sub"],
                              "owner_email": r["owner_email"]} for r in rows}


def set_account_group_grant(owner_sub: str, provider: str, account_id: str,
                            grantee_group_id: int, granted_by: str) -> None:
    """Accorde (upsert) à TOUS LES MEMBRES ACTUELS de `grantee_group_id` le droit
    d'opérer le compte `provider` de `owner_sub` — même contrat que
    `set_account_grant`, cible groupe plutôt que sub nommé."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO connector_account_group_grants "
            "(owner_sub, provider, account_id, grantee_group_id, granted_by) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (owner_sub, provider, grantee_group_id) DO UPDATE SET "
            "account_id = EXCLUDED.account_id, granted_by = EXCLUDED.granted_by, "
            "granted_at = NOW()",
            (owner_sub, provider, account_id, grantee_group_id, granted_by),
        )


def clear_account_group_grant(owner_sub: str, provider: str,
                              grantee_group_id: int) -> bool:
    """Révoque le grant de groupe. True si une ligne a été supprimée (idempotent
    sinon)."""
    with _connect() as conn:
        n = conn.execute(
            "DELETE FROM connector_account_group_grants "
            "WHERE owner_sub = %s AND provider = %s AND grantee_group_id = %s",
            (owner_sub, provider, grantee_group_id),
        ).rowcount
    return n > 0


def list_account_group_grants_by_owner(owner_sub: str) -> list[dict]:
    """Grants de GROUPE accordés PAR ce propriétaire (face « qui opère mes
    comptes », pendant groupe de `list_account_grants_by_owner`)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT gg.provider, ua.account_id, ua.account_name, "
            "gg.grantee_group_id, grp.name AS grantee_group_name, "
            "gg.granted_by, gg.granted_at, (ua.account_id IS NOT NULL) AS active "
            "FROM connector_account_group_grants gg "
            "LEFT JOIN org_groups grp ON grp.id = gg.grantee_group_id "
            "LEFT JOIN unipile_accounts ua "
            "  ON ua.sub = gg.owner_sub AND ua.provider = gg.provider "
            "  AND ua.disconnected_at IS NULL "
            "WHERE gg.owner_sub = %s ORDER BY gg.provider, gg.granted_at",
            (owner_sub,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_operated_account(sub: str, provider: str) -> Optional[dict]:
    """Pointeur « identité opérée » du user pour ce canal
    (`{account_id, owner_sub}`), ou None (= il opère son propre compte)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT account_id, owner_sub FROM unipile_operated_accounts "
            "WHERE sub = %s AND provider = %s",
            (sub, provider),
        ).fetchone()
    return dict(row) if row else None


def set_operated_account(sub: str, provider: str, account_id: str,
                         owner_sub: str) -> None:
    """Pose (upsert) le pointeur « j'opère ce compte accordé » pour ce canal."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO unipile_operated_accounts (sub, provider, account_id, owner_sub) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (sub, provider) DO UPDATE SET "
            "account_id = EXCLUDED.account_id, owner_sub = EXCLUDED.owner_sub, "
            "selected_at = NOW()",
            (sub, provider, account_id, owner_sub),
        )


def clear_operated_account(sub: str, provider: str) -> None:
    """Retour-à-soi : efface le pointeur du canal (le user opère à nouveau SON compte)."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM unipile_operated_accounts WHERE sub = %s AND provider = %s",
            (sub, provider),
        )


def clear_operated_pointers_to(owner_sub: str, provider: str, grantee_sub: str) -> None:
    """Hygiène au revoke : efface le pointeur du grantee s'il opérait ce compte.
    Best-effort — le backstop (`granted_accounts_for` à chaque appel) ne repose PAS
    dessus, un pointeur orphelin lève une erreur explicite à l'appel suivant."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM unipile_operated_accounts "
            "WHERE sub = %s AND provider = %s AND owner_sub = %s",
            (grantee_sub, provider, owner_sub),
        )


def clear_operated_pointers_to_group(owner_sub: str, provider: str,
                                     grantee_group_id: int) -> None:
    """Hygiène au revoke d'un grant de groupe : efface le pointeur de CHAQUE membre
    actuel du groupe qui opérait ce compte. Même best-effort que
    `clear_operated_pointers_to` — un membre déjà sorti du groupe n'a plus de ligne
    ici à nettoyer (`granted_accounts_for` le refuserait de toute façon)."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM unipile_operated_accounts "
            "WHERE provider = %s AND owner_sub = %s AND sub IN "
            "(SELECT sub FROM org_group_members WHERE group_id = %s)",
            (provider, owner_sub, grantee_group_id),
        )
