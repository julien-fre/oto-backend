"""Ce qui est MÉTRÉ et ce qui est PAYÉ (ADR 0043, ADR 0070 §7).

Deux crans distincts, souvent confondus :

- le **quota** journalier d'une clé PLATEFORME (`quota_for`, `usage_today`,
  `record_platform_usage`) — un garde-fou d'essai, levé pour toute l'org par le droit
  déclaré `platform_unmetered` (lu par `resolve._win_quota`) ;
- l'**option payante** d'un connecteur (`paid_option_for`, `has_option`) — un droit
  déclaré de l'ORG (`entitlements.org_has`), une seule règle. Le don fait à une
  personne n'ouvre plus d'option payante.

Ne dépend que de `scope` (le contexte de l'acteur) et d'`entitlements` (les droits
déclarés) — jamais de `billing` : le commerce écrit les droits, le cœur les relit.
Le verdict « l'option est-elle LEVÉE pour ce connecteur » (qui tient compte du BYO)
vit dans `views.option_open`, au-dessus de la cascade.
"""
from __future__ import annotations

import os
from typing import Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import providers, db, grants_chain
from ..auth.hooks import current_user_sub_from_token
from ..mcp_errors import McpError
from . import entitlements, heritage, scope

# DÉRIVÉ du registre source unique (package `providers/`) : quota daily par
# provider (fallback si pas d'env ni de grant).
_QUOTA_DEFAULTS = providers.QUOTA_DEFAULTS


# Add-on payant requis par un connecteur (couche 3, ADR 0043). None = aucun. HOME
# canonique de ce mapping (les surfaces org ET user en dérivent — derive don't duplicate).
_PAID_OPTION_BY_CONNECTOR = {"unipile": "unipile"}


# Les options PAYANTES : un droit déclaré de l'org, jamais d'une personne. Dérivé du
# mapping ci-dessus.
_PAID_OPTIONS = frozenset(_PAID_OPTION_BY_CONNECTOR.values())


def paid_option_for(connector: str) -> Optional[str]:
    """Option payante requise par un connecteur (ou None).

    Suit la **délégation de credential** : les six canaux unipile n'ont pas d'option
    à eux, ils partagent celle du compte. Une option par canal serait un contresens
    métier (l'option paie des SIÈGES sur la clé plateforme, et un siège est un compte
    chez le fournisseur, pas un canal) et une régression : le comp `unipile` d'un
    client abonné cesserait d'ouvrir WhatsApp le jour du split."""
    return _PAID_OPTION_BY_CONNECTOR.get(
        connector) or _PAID_OPTION_BY_CONNECTOR.get(
        providers.credential_provider(connector))


def paid_option_refusal(connector: str, org: "int | None") -> Optional[str]:
    """Le refus à servir quand `connector` s'apprête à consommer la clé PLATEFORME pour
    une org qui n'a pas (ou plus) le droit de son option payante — `None` si rien ne
    s'y oppose. **Relu à chaque usage** (ADR 0070 §7) : une org dont l'essai ou
    l'abonnement a pris fin cesse d'être servie dès l'appel suivant, pas au prochain
    branchement d'un compte.

    Le message NOMME la cause et ce qui la lève ; aucun repli silencieux."""
    option = paid_option_for(connector)
    if option is None:
        return None
    if org is not None and entitlements.org_has(int(org), option):
        return None
    porteur = providers.REGISTRY.get(providers.credential_provider(connector))
    nom = (porteur.label if porteur and porteur.label else option)
    if org is None:
        return (f"L'option « {nom} » est un droit d'organisation, et aucune org qui "
                "la porte ne couvre cet appel : travaille dans une org abonnée.")
    return (f"L'option « {nom} » n'est pas active pour cette org : essai terminé "
            "ou abonnement requis. Un admin de l'org peut s'abonner ; une clé "
            f"`{providers.credential_provider(connector)}` propre reste servie.")


def exiger_option_payante(connector: str, sub: "str | None", org: "int | None") -> None:
    """Lève le refus de `paid_option_refusal` au palier PLATEFORME d'une résolution. Les
    droits lus sont ceux de l'org que l'appelant peut consommer : pour le bénéficiaire
    d'un projet partagé à qui rien n'est prêté, aucune (#480, `heritage.org_partagee`)."""
    refus = paid_option_refusal(
        connector, heritage.org_partagee(org, heritage.du_contexte(sub, org)))
    if refus:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=refus))


