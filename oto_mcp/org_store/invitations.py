"""Les INVITATIONS — plateforme, org, équipe : émission, listing, acceptation.

Une seule table `org_invitations` porte les trois scopes de la cascade, dérivés
des cibles posées (`org_id`/`group_id` ⇒ `_scope_of`). Un lien mail (token haché)
et un code court partageable adressent la même invitation.

Étage 1 du package : consomme `members` (adhésion + maison) et `orgs` (nom d'org
dans l'inbox). Les paliers voisins (`roles`, `group_store`) restent en import
PARESSEUX au point d'appel — c'est ce qui évite le cycle.
"""
from __future__ import annotations

import secrets
from typing import Optional

from . import members
from . import orgs
from ..db import _connect, _hash_token


# Codes courts lisibles (code d'invitation d'org). Alphabet Crockford sans
# caractères ambigus (pas de I/L/O/U, 0/1 retirés) → dictable à l'oral, sans
# collision visuelle. 7 chars = ~34 bits ; single-use + TTL + rate-limit côté
# capacité couvrent le brute-force.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"


def _gen_code(n: int = 7) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


def create_invitation(org_id: Optional[int], email: Optional[str], org_role: str, invited_by: str,
                      ttl_days: int = 7, source: Optional[str] = None,
                      group_id: Optional[int] = None,
                      group_role: Optional[str] = None) -> tuple[int, str, str]:
    """Crée une invitation nominative. **Scope dérivé** des cibles (feature cascade
    plateforme/org/équipe, comme les connecteurs) :
    - `org_id=None, group_id=None` → invitation **plateforme** (onboarding pur : à
      l'acceptation l'invité a juste son compte + org perso) ;
    - `org_id` seul → invitation **org** (rejoint l'org) ;
    - `org_id` + `group_id` → invitation **équipe** (rejoint l'org PUIS l'équipe avec
      `group_role`).
    `email` est OPTIONNEL : sans email, l'émetteur partage le code lui-même (pas d'envoi
    mail). Renvoie (id, token plaintext, code court) — token pour le lien mail legacy
    (seul son hash est persisté), code pour le lien /invitation/<code> partageable."""
    email = (email or "").strip().lower() or None
    if email is not None and "@" not in email:
        raise ValueError("email invalide")
    if org_role not in members.ORG_ROLES:
        raise ValueError(f"org_role invalide {org_role!r}")
    if group_id is not None and org_id is None:
        raise ValueError("une invitation d'équipe exige l'org parente (org_id)")
    token = "inv_" + secrets.token_urlsafe(32)
    with _connect() as conn:
        for _ in range(8):
            code = _gen_code()
            if conn.execute(
                "SELECT 1 FROM org_invitations WHERE code = %s", (code,)).fetchone():
                continue
            row = conn.execute(
                """
                INSERT INTO org_invitations
                    (org_id, email, org_role, token_hash, code, invited_by, source,
                     group_id, group_role, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        NOW() + (%s || ' days')::interval)
                RETURNING id
                """,
                (org_id, email, org_role, _hash_token(token), code, invited_by, source,
                 group_id, group_role, str(int(ttl_days))),
            ).fetchone()
            return int(row["id"]), token, code
        raise RuntimeError("impossible de générer un code d'invitation unique")


# Listing enrichi : chaque ligne porte de quoi afficher le scope (nom d'org/équipe)
# + un `scope` dérivé ('platform'|'org'|'team'), commun aux 3 niveaux de la cascade.
_INV_LIST_SELECT = """
    SELECT i.id, i.email, i.code, i.org_role, i.group_role, i.org_id, i.group_id,
           i.invited_by, i.source, i.created_at, i.expires_at,
           o.name AS org_name, g.name AS group_name
      FROM org_invitations i
      LEFT JOIN orgs       o ON o.id = i.org_id
      LEFT JOIN org_groups g ON g.id = i.group_id
     WHERE {pred} AND i.accepted_at IS NULL AND i.expires_at > NOW()
     ORDER BY i.created_at DESC
"""


def _scope_of(r: dict) -> str:
    if r.get("group_id") is not None:
        return "team"
    if r.get("org_id") is not None:
        return "org"
    return "platform"


