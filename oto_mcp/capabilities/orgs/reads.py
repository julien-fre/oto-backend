"""Capacités de lecture du domaine orgs (ADR 0009, barreau 2d).

Pas de divergence d'autz — mais des formes de réponse divergentes (MCP éclaté
vs REST agrégé). On unifie en **superset** : le handler renvoie toutes les clés
que chaque face consommait → ni le dashboard ni les agents MCP ne cassent.

Surfaces asymétriques préservées : `org.get` est REST-only (le MCP n'avait pas
d'agrégat, mais des tools list séparés, conservés MCP-only). `org.get` (membre)
et `org.admin.get` (platform) partagent le handler, diffèrent par autz+path.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ... import access, billing, db, org_store
from ...tool_visibility import BETA_OPTION
from .._authz import ORG_MEMBER_OF, PLATFORM_ADMIN, SUB_ONLY
# Le quota de création vit avec la capacité qui REFUSE (`org.create`) : le lire ici
# plutôt que le recalculer est ce qui garantit que la liste et le refus ne pourront
# jamais annoncer deux nombres différents (#464).
from .core import org_quota
from .._types import Capability, ResolvedCtx, RestBinding

from ..registry import CAPABILITIES

_ID = {"id": "org_id"}


class NoInput(BaseModel):
    pass


class OrgIdInput(BaseModel):
    org_id: int


# ── Formes de réponse ────────────────────────────────────────────────────────
# Rappel transverse : tous les horodatages de ces réponses sortent du row factory
# `db/_conn._str_dict_row` en `"YYYY-MM-DD HH:MM:SS"` — **pas** de l'ISO-8601 : ni
# `T`, ni offset (le `tzinfo` est RETIRÉ, pas converti). Ne pas les passer à un
# `Date.parse` qui suppose UTC.

class MyOrgEntry(BaseModel):
    """Une org dont tu es membre. **Superset assumé de deux contrats historiques** :
    `id` == `org_id` et `my_role` == `role` — même valeur, deux noms, aucun second
    concept derrière. Les orgs ARCHIVÉES n'apparaissent jamais ici."""
    id: int
    org_id: int
    name: str
    # Logo EFFECTIF : l'upload s'il existe, SINON une URL logo.dev dérivée du domaine
    # de marque déclaré. Une valeur non-nulle ne prouve donc pas qu'un logo a été
    # téléversé (c'est `logo_custom` de `org.get` qui le dit). None ⟹ monogramme.
    logo_url: Optional[str] = None
    member_count: int
    my_role: str
    role: str
    # `true` sur l'org MAISON (le défaut persistant), jamais sur une org « de
    # session » — ADR 0038 a retiré tout état de session côté serveur.
    active: bool
    # Le compte est-il BÊTA dans cette org (option `beta`, seam `access.has_option` :
    # comp user OU comp org OU plan) ? Par ORG et non sur /api/me : le front consulte
    # l'org de l'URL, pas l'org maison. C'est ce qui décide si une surface bêta
    # (Agents : `oto_fleet`, cf. `BETA_TOOLS`) se MONTRE — la visibilité MCP masque
    # la liste d'outils, mais un front ne lit pas cette liste ; sans ce champ il ne
    # peut que tout montrer ou rien.
    beta: bool = False


class OrgQuota(BaseModel):
    """Où tu en es du plafond d'espaces CRÉÉS, lisible **avant** de s'y cogner (#464).

    `created` ne compte que ce qui occupe encore une place : ni les espaces archivés
    (l'archivage rend sa place), ni ton espace personnel (posé d'office, non
    supprimable en tant que tel). `created` peut donc être plus petit que le nombre
    d'entrées d'`orgs` ci-dessus — l'espace perso y figure, il est bien à toi.
    ⚠️ Le plafond porte sur les espaces que TU as créés : rejoindre celui d'autrui
    n'en consomme aucun, et une org dont tu es membre sans l'avoir créée n'y entre pas.
    `remaining == 0` ⟹ la prochaine création sera refusée (429 `org_quota`)."""
    created: int
    cap: int
    remaining: int


class MyOrgs(BaseModel):
    orgs: list[MyOrgEntry]
    quota: OrgQuota
    # Id de l'org maison. **None est un état atteignable** (aucune ligne
    # d'appartenance active), pas une erreur : le repli est l'espace personnel.
    active_org: Optional[int] = None