def has_option(sub: str, option: str, *, org: "int | None | object" = scope._UNSET) -> bool:
    """Couche 3 du modèle de connecteur (cf. docs/connector-model.md) : l'option de
    connecteur `option` est-elle débloquée pour `sub` dans son org ? **Seam unique.**

    - Option PAYANTE (`unipile`) : un droit déclaré VIVANT de l'org
      (`entitlements.org_has`), quelle que soit sa source — abonnement, don d'org,
      partenaire, essai. **Le don fait à une personne n'ouvre plus d'option payante**
      (ADR 0070 §7) : seule l'org porte un droit payant.
    - Option non payante (`beta`, un drapeau de population) : la marque du compte
      (`user_has_option`) ou celle de l'org.

    Ne JAMAIS lire les sources en direct ailleurs (un nouveau chemin passe par ici).
    `org` explicite (≠ _UNSET) = calcul pour un tiers contre une org donnée (fiche admin),
    sans current_org (anti-fuite de contexte)."""
    if option in _PAID_OPTIONS:
        org = scope.current_org(sub) if org is scope._UNSET else org
        return org is not None and entitlements.org_has(int(org), option)
    if user_has_option(sub, option):
        return True
    org = scope.current_org(sub) if org is scope._UNSET else org
    return org is not None and db.has_option_comp("org", str(org), option)


def user_has_option(sub: str, option: str) -> bool:
    """La moitié COMPTE du seam — « CET ACTEUR porte-t-il la marque », sans espace.

    Pour les options NON payantes seulement : une option payante est un droit de
    l'org (`entitlements.org_has`), et une marque de compte ne l'ouvre pas. Certaines
    questions portent sur l'identité de l'appelant et sur elle seule. `has_option`
    ne convient pas — il répond vrai dès que l'ORG ACTIVE porte la marque, donc il
    transforme une marque de compte en propriété d'espace, partagée par tous les
    membres.

    ⚠️ L'exemple qui l'a fait naître — la marque `runner_worker` sur un compte —
    n'existe plus (09/09/2026) : un worker n'est plus un compte marqué, c'est un
    secret de machine déclaré en base (`db.runner_workers`). La distinction
    reste vraie pour toute autre marque d'ACTEUR : passer par `has_option`
    la servirait à **tous les membres** de l'org dès qu'un don serait posé sur
    l'org ou qu'un plan l'inclurait.

    L'échéance mord dans `has_option_comp`, comme pour toutes les autres surfaces.
    """
    return db.has_option_comp("user", sub, option)


def quota_for(provider: str) -> int:
    """Quota journalier de la clé PLATEFORME d'un connecteur.

    Normalisé vers le PORTEUR du credential : un quota est une propriété de la clé,
    et six canaux qui empruntent la même clé partagent forcément son compteur. Six
    compteurs indépendants laisseraient consommer 6× le quota sur une seule clé."""
    provider = providers.credential_provider(provider)
    raw = os.environ.get(f"OTO_MCP_QUOTA_{provider.upper()}_DAILY")
    if raw is not None:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return _QUOTA_DEFAULTS.get(provider, 0)


def usage_today(sub: str, provider: str) -> int:
    """Consommation du jour SUR LA CLÉ de `provider` — normalisée vers son porteur.

    Pendant de `quota_for` : compteur et plafond doivent nommer la même clé, sinon
    un canal lirait 0 face au plafond d'une clé déjà épuisée (« quota intact » chez
    quelqu'un qui n'a plus rien). Tout lecteur de quota passe par ici."""
    return db.get_usage_today(sub, providers.credential_provider(provider))


def record_platform_usage(provider: str, calls: int = 1) -> None:
    """À appeler APRÈS un appel réussi avec la platform key. No-op si pas authentifié.

    Deux compteurs pendant la fenêtre de double lecture (blueprint ADR 0053, L5) :
    l'historique `usage(sub, tool, day)` — qui garde l'AUTORITÉ du refus, cf.
    `grants_chain` §Le comptage — et le compteur d'ARÊTE de 0053-D7, tenu en parallèle
    pour que la bascule d'autorité soit vérifiée avant d'être faite. No-op (et aucune
    requête) hors connecteurs basculés.

    `calls` = consommation d'UN appel qui compte pour plusieurs (un bulk facturé au
    contact, un appel Serper facturé au crédit déduit). Les DEUX compteurs débitent en
    UNE fois. L'historique bouclait, faute d'un pas dans sa signature ; il en a un
    depuis que le métrage se compte en crédits et plus en appels — un recensement Maps
    par défaut aurait sinon pris 81 connexions du pool et 81 transactions pour un seul
    appel d'outil, jusqu'à 2 000 sur une grille dense, sur le chemin chaud d'un serveur
    mono-loop. Le compteur vaut la même chose qu'après N incréments."""
    sub = current_user_sub_from_token()
    if not sub:
        return
    # Métré sur la clé RÉELLEMENT consommée (délégation) : un appel WhatsApp brûle
    # le quota du compte unipile, pas celui d'un compteur « whatsapp » que personne
    # ne lit. Écriture et lecture (`usage_today`) normalisent pareil.
    provider = providers.credential_provider(provider)
    unites = max(1, calls)
    db.increment_usage(sub, provider, unites)
    if grants_chain.is_chained(provider):
        grants_chain.record_usage(sub, provider, scope.current_org(sub), unites)