def _list_invitations(pred: str, *args) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(_INV_LIST_SELECT.format(pred=pred), args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scope"] = _scope_of(d)
        out.append(d)
    return out


def list_invitations(org_id: int) -> list[dict]:
    """Invitations d'ORG en attente (hors invitations d'équipe, qui vivent sur l'écran
    équipe). Non acceptées, non expirées."""
    return _list_invitations("i.org_id = %s AND i.group_id IS NULL", org_id)


def list_group_invitations(group_id: int) -> list[dict]:
    """Invitations d'ÉQUIPE en attente pour ce groupe."""
    return _list_invitations("i.group_id = %s", group_id)


def list_platform_invitations() -> list[dict]:
    """Invitations émises PAR LA PLATEFORME (source='platform_admin'), tous scopes —
    onboarding pur (org_id NULL) ou rattachement direct à une org choisie par l'admin."""
    return _list_invitations("i.source = 'platform_admin'")


def find_pending_invitation(org_id: int, email: str) -> Optional[dict]:
    """L'invitation d'ORG encore valide (non acceptée, non expirée — une révoquée est
    SUPPRIMÉE) adressée à cet email, hors invitations d'équipe : `{id, created_at,
    expires_at}`, sans le code. None s'il n'y en a pas. La plus récente si la file en
    porte plusieurs (possible pour les lignes d'avant le refus #622)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, created_at, expires_at FROM org_invitations "
            "WHERE org_id = %s AND group_id IS NULL AND lower(email) = %s "
            "AND accepted_at IS NULL AND expires_at > NOW() "
            "ORDER BY created_at DESC LIMIT 1",
            (org_id, email),
        ).fetchone()
        return dict(row) if row else None


def revoke_invitation(org_id: int, inv_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM org_invitations WHERE org_id = %s AND group_id IS NULL "
            "AND id = %s AND accepted_at IS NULL",
            (org_id, inv_id),
        )
        return (cur.rowcount or 0) > 0


def revoke_group_invitation(group_id: int, inv_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM org_invitations WHERE group_id = %s AND id = %s "
            "AND accepted_at IS NULL",
            (group_id, inv_id),
        )
        return (cur.rowcount or 0) > 0


def revoke_platform_invitation(inv_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM org_invitations WHERE id = %s AND source = 'platform_admin' "
            "AND accepted_at IS NULL",
            (inv_id,),
        )
        return (cur.rowcount or 0) > 0


def _preview_from_row(r: dict) -> dict:
    return {"email": r.get("email"), "inviter": r.get("inviter"),
            "org_name": r.get("org_name"), "group_name": r.get("group_name"),
            "scope": _scope_of(r)}


_PREVIEW_SELECT = """
    SELECT i.email, i.org_id, i.group_id,
           COALESCE(u.name, u.email) AS inviter,
           o.name AS org_name,
           g.name AS group_name
      FROM org_invitations i
      LEFT JOIN users      u ON u.sub = i.invited_by
      LEFT JOIN orgs       o ON o.id  = i.org_id
      LEFT JOIN org_groups g ON g.id  = i.group_id
     WHERE {pred} AND i.accepted_at IS NULL AND i.expires_at > NOW()
"""


def preview_invitation(token: str) -> Optional[dict]:
    """Aperçu PUBLIC d'une invitation nominative valide (page d'accueil d'invitation,
    avant authentification), par token mail. None si invalide/expirée/déjà acceptée."""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            _PREVIEW_SELECT.format(pred="i.token_hash = %s"), (_hash_token(token),)
        ).fetchone()
        return _preview_from_row(dict(row)) if row else None


def preview_invitation_by_code(code: str) -> Optional[dict]:
    """Aperçu PUBLIC par code court (lien /invitation/<code>)."""
    code = (code or "").strip().upper()
    if not code:
        return None
    with _connect() as conn:
        row = conn.execute(
            _PREVIEW_SELECT.format(pred="i.code = %s"), (code,)
        ).fetchone()
        return _preview_from_row(dict(row)) if row else None


def get_invitation_by_token(token: str) -> Optional[dict]:
    """Invitation valide (non acceptée, non expirée) pour ce token, sinon None."""
    if not token:
        return None
    return _get_invitation("token_hash = %s", _hash_token(token))


def get_invitation_by_code(code: str) -> Optional[dict]:
    """Invitation valide (non acceptée, non expirée) pour ce code court, sinon None."""
    code = (code or "").strip().upper()
    if not code:
        return None
    return _get_invitation("code = %s", code)


def _get_invitation(pred: str, val) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT id, org_id, email, org_role, group_id, group_role,
                   invited_by, source, expires_at
              FROM org_invitations
             WHERE {pred} AND accepted_at IS NULL AND expires_at > NOW()
            """,
            (val,),
        ).fetchone()
        return dict(row) if row else None


def _mark_invitation_accepted(inv_id: int, sub: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE org_invitations SET accepted_at = NOW(), accepted_sub = %s "
            "WHERE id = %s AND accepted_at IS NULL",
            (sub, inv_id),
        )


def _idempotent_accept(pred: str, val, sub: str) -> Optional[dict]:
    """Retour idempotent quand l'invitation ciblée a DÉJÀ été acceptée par le MÊME
    sub (cas vécu : `reconcile_signup_with_invitation` la consomme au 1er getMe, puis
    l'accept explicite la retrouve déjà utilisée → faux 410 alors que l'user est bien
    membre). Renvoie le même dict de succès qu'une acceptation fraîche, ou None si
    l'invitation est vraiment invalide / expirée / acceptée par un AUTRE sub."""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT org_id, org_role, group_id, group_role, accepted_sub "
            f"FROM org_invitations WHERE {pred}",
            (val,),
        ).fetchone()
    if row and row["accepted_sub"] == sub:
        return {"org_id": row.get("org_id"), "org_role": row.get("org_role"),
                "group_id": row.get("group_id"), "group_role": row.get("group_role")}
    return None