class OrgMemberEntry(BaseModel):
    """Un membre de l'org. ⚠️ `active` ne qualifie PAS le membre (compte activé /
    suspendu) : il dit que CETTE org est l'org maison de cette personne. Un membre
    parfaitement actif y est `false` dès qu'il travaille par défaut ailleurs."""
    sub: str
    # None quand aucune ligne `users` ne correspond (invité jamais connecté, compte
    # machine) — le membre existe quand même.
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str
    active: bool


class OrgSecretEntry(BaseModel):
    """Un credential d'org POSÉ — jamais sa valeur : le coffre ne rend aucun secret
    par API. `base_url` est **absent de l'objet** (pas nul) hors connecteur remote."""
    provider: str
    set_by: Optional[str] = None
    set_at: Optional[str] = None
    base_url: Optional[str] = None


class OrgBilling(BaseModel):
    """Abonnement de l'org (ADR 0043). ⚠️ `subscribed: true` couvre **`active` ET
    `past_due`** : un impayé en cours de relance reste « abonné » ici — c'est
    `status` qu'il faut lire pour savoir s'il faut alerter. Sans abonnement, seuls
    `subscribed:false` + `plans` reviennent (tous les autres champs absents)."""
    subscribed: bool
    # Catalogue public des paliers — présent UNIQUEMENT quand il n'y a pas
    # d'abonnement (c'est l'écran « choisir un plan »).
    plans: Optional[list[dict]] = None
    plan: Optional[str] = None
    label: Optional[str] = None
    amount: Optional[int] = None        # centimes
    currency: Optional[str] = None
    interval: Optional[str] = None
    status: Optional[str] = None        # active | past_due | canceled | …
    method: Optional[str] = None
    # `true` = plan FORCÉ par un admin plateforme (offert), aucun paiement derrière
    # et aucune échéance tirée. Ne pas le présenter comme un abonnement payant.
    comp: Optional[bool] = None
    current_period_end: Optional[str] = None
    next_billing_at: Optional[str] = None
    grace_until: Optional[str] = None
    canceled_at: Optional[str] = None


class OrgBrief(BaseModel):
    """Identité de l'org."""
    id: int
    name: str
    # Cf. `MyOrgEntry.logo_url` : EFFECTIF (upload > logo.dev du domaine).
    logo_url: Optional[str] = None
    # `true` = un logo a réellement été téléversé (gate du bouton « retirer le logo »).
    # C'est la seule clé qui distingue un logo posé d'un logo dérivé du domaine.
    logo_custom: bool
    # Chaîne VIDE quand non renseigné (pas `null`) : le store écrit `""`.
    description: str
    domain: Optional[str] = None
    industry: str
    location: str
    # `true` = espace personnel : non archivable, recréé au boot s'il manque.
    personal: bool
    member_count: int
    # ⚠️ Clé **ABSENTE** (pas nulle) quand l'appelant n'est pas membre — cas réel sur
    # la face `/api/admin/orgs/{id}`, où un admin plateforme lit une org étrangère.
    my_role: Optional[str] = None


class OrgDetail(BaseModel):
    """Fiche d'org, servie à l'identique aux deux faces (membre et admin plateforme) :
    l'autz change, la forme non."""
    org: OrgBrief
    members: list[OrgMemberEntry]
    secrets: list[OrgSecretEntry]
    # Options payantes OFFERTES à l'org (comp admin), par nom d'option. Une liste vide
    # ne veut pas dire « aucune option » : un plan payant en ouvre sans passer par là.
    option_comps: list[str]
    billing: OrgBilling


def _members(org_id: int) -> list[dict]:
    out = []
    for m in org_store.list_org_members(org_id):
        u = db.get_user(m["sub"]) or {}
        out.append({"sub": m["sub"], "email": u.get("email"), "name": u.get("name"),
                    "avatar_url": u.get("avatar_url"),
                    "role": m["org_role"], "active": m["is_active"]})
    return out


