"""Capacité : MFA obligatoire par org (ADR 0009 + voie « org Logto miroir »).

Un org_admin active/désactive l'exigence du 2ᵉ facteur pour les membres de l'org.
L'activation provisionne une **organization Logto miroir** (isMfaRequired + membres
synchronisés par `sub`) via `mfa_mirror` ; combiné au réglage tenant
`organizationRequiredMfaPolicy=Mandatory`, Logto force alors le MFA au **login
ordinaire** de tout membre. Voir `mfa_mirror.py` et `docs/auth-logto.md` §MFA par org.

C'est donc une capacité du tenant **`oto`** : le miroir vit dans NOTRE Logto, et un
membre venu d'un tenant tiers n'y est pas inscriptible (arbitrage oto-backend#274).
`org.mfa.get` rend le compte de ces membres (`members_other_tenant`) — le filtrage
doit se constater, sinon une org mixte lirait « MFA actif » pour tout le monde.

Lecture = membre ; écriture = org_admin. **Pas de fail-open** : si le provisioning
Logto échoue, le drapeau n'est pas posé (activation) ou reste posé (désactivation)
— l'état PG ne prétend jamais un MFA actif qui ne l'est pas.

Une déclaration → deux surfaces (MCP `oto_*` + REST `/api/orgs/{id}/mfa`).
Pattern de référence : `orgs/field_filters.py`.
"""
from __future__ import annotations

from pydantic import BaseModel

from ... import mfa_mirror, org_store
from .._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}


class OrgMfaState(BaseModel):
    """Exigence de 2ᵉ facteur de l'org.

    ⚠️ **`provisioned` ne suit pas `require_mfa`.** Il dit que l'organization Logto
    miroir EXISTE, pas qu'elle impose quoi que ce soit : après une désactivation on
    **conserve** le miroir (réactivation sans re-sync) → l'état durable
    `require_mfa: false` + `provisioned: true` est parfaitement normal. Seul
    `require_mfa` fait foi ; `provisioned: false` avec `require_mfa: true` serait, lui,
    une incohérence (le provisioning précède toujours la pose du drapeau).

    `members_other_tenant` = combien de membres de l'org ce drapeau **ne couvre pas**,
    parce qu'ils relèvent d'un autre émetteur d'identité (tenant) : ils ne sont pas
    dans notre annuaire Logto, donc pas inscriptibles au miroir, et c'est la politique
    MFA de leur propre émetteur qui s'applique à eux. Vaut 0 partout aujourd'hui (le
    tenant `oto` est le seul à porter des comptes). Il est exposé ici parce que le
    filtrage doit être CONSTATABLE : muet, il ferait dire « MFA actif » à une org dont
    trois membres n'y sont pas soumis (oto-backend#274)."""
    org_id: int
    require_mfa: bool
    provisioned: bool
    members_other_tenant: int


class OrgMfaSet(BaseModel):
    """Bascule de l'exigence MFA. `require_mfa` est l'écho de la valeur DEMANDÉE — il
    est fiable parce qu'il n'y a pas de succès partiel : si Logto refuse, l'appel sort
    en 502 (`logto_provisioning_failed` / `logto_deprovisioning_failed`) **sans avoir
    changé l'état** (pas de fail-open sur un contrôle de sécurité).

    ⚠️ `ok: true` ne veut pas dire « les membres sont protégés maintenant » : les
    sessions ouvertes ne sont pas coupées, l'enrôlement est exigé à la **prochaine
    connexion** de chaque membre."""
    ok: bool
    org_id: int
    require_mfa: bool


class GetOrgMfaInput(BaseModel):
    org_id: int


class SetOrgMfaInput(BaseModel):
    org_id: int
    require: bool


def _get_org_mfa(ctx: ResolvedCtx, inp: GetOrgMfaInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    state = org_store.get_org_mfa(inp.org_id)
    return {"org_id": inp.org_id, "require_mfa": state["require_mfa"],
            "provisioned": bool(state["logto_org_id"]),
            "members_other_tenant": len(
                mfa_mirror.foreign_tenant_members(inp.org_id))}


def _set_org_mfa(ctx: ResolvedCtx, inp: SetOrgMfaInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    if inp.require:
        # Provisionner AVANT de poser le drapeau : si Logto échoue, ensure_mirror
        # lève → drapeau reste false, l'org sait que ce n'est PAS actif (pas de
        # fail-open sur un contrôle de sécurité).
        try:
            mfa_mirror.ensure_mirror(inp.org_id)
        except Exception as e:
            raise AuthzDenied(502, "logto_provisioning_failed",
                              f"Impossible d'activer le MFA côté Logto : {e}")
        org_store.set_org_require_mfa(inp.org_id, True)
    else:
        # Retirer l'exigence Logto AVANT le drapeau : si ça échoue, le drapeau reste
        # true (toujours enforced) — fail-closed pour un contrôle de sécurité.
        try:
            mfa_mirror.disable_mirror(inp.org_id)
        except Exception as e:
            raise AuthzDenied(502, "logto_deprovisioning_failed",
                              f"Impossible de retirer l'exigence MFA côté Logto : {e}")
        org_store.set_org_require_mfa(inp.org_id, False)
    return {"ok": True, "org_id": inp.org_id, "require_mfa": inp.require}


CAPABILITIES += [
    Capability(
        key="org.mfa.get", handler=_get_org_mfa, Input=GetOrgMfaInput,
        authz=ORG_MEMBER_OF("org_id"), Output=OrgMfaState,
        description=("Read whether this org requires its members to use MFA (a second "
                     "factor). Returns require_mfa, whether the Logto enforcement "
                     "mirror is provisioned, and members_other_tenant = how many "
                     "members this requirement does NOT cover because they belong to "
                     "another identity tenant (their own issuer's MFA policy applies)."),
        rest=RestBinding("GET", "/api/orgs/{id}/mfa", _ID),
    ),
    Capability(
        key="org.mfa.set", handler=_set_org_mfa, Input=SetOrgMfaInput,
        authz=ORG_ADMIN_OF("org_id"), Output=OrgMfaSet,
        description=("Turn the org's mandatory-MFA requirement on/off (require=true|false). "
                     "When on, every member must enroll and use a second factor at their "
                     "next sign-in (enforced by Logto). Provisions/updates the Logto "
                     "enforcement mirror; on Logto failure it errors WITHOUT changing state."),
        rest=RestBinding("PUT", "/api/orgs/{id}/mfa", _ID),
    ),
]
