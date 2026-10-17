"""`service.org.spend_cap` — ce qu'une org peut encore ENGAGER sur une clé plateforme.

Le commerce connaît le solde de chaque org et le prix de chaque résultat ; le cœur n'en
connaît aucun. Il pousse donc, par org et par connecteur, ce qui reste engageable en
unités du FOURNISSEUR, et le cœur le réserve à chaque soumission
(`access/spend_cap.py`) : un job facturé au relevé que l'org ne peut pas payer est
refusé avant d'être envoyé. `units_left: null` retire le plafond.

Mêmes portes que la fin d'essai (`service.org.trial`) : l'identité de service
`commerce`, REST seule, et le même geste pour un super admin, à la main.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import org_store
from ..access import spend_cap
from ..db import spend_cap as db_cap
from ._authz import COMMERCE_SERVICE, SUPER_ADMIN
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)


class OrgSpendCapInput(BaseModel):
    org_id: int
    connector: Literal["fullenrich"]
    # Ce qui restait engageable à `as_of`, en unités du fournisseur. `null` = pas de
    # plafond (l'org paie sans limite, ou le commerce cesse de la plafonner).
    units_left: Optional[int] = Field(default=None, ge=0)
    # L'instant jusqu'où le commerce a lu ce que l'org a engagé : ce qui a été soumis
    # APRÈS est recompté du journal, jamais oublié.
    as_of: datetime


class OrgSpendCapOut(BaseModel):
    org_id: int
    connector: str
    units_left: Optional[int] = None
    # Ce que l'org a engagé depuis `as_of`, recompté du journal à la pose.
    engaged: Optional[int] = None
    as_of: Optional[str] = None


def _org_spend_cap(ctx: ResolvedCtx, inp: OrgSpendCapInput) -> dict:
    if inp.as_of.tzinfo is None:
        raise AuthzDenied(422, "as_of_without_timezone",
                          "`as_of` doit porter son fuseau (ISO 8601 avec `Z` ou "
                          "`+HH:MM`) : sans lui, l'instant serait deviné.")
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    row = db_cap.poser(inp.org_id, inp.connector, inp.units_left, inp.as_of,
                       spend_cap.ENGAGING_TOOLS[inp.connector])
    logger.info("plafond d'engagement org=%s %s par=%s : %s", inp.org_id, inp.connector,
                ctx.sub, row)
    return {"org_id": inp.org_id, "connector": inp.connector,
            "units_left": row["units_left"] if row else None,
            "engaged": row["engaged"] if row else None,
            "as_of": inp.as_of.isoformat() if row else None}


_ERREURS = (
    DeclaredError(404, "unknown_org", "aucune org ne porte cet id"),
    DeclaredError(422, "as_of_without_timezone", "`as_of` sans fuseau"),
)

_DESCRIPTION = (
    "Set what an org can still COMMIT on a platform key, in the provider's units "
    "(`units_left`, as of `as_of` — ISO 8601 with its offset), or clear it with "
    "`units_left: null`. Each submission of a job billed at result time reserves its "
    "worst case against it, and is refused when it does not fit. What the org "
    "committed after `as_of` is recounted from the journal, never forgotten.")

CAPABILITIES += [
    # REST seule, comme `service.org.trial` : un tuyau de service.
    Capability(
        key="service.org.spend_cap", handler=_org_spend_cap, Input=OrgSpendCapInput,
        Output=OrgSpendCapOut, authz=COMMERCE_SERVICE, mcp=None,
        errors=_ERREURS,
        description="[service commerce] " + _DESCRIPTION,
        rest=RestBinding("POST", "/api/service/orgs/{id}/spend-cap", {"id": "org_id"}),
    ),
    Capability(
        key="admin.org_spend_cap", handler=_org_spend_cap, Input=OrgSpendCapInput,
        Output=OrgSpendCapOut, authz=SUPER_ADMIN, mcp=None,
        errors=_ERREURS,
        description="[super admin] " + _DESCRIPTION,
        rest=RestBinding("POST", "/api/admin/orgs/{id}/spend-cap", {"id": "org_id"}),
    ),
]