def _list_my_orgs(ctx: ResolvedCtx, inp: NoInput) -> dict:
    orgs, active = [], None
    for o in org_store.list_orgs_for_user(ctx.sub):
        if o["is_active"]:
            active = o["org_id"]
        orgs.append({  # superset REST(id/member_count/my_role) + MCP(org_id/role/active)
            "id": o["org_id"], "org_id": o["org_id"], "name": o["name"],
            # logo EFFECTIF : upload sinon dérivé logo.dev du domaine déclaré.
            "logo_url": org_store.effective_logo_url(o),
            "member_count": len(org_store.list_org_members(o["org_id"])),
            "my_role": o["org_role"], "role": o["org_role"], "active": o["is_active"],
            # `org=` EXPLICITE : calcul contre CETTE org, jamais contre current_org
            # (le seam le prévoit pour la fiche admin — même besoin ici).
            "beta": access.has_option(ctx.sub, BETA_OPTION, org=o["org_id"]),
        })
    # Le quota voyage avec la liste : c'est l'outil par lequel un agent regarde ses
    # espaces, donc le seul endroit où il peut apprendre qu'il approche du mur sans
    # tenter une création pour le découvrir (#464, 2ᵉ demande).
    return {"orgs": orgs, "active_org": active, "quota": org_quota(ctx.sub)}


def _list_all_orgs(ctx: ResolvedCtx, inp: NoInput) -> dict:
    return {"orgs": [
        {**o, "logo_url": org_store.effective_logo_url(o),
         "member_count": len(org_store.list_org_members(o["id"]))}
        for o in org_store.list_all_orgs()
    ]}


def _org_detail(ctx: ResolvedCtx, inp: OrgIdInput) -> dict:
    org = org_store.get_org(inp.org_id)
    if not org:
        from .._types import AuthzDenied
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    my_role = org_store.get_org_role(inp.org_id, ctx.sub)
    # `logo_url` = EFFECTIF (upload > logo.dev du domaine) ; `logo_custom` dit
    # au front si un upload existe (gate du bouton « remove logo »).
    brief = {"id": org["id"], "name": org["name"],
             "logo_url": org_store.effective_logo_url(org),
             "logo_custom": bool(org.get("logo_url")),
             "description": org.get("description") or "",
             "domain": org.get("domain"),
             "industry": org.get("industry") or "",
             "location": org.get("location") or "",
             # espace perso : non supprimable (gate du bouton « supprimer l'org »).
             "personal": org_store.is_personal_org(org["id"]),
             "member_count": len(org_store.list_org_members(org["id"]))}
    if my_role is not None:
        brief["my_role"] = my_role
    return {
        "org": brief,
        "members": _members(inp.org_id),
        "secrets": org_store.list_org_secrets(inp.org_id),
        # Options payantes offertes (comp admin) au niveau ORG (couche abonnement).
        "option_comps": db.list_option_comps("org", str(inp.org_id)),
        # Plan/abonnement de l'org (ADR 0043) — pilote le cockpit admin (forcer/
        # retirer un plan comp). `subscribed=False` + `plans` si aucun abonnement.
        "billing": billing.status(inp.org_id),
    }


CAPABILITIES += [
    Capability(key="org.list", handler=_list_my_orgs, Input=NoInput, authz=SUB_ONLY,
               Output=MyOrgs,
               description="List the organizations you belong to and which one is active.",
               mcp="oto_list_orgs", rest=RestBinding("GET", "/api/me/orgs")),
    # MCP fusionné dans oto_admin_org(op=list). REST conservé (dashboard).
    Capability(key="org.admin.list", handler=_list_all_orgs, Input=NoInput, authz=PLATFORM_ADMIN,
               description="[platform admin] List all organizations.",
               rest=RestBinding("GET", "/api/admin/orgs")),
    # org.get : REST-only, deux faces (membre vs platform), handler partagé.
    Capability(key="org.get", handler=_org_detail, Input=OrgIdInput, authz=ORG_MEMBER_OF("org_id"),
               Output=OrgDetail,
               rest=RestBinding("GET", "/api/orgs/{id}", _ID)),
    Capability(key="org.admin.get", handler=_org_detail, Input=OrgIdInput, authz=PLATFORM_ADMIN,
               rest=RestBinding("GET", "/api/admin/orgs/{id}", _ID)),
    # org.member.list (MCP-only) fusionné dans oto_admin_org_member(op=list).
    # org.secret.list (MCP-only) retiré du MCP (2026-06-25) : le dashboard lit les
    # secrets via la fiche org (org.admin.get → _org_detail). Pose = dashboard-only.
    # org.entitlement.list (MCP-only) fusionné dans oto_admin_namespace_access(op=list, scope=org).
    # Le dashboard lit les entitlements via la fiche org (org.admin.get → _org_detail).
]
