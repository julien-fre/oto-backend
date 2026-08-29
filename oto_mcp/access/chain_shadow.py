"""La DOUBLE LECTURE de L7 : la chaîne calcule, l'ancien chemin décide.

**Ce que ce module n'est pas.** Il ne décide rien, ne refuse rien, ne change pas d'un
octet ce qui est servi. Il observe. Le seul effet visible de son existence est une
ligne de plus dans `access_shadow_l7` — et le jour où la fenêtre est concluante, le
droit de retourner l'autorité (PR 2), puis de retirer `walk_cascade` et
`connector_acl` (PR 3).

**Ce qu'il calcule.** La résolution telle que [0053-D2](blueprint) la pose :

1. l'**ensemble atteignable** — les instances des scopes dont le sujet est MEMBRE,
   plus celles qui lui descendent par une arête de `grants` vivante ;
2. la **désignation** — l'appel qui nomme une instance et le binding de procédure
   priment, mais ils court-circuitent déjà la marche en amont (`resolve`), donc ce
   qui reste ici est la **proximité** : `user > group > org > platform`.

Et surtout **ce qu'il ne calcule pas** : la restriction de `connector_acl`. C'est
0053-D1 — restreindre, c'est PLACER l'ownership au bon niveau, jamais poser une
interdiction par-dessus. Les endroits où la restriction mord sont donc des
divergences ATTENDUES, et c'est exactement ce qu'on est venu compter.

## Les quatre écarts qu'on sait nommer d'avance

Relevé prod du 2026-08-29 — ils ne sont pas des anomalies, ce sont les décisions de
0053 qui deviennent visibles. Une divergence qui n'entre dans aucun est `inconnu`,
et c'est la seule que la fenêtre doit voir à zéro.

| classe | ce qui la produit |
|---|---|
| `elargissement_equipe` | la cascade ne lit que l'équipe **ACTIVE** ; l'ensemble atteignable lit **toutes** les équipes du sujet dans l'org. Un membre de « finance » actif dans « sales » ne résout rien aujourd'hui et résoudrait la clé de finance demain. **Comptée par org**, parce que c'est un comportement servi qui change chez un client nommé |
| `restriction_acl` | l'ancien chemin a refusé sur `connector_acl` (D1 dissout la table). 4 couples (org, connecteur) mordent en prod, pour 7 refus de personne |
| `free_tier_hors_modele` | l'ancien chemin gagne le palier plateforme par le free-tier OUVERT (`share_mode='open'`, `share_down` vide) — et 0053 n'a **pas** de bénéficiaire « tout le monde ». C'était le seul vrai trou du modèle ; **tranché le 29/08 : une arête « tout le monde » explicite d'abord, l'extinction mesurée connecteur par connecteur ensuite.** Cette classe doit donc tomber à **zéro** avant le retrait (PR 3), et c'est l'arête posée en PR 2 qui l'y amène |
| `perso_cross_org` | l'instance personnelle cross-org (#172) : la cascade suit la clé du sujet dans une AUTRE org, l'ensemble atteignable de 0053 est scopé à l'org de contexte |

## Deux règles de méthode, tenues mécaniquement

1. **Aucune règle n'est recopiée.** Les crans du connecteur sont lus à leur SOURCE —
   le registre (`is_byo_user`, `org_shareable`, `auth_modes`), la suspension d'une
   instance, les arêtes de `grants`. Ce module écrit une TRAVERSÉE différente, pas
   une seconde copie des gates. C'est la même discipline que
   `connectors/instance_visibility.py`, qui inverse déjà le walker sans le cloner.
2. **La comparaison porte sur le PALIER, pas sur le compte.** Le choix de compte
   multi-identités est un cran de l'instance (0053-D9), pas une autorisation : le
   rejouer ici serait dupliquer `_shared_auto_account` pour produire du faux écart.
   Brancher la résolution sur les identifiants stables d'instance est la PR 2.

⚠️ **Interrupteur** : `OTO_L7_SHADOW=0` éteint tout (aucune lecture, aucune écriture).
C'est le levier de réversibilité qui ne demande pas un déploiement, seulement un
redémarrage — comme les autres crans d'environnement de la box.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .. import (credentials_store, grants_chain, group_store, org_store, providers,
                tenant_vault)
from ..db import access_shadow as db_shadow
from ..db import grants as db_grants
from . import scope

logger = logging.getLogger(__name__)

# Vocabulaire FERMÉ des classes. Une divergence qui n'y entre pas est `INCONNU` —
# jamais une sixième valeur inventée à l'exécution, sinon la porte vers la PR 2
# (« zéro inconnu ») se déplacerait toute seule.
ACCORD = "accord"
ELARGISSEMENT_EQUIPE = "elargissement_equipe"
RESTRICTION_ACL = "restriction_acl"
FREE_TIER_HORS_MODELE = "free_tier_hors_modele"
PERSO_CROSS_ORG = "perso_cross_org"
INCONNU = "inconnu"
CLASSES = (ACCORD, ELARGISSEMENT_EQUIPE, RESTRICTION_ACL, FREE_TIER_HORS_MODELE,
           PERSO_CROSS_ORG, INCONNU)

# Période de versement de l'ACCORD, en secondes. L'accord est le cas nominal : le
# compter en base à chaque appel mettrait une écriture sur le chemin chaud d'un
# serveur mono-loop, et ferait viser la MÊME ligne à toutes les sessions (la
# contention mesurée pour R8). On accumule, on verse au plus une fois par minute et
# par (connecteur, org) : le dénominateur reste exact, le prix est borné.
FLUSH_SECONDS = 60


def _enabled() -> bool:
    return (os.environ.get("OTO_L7_SHADOW", "1") or "").lower() not in ("0", "false", "no")


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


def _platform_pick(sub: str, provider: str, org: Optional[int]) -> "tuple[Optional[ChainPick], bool]":
    """Le palier plateforme vu par la CHAÎNE SEULE, et rien d'autre.

    Rend `(pick, free_tier_ouvert)`. À la différence de `grants_chain.platform_rung`,
    aucun gate `CHAIN_CONNECTORS` : L7 fait de la chaîne l'unique autorité, donc la
    question « et pour un connecteur non basculé ? » est précisément celle qu'on
    mesure. Les arêtes sont lues par la MÊME fonction que le chemin servi
    (`db_grants.edges_for`) — pas une requête recopiée.

    `free_tier_ouvert` = il existe une instance plateforme ouverte à tous
    (`share_mode='open'` et aucune allowlist). C'est ce que 0053 ne sait pas dire,
    et le drapeau est ce qui permet de le CLASSER au lieu de le subir."""
    scopes = grants_chain.grantee_scopes(sub, org)
    ouvert = False
    for inst in credentials_store.list_platform_instances(provider):
        if inst.get("share_mode") != "closed" and not (inst.get("share_down") or []):
            ouvert = True
        edges = db_grants.edges_for(grants_chain.instance_ref(inst["label"], provider),
                                    scopes)
        if not edges:
            continue
        if any(e.get("revoked_at") is None for e in edges):
            return (ChainPick("platform", credentials_store.PLATFORM, inst["label"],
                              via="grant"), ouvert)
        # Toutes révoquées : la chaîne REFUSE cette instance, sans repli (0053-D6).
        return (None, ouvert)
    return (None, ouvert)


def chain_verdict(sub: str, provider: str, *, org: Optional[int],
                  want: str = "auto") -> "tuple[Optional[ChainPick], bool]":
    """L'instance que 0053-D2 désignerait pour cet appel, **et** le drapeau
    « free-tier ouvert ». Les deux ensemble, en UNE passe : les rendre séparément
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
                                  credentials_store.member_id(org, sub)), False)
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
                    return (ChainPick("group", "group", str(gid), group_id=gid), False)
            except Exception:  # noqa: BLE001
                logger.debug("shadow L7 : équipe %s illisible", gid, exc_info=True)
        if org is not None:
            try:
                if org_store.has_org_secret(org, porteur):
                    return (ChainPick("org", "org", str(org)), False)
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
                    return (ChainPick("tenant", credentials_store.TENANT, slug), False)
            except Exception:  # noqa: BLE001
                logger.debug("shadow L7 : palier tenant illisible", exc_info=True)
    if want != "byo":
        con = providers.connector_for_provider(porteur)
        if con is not None and "platform" in con.auth_modes:
            return _platform_pick(sub, porteur, org)
    return (None, False)


