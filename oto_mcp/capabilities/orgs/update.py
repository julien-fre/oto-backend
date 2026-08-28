"""Capacité d'écriture des métadonnées d'org (ADR 0009).

Renommer / re-décrire son org était impossible : `org.create` posait le nom une
fois, et aucune capacité ne l'éditait ensuite. On comble le trou en miroir de
`group.update` (groups.py) : un handler core + Input pydantic + autz
`ORG_ADMIN_OF` (org_admin de cette org, ou escalade platform_admin). Multi-binding
REST (self `/api/orgs/{id}` + admin `/api/admin/orgs/{id}`), comme membres/secrets.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ... import org_store, session_org
from ...db import users as db_users
from .._authz import ORG_ADMIN_OF, ORG_ADMIN_OF_LIVE
from .._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from ..registry import CAPABILITIES

_ID = {"id": "org_id"}

_log = logging.getLogger(__name__)


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
    orphelins). Si l'org archivée était l'org de session courante, on lève
    l'override → plus de bracelet pendouillant.

    **Espace personnel (2026-08-25).** Il n'est plus refusé en bloc : un compte SOLO
    n'a QUE lui, le refus le laissait donc sans rien à supprimer du tout. Ce qui reste
    refusé, c'est l'espace perso de QUELQU'UN D'AUTRE : `ORG_ADMIN_OF` s'obtient aussi
    par escalade platform_admin, et ce chemin self-service ne doit pas effacer l'espace
    privé d'un tiers (la console admin a le sien).

    Pas de garde « et seulement s'il est seul » en plus : elle serait morte. Un espace
    perso EST mono-membre par construction — `add_org_member` efface `personal_of` dès
    qu'un 2ᵉ membre distinct arrive (org_store.py, correctif 2026-08-04), l'org tombant
    alors dans le cas ordinaire ci-dessous.

    ⚠️ Ce n'est pas une suppression de compte. Si ce geste laisse l'appelant SANS
    aucune org, on lui repose immédiatement un espace perso VIDE et neuf — son contenu
    quitte les listings, pas sa capacité à revenir. Ce n'est pas une politesse : « tout
    user a TOUJOURS une org maison » est un invariant que tout le backend suppose
    (db/users.py), et `backfill_personal_orgs` ne le réparerait qu'au PROCHAIN boot —
    entre les deux, le compte traverse le système en org-less. Le faire ici évite
    aussi que le backfill ne vienne, des heures plus tard, tamponner `personal_of` sur
    l'espace que l'utilisateur se serait recréé entre-temps
    (`_reclaim_or_create_personal` ne réclame que faute de perso existante)."""
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    if org_store.is_personal_org(inp.org_id) and org_store.get_personal_org(ctx.sub) != inp.org_id:
        raise AuthzDenied(400, "personal_org",
                          "L'espace personnel d'un autre utilisateur ne peut pas être "
                          "supprimé ici.")
    archived = org_store.archive_org(inp.org_id)
    if archived:
        sid = session_org.current_session_id()
        present, ov = session_org.get_override(sid)
        if present and ov == inp.org_id:
            session_org.set_override(sid, None)
            session_org.clear_group_override(sid)
        # Plus une seule org pour l'appelant (le cas SOLO qui supprime son unique
        # espace) : on rétablit l'invariant tout de suite. `ensure_personal_org` est
        # celui du boot, avec son projet « Découverte » — donc exactement l'accueil
        # d'un compte neuf, pas un état inventé pour l'occasion. Best-effort : la
        # suppression, elle, a réussi, et le backfill du prochain boot reste le filet.
        if not org_store.list_orgs_for_user(ctx.sub):
            try:
                u = db_users.get_user(ctx.sub) or {}
                org_store.ensure_personal_org(ctx.sub, email=u.get("email"),
                                              name=u.get("name"))
            except Exception:
                _log.warning("archive_org: espace perso non recréé pour %s", ctx.sub,
                             exc_info=True)
    return {"ok": True, "org_id": inp.org_id, "archived": archived}


CAPABILITIES += [
    Capability(
        key="org.update", handler=_update_org, Input=UpdateOrgInput,
        # `_LIVE` : org_admin **d'une org vivante**. Une org archivée est sortie de tous
        # les listings et n'est plus joignable par `_org=` — la laisser renommable était
        # l'incohérence du signal #467 (renommage réussi sur l'org #229 pendant que la
        # lecture répondait « tu n'es membre d'aucune org #229 »). Le refus part d'ici,
        # pas du handler : l'autz se déclare au niveau capacité (ADR 0009 §7).
        # ⚠️ `org.archive` ci-dessous garde `ORG_ADMIN_OF` — voir son commentaire.
        authz=ORG_ADMIN_OF_LIVE("org_id"), Output=OrgUpdated,
        description=("Update an organization's profile (name, description, brand "
                     "domain like acme.com, industry, location). The domain also "
                     "drives the org logo when none is uploaded. "
                     "You must be org_admin of this org."),
        rest=(RestBinding("PATCH", "/api/orgs/{id}", _ID),
              RestBinding("PATCH", "/api/admin/orgs/{id}", _ID)),
    ),
    Capability(
        key="org.archive", handler=_archive_org, Input=OrgIdInput,
        # PAS `ORG_ADMIN_OF_LIVE`, délibérément : `OrgArchived` promet qu'archiver une
        # org DÉJÀ archivée répond `ok:true, archived:false` (« c'était déjà fait »).
        # Refuser l'org archivée ici changerait cet idempotent documenté en 409 et
        # casserait tout client qui retente une suppression dont il a perdu la réponse.
        authz=ORG_ADMIN_OF("org_id"), Output=OrgArchived,
        description=("Archive (delete) an organization you administer: it disappears "
                     "from every listing and its members fall back to their other "
                     "orgs. Reversible in DB, data is kept. You must be org_admin. "
                     "You may archive YOUR OWN personal space — never someone else's. "
                     "Archiving your last remaining org immediately provisions a fresh, "
                     "empty personal space so you are never left without one."),
        rest=RestBinding("DELETE", "/api/orgs/{id}", _ID),
        refresh_visibility=True,  # org active archivée → recharge la toolbox (repli)
    ),
]
