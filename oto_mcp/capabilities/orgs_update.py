"""Capacité d'écriture des métadonnées d'org (ADR 0009).

Renommer / re-décrire son org était impossible : `org.create` posait le nom une
fois, et aucune capacité ne l'éditait ensuite. On comble le trou en miroir de
`group.update` (groups.py) : un handler core + Input pydantic + autz
`ORG_ADMIN_OF` (org_admin de cette org, ou escalade platform_admin). Multi-binding
REST (self `/api/orgs/{id}` + admin `/api/admin/orgs/{id}`), comme membres/secrets.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .. import org_store, session_org
from ._authz import ORG_ADMIN_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES

_ID = {"id": "org_id"}


class OrgIdInput(BaseModel):
    org_id: int


class OrgUpdated(BaseModel):
    """Profil d'org après écriture — **relu du store**, donc normalisé : ce que tu
    reçois n'est pas forcément ce que tu as envoyé.

    - `domain` passe par `normalize_domain` : `https://WWW.Acme.com/x` ressort
      `acme.com`. Un domaine non normalisable est un 400, jamais un silence.
    - `description`/`industry`/`location` sont *strippés* ; une chaîne VIDE **efface**
      le champ (et `domain: ""` le met à NULL) — c'est une écriture, pas un « ne
      touche pas » (pour ça, omettre le champ).
    - ⚠️ Un PATCH **sans aucun champ** est un no-op qui répond `ok: true` avec l'org
      inchangée : le succès ne prouve pas qu'une modification a eu lieu."""
    ok: bool
    org_id: int
    name: Optional[str] = None
    description: Optional[str] = None
    domain: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    # Logo EFFECTIF (upload > logo.dev dérivé du `domain`) : éditer `domain` change
    # donc `logo_url` sans qu'aucun logo n'ait été téléversé.
    logo_url: Optional[str] = None


class OrgArchived(BaseModel):
    """Archivage (soft-delete) d'une org. ⚠️ **`archived: false` n'est pas un échec** :
    avec `ok: true` en HTTP 200, il dit que l'org était **déjà archivée** — l'opération
    est idempotente et le résultat voulu est atteint. Un client qui traite `false`
    comme une erreur re-tentera indéfiniment.

    L'org sort de tous les listings mais rien n'est détruit (réversible en DB, membres
    et credentials conservés). Les membres qui l'avaient pour maison basculent sur leur
    plus ancienne org restante ; l'espace personnel est le repli."""
    ok: bool
    org_id: int
    archived: bool


class UpdateOrgInput(BaseModel):
    org_id: int
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=2000)
    # Profil d'entreprise (2026-07-02). `domain` = domaine de marque (acme.com),
    # normalisé org_store.normalize_domain ; dérive le logo via logo.dev quand
    # aucun logo n'est uploadé. Chaîne vide = effacer le champ.
    domain: Optional[str] = Field(None, max_length=253)
    industry: Optional[str] = Field(None, max_length=120)
    location: Optional[str] = Field(None, max_length=120)


def _update_org(ctx: ResolvedCtx, inp: UpdateOrgInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    if inp.name is not None and not inp.name.strip():
        raise AuthzDenied(400, "invalid_name", "Nom d'org vide.")
    try:
        org_store.update_org(inp.org_id, name=inp.name, description=inp.description,
                             domain=inp.domain, industry=inp.industry,
                             location=inp.location)
    except ValueError as e:  # domaine non-normalisable (saisie libre org_admin)
        raise AuthzDenied(400, "invalid_domain", str(e))
    o = org_store.get_org(inp.org_id) or {}
    return {"ok": True, "org_id": inp.org_id,
            "name": o.get("name"), "description": o.get("description"),
            "domain": o.get("domain"), "industry": o.get("industry"),
            "location": o.get("location"),
            "logo_url": org_store.effective_logo_url(o)}


def _archive_org(ctx: ResolvedCtx, inp: OrgIdInput) -> dict:
    """Self-service : un org_admin archive (soft-delete) SA propre org. Réutilise
    `org_store.archive_org` (masque partout, réversible en DB, rebascule les membres
    orphelins). Refuse l'espace personnel (recréé au boot). Si l'org archivée était
    l'org de session courante, on lève l'override → plus de bracelet pendouillant."""
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    if org_store.is_personal_org(inp.org_id):
        raise AuthzDenied(400, "personal_org",
                          "Ton espace personnel ne peut pas être supprimé.")
    archived = org_store.archive_org(inp.org_id)
    if archived:
        sid = session_org.current_session_id()
        present, ov = session_org.get_override(sid)
        if present and ov == inp.org_id:
            session_org.set_override(sid, None)
            session_org.clear_group_override(sid)
    return {"ok": True, "org_id": inp.org_id, "archived": archived}


CAPABILITIES += [
    Capability(
        key="org.update", handler=_update_org, Input=UpdateOrgInput,
        authz=ORG_ADMIN_OF("org_id"), Output=OrgUpdated,
        description=("Update an organization's profile (name, description, brand "
                     "domain like acme.com, industry, location). The domain also "
                     "drives the org logo when none is uploaded. "
                     "You must be org_admin of this org."),
        rest=(RestBinding("PATCH", "/api/orgs/{id}", _ID),
              RestBinding("PATCH", "/api/admin/orgs/{id}", _ID)),
    ),
    Capability(
        key="org.archive", handler=_archive_org, Input=OrgIdInput,
        authz=ORG_ADMIN_OF("org_id"), Output=OrgArchived,
        description=("Archive (delete) an organization you administer: it disappears "
                     "from every listing and its members fall back to their other "
                     "orgs. Reversible in DB, data is kept. You must be org_admin; "
                     "your personal space cannot be archived."),
        rest=RestBinding("DELETE", "/api/orgs/{id}", _ID),
        refresh_visibility=True,  # org active archivée → recharge la toolbox (repli)
    ),
]
