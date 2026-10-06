"""`admin.tenant_disablement` — désactiver un tenant, et le réactiver (oto-backend#1165).

**Le geste qui manquait.** Désactiver un tenant se faisait à la main : vider `issuer` et
`jwks_uri` de sa ligne, puis `oto_admin_tenant op=reload`. Une session de tableau de bord
déjà ouverte est restée servie — un compte qualifié par le tenant a continué de lire
l'admin REST jusqu'à ce qu'on rétrograde son rôle. Retirer l'émetteur n'éteint que les
jetons signés par l'annuaire du tenant, dans le processus rechargé ; un jeton `oto_`, un
jeton de délégation, ou un ANCIEN identifiant de notre annuaire redirigé vers le compte
qualifié (`sub_aliases`, drain d'alias) ne passent jamais par cet émetteur
(`tenant_desactive`, docstring).

**Ce que fait `disable`, d'un seul geste :**

1. pose l'état (`tenants.disabled_at|_by|_reason`), lu à CHAQUE vérification d'identité
   d'un compte qualifié sous le tenant (`garde_identite`) — REST, MCP, lien d'upload —
   sur toutes les instances, sans rechargement ; et recharge le registre du processus,
   dont la façade OAuth refuse alors sur les hosts du tenant tout enregistrement,
   autorisation ou rafraîchissement (les autres processus : à leur `op=reload`) ;
2. révoque les jetons que NOUS stockons pour TOUS ses comptes (jetons d'API et de
   délégation, `user_api_tokens.revoked_*`). Les jetons signés par son annuaire
   (session de tableau de bord, accès et rafraîchissement OAuth du MCP) n'existent pas
   chez nous : ils sont refusés à chaque présentation ;
3. suspend toutes ses ORGS (son rattachement déclaré, lu par `db.desactiver_tenant`)
   par la suspension d'org existante
   (`org_suspension`, décision du 06/10/2026) : plus personne n'y agit — un compte d'un
   autre tenant membre d'une de ses orgs compris —, ses projets publiés ne sont plus
   servis, ses automatisations (cron, webhooks, travaux d'agent) n'enfilent ni ne
   réservent plus rien. Une org déjà suspendue pour une autre raison n'est pas touchée ;
4. journalise et rend les compteurs : jetons par type, comptes, orgs suspendues.

**Rejoué sur un tenant déjà désactivé**, le geste ne réécrit pas l'état d'origine mais
rattrape ce qui manque : jetons émis depuis, orgs pas encore suspendues (une org née
depuis, ou un tenant désactivé avant que le geste ne suspende ses orgs).

**`enable` rouvre les nouvelles connexions et les orgs que la désactivation a
suspendues** — elles seules (`orgs.suspended_tenant_id`) : une org suspendue pour une
autre raison (un essai fini sans abonnement) le reste. Aucun jeton révoqué ne revit — les
personnes se reconnectent, et leurs jetons d'API se réémettent. Tant que le tenant est
désactivé, le geste d'org (`admin.org_suspension`, `service.org.suspension`) ne lève pas
la suspension d'une de ses orgs (`409 tenant_disabled`).
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import db, org_suspension, tenancy
from ._authz import SUPER_ADMIN
from ._types import AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

logger = logging.getLogger(__name__)

_MOTIF_MAX = 500


class TenantDisablementInput(BaseModel):
    slug: str
    op: Literal["disable", "enable"]
    # Exigé pour `disable`, refusé entier au-delà de `_MOTIF_MAX` (jamais raboté) — même
    # règle que la pause d'un compte : un état posé sans motif écrit devient un état que
    # personne n'ose lever.
    reason: Optional[str] = None


class TenantDisablementOut(BaseModel):
    """L'état APRÈS le geste, et ce que CE geste a coupé."""
    slug: str
    disabled: bool
    changed: bool = Field(description="Ce geste a changé l'état (false : il l'était "
                                      "déjà / il ne l'était pas).")
    disabled_at: Optional[str] = None
    disabled_by: Optional[str] = None
    disabled_reason: Optional[str] = None
    revoked: dict[str, int] = Field(
        default_factory=dict,
        description="Jetons RÉVOQUÉS par ce geste, par type : `user` (jeton d'API), "
                    "`delegation` (jeton d'un travail d'agent). Vide pour `enable` : la "
                    "révocation ne se réactive pas.")
    accounts_cut: int = Field(
        default=0, description="Comptes qualifiés sous ce tenant : chacune de leurs "
                               "requêtes est refusée (`tenant_disabled`), quel que soit "
                               "le jeton présenté.")
    aliases_cut: int = Field(
        default=0, description="Anciens identifiants redirigés vers un compte du tenant "
                               "(`sub_aliases`) — refusés aussi, après canonicalisation.")
    orgs_suspended: int = Field(
        default=0, description="Orgs du tenant suspendues PAR CE GESTE (`disable`) : plus "
                               "personne n'y agit, leurs projets publiés ne sont plus "
                               "servis, leurs automatisations n'enfilent plus rien. Un "
                               "geste rejoué ne compte que celles qu'il rattrape.")
    orgs_suspended_ids: list[int] = Field(default_factory=list)
    orgs_already_suspended: int = Field(
        default=0, description="Orgs du tenant déjà suspendues pour une AUTRE raison : non "
                               "touchées, et `enable` ne les lèvera pas.")
    orgs_resumed: int = Field(
        default=0, description="Orgs rouvertes par `enable` : celles que la désactivation "
                               "avait suspendues, et elles seules.")
    orgs_resumed_ids: list[int] = Field(default_factory=list)
    registry_reloaded: bool = Field(
        default=False,
        description="Le registre d'émetteurs de CE processus a été relu : la façade OAuth "
                    "des hosts du tenant suit l'état. Un autre processus le suit à son "
                    "`op=reload` ou à son démarrage — ses jetons, eux, sont refusés "
                    "partout dès le geste.")
    refused_not_stored: list[str] = Field(
        default_factory=list,
        description="Porteurs que la plateforme ne STOCKE pas, donc ne peut ni compter ni "
                    "révoquer à la source : ils restent signés jusqu'à leur expiration "
                    "et sont refusés à chaque présentation.")


