"""Tenant désactivé : le prédicat sur un sub, et la phrase qui le refuse (oto-backend#1165).

**Le trou qu'on ferme.** Désactiver un tenant se faisait en vidant `issuer` et `jwks_uri`
de sa ligne, puis en rechargeant le registre d'émetteurs. Ça retirait du verifier les
jetons signés par SON annuaire — dans le seul processus rechargé — et rien d'autre :

- un jeton d'API `oto_` d'un compte du tenant porte son sub en BASE, il ne passe jamais
  par l'émetteur ; un jeton de délégation (travail d'agent) non plus ;
- un ANCIEN identifiant (sub nu, signé par NOTRE annuaire) que la bascule du tenant a
  redirigé vers un compte qualifié (`sub_aliases`) est canonicalisé par le drain d'alias
  en `<slug>:…` : la session de tableau de bord ouverte sur notre annuaire continue de
  servir le compte du tenant, émetteur du tenant ou pas ;
- le registre est par processus : recharger l'un laisse l'autre servir.

Une session restée ouverte après la désactivation, c'est l'une de ces trois portes. Aucune
ne passe par l'émetteur ; toutes passent par la vérification d'identité de CHAQUE requête,
sur le sub CANONIQUE. C'est là que l'état se lit (`garde_identite`), dans la base, sans
cache.

**Coût.** Zéro lecture pour un sub nu (tenant primaire, l'immense majorité du trafic) :
un sub sans `:` ne peut être qualifié par aucun tenant tiers (`tenancy.qualify`). Une
lecture d'une table de quelques lignes pour un compte qualifié.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import db

logger = logging.getLogger(__name__)

# Le code servi aux deux faces, sans traduction (même règle que `account_suspension.CODE`).
CODE = "tenant_disabled"


def etat(sub: Optional[str]) -> Optional[dict]:
    """L'état de désactivation du tenant qui qualifie `sub`, ou `None`.

    ⚠️ Ne rattrape rien : une panne de base REMONTE. Le fail-safe d'une coupure est le
    refus, pas le laisser-passer."""
    if not sub or ":" not in sub:
        return None
    return db.tenant_desactive_du_sub(sub)


def message(etat_tenant: dict) -> str:
    """La phrase servie au porteur — la même sur les deux faces. Elle dit que l'accès est
    coupé pour tout l'espace (pas son compte seul), que rien n'est supprimé, et que le
    retour passe par l'exploitant. Pas le motif : il appartient à l'exploitant de la
    plateforme, pas aux comptes du tenant."""
    slug = etat_tenant.get("slug") or "?"
    return (f"L'accès par l'espace « {slug} » est désactivé : aucune connexion, aucun "
            "jeton émis pour ses comptes n'est plus servi. Rien de ce qui lui appartient "
            "n'a été supprimé. Le rétablissement est un acte de l'exploitant de la "
            "plateforme.")


def refus(sub: Optional[str]) -> Optional[tuple[str, dict]]:
    """`(message, état)` si `sub` relève d'un tenant désactivé, `None` sinon. Journalise
    chaque refus : un compte coupé qui frappe encore dit qu'un jeton ou une automatisation
    tourne toujours sous cette identité."""
    coupe = etat(sub)
    if not coupe:
        return None
    logger.warning("compte d'un tenant désactivé refusé à l'entrée : tenant=%s sub=%s",
                   coupe.get("slug"), sub)
    return message(coupe), coupe