def accept_invitation(token: str, sub: str) -> Optional[dict]:
    """Accepte une invitation d'org par token mail. Idempotent si déjà acceptée
    par le même sub ; None si token invalide/expiré/à autrui."""
    if not token:
        return None
    inv = get_invitation_by_token(token)
    if inv:
        return _accept_invitation_row(inv, sub)
    return _idempotent_accept("token_hash = %s", _hash_token(token), sub)


def accept_invitation_by_code(code: str, sub: str) -> Optional[dict]:
    """Accepte une invitation d'org par code court. Idempotent si déjà acceptée
    par le même sub ; None si code invalide/expiré/à autrui."""
    code = (code or "").strip().upper()
    if not code:
        return None
    inv = get_invitation_by_code(code)
    if inv:
        return _accept_invitation_row(inv, sub)
    return _idempotent_accept("code = %s", code, sub)


def _accept_invitation_row(inv: dict, sub: str) -> dict:
    """Cœur de l'acceptation d'une invitation à partir d'une ligne déjà résolue (par
    token, code OU email lors d'une réconciliation de signup). Selon le scope :
    - **org** (org_id présent) → ajoute le membre d'org + bascule l'org active ;
    - **équipe** (group_id présent) → ajoute AUSSI l'équipe (avec `group_role`) et la
      rend active (l'org parente est jointe d'abord — invariant équipe ⊂ org) ;
    - **plateforme** (ni l'un ni l'autre) → l'invité a déjà son compte + org perso au
      signup ; l'acceptation ne fait que marquer l'invitation consommée (attribution).

    **Accepter est un AJOUT, jamais une rétrogradation (#297).** `add_org_member` et
    `add_group_member` sont des upserts : écrire le rôle de l'invitation tel quel
    écrasait VERS LE BAS le rôle déjà détenu — un org_admin invité en `org_member`
    perdait ses droits en cliquant « accepter », et au palier équipe le défaut
    `group_member` rétrogradait un chef même quand l'invitation ne parlait pas
    d'équipe. On garde donc le **maximum des deux rôles** ; l'administrateur qui veut
    rétrograder a la route dédiée (`org.member.set_role`, gardée #273/#280). Les rangs
    viennent de `roles` (source unique de la hiérarchie), jamais recopiés ici.
    """
    # Import paresseux : `roles` importe org_store (et group_store) au niveau module
    # → cycle si on l'importait en tête. À l'appel, tout est chargé.
    from .. import roles
    org_id = inv.get("org_id")
    org_role = inv.get("org_role")
    if org_id is not None:
        org_role = roles.max_org_role(members.get_org_role(org_id, sub), org_role)
        members.add_org_member(org_id, sub, org_role)
        members.set_active_org(sub, org_id)
    group_id = inv.get("group_id")
    group_role = inv.get("group_role")
    if group_id is not None:
        # Import paresseux : org_store n'importe PAS group_store au niveau module
        # (group_store dépend d'org_store → cycle). À l'appel, les deux sont chargés.
        from .. import group_store
        group_role = roles.max_group_role(group_store.get_group_role(group_id, sub),
                                          group_role or "group_member")
        group_store.add_group_member(group_id, sub, group_role)
        group_store.set_active_group(sub, group_id)
    _mark_invitation_accepted(inv["id"], sub)
    # Les rôles rendus sont ceux ÉCRITS, pas ceux de l'invitation : sinon l'écho
    # annonce « tu es org_member » à quelqu'un qui vient de rester org_admin.
    return {"org_id": org_id, "org_role": org_role,
            "group_id": group_id, "group_role": group_role}


def list_pending_invitations_for_email(email: str) -> list[dict]:
    """Invitations en attente ADRESSÉES à cet email (inbox « À traiter », Ship 3 G1) —
    lecture seule, sans accepter. Cross-org par construction (une invitation à rejoindre
    une org vise quelqu'un qui n'en est pas encore membre) → hors scope org active."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, org_id, group_id, invited_by, created_at, code "
            "FROM org_invitations WHERE accepted_at IS NULL AND expires_at > NOW() "
            "AND lower(email) = %s ORDER BY created_at DESC", (email,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            o = orgs.get_org(int(d["org_id"])) if d.get("org_id") else None
            d["org_name"] = o["name"] if o else None
            out.append(d)
        return out


def reconcile_signup_with_invitation(sub: str, email: str) -> Optional[dict]:
    """Honore une invitation d'org par l'EMAIL au signup : si un nouvel inscrit a une
    invitation d'org en attente pour son email vérifié, on l'accepte automatiquement
    — il rejoint directement l'org au lieu de rester avec une invitation orpheline
    (cas vécu : invité qui s'inscrit sans passer par le lien /invite). Sûr car l'email
    est vérifié par Logto (signup email+code). None si aucune invitation."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, org_id, org_role, group_id, group_role, invited_by
              FROM org_invitations
             WHERE accepted_at IS NULL AND expires_at > NOW() AND lower(email) = %s
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (email,),
        ).fetchone()
    if not row:
        return None
    return _accept_invitation_row(dict(row), sub)
