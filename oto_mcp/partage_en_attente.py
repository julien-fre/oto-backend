"""Le PARTAGE EN ATTENTE : partager un objet avec une adresse qui n'a pas encore de compte.

Jusqu'au 29/09/2026, `oto_resource op=share` vers une adresse inconnue rendait un 404
`unknown_user`. La seule issue restait d'inviter la personne dans l'ORG entière — ce
qui lui ouvre tout ce que l'org possède, pour partager UN projet. C'est arrivé.

Un partage en attente est une ligne d'`org_invitations` qui porte un OBJET au lieu
d'une org (`org_id` NULL, colonnes `resource_*`, schéma `INVITATIONS_RESSOURCE`) :
même jeton long (seul son hash est persisté, masqué au journal comme tout `{token}`),
même lien `/invitation/<token>`, même échéance, même refus par l'invité. Ce qui
change est ce qu'on obtient : **l'accès à CET objet, par `ownership.grant`, et
jamais une adhésion** — ni à l'inscription par l'adresse vérifiée, ni en cliquant le
lien.

Au moment d'honorer, l'émetteur doit toujours GOUVERNER l'objet : un partage émis
par quelqu'un qui a perdu la main depuis, ou vers un objet supprimé, ne donne rien.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from .db import _connect, _hash_token
from . import org_store
from .org_store import _PENDING

logger = logging.getLogger(__name__)

SOURCE = "resource_share"

_COLONNES = ("resource_type, resource_kind, resource_id, resource_role, "
             "resource_ttl_days, resource_name")


def _norm(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def creer(*, resource_type: str, kind: str, resource_id: str, email: str, role: str,
          ttl_days: Optional[int], invited_by: str, nom: Optional[str],
          jours: int) -> dict:
    """Pose le partage en attente de l'objet vers `email`. Rend `{id, token,
    expires_at, nouveau}` — `token` en clair (à mettre dans le lien, jamais
    persisté) seulement quand la ligne est NEUVE : un partage déjà en attente pour
    le même objet et la même adresse est rendu tel quel (`nouveau=False`, `token`
    None), sans second secret porteur."""
    email = _norm(email)
    if "@" not in email:
        raise ValueError("email invalide")
    token = "inv_" + secrets.token_urlsafe(32)
    with _connect() as conn:
        # Une ligne ÉCHUE garde sa place dans l'index unique (l'échéance n'est pas dans
        # son prédicat) : on la retire avant d'en poser une neuve.
        conn.execute(
            "DELETE FROM org_invitations WHERE resource_kind = %s AND resource_id = %s "
            "AND lower(email) = %s AND accepted_at IS NULL AND declined_at IS NULL "
            "AND expires_at <= NOW()", (kind, resource_id, email))
        row = conn.execute(
            f"""
            INSERT INTO org_invitations
                (org_id, email, token_hash, invited_by, source, expires_at, {_COLONNES})
            VALUES (NULL, %s, %s, %s, %s, NOW() + (%s || ' days')::interval,
                    %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id, expires_at
            """,
            (email, _hash_token(token), invited_by, SOURCE, str(int(jours)),
             resource_type, kind, resource_id, role, ttl_days, nom),
        ).fetchone()
        if row:
            return {"id": int(row["id"]), "token": token,
                    "expires_at": row["expires_at"], "nouveau": True}
        row = conn.execute(
            f"SELECT id, expires_at FROM org_invitations WHERE resource_kind = %s "
            f"AND resource_id = %s AND lower(email) = %s AND {_PENDING}",
            (kind, resource_id, email)).fetchone()
    if not row:  # la ligne concurrente a été acceptée ou refusée entre-temps
        raise RuntimeError("partage en attente introuvable après un conflit")
    return {"id": int(row["id"]), "token": None, "expires_at": row["expires_at"],
            "nouveau": False}


def lister(kind: str, resource_id: str) -> list[dict]:
    """Les partages EN ATTENTE de cet objet (non acceptés, non refusés, non échus)."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, email, resource_role, created_at, expires_at, invited_by "
            f"FROM org_invitations WHERE resource_kind = %s AND resource_id = %s "
            f"AND {_PENDING} ORDER BY created_at",
            (kind, resource_id)).fetchall()
    return [dict(r) for r in rows]


def retirer(kind: str, resource_id: str, email: str) -> bool:
    """Retire le partage en attente de cet objet vers `email` (geste de l'émetteur,
    comme la révocation d'une invitation : la ligne est SUPPRIMÉE)."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM org_invitations WHERE resource_kind = %s AND resource_id = %s "
            "AND lower(email) = %s AND accepted_at IS NULL",
            (kind, resource_id, _norm(email)))
        return (cur.rowcount or 0) > 0


def honorer(inv: dict, sub: str) -> Optional[dict]:
    """Marque ce partage en attente consommé par `sub` et lui donne l'accès qu'il porte.

    None — et RIEN n'est accordé — quand l'émetteur ne gouverne plus l'objet (droits
    retirés, objet supprimé) : le partage est caduc, on ne le rend pas vivant ; None
    aussi quand un autre appel l'a consommé entre-temps. Import
    paresseux d'`ownership` : il dépend d'`org_store`, qui nous appelle."""
    from . import ownership
    kind, rid = inv["resource_kind"], str(inv["resource_id"])
    if not ownership.can_govern(inv.get("invited_by") or "", kind, rid):
        logger.warning("partage en attente #%s caduc : l'émetteur ne gouverne plus "
                       "%s %s — rien n'est accordé", inv.get("id"), kind, rid)
        return None
    # La consommation D'ABORD, dans une transaction tenue ouverte pendant l'accès : de
    # deux porteurs simultanés du même jeton, un seul obtient la ligne (l'autre attend
    # son verrou, puis n'a plus rien à consommer) ; un accès qui échoue annule la
    # consommation. `ownership.grant` écrit sur sa propre connexion : seul un échec du
    # COMMIT, après lui, laisserait l'accès donné sans le partage consommé.
    with _connect() as conn:
        with conn.transaction():
            if not org_store._consommer(conn, inv["id"], sub):
                return None
            ownership.grant(kind, rid, "user", sub, role=inv.get("resource_role"),
                            granted_by=inv.get("invited_by"),
                            ttl_days=inv.get("resource_ttl_days"))
    return {"resource_type": inv.get("resource_type"), "resource_id": rid,
            "resource_role": inv.get("resource_role"),
            "resource_name": inv.get("resource_name")}


def honorer_au_signup(sub: str, email: str) -> list[dict]:
    """À la première inscription, par l'adresse VÉRIFIÉE (signup email + code) :
    honore TOUS les partages en attente adressés à cette adresse — un par objet, et
    une personne peut en avoir reçu plusieurs avant de créer son compte."""
    email = _norm(email)
    if "@" not in email:
        return []
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, invited_by, {_COLONNES} FROM org_invitations "
            f"WHERE resource_kind IS NOT NULL AND lower(email) = %s AND {_PENDING} "
            f"ORDER BY created_at", (email,)).fetchall()
    return [r for r in (honorer(dict(row), sub) for row in rows) if r]
