"""Ce que la chaîne de grants DÉSIGNE — la résolution de 0053, isolée (lot L7).

**Pourquoi ce module existe à part.** C'est la moitié du lot qui SURVIT : quand
`walk_cascade` sera retiré (PR 3), l'observation et son compteur disparaissent, mais
ceci reste — c'est la résolution servie. Les tenir dans le même fichier aurait mélangé
ce qu'on installe et ce qu'on jette.

**Ce qu'il calcule**, tel que [0053-D2](blueprint) le pose :

1. l'**ensemble atteignable** — les instances des scopes dont le sujet est MEMBRE,
   plus celles qui lui descendent par une arête de `grants` vivante ;
2. la **désignation** — l'appel qui nomme une instance et le binding de procédure
   priment, mais ils court-circuitent déjà la marche en amont (`resolve`), donc ce
   qui reste ici est la **proximité** : `user > group > org > platform`.

Et surtout **ce qu'il ne calcule pas** : la restriction de `connector_acl`. C'est
0053-D1 — restreindre, c'est PLACER l'ownership au bon niveau, jamais poser une
interdiction par-dessus.

**Deux règles de méthode, tenues mécaniquement :**

1. **Aucune règle n'est recopiée.** Les crans du connecteur sont lus à leur SOURCE —
   le registre (`is_byo_user`, `org_shareable`, `auth_modes`), la suspension d'une
   instance, les arêtes de `grants`. Ce module écrit une TRAVERSÉE différente, pas
   une seconde copie des gates. Même discipline que
   `connectors/instance_visibility.py`, qui inverse déjà le walker sans le cloner.
2. **La désignation porte sur le PALIER, pas sur le compte.** Le choix de compte
   multi-identités est un cran de l'instance (0053-D9), pas une autorisation :
   `rung_for_pick` le délègue à la SONDE que `resolve` a composée, et ne le rejoue
   jamais.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .. import (credentials_store, grants_chain, group_store, org_store, providers,
                tenant_vault)
from ..db import grants as db_grants
from . import scope

logger = logging.getLogger(__name__)

# Les deux NUANCES du trou — « le coffre accorde, la chaîne ne sait pas le dire ».
# Elles vivent ici parce que c'est la résolution qui les CONSTATE ; `chain_shadow` les
# reprend telles quelles dans son vocabulaire de classes, sans les redéclarer (une
# valeur servie déclarée deux fois finit par diverger).
FREE_TIER_HORS_MODELE = "free_tier_hors_modele"
PARTAGE_HORS_MODELE = "partage_hors_modele"

# ── L'ensemble atteignable, et sa désignation ─────────────────────────────────

@dataclass(frozen=True)
class ChainPick:
    """Ce que la chaîne DÉSIGNERAIT. `mode` parle le même vocabulaire que
    `CascadeRung.mode`, pour que la comparaison soit une égalité et pas une
    traduction. `via` dit POURQUOI l'instance est atteignable — appartenance au
    scope propriétaire (D1, premier membre de phrase) ou arête de grant (second)."""
    mode: str                       # user | group | org | tenant | platform
    entity_type: Optional[str]
    entity_id: Optional[str]
    via: str = "appartenance"       # appartenance | grant
    group_id: Optional[int] = None


def _group_ids(sub: str, org: Optional[int]) -> list[int]:
    """Toutes les équipes du sujet dans l'org de contexte — **toutes**, pas l'active.
    C'est là que 0053-D2 élargit, et l'élargissement est le sujet de la mesure."""
    if org is None:
        return []
    try:
        return sorted(int(g["group_id"]) for g in group_store.list_groups_for_user(sub, org))
    except Exception:  # noqa: BLE001
        logger.debug("shadow L7 : équipes illisibles", exc_info=True)
        return []


def _platform_pick(sub: str, provider: str, org: Optional[int]) -> "tuple[Optional[ChainPick], Optional[str]]":
    """Le palier plateforme vu par la CHAÎNE SEULE, et rien d'autre.

    Rend `(pick, hors_modele)`. À la différence de `grants_chain.platform_rung`,
    aucun gate `CHAIN_CONNECTORS` : L7 fait de la chaîne l'unique autorité, donc la
    question « et pour un connecteur non basculé ? » est précisément celle qu'on
    mesure. Les arêtes sont lues par la MÊME fonction que le chemin servi
    (`db_grants.edges_for`) — pas une requête recopiée.

    **`hors_modele` nomme la NUANCE du trou**, et ce n'est plus un booléen. Une ligne
    du coffre peut accorder de deux façons que la chaîne ne sait pas encore dire, et
    elles n'ont ni le même remède ni la même lecture :

    - **ouverte à tous** (`share_mode='open'`, aucune allowlist) ⟹ il manque l'arête
      « tout le monde » ;
    - **fermée sur une allowlist** (`share_down`) ⟹ il manque les arêtes NOMINATIVES
      de cette allowlist. Le semis de L5 ne couvrait que `CHAIN_CONNECTORS`, donc
      toute clé fermée hors de cette liste est dans ce cas.

    Les distinguer n'est pas un raffinement : sans la seconde, une divergence
    parfaitement explicable tombait en `inconnu` — la classe qui doit rester à zéro
    pour autoriser le retrait — et fermait la porte pour une raison fausse. Vécu le
    2026-08-29 : 17 observations sur `aiark` et `apify`, deux clés FERMÉES accordées
    à une org, sans une seule arête.

    La forme est lue au coffre, à sa source, sans rejouer la règle d'accès de
    l'ancien chemin : on regarde ce que l'instance EST, pas qui elle autorise."""
    nominatifs = grants_chain.grantee_scopes(sub, org)
    hors_modele = None
    for inst in credentials_store.list_platform_instances(provider):
        ref = grants_chain.instance_ref(inst["label"], provider)
        edges = db_grants.edges_for(ref, nominatifs + [grants_chain.EVERYONE])
        nomme = [e for e in edges
                 if (e["grantee_kind"], e["grantee_id"]) != grants_chain.EVERYONE]
        tous = [e for e in edges
                if (e["grantee_kind"], e["grantee_id"]) == grants_chain.EVERYONE
                and e.get("revoked_at") is None]
        if not edges:
            # Rien à dire sur CETTE instance : on note de quelle nuance de trou il
            # s'agirait si l'ancien chemin, lui, accordait. La première rencontrée
            # gagne — même ordre que l'ancien chemin (récente d'abord).
            if hors_modele is None:
                ouverte = (inst.get("share_mode") != "closed"
                           and not (inst.get("share_down") or []))
                hors_modele = (FREE_TIER_HORS_MODELE if ouverte
                               else PARTAGE_HORS_MODELE)
            continue
        # Une arête qui NOMME l'appelant prime sur « tout le monde », vivante ou non :
        # sans cette priorité, révoquer l'accès d'une personne sur une clé ouverte ne
        # couperait rien (l'arête « tout le monde » la re-accorderait aussitôt) — le
        # mode de panne exact que L5 avait éliminé en refusant sans repli.
        if nomme:
            if any(e.get("revoked_at") is None for e in nomme):
                return (ChainPick("platform", credentials_store.PLATFORM,
                                  inst["label"], via="grant"), hors_modele)
            # Toutes révoquées : la chaîne REFUSE cette instance, sans repli (D6).
            return (None, hors_modele)
        if tous:
            return (ChainPick("platform", credentials_store.PLATFORM, inst["label"],
                              via="tout_le_monde"), hors_modele)
    return (None, hors_modele)


def chain_verdict(sub: str, provider: str, *, org: Optional[int],
                  want: str = "auto") -> "tuple[Optional[ChainPick], Optional[str]]":
    """L'instance que 0053-D2 désignerait pour cet appel, **et** le drapeau
    **la nuance du trou** quand la chaîne se tait. Les deux ensemble, en UNE passe :
    les rendre séparément
    ferait relire les instances plateforme deux fois par appel, précisément sur le
    connecteur le plus trafiqué (une clé ouverte est le cas où la chaîne se tait).

    Les crans du connecteur (byo_user, org-partageable, palier plateforme déclaré,
    instance suspendue) sont lus à leur source — ce sont des propriétés de
    l'instance, pas des autorisations, et ils valent des deux côtés de la fenêtre.
    La restriction `connector_acl`, elle, n'est PAS lue : c'est le fond du lot."""
    porteur = providers.credential_provider(provider)
    if org is not None and providers.is_byo_user(porteur):
        try:
            if (credentials_store.has_credential(
                    credentials_store.MEMBER, credentials_store.member_id(org, sub),
                    porteur, account=None)
                    and not credentials_store.instance_suspended(
                        credentials_store.MEMBER, credentials_store.member_id(org, sub),
                        porteur)):
                # Le drapeau free-tier ne sert QUE si la chaîne se tait : un
                # palier gagnant le rend sans avoir lu les instances plateforme.
                return (ChainPick("user", credentials_store.MEMBER,
                                  credentials_store.member_id(org, sub)), None)
        except Exception:  # noqa: BLE001
            logger.debug("shadow L7 : palier membre illisible", exc_info=True)
    if porteur in providers.ORG_SHAREABLE_PROVIDERS:
        # À proximité égale, l'équipe ACTIVE d'abord — c'est la voie la plus
        # favorable au sens de D5, et ça rend la désignation déterministe quand le
        # sujet appartient à plusieurs équipes qui détiennent toutes une clé.
        active = None
        try:
            active = scope.current_group(sub)
        except Exception:  # noqa: BLE001
            logger.debug("shadow L7 : équipe active illisible", exc_info=True)
        gids = _group_ids(sub, org)
        if active is not None and int(active) in gids:
            gids = [int(active)] + [g for g in gids if g != int(active)]
        for gid in gids:
            try:
                if group_store.has_group_secret(gid, porteur):
                    return (ChainPick("group", "group", str(gid), group_id=gid), None)
            except Exception:  # noqa: BLE001
                logger.debug("shadow L7 : équipe %s illisible", gid, exc_info=True)
        if org is not None:
            try:
                if org_store.has_org_secret(org, porteur):
                    return (ChainPick("org", "org", str(org)), None)
            except Exception:  # noqa: BLE001
                logger.debug("shadow L7 : palier org illisible", exc_info=True)
        # Étage TENANT (L-clés PR 1) : le même que dans le walker, lu à la même source
        # (`rung_tenant` — le sub qualifié, jamais l'org). Sans lui, chaque clé tenant
        # servie compterait une divergence `inconnu` que ce lot aurait créée.
        slug = tenant_vault.rung_tenant(sub)
        if slug is not None:
            try:
                if (credentials_store.has_credential(credentials_store.TENANT, slug, porteur)
                        and not credentials_store.instance_suspended(
                            credentials_store.TENANT, slug, porteur)):
                    # L'arête tenant→org (PR 2), lue par la MÊME fonction que le
                    # walker : MUETTE ⟹ appartenance ; ACCORDE ⟹ grant ; REFUSE ⟹ on
                    # passe au palier suivant, comme lui (pas un « rien » précoce).
                    verdict = grants_chain.tenant_rung(slug, porteur, org)
                    if verdict is None or verdict.granted:
                        return (ChainPick("tenant", credentials_store.TENANT, slug,
                                          via="grant" if verdict else "appartenance"), None)
            except Exception:  # noqa: BLE001
                logger.debug("shadow L7 : palier tenant illisible", exc_info=True)
    if want != "byo":
        con = providers.connector_for_provider(porteur)
        if con is not None and "platform" in con.auth_modes:
            return _platform_pick(sub, porteur, org)
    return (None, None)


def chain_winner(sub: str, provider: str, *, org: Optional[int],
                 want: str = "auto") -> Optional[ChainPick]:
    """`chain_verdict` sans son drapeau — la vue qui se lit, et celle que la PR 2
    promouvra en résolution servie."""
    return chain_verdict(sub, provider, org=org, want=want)[0]



def rung_for_pick(pick: Optional[ChainPick], probe, sub: str, provider: str,
                  org: Optional[int]):
    """Le barreau SERVI correspondant à la désignation de la chaîne, ou None.

    **On ne réécrit pas le FETCH, on réutilise les sondes.** Le walker faisait deux
    choses : traverser (l'ordre des barreaux, les gates) et lire (la sonde, avec sa
    sélection de compte multi-identités, sa suspension, son déchiffrement du seul
    gagnant). L7 ne remplace que la **traversée** ; la lecture reste la sonde que
    `resolve` a déjà composée. C'est ce qui fait qu'inverser l'autorité ne rejoue
    aucune règle de compte — donc n'en fait diverger aucune.

    Rend un `cascade.CascadeRung`, la même forme que ce que `cascade_winner` rendait :
    tout ce qui suit dans `resolve` (garde du compte nommé, quota, `ResolvedCredential`)
    est alors inchangé, ligne pour ligne."""
    if pick is None:
        return None
    from . import cascade  # import tardif : `cascade` est un frère, pas une dépendance
    if pick.mode == "user":
        hit = probe.member(sub, org, provider)
        if hit is None:
            return None
        payload, account = hit
        return cascade.CascadeRung("user", pick.entity_type, pick.entity_id, payload,
                                   account)
    if pick.mode == "group":
        hit = probe.group(int(pick.entity_id), provider)
        if hit is None:
            return None
        payload, account = hit if isinstance(hit, tuple) else (hit, "")
        return cascade.CascadeRung("group", "group", pick.entity_id, payload, account)
    if pick.mode == "org":
        hit = probe.org(int(pick.entity_id), provider)
        if hit is None:
            return None
        payload, account = hit if isinstance(hit, tuple) else (hit, "")
        return cascade.CascadeRung("org", "org", pick.entity_id, payload, account)
    if pick.mode == "tenant":
        # ⚠️ **Ce barreau manquait, et son absence était INVISIBLE.** Sans lui, une
        # désignation `tenant` retombait dans la branche plateforme ci-dessous : la
        # sonde `probe.tenant` n'était jamais appelée, la clé SERVIE devenait celle de
        # la plateforme, et `tenant_budget.enforce` — conditionné à `win.mode ==
        # "tenant"` chez l'appelant — était sauté. Le shadow, lui, comparait deux
        # DÉSIGNATIONS et voyait un `accord` : le drapeau aurait annulé en silence la
        # pièce 1 des L-clés, qui est en prod. Dormant tant qu'aucune clé de tenant
        # n'est posée ; la première pose l'aurait réveillé.
        # Le `via` vient de la DÉSIGNATION (la chaîne a déjà lu l'arête tenant→org) :
        # le relire ici en ferait une seconde source, et deux sources d'un même
        # verdict finissent par diverger. Il est TRADUIT dans le vocabulaire du
        # walker — qui dit `local` là où la chaîne dit `appartenance` — parce que le
        # barreau servi doit être celui que le walker aurait produit, à l'octet :
        # `status.py` lit `via == "local"` pour dire « clé perso configurée ».
        hit = probe.tenant(pick.entity_id, provider)
        if hit is None:
            return None
        payload, account = hit if isinstance(hit, tuple) else (hit, "")
        return cascade.CascadeRung("tenant", credentials_store.TENANT, pick.entity_id,
                                   payload, account,
                                   via="grant" if pick.via == "grant" else "local")
    # Palier plateforme : la sonde rend le grant résolu (label + secret + quota). La
    # chaîne a déjà dit QUELLE instance ; la sonde de `resolve` lit celle que l'ancien
    # chemin lirait. Tant que les deux désignent la même, c'est la même clé — et quand
    # elles divergent, la fenêtre de shadow l'a dit avant qu'on bascule.
    grant = probe.platform(sub, provider, org)
    if not grant:
        return None
    return cascade.CascadeRung("platform", credentials_store.PLATFORM,
                               grant.get("label"), grant)


