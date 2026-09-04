"""Capacités « partager un tableau » : lister, accorder, retirer un accès nominatif (#302).

Trois verbes sur un même chemin (`…/namespaces/{ns}/share`), qui vivaient en routes
écrites à la main. Le dashboard passe aujourd'hui par la surface générique
`oto_resource` (ADR 0048), mais ces chemins restent le contrat du client HTTP
d'`oto-core` (`DatastoreClient.share`/`unshare`) : ils ne bougent pas, ils gagnent
seulement un schéma d'entrée et de sortie.

`DELETE` avec un corps `{email}` est une forme historique — `RestBinding.reads_body`
la déclare explicitement plutôt que de la deviner (cf. `_types.py`). Sans ce cran, le
corps du retrait aurait été ignoré et chaque appel serait devenu `email_required` : le
genre de régression qu'une migration « invisible » produit sans bruit.

Autz `SUB_ONLY` au seuil, puis la VRAIE garde dans le handler : `govern_ns`, c'est-à-
dire `ownership.can_govern` (propriétaire ∪ gérant ∪ escalade `roles.py`, ADR 0030/
0048). Partager est un acte de gouvernance — jamais un rôle d'org.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ... import db, ownership
from .._authz import SUB_ONLY
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .common import HORODATAGE, govern_ns
from ..registry import CAPABILITIES


class ShareInput(BaseModel):
    namespace: str
    email: str = ""
    # ADR 0068 : partager sans préciser donnait l'ÉCRITURE. « Partager », dans la tête
    # de qui le demande, veut dire « qu'il puisse le lire ».
    permission: str = "read"


class UnshareInput(BaseModel):
    namespace: str
    email: str = ""


class NamespaceRefInput(BaseModel):
    namespace: str


class Share(BaseModel):
    """Un partage nominatif tel que le rend cette surface (vue APLATIE d'un grant)."""
    email: Optional[str] = None
    # `read` | `write` — la projection CONTENU du rôle (ADR 0048).
    permission: Optional[str] = None
    principal_type: Optional[str] = None
    principal_id: Optional[str] = None
    # Date d'octroi (`granted_at` côté `resource_grants`).
    created_at: Optional[str] = Field(default=None, description=HORODATAGE)


class ShareList(BaseModel):
    shares: list[Share]


class Shared(BaseModel):
    ok: bool
    namespace: str
    shared_with: str
    permission: str


class Unshared(BaseModel):
    ok: bool
    namespace: str
    removed: str


def _recipient(email: str) -> dict:
    """Le destinataire, ou le refus tel quel.

    ⚠️ L'ordre des gardes est celui de la route d'avant : le destinataire est résolu
    AVANT la vérification de gouvernance. Un appelant sans droit sur le tableau peut
    donc distinguer « cet email a un compte oto » de « il n'en a pas ». Conservé à
    l'identique — une migration de plomberie ne change pas le comportement observable ;
    l'inversion est un correctif à part, à trancher pour ses propres raisons.
    """
    row = db.get_user_by_email(email)
    if not row:
        raise AuthzDenied(404, f"no oto user with email {email}")
    return row


def _share(ctx: ResolvedCtx, inp: ShareInput) -> dict:
    email = (inp.email or "").strip()
    permission = (inp.permission or "read").strip()
    if not email:
        raise AuthzDenied(400, "email_required")
    if permission not in ("read", "write"):
        # Le message EST le code ici — forme héritée de la route, gardée telle quelle.
        raise AuthzDenied(400, "permission must be 'read' or 'write'")
    recipient = _recipient(email)
    ns_id = govern_ns(ctx.sub, inp.namespace)
    ownership.grant("datastore_namespace", str(ns_id), "user", recipient["sub"],
                    permission, granted_by=ctx.sub)
    return {"ok": True, "namespace": inp.namespace, "shared_with": email,
            "permission": permission}


def _unshare(ctx: ResolvedCtx, inp: UnshareInput) -> dict:
    email = (inp.email or "").strip()
    if not email:
        raise AuthzDenied(400, "email_required")
    recipient = _recipient(email)
    ns_id = govern_ns(ctx.sub, inp.namespace)
    if not ownership.revoke("datastore_namespace", str(ns_id), "user", recipient["sub"]):
        raise AuthzDenied(404, f"no active share for {email} on {inp.namespace}")
    return {"ok": True, "namespace": inp.namespace, "removed": email}


def _list_shares(ctx: ResolvedCtx, inp: NamespaceRefInput) -> dict:
    ns_id = govern_ns(ctx.sub, inp.namespace)
    return {"shares": [
        {"email": s.get("email"), "permission": s.get("permission"),
         "principal_type": s.get("principal_type"), "principal_id": s.get("principal_id"),
         "created_at": s.get("granted_at")}
        for s in ownership.list_grants("datastore_namespace", str(ns_id))
    ]}


_SHARE = "/api/datastore/namespaces/{namespace}/share"

CAPABILITIES += [
    Capability(
        key="me.datastore.list_shares",
        handler=_list_shares,
        Input=NamespaceRefInput,
        Output=ShareList,
        authz=SUB_ONLY,
        mcp=None,  # la face agent du partage est `oto_resource op=share` (ADR 0048)
        rest=RestBinding(verb="GET", path=_SHARE),
        description="Liste les partages nominatifs d'un tableau (droit de gouvernance).",
    ),
    Capability(
        key="me.datastore.share",
        handler=_share,
        Input=ShareInput,
        Output=Shared,
        authz=SUB_ONLY,
        mcp=None,
        rest=RestBinding(verb="POST", path=_SHARE),
        description="Partage un tableau avec un utilisateur oto, en lecture ou écriture.",
    ),
    Capability(
        key="me.datastore.unshare",
        handler=_unshare,
        Input=UnshareInput,
        Output=Unshared,
        authz=SUB_ONLY,
        mcp=None,
        # Corps sur un DELETE : forme historique du client `oto-core`, déclarée.
        rest=RestBinding(verb="DELETE", path=_SHARE, reads_body=True),
        description="Retire le partage d'un tableau pour un utilisateur.",
    ),
]