def chain_winner(sub: str, provider: str, *, org: Optional[int],
                 want: str = "auto") -> Optional[ChainPick]:
    """`chain_verdict` sans son drapeau — la vue qui se lit, et celle que la PR 2
    promouvra en résolution servie."""
    return chain_verdict(sub, provider, org=org, want=want)[0]


# ── La comparaison, et sa classe ──────────────────────────────────────────────

def _key(x) -> Optional[tuple]:
    """L'identité comparable d'un verdict : le PALIER et l'entité, jamais le compte
    (cf. le §2 du docstring de module)."""
    if x is None:
        return None
    return (getattr(x, "mode", None), getattr(x, "entity_type", None),
            str(getattr(x, "entity_id", None)))


def classify(legacy, chain: Optional[ChainPick], *, acl_refus: bool,
             free_tier_ouvert: bool) -> str:
    """La classe d'un couple de verdicts. Fonction PURE — c'est elle que le test
    exerce sur les formes relevées en prod, sans base."""
    if acl_refus:
        # L'ancien chemin a refusé avant même de marcher. Les deux refusent ⟹ accord.
        return RESTRICTION_ACL if chain is not None else ACCORD
    if _key(legacy) == _key(chain):
        return ACCORD
    if legacy is not None and getattr(legacy, "via", "local") == "cross_org":
        return PERSO_CROSS_ORG
    if chain is not None and chain.mode == "group":
        return ELARGISSEMENT_EQUIPE
    if (chain is None and legacy is not None
            and getattr(legacy, "mode", None) == "platform" and free_tier_ouvert):
        return FREE_TIER_HORS_MODELE
    return INCONNU


