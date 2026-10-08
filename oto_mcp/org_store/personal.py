"""L'org PERSONNELLE (`orgs.personal_of`) et le rattrapage de boot.

Depuis la suppression du « perso » org-less (ADR 0030 §8), tout user est toujours
dans une org : `ensure_personal_org` garantit son espace privé mono-membre et
qu'il a une org maison. `backfill_personal_orgs` le rejoue au boot, idempotent.

Étage 1 du package : consomme `orgs` (création) et `members` (adhésion, maison).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import members
from . import orgs
from ..db import _connect

_log = logging.getLogger(__name__)


def get_personal_org(sub: str) -> Optional[int]:
    """Org PERSO de `sub` (son étiquette `personal_of=sub`), ou None. L'étiquette fait
    foi : l'org peut avoir d'autres membres (29/09/2026)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM orgs WHERE personal_of = %s AND archived_at IS NULL", (sub,)
        ).fetchone()
        return int(row["id"]) if row else None


def is_personal_org(org_id: int) -> bool:
    """True si l'org est un **espace personnel** (`personal_of` renseigné) — non
    supprimable (elle serait recréée au boot par `ensure_personal_org`)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT personal_of FROM orgs WHERE id = %s", (org_id,)
        ).fetchone()
        return bool(row and row["personal_of"] is not None)


def _personal_label(email: Optional[str], name: Optional[str]) -> str:
    return (name or (email.split("@")[0] if email else None) or "Mon espace").strip() or "Mon espace"


def _reclaim_or_create_personal(sub: str, email: Optional[str], name: Optional[str]) -> int:
    """Récupère ou crée l'org perso de `sub`. **Réclamation** : sans étiquette, si `sub`
    n'est membre que d'UNE org vivante et qu'il l'a créée, elle reçoit l'étiquette,
    quel que soit son nombre de membres — « perso » n'est qu'une étiquette (29/09/2026),
    et en recréer une à côté lui cacherait ses objets sans org de création (vécu le
    29/09 : une org perso neuve créée au boot pour un compte dont l'org d'inscription
    avait perdu son étiquette en accueillant un 2ᵉ membre). Un compte membre de
    plusieurs orgs garde ses orgs telles quelles : on lui en crée une neuve."""
    with _connect() as conn:
        # Auto-soin (couvre les DEUX branches, reclaim ET create) : une org perso
        # ARCHIVÉE détient encore le slot unique `uq_orgs_personal_of` tout en étant
        # invisible à `get_personal_org` (filtre `archived_at IS NULL`) → la relâcher
        # AVANT tout marquage, sinon UniqueViolation en boucle à chaque boot (vécu
        # 2026-07-01 : perso archivée → orgs orphelines recréées, une par boot ; la
        # collision frappait aussi bien la branche reclaim que la branche create).
        conn.execute(
            "UPDATE orgs SET personal_of = NULL "
            "WHERE personal_of = %s AND archived_at IS NOT NULL",
            (sub,),
        )
        row = conn.execute(
            """
            SELECT o.id FROM orgs o
             WHERE o.created_by = %s AND o.personal_of IS NULL AND o.archived_at IS NULL
               AND EXISTS (SELECT 1 FROM org_members m WHERE m.org_id = o.id AND m.sub = %s)
               AND (SELECT count(*) FROM org_members m2 JOIN orgs o2 ON o2.id = m2.org_id
                     WHERE m2.sub = %s AND o2.archived_at IS NULL) = 1
             LIMIT 1
            """,
            (sub, sub, sub),
        ).fetchone()
        if row:
            oid = int(row["id"])
            conn.execute("UPDATE orgs SET personal_of = %s WHERE id = %s", (sub, oid))
            _log.info("ensure_personal_org: org #%s réclamée comme perso de %s", oid, sub)
            return oid
    oid = orgs.create_org(_personal_label(email, name), created_by=sub)
    members.add_org_member(oid, sub, org_role="org_admin", actor=None)  # le système
    with _connect() as conn:
        conn.execute("UPDATE orgs SET personal_of = %s WHERE id = %s", (sub, oid))
    _log.info("ensure_personal_org: org perso #%s créée pour %s", oid, sub)
    # Onboarding = un projet (ADR 0032 §7) : on sème le projet « Découverte » dans l'org
    # perso fraîchement créée (une seule fois, ici — pas sur la branche reclaim). Best-effort.
    from .. import discovery
    discovery.seed_for_org(sub, oid)
    return oid


def ensure_personal_org(sub: str, email: Optional[str] = None, name: Optional[str] = None) -> int:
    """Garantit l'**org perso** de `sub` (suppression du perso `org_id=0`) ET qu'il a une
    org active (la perso si aucune autre). Idempotent."""
    pid = get_personal_org(sub)
    if pid is None:
        pid = _reclaim_or_create_personal(sub, email, name)
    if members.get_active_org(sub) is None:   # nouveau user / ex-perso → la perso devient maison
        members.set_active_org(sub, pid)
    return pid


# Les seuls comptes que `ensure_personal_org` aurait à toucher : sans org perso vivante
# (`get_personal_org`) OU sans org active (`get_active_org`). Pour tout autre compte,
# `ensure_personal_org` ne fait que ces deux lectures — et le boot les payait, par compte.
_A_RATTRAPER = """
    SELECT u.sub, u.email, u.name FROM users u
     WHERE NOT EXISTS (SELECT 1 FROM orgs o
                        WHERE o.personal_of = u.sub AND o.archived_at IS NULL)
        OR NOT EXISTS (SELECT 1 FROM org_members m WHERE m.sub = u.sub AND m.is_active)
    -- Le compte de service d'une org (`members.assurer_compte_de_service`) n'a ni org
    -- perso ni ligne de membre, et ne doit pas en recevoir.
    EXCEPT SELECT u.sub, u.email, u.name FROM users u
      JOIN org_service_accounts s ON s.sub = u.sub
