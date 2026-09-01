"""Les emails que les propositions déclenchent — et l'adresse, la marque et la langue
calculées **PAR destinataire**.

Le point qui justifie ce module à part : une seule URL et une seule marque pour tout le
monde envoyaient la moitié des validateurs chez un produit qui n'est pas le leur. Chaque
destinataire vit sous un tenant, donc tout ce qui l'atteint se calcule à partir de SON
`sub`. Les deux notifications sont **best-effort** : elles ne cassent jamais l'écriture
qui les a provoquées (`docs/silences-2026-08-27.md`).
"""
from __future__ import annotations

import logging
from typing import Optional

from ... import config, db, email, org_store

logger = logging.getLogger(__name__)


def _dash_url(sub: Optional[str] = None) -> str:
    """L'adresse du tableau de bord servie à ce compte (celle de son tenant s'il en
    a une). Sans `sub` : la nôtre — les surfaces anonymes n'ont pas de tenant."""
    return config.dashboard_url_for(sub)


def _email_of(sub: Optional[str]) -> Optional[str]:
    if not sub:
        return None
    return (db.get_user(sub) or {}).get("email")


def _locale_of(sub: Optional[str]) -> Optional[str]:
    """Préférence de langue du DESTINATAIRE (`users.locale`, oto-backend#700).
    None (pas de sub, ou compte sans préférence posée) ⟹ le gabarit sert FR,
    comportement inchangé."""
    if not sub:
        return None
    return (db.get_user(sub) or {}).get("locale")


def _project_url(sub: Optional[str], pid: Optional[int], org: Optional[int]) -> Optional[str]:
    """Le lien du projet TEL QU'IL EXISTE chez ce destinataire, ou None.

    ⚠️ On ne colle PAS notre chemin sous le domaine du partenaire : ses vues ne
    portent pas les mêmes adresses (`links.py`), et un lien mort est pire qu'un lien
    absent. `link_for` rend None quand le tenant n'a pas cette vue, et l'email part
    alors SANS bouton — la nouvelle reste utile sans lien.
    """
    if not pid:
        return None
    from ... import links
    return links.link_for("project", sub=sub, id=int(pid),
                          org=int(org) if org is not None else "")


def _brand_of(sub: Optional[str]) -> str:
    """Le nom du produit sous lequel CE destinataire nous connaît. « ouvrir dans oto »
    dans un email envoyé à un utilisateur d'un tenant tiers est un faux, même quand l'URL est
    juste."""
    _base, marque = config.front_for(sub)
    return marque or "oto"


def cr_created(pid: int, proposer_sub: str, *, is_create: bool,
               doc_title: Optional[str]) -> None:
    """Prévient les VALIDATEURS qu'une proposition attend (oto/#6, « les auteurs
    valident »). Destinataires = org_admins de l'org du projet + le propriétaire si le
    projet est user-owned, SAUF le proposeur. Best-effort — ne casse jamais la création."""
    try:
        project = db.get_project_by_id(int(pid)) or {}
        pname = project.get("name")
        org = project.get("context_org_id")
        # On garde le SUB à côté de l'email : chaque destinataire peut vivre sous un
        # produit différent, donc l'adresse et la marque se calculent PAR PERSONNE.
        # Une seule URL pour tout le monde envoyait la moitié des validateurs chez un
        # produit qu'ils n'ont pas.
        recips: set[tuple[str, str]] = set()
        if org is not None:
            for m in org_store.list_org_members(int(org)):
                if m.get("org_role") == "org_admin" and m.get("sub") != proposer_sub:
                    if e := _email_of(m.get("sub")):
                        recips.add((str(m["sub"]), e))
        if project.get("owner_type") == "user" and project.get("owner_id") != proposer_sub:
            if e := _email_of(project.get("owner_id")):
                recips.add((str(project["owner_id"]), e))
        if not recips:
            return
        proposer = (db.get_user(proposer_sub) or {}).get("name") or (db.get_user(proposer_sub) or {}).get("email")
        for sub_dest, to in recips:
            email.send_change_request_email(
                to, project_name=pname, doc_title=doc_title, proposer=proposer,
                is_create=is_create, app_url=_project_url(sub_dest, pid, org),
                brand=_brand_of(sub_dest), locale=_locale_of(sub_dest))
    except Exception as e:  # best-effort
        logger.warning("notify CR created (project %s) failed: %s", pid, e)


def cr_resolved(cr: dict, accepted: bool) -> None:
    """Prévient le PROPOSEUR que sa proposition a été tranchée (oto/#6). Best-effort."""
    try:
        to = _email_of(cr.get("requested_by"))
        if not to:
            return
        pname = cr.get("project_name")
        pid = cr.get("project_id") or (cr.get("doc_id") and (db.get_doc_by_id(int(cr["doc_id"])) or {}).get("project_id"))
        dest = cr.get("requested_by")
        email.send_change_request_resolved_email(
            to, project_name=pname, doc_title=cr.get("doc_title"), accepted=accepted,
            app_url=_project_url(dest, pid, None), brand=_brand_of(dest),
            locale=_locale_of(dest))
    except Exception as e:  # best-effort
        logger.warning("notify CR resolved (#%s) failed: %s", cr.get("id"), e)
