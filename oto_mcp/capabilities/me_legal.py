"""Acceptation des documents légaux — face de `me.legal` (gate frontend LegalGate).

`GET /api/me/legal` rend le `LegalStatus` (docs + reste-à-accepter par contexte) ;
`POST /api/me/legal/accept {context}` enregistre l'acceptation des docs requis du
contexte à leur version COURANTE. SUB_ONLY (self-service, `/api/me/*`). Source des
docs = `legal_docs.docs_for(tenant)` (défaut plateforme, ou l'override du tenant de
CE sub — `tenancy.current().tenant_of`) ; trace = table `legal_acceptances` (`db.*`),
elle-même jamais tenant-scopée (un sub qualifié `tulina:...` en est déjà le scope).

REST-only : le consentement est un acte de l'utilisateur dans le dashboard, pas un
canal agent → pas de binding MCP.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import db, legal_docs, tenancy
from ._authz import SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class _NoInput(BaseModel):
    pass


class AcceptInput(BaseModel):
    context: str


class LegalDocument(BaseModel):
    slug: str                                    # terms | cgv | dpa | privacy | legal
    version: str                                 # version COURANTE du doc
    url: str
    label: str
    accepted: bool                               # accepté À LA VERSION COURANTE
    accepted_version: Optional[str] = None       # ce qu'il a accepté, s'il l'a fait
    accepted_at: Optional[str] = None


class LegalContext(BaseModel):
    """Ce qu'un contexte exige, et ce qui manque encore — `outstanding` vide = le
    gate passe."""
    required: list[str]
    outstanding: list[str]


class LegalStatus(BaseModel):
    """Forme des DEUX faces : `accept` renvoie le statut rafraîchi, pas un accusé."""
    documents: list[LegalDocument]
    contexts: dict[str, LegalContext]            # clé = 'access' | 'purchase'


def _is_current(acc: dict, docs: dict, slug: str) -> bool:
    a = acc.get(slug)
    return a is not None and a["version"] == docs[slug]["version"]


def _status(sub: str, tenant_slug: str = tenancy.PRIMARY_SLUG) -> dict:
    """Compose le LegalStatus attendu par le front (documents + contexts), contre
    les docs EFFECTIFS de `tenant_slug` (défaut : la plateforme, `oto`)."""
    docs = legal_docs.docs_for(tenant_slug)
    acc = db.get_legal_acceptances(sub)
    documents = []
    for slug, meta in docs.items():
        a = acc.get(slug)
        documents.append({
            "slug": slug,
            "version": meta["version"],
            "url": meta["url"],
            "label": meta["label"],
            "accepted": _is_current(acc, docs, slug),
            "accepted_version": a["version"] if a else None,
            "accepted_at": a["accepted_at"] if a else None,
        })
    contexts = {}
    for ctx, required in legal_docs.CONTEXTS.items():
        outstanding = [s for s in required if not _is_current(acc, docs, s)]
        contexts[ctx] = {"required": required, "outstanding": outstanding}
    return {"documents": documents, "contexts": contexts}


def _get(ctx: ResolvedCtx, inp: _NoInput) -> dict:
    return _status(ctx.sub, tenancy.current().tenant_of(ctx.sub))


def _accept(ctx: ResolvedCtx, inp: AcceptInput) -> dict:
    required = legal_docs.CONTEXTS.get(inp.context)
    if required is None:
        raise AuthzDenied(400, "unknown_context", f"Contexte légal inconnu : {inp.context!r}.")
    tenant_slug = tenancy.current().tenant_of(ctx.sub)
    docs = legal_docs.docs_for(tenant_slug)
    # La version enregistrée est celle du doc que CE sub a vu (son tenant), pas
    # forcément celle d'oto — sinon un Tulina qui accepte les CGU de Tulina se
    # verrait rouvrir le gate au prochain bump d'oto, sans rapport avec lui.
    db.record_legal_acceptances(ctx.sub, [(slug, docs[slug]["version"]) for slug in required])
    return _status(ctx.sub, tenant_slug)


CAPABILITIES += [
    Capability(
        key="me.legal.get", handler=_get, Input=_NoInput,
        authz=SUB_ONLY, Output=LegalStatus,
        description="The user's legal acceptance status: current documents "
                    "(slug/version/url/label + whether accepted) and, per context "
                    "('access'|'purchase'), the required docs and those still outstanding.",
        rest=RestBinding("GET", "/api/me/legal"),
    ),
    Capability(
        key="me.legal.accept", handler=_accept, Input=AcceptInput,
        authz=SUB_ONLY, Output=LegalStatus,
        description="Record the user's acceptance of the documents required by a "
                    "context ('access' at signup, 'purchase' at checkout) at their "
                    "current version. Returns the refreshed legal status.",
        rest=RestBinding("POST", "/api/me/legal/accept"),
    ),
]
