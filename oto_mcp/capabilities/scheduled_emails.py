"""Capacités de gestion de la file d'envoi d'email différé (ADR 0009).

`email_send` peut différer un envoi (paramètre `send_at` ou garde-fou quiet hours
de l'org). Ces capacités permettent de **lister** et d'**annuler** les emails encore
en attente. Lecture + annulation = membre de l'org (un envoi part au nom de l'org).

Une déclaration → MCP `oto_*` + REST `/api/orgs/{id}/scheduled-emails`.
Pattern de référence : `orgs_invites.py` (list + action par id).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import org_store
from ._authz import ORG_MEMBER_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding

from .registry import CAPABILITIES

_ID = {"id": "org_id"}


class ScheduledEmail(BaseModel):
    """Un email de la file d'envoi. Le corps HTML n'est **jamais** rendu ici (la liste
    reste légère) — seul l'objet permet de le reconnaître."""
    id: int
    to_email: str
    subject: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    # Dérivé du CONNECTEUR au moment de la composition (scaleway→TEM, resend→resend) :
    # figé sur la ligne, il ne suit pas un changement de config ultérieur.
    transport: Optional[str] = None
    # pending | sent | failed | cancelled.
    status: str
    # Instant d'envoi prévu. ⚠️ "YYYY-MM-DD HH:MM:SS" **sans fuseau** (le tzinfo est
    # retiré par le row factory, pas converti) — alors que la fenêtre calme qui l'a
    # calculé, elle, est exprimée dans le fuseau de l'org.
    scheduled_at: Optional[str] = None
    attempts: int
    sent_at: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    created_by: Optional[str] = None


class ScheduledEmails(BaseModel):
    """File d'envoi différé de l'org.

    ⚠️ **Une liste vide ne veut pas dire « rien de programmé »** dans le cas
    général : le filtre par défaut est `status="pending"` (les envoyés, échoués et
    annulés sont exclus — `status="all"` pour tout voir), et la lecture est **plafonnée
    à 100 lignes** côté store, sans compteur total ni curseur. Il n'y a donc aucun
    moyen, depuis cette réponse, de savoir si la file est tronquée."""
    scheduled_emails: list[ScheduledEmail]


class ScheduledEmailCancelled(BaseModel):
    """Annulation d'un envoi différé. Pas d'idempotence ici, contrairement aux autres
    suppressions du domaine : un email déjà parti, déjà annulé ou inexistant rend le
    **même 404** (`unknown_scheduled_email`) — on ne distingue pas « trop tard » de
    « inconnu ». `cancelled` est l'id demandé, réécho ; il ne vaut jamais autre chose."""
    ok: bool
    cancelled: int


class ScheduledListInput(BaseModel):
    org_id: int
    status: str = "pending"     # pending | sent | failed | cancelled | all


class ScheduledCancelInput(BaseModel):
    org_id: int
    email_id: int


def _scheduled_list(ctx: ResolvedCtx, inp: ScheduledListInput) -> dict:
    return {"scheduled_emails": org_store.list_scheduled_emails(inp.org_id, status=inp.status)}


def _scheduled_cancel(ctx: ResolvedCtx, inp: ScheduledCancelInput) -> dict:
    if not org_store.cancel_scheduled_email(inp.org_id, inp.email_id):
        raise AuthzDenied(404, "unknown_scheduled_email",
                          "Email introuvable, déjà parti ou déjà annulé.")
    return {"ok": True, "cancelled": inp.email_id}


CAPABILITIES += [
    Capability(
        key="org.scheduled_email.list", handler=_scheduled_list, Input=ScheduledListInput,
        authz=ORG_MEMBER_OF("org_id"), Output=ScheduledEmails,
        description=("List the org's scheduled (deferred) emails. `status` filters "
                     "pending|sent|failed|cancelled|all (default pending)."),
        rest=RestBinding("GET", "/api/orgs/{id}/scheduled-emails", _ID),
    ),
    Capability(
        key="org.scheduled_email.cancel", handler=_scheduled_cancel, Input=ScheduledCancelInput,
        authz=ORG_MEMBER_OF("org_id"), Output=ScheduledEmailCancelled,
        description="Cancel a still-pending scheduled email of the org by id.",
        rest=RestBinding("DELETE", "/api/orgs/{id}/scheduled-emails/{eid}",
                         {"id": "org_id", "eid": "email_id"}),
    ),
]
