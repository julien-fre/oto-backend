"""Qui détient le rôle qui lève un refus — nommé à qui a le droit de le savoir (oto#108).

Un refus « réservé à un administrateur » disait QUEL rôle, jamais QUI le tient. La
personne qui a légitimement besoin du geste — la gestionnaire du compte d'un fournisseur
qui régénère sa clé, simple membre de l'équipe — restait sans destination, et le
contournement observé a été le pire possible : le secret envoyé par un lien externe
pour qu'un administrateur le recolle. Un refus qui ne nomme pas de destination produit
soit un abandon, soit un contournement dangereux.

Ce module rend les détenteurs d'un palier d'administration, par leur NOM et rien d'autre :

- ⚠️ **à un MEMBRE de l'org concernée, et à lui seul.** L'annuaire d'une org n'est pas
  public : un tiers (un compte d'une autre org, un ex-membre) reçoit le rôle et le
  niveau, jamais les personnes. L'appartenance est celle de `roles.is_org_member` — le
  seam unique de la hiérarchie, qui y range aussi l'administrateur de la plateforme,
  lequel lit déjà la fiche de toute org ;
- ⚠️ **par leur nom SEUL, jamais par leur adresse** (décision produit, oto#108) : le nom
  suffit à trouver la personne dans son org ; l'adresse ferait du refus un annuaire de
  contacts, rendu à quiconque bute sur une garde. Ce module ne lit donc aucune colonne
  d'adresse, et `details.holders` ne porte que `name` ;
- sans les comptes en pause (ils ne répondront pas), bornés à `_MAX_NOMMES`. Un
  détenteur sans nom renseigné est COMPTÉ (`name: None`), jamais tu : « personne » serait
  faux.

Il ne décide RIEN : les règles d'autz restent seules juges (`capabilities._authz`,
`roles`). Il dit seulement à qui s'adresser une fois le refus prononcé.
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import db, group_store, org_store, roles

# Au-delà, la phrase devient un annuaire : les premiers suffisent à trouver quelqu'un.
_MAX_NOMMES = 5

ORG_ADMIN, GROUP_ADMIN = "org_admin", "group_admin"


def _nommes(subs: Iterable[str]) -> list[dict]:
    """`[{name}]` des comptes parmi `subs` qui ne sont pas en pause — le NOM seul
    (`None` quand il n'est pas renseigné). Aucune autre colonne n'est lue."""
    out: list[dict] = []
    for s in subs:
        u = db.get_user(s) or {}
        if u.get("suspended_at"):
            continue
        out.append({"name": (u.get("name") or "").strip() or None})
        if len(out) >= _MAX_NOMMES:
            break
    return out


def est_membre(sub: Optional[str], org_id: Optional[int]) -> bool:
    """`sub` est-il membre de `org_id` (au sens de `roles`, escalade plateforme incluse) ?"""
    return bool(sub) and org_id is not None and roles.is_org_member(sub, int(org_id))


def admins_de_l_org(sub: Optional[str], org_id: Optional[int]) -> Optional[list[dict]]:
    """Les administrateurs de l'org, nommés — `None` quand `sub` n'en est pas membre
    (rien à lui dire de plus que le rôle et le niveau)."""
    if not est_membre(sub, org_id):
        return None
    return _nommes(m["sub"] for m in org_store.list_org_members(int(org_id))
                   if m.get("org_role") == ORG_ADMIN)


def chefs_de_l_equipe(sub: Optional[str], group_id: int,
                      org_id: Optional[int]) -> Optional[list[dict]]:
    """Les chefs de l'équipe (`group_admin` explicite), nommés — `None` quand `sub`
    n'est pas membre de l'org PARENTE de l'équipe."""
    if not est_membre(sub, org_id):
        return None
    return _nommes(m["sub"] for m in group_store.list_group_members(int(group_id))
                   if m.get("group_role") == GROUP_ADMIN)


def _liste(personnes: list[dict]) -> str:
    noms = [p["name"] for p in personnes if p.get("name")]
    sans_nom = len(personnes) - len(noms)
    if sans_nom:
        noms.append(f"{sans_nom} sans nom renseigné")
    return ", ".join(noms)


def phrase(libelle: str, personnes: Optional[list[dict]]) -> str:
    """« Administrateurs de cette org : Alice Martin, Bruno Petit. » — vide pour un
    tiers (`None`), et DIT quand le palier n'a personne de joignable plutôt que de se
    taire."""
    if personnes is None:
        return ""
    if not personnes:
        return f" {libelle} : personne de joignable."
    return f" {libelle} : {_liste(personnes)}."


def qui_leve_une_option(sub: Optional[str], org_id: Optional[int]) -> str:
    """Qui lève le refus d'une option payante de l'org, et où (oto#108).

    Une seule porte depuis la coupure du cœur (#1097) : la FACTURATION de l'org, tenue
    par le service de facturation, qui pose le droit — qu'un administrateur de l'org y
    prenne une formule, ou que l'équipe de la plateforme l'y offre. Le cœur ne vend ni
    n'offre plus rien (`billing_moved`). La marque n'est pas
    nommée : sous un tenant, la plateforme que voit l'utilisateur n'est pas la nôtre. « À accorder par un
    admin » seul laissait l'administrateur de l'org se croire visé, chercher un geste
    qu'il n'a pas, et conclure que le connecteur n'était pas supporté."""
    from . import access, links  # paresseux : `links` lit la config des tenants à l'appel
    org = (access.current_org(sub)
           if links.patron_reclame("billing", "org", sub=sub) else None)
    facturation = links.link_for("billing", sub=sub, org=org)
    ou = f" ({facturation})" if facturation else ""
    return (" Qui la lève : la facturation de l'org, qui pose ce droit — un "
            f"administrateur de cette org y prend une formule qui l'inclut{ou}, ou "
            "l'équipe de la plateforme l'y offre."
            + phrase("Administrateurs de cette org", admins_de_l_org(sub, org_id)))