# Les porteurs sans état chez nous, nommés dans la réponse plutôt que comptés à zéro :
# un « 0 session révoquée » se lirait « aucune session ouverte ».
_SANS_ETAT = ["dashboard_session_jwt", "mcp_oauth_access_token",
              "mcp_oauth_refresh_token", "signed_upload_link"]


def _tenant_cible(slug: str) -> str:
    slug = (slug or "").strip()
    ligne = db.tenant_ligne(slug) if slug else None
    if ligne is None:
        raise AuthzDenied(404, "unknown_tenant",
                          f"Aucun tenant `{slug}`. Un tenant se déclare en base (runbook "
                          "de provisioning), il n'est pas désignable autrement.")
    if ligne["id"] == 1 or slug == tenancy.primary_slug():
        raise AuthzDenied(409, "primary_tenant",
                          f"`{slug}` est le tenant primaire de l'instance : le désactiver "
                          "couperait la plateforme entière. Pour un compte, "
                          "oto_admin_account op=suspend.")
    return slug


def _vue(slug: str, etat: Optional[dict], *, changed: bool, **coupe) -> dict:
    return {"slug": slug, "disabled": bool(etat and etat.get("disabled_at")),
            "changed": changed,
            "disabled_at": (etat or {}).get("disabled_at"),
            "disabled_by": (etat or {}).get("disabled_by"),
            "disabled_reason": (etat or {}).get("disabled_reason"), **coupe}


def _relire_registre(slug: str) -> bool:
    """Fait relire au registre de CE processus l'état qu'on vient d'écrire (la façade
    OAuth le lit là). L'état est déjà ÉCRIT et la garde d'identité le lit en base : un
    échec ici ne défait rien et ne rouvre rien — il se dit (journal, `registry_reloaded`)
    pour que l'opérateur relance `op=reload`."""
    from .. import server
    try:
        server.reload_tenant_registry()
        return True
    except Exception:
        logger.exception("tenant %s : état écrit, mais le registre de ce processus n'a pas "
                         "pu être relu — relancer oto_admin_tenant op=reload", slug)
        return False