def _sample(sub: str, legacy, chain: Optional[ChainPick]) -> dict:
    """L'échantillon d'une divergence, SANS donnée nominative : le sub est haché
    (assez pour recroiser deux occurrences, pas pour désigner quelqu'un), et seuls
    les paliers et l'équipe en cause restent en clair — une équipe est ce sur quoi
    on agit, un sub ne l'est pas."""
    def _palier(x) -> str:
        if x is None:
            return "aucun"
        return f"{getattr(x, 'mode', '?')}/{getattr(x, 'entity_type', '?') or '-'}"
    out = {"sub_h": hashlib.md5(sub.encode("utf-8")).hexdigest()[:8],
           "ancien": _palier(legacy), "chaine": _palier(chain)}
    if chain is not None and chain.group_id is not None:
        out["equipe"] = chain.group_id
    if legacy is not None and getattr(legacy, "via", "local") != "local":
        out["ancien_via"] = getattr(legacy, "via")
    return out


# ── Le versement : divergence à l'occurrence, accord par battement ────────────

_lock = threading.Lock()
_accords: dict = {}          # (connector, org_id) -> occurrences en attente
_dernier_versement: dict = {}  # (connector, org_id) -> monotonic du dernier flush


def _compte_accord(connector: str, org_id: int) -> None:
    """Accumule un accord et ne verse qu'au battement. Le compteur en attente est
    remis à zéro AVANT l'écriture : si elle échoue, on perd un battement, jamais on
    ne compte deux fois."""
    cle = (connector, org_id)
    maintenant = time.monotonic()
    with _lock:
        _accords[cle] = _accords.get(cle, 0) + 1
        if maintenant - _dernier_versement.get(cle, 0.0) < FLUSH_SECONDS:
            return
        a_verser = _accords.pop(cle, 0)
        _dernier_versement[cle] = maintenant
    if a_verser:
        db_shadow.bump_shadow(connector, org_id, ACCORD, a_verser)


def observe(provider: str, sub: Optional[str], org: Optional[int], legacy, *,
            want: str = "auto", acl_refus: bool = False) -> None:
    """Compare les deux voies et range le résultat. **Best-effort absolu** : aucune
    exception ne sort d'ici, aucune valeur n'en revient. Appelée depuis `resolve`,
    après la marche — ou depuis le refus d'ACL, qui se produit avant elle."""
    if not sub or not _enabled():
        return
    try:
        porteur = providers.credential_provider(provider)
        chain, ouvert = chain_verdict(sub, porteur, org=org, want=want)
        classe = classify(legacy, chain, acl_refus=acl_refus, free_tier_ouvert=ouvert)
        if classe == ACCORD:
            _compte_accord(porteur, int(org or 0))
            return
        db_shadow.bump_shadow(porteur, int(org or 0), classe, 1,
                              _sample(sub, legacy, chain))
        if classe == INCONNU:
            # La seule classe qui doit rester à zéro : elle mérite une ligne de
            # journal en plus du compteur, parce qu'elle appelle une lecture de code.
            logger.warning(
                "shadow L7 : divergence INCONNUE sur %s (org=%s) — ancien=%s chaîne=%s "
                "(ADR 0053 L7, fenêtre de double lecture)",
                porteur, org, _key(legacy), _key(chain))
    except Exception:  # noqa: BLE001
        # Un shadow qui casserait une résolution serait pire que pas de shadow.
        logger.warning("shadow L7 : observation échouée (%s) — la résolution servie "
                       "n'est PAS affectée", provider, exc_info=True)


def observe_acl_refus(provider: str, sub: Optional[str], *, want: str = "auto") -> None:
    """`observe` pour le refus d'ACL, qui survient AVANT que `resolve` n'ait résolu
    l'org de contexte. L'org se lit ici, dans un try à elle : sur ce chemin on est
    déjà à l'intérieur d'un `except McpError`, et une exception d'observation y
    REMPLACERAIT le refus servi par une erreur sans rapport."""
    if not sub or not _enabled():
        return
    try:
        org = scope.current_org(sub)
    except Exception:  # noqa: BLE001
        logger.debug("shadow L7 : org de contexte illisible au refus d'ACL", exc_info=True)
        return
    observe(provider, sub, org, None, want=want, acl_refus=True)