"""


def backfill_personal_orgs() -> dict:
    """Idempotent (boot) : chaque user a une **org perso** marquée, et une org active
    (la perso si aucune autre). Rend `{"users": <comptes rattrapés>}`.

    **Garde « déjà fait », par compte** (oto-backend#534) : une seule requête retient les
    comptes à rattraper — le prédicat même d'`ensure_personal_org` — au lieu de lire
    TOUS les users et de faire deux allers-retours par compte à chaque boot (1,4 s
    mesurés en préproduction, croissant avec la base d'utilisateurs). Le filet reste
    entier : un compte laissé sans espace (naissance incomplète, archivage) est
    toujours réparé au boot suivant (`db/users.py`, `capabilities/orgs/update.py`).

    ⚠️ Ne TOUCHE PLUS aux ressources. La migration `owner_type='user'` → org perso qui
    vivait ici datait de la suppression du perso `org_id=0` ; depuis l'amendement ADR
    0030 §8 (2026-07-17) `owner_type='user'` n'est plus un vestige à rattraper mais le
    **scope membre** — un projet PRIVÉ rangé dans le contexte d'une org (`context_org_id`).
    Rejouée à chaque boot, elle DÉTRUISAIT ce scope : le projet privé quittait l'org de
    travail pour l'espace perso de son auteur → « mon projet a disparu » côté user (vécu
    2026-07-28, aucun projet `owner_type='user'` ne survivait en prod)."""
    counts = {"users": 0}
    with _connect() as conn:
        users = conn.execute(_A_RATTRAPER).fetchall()
    for u in users:
        sub = u["sub"]
        try:
            ensure_personal_org(sub, u.get("email"), u.get("name"))
        except Exception:
            _log.warning("backfill_personal_orgs: ensure échoué %s", sub, exc_info=True)
            continue
        counts["users"] += 1
    return counts
