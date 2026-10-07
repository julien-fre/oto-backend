"""Identité de facturation d'une org, vue de l'ADMIN PLATEFORME (#917) — et le
client Pennylane qu'elle désigne.

Le client Pennylane d'une org n'est **jamais** rapproché ni créé par le code : il
est posé ici, à la main, par qui tient la comptabilité. Le rapprochement par une
référence frappée par oto (`oto-org-<id>`) a créé un second client chez le
comptable dès que le client existait déjà, créé à la main (une facture réelle de septembre 2026) ;
une heuristique TVA/SIREN a été écartée — sur des pièces comptables, une fusion à
tort est pire qu'un doublon visible. Un identifiant explicite est la seule chose
qui relie les deux mondes de façon fiable.

Deux surfaces, une seule fiche :
- côté org, `me.billing.identity.set` (billing_identity.py) remplace la fiche en
  bloc et **ne connaît pas** `pennylane_customer_id` — c'est le piège de l'issue :
  un org_admin qui resauvegarde son formulaire ne doit pas effacer l'id ;
- côté admin, `set` ci-dessous remplace la fiche ET pose l'id, dans le même geste
  (le formulaire admin est prérempli de la fiche courante).

REST-only, comme toute la famille billing (ADR 0043) : c'est un formulaire humain,
pas un geste d'agent. `PLATFORM_ADMIN` (admin ou super_admin) : désigner un client
chez le comptable n'ouvre aucun droit à l'org, contrairement à forcer un plan
(réservé super_admin).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from pydantic import BaseModel, Field

from .. import billing, org_store
from ._authz import PLATFORM_ADMIN
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .billing import _domain
from .billing_identity import IdentityInput, IdentityView, _identity_view, write_identity
from .registry import CAPABILITIES

_ID = {"org_id": "org_id"}


class AdminOrgIdInput(BaseModel):
    org_id: int


class AdminIdentityInput(IdentityInput):
    """La fiche ENTIÈRE, plus le client Pennylane. Comme côté org, la capacité
    remplace, elle ne fusionne pas : `pennylane_customer_id` omis ou `null` RETIRE
    la désignation — le formulaire admin est prérempli, il reposte tout."""
    org_id: int
    pennylane_customer_id: Optional[int] = Field(
        default=None,
        description="Identifiant du client chez Pennylane (celui de l'URL de la "
                    "fiche client), posé à la main. `null` = aucun client désigné : "
                    "aucune facture ne sera émise pour cette org tant qu'il manque.")


class AdminIdentityView(IdentityView):
    """La vue de l'org, plus ce que seul l'admin plateforme voit et pose."""
    pennylane_customer_id: Optional[int] = Field(
        default=None,
        description="Client Pennylane désigné pour cette org, ou `null`. Absent de "
                    "la vue servie à l'org : c'est un lien vers la comptabilité "
                    "d'Otomata, pas une donnée du client.")


def _admin_view(org_id: int) -> dict:
    from ..db import billing as db_billing

    vue = _identity_view(org_id)
    row = db_billing.get_billing_identity(org_id)
    vue["pennylane_customer_id"] = row.get("pennylane_customer_id") if row else None
    return vue


def _require_org(org_id: int) -> None:
    if not org_store.get_org(org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{org_id} inconnue.")


def _admin_identity_get(ctx: ResolvedCtx, inp: AdminOrgIdInput) -> dict:
    _require_org(inp.org_id)
    return _admin_view(inp.org_id)


def _admin_identity_set(ctx: ResolvedCtx, inp: AdminIdentityInput) -> dict:
    from ..db import billing as db_billing

    _require_org(inp.org_id)

    def call():
        write_identity(inp.org_id, inp)
        # La fiche vient d'être posée : l'UPDATE trouve toujours sa ligne. Un `False`
        # ici serait un invariant cassé, pas un cas d'usage — on le dit.
        if not db_billing.set_billing_identity_pennylane_customer_id(
                inp.org_id, inp.pennylane_customer_id):
            raise RuntimeError("billing_identity_missing_after_write")
        return _admin_view(inp.org_id)

    return _domain(call)


CAPABILITIES += [replace(_cap, gate=billing.is_enabled) for _cap in [
    Capability(
        key="admin.orgs.billing_identity.get", handler=_admin_identity_get,
        Input=AdminOrgIdInput, authz=PLATFORM_ADMIN, Output=AdminIdentityView,
        rest=RestBinding("GET", "/api/admin/orgs/{org_id}/billing-identity", _ID),
    ),
    Capability(
        key="admin.orgs.billing_identity.set", handler=_admin_identity_set,
        Input=AdminIdentityInput, authz=PLATFORM_ADMIN, Output=AdminIdentityView,
        rest=RestBinding("PUT", "/api/admin/orgs/{org_id}/billing-identity", _ID),
    ),
]]