def _disablement(ctx: ResolvedCtx, inp: TenantDisablementInput) -> dict:
    slug = _tenant_cible(inp.slug)

    if inp.op == "enable":
        fait = db.reactiver_tenant(slug)
        if fait is None:  # supprimé entre la garde et le geste
            raise AuthzDenied(404, "unknown_tenant", f"Aucun tenant `{slug}`.")
        org_suspension.invalider()
        logger.warning("tenant RÉACTIVÉ slug=%s par=%s (changed=%s) orgs_rouvertes=%s — "
                       "les nouvelles connexions repassent, aucun jeton révoqué ne revit",
                       slug, ctx.sub, fait["changed"], fait["orgs_resumed"])
        return _vue(slug, None, changed=fait["changed"],
                    orgs_resumed=len(fait["orgs_resumed"]),
                    orgs_resumed_ids=fait["orgs_resumed"],
                    registry_reloaded=_relire_registre(slug))

    motif = (inp.reason or "").strip()
    if not motif:
        raise AuthzDenied(400, "missing_reason",
                          "`reason` requis : une désactivation sans motif écrit devient "
                          "une désactivation que personne ne saura expliquer ni lever.")
    if len(motif) > _MOTIF_MAX:
        raise AuthzDenied(400, "reason_too_long",
                          f"`reason` fait {len(motif)} caractères pour {_MOTIF_MAX} au "
                          "plus. Refusé entier plutôt que raboté : raccourcis-le en "
                          "gardant pourquoi, et à quelle condition réactiver.")
    if (ctx.sub or "").startswith(f"{slug}:"):
        raise AuthzDenied(409, "self_tenant",
                          f"Ton compte relève du tenant `{slug}` : le désactiver te "
                          "retirerait l'accès qui permet de le défaire.")
    fait = db.desactiver_tenant(slug, by=ctx.sub or "?", reason=motif)
    if fait is None:  # supprimé entre la garde et le geste
        raise AuthzDenied(404, "unknown_tenant", f"Aucun tenant `{slug}`.")
    # Ce processus voit les orgs suspendues tout de suite ; les autres sous `TTL_S`, et la
    # réservation des travaux, qui lit la base, dès le geste.
    org_suspension.invalider()
    logger.warning("tenant DÉSACTIVÉ slug=%s par=%s changed=%s jetons_revoques=%s "
                   "comptes_coupes=%d anciens_identifiants_coupes=%d orgs_suspendues=%s "
                   "orgs_deja_suspendues=%d motif=%r",
                   slug, ctx.sub, fait["changed"], fait["revoked"], fait["accounts_cut"],
                   fait["aliases_cut"], fait["orgs_suspended"],
                   fait["orgs_already_suspended"], motif)
    return _vue(slug, fait, changed=fait["changed"], revoked=fait["revoked"],
                accounts_cut=fait["accounts_cut"], aliases_cut=fait["aliases_cut"],
                orgs_suspended=len(fait["orgs_suspended"]),
                orgs_suspended_ids=fait["orgs_suspended"],
                orgs_already_suspended=fait["orgs_already_suspended"],
                registry_reloaded=_relire_registre(slug),
                refused_not_stored=list(_SANS_ETAT))


CAPABILITIES += [
    Capability(
        key="admin.tenant_disablement", handler=_disablement,
        Input=TenantDisablementInput, Output=TenantDisablementOut, authz=SUPER_ADMIN,
        errors=(
            DeclaredError(404, "unknown_tenant", "aucun tenant ne porte ce slug"),
            DeclaredError(409, "primary_tenant",
                          "le tenant primaire de l'instance (ligne 1) ne se désactive pas"),
            DeclaredError(409, "self_tenant",
                          "op=disable sur le tenant du compte appelant"),
            DeclaredError(400, "missing_reason", "op=disable sans `reason`"),
            DeclaredError(400, "reason_too_long",
                          "`reason` au-delà de 500 caractères, refusé entier"),
        ),
        description=(
            "Disable a tenant (op=disable, `reason` required) or re-enable it "
            "(op=enable). Disable, in one gesture: refuses every request of every "
            "account qualified under the tenant from the next one on — REST, MCP and "
            "signed upload links, whatever the token, dashboard session included — and "
            "every new registration, authorization or refresh on its hosts; revokes the "
            "API and delegation tokens of all its accounts; suspends all its orgs (no one "
            "acts in them any more, members from other tenants included; their published "
            "projects are no longer served; their schedules, webhooks and agent jobs "
            "enqueue nothing); returns the counts. Replayed on a disabled tenant, it "
            "catches up what is missing (new tokens, orgs not yet suspended). Enable "
            "reopens new connections and the orgs the disablement suspended — not an org "
            "suspended for another reason; revoked tokens stay revoked. Nothing is "
            "deleted. Super admin. The primary tenant cannot be disabled."),
        # POST y compris pour `enable` : l'adaptateur REST fusionne la requête dans
        # l'`Input`, un GET muterait.
        rest=RestBinding("POST", "/api/admin/tenants/{slug}/disablement"),
    ),
]
