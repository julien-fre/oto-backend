"""La DOUBLE LECTURE de L7 : la chaîne calcule, l'ancien chemin décide.

**Ce que ce module n'est pas.** Il ne décide rien, ne refuse rien, ne change pas d'un
octet ce qui est servi. Il observe. Le seul effet visible de son existence est une
ligne de plus dans `access_shadow_l7` — et le jour où la fenêtre est concluante, le
droit de retourner l'autorité (PR 2), puis de retirer `walk_cascade` (PR 3).

**Ce qu'il calcule.** La résolution telle que [0053-D2](blueprint) la pose :

1. l'**ensemble atteignable** — les instances des scopes dont le sujet est MEMBRE,
   plus celles qui lui descendent par une arête de `grants` vivante ;
2. la **désignation** — l'appel qui nomme une instance et le binding de procédure
   priment, mais ils court-circuitent déjà la marche en amont (`resolve`), donc ce
   qui reste ici est la **proximité** : `user > group > org > platform`.

Aucune des deux voies ne connaît de restriction d'accès par-dessus : 0053-D1 —
restreindre, c'est PLACER l'ownership au bon niveau, jamais poser une interdiction
par-dessus. La table `connector_acl` n'est plus lue depuis le 24/09/2026 (la classe
`restriction_acl`, qui comptait ses refus, est partie avec elle).

## Les écarts qu'on sait nommer d'avance

Relevé prod du 2026-08-29 — ils ne sont pas des anomalies, ce sont les décisions de
0053 qui deviennent visibles. Une divergence qui n'entre dans aucun est `inconnu`,
et c'est la seule que la fenêtre doit voir à zéro.

| classe | ce qui la produit |
|---|---|
| `elargissement_equipe` | la cascade ne lit que l'équipe **ACTIVE** ; l'ensemble atteignable lit **toutes** les équipes du sujet dans l'org. Un membre de « finance » actif dans « sales » ne résout rien aujourd'hui et résoudrait la clé de finance demain. **Comptée par org**, parce que c'est un comportement servi qui change chez un client nommé |
| `free_tier_hors_modele` | l'ancien chemin gagne le palier plateforme par le free-tier OUVERT (`share_mode='open'`, `share_down` vide) — et 0053 n'a **pas** de bénéficiaire « tout le monde ». C'était le seul vrai trou du modèle ; **tranché le 29/08 : une arête « tout le monde » explicite d'abord, l'extinction mesurée connecteur par connecteur ensuite.** Cette classe doit donc tomber à **zéro** avant le retrait (PR 3), et c'est l'arête posée en PR 2 qui l'y amène |
| `partage_hors_modele` | la clé plateforme est FERMÉE sur une allowlist (`share_down`) **et aucune arête ne l'exprime**. Sœur de la précédente, autre remède : ce sont les arêtes NOMINATIVES qui manquent. Le semis de L5 ne couvrait que les connecteurs basculés, donc toute clé fermée hors de cette liste est dans ce cas. Vécu le 29/08 : 17 observations sur `aiark` et `apify` tombaient en `inconnu` faute de ce nom — une divergence parfaitement explicable qui fermait la porte pour une raison fausse |
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

from .. import credentials_store, grants_chain, providers
from ..db import access_shadow as db_shadow
from ..db import grants as db_grants
from . import chain_resolution, scope

logger = logging.getLogger(__name__)

# Vocabulaire FERMÉ des classes. Une divergence qui n'y entre pas est `INCONNU` —
# jamais une sixième valeur inventée à l'exécution, sinon la porte vers la PR 2
# (« zéro inconnu ») se déplacerait toute seule.
ACCORD = "accord"
ELARGISSEMENT_EQUIPE = "elargissement_equipe"
# Reprises de `chain_resolution`, qui les constate — jamais redéclarées.
FREE_TIER_HORS_MODELE = chain_resolution.FREE_TIER_HORS_MODELE
PARTAGE_HORS_MODELE = chain_resolution.PARTAGE_HORS_MODELE
PERSO_CROSS_ORG = "perso_cross_org"
INCONNU = "inconnu"
CLASSES = (ACCORD, ELARGISSEMENT_EQUIPE, FREE_TIER_HORS_MODELE,
           PARTAGE_HORS_MODELE, PERSO_CROSS_ORG, INCONNU)

# Période de versement de l'ACCORD, en secondes. L'accord est le cas nominal : le
# compter en base à chaque appel mettrait une écriture sur le chemin chaud d'un
# serveur mono-loop, et ferait viser la MÊME ligne à toutes les sessions (la
# contention mesurée pour R8). On accumule, on verse au plus une fois par minute et
# par (connecteur, org) : le dénominateur reste exact, le prix est borné.
FLUSH_SECONDS = 60


def _enabled() -> bool:
    return (os.environ.get("OTO_L7_SHADOW", "1") or "").lower() not in ("0", "false", "no")


# ── La comparaison, et sa classe ──────────────────────────────────────────────

def _key(x) -> Optional[tuple]:
    """L'identité comparable d'un verdict : le PALIER et l'entité, jamais le compte
    (cf. le §2 du docstring de module)."""
    if x is None:
        return None
    return (getattr(x, "mode", None), getattr(x, "entity_type", None),
            str(getattr(x, "entity_id", None)))


def classify(legacy, chain: Optional[chain_resolution.ChainPick], *,
             hors_modele: Optional[str] = None) -> str:
    """La classe d'un couple de verdicts. Fonction PURE — c'est elle que le test
    exerce sur les formes relevées en prod, sans base."""
    if _key(legacy) == _key(chain):
        return ACCORD
    if legacy is not None and getattr(legacy, "via", "local") == "cross_org":
        return PERSO_CROSS_ORG
    if chain is not None and chain.mode == "group":
        return ELARGISSEMENT_EQUIPE
    if (chain is None and legacy is not None
            and getattr(legacy, "mode", None) == "platform" and hors_modele):
        # La NUANCE vient de la forme de l'instance, pas d'un `if` de plus ici.
        return hors_modele
    return INCONNU


def _sample(sub: str, legacy, chain: Optional[chain_resolution.ChainPick]) -> dict:
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
            want: str = "auto") -> None:
    """Compare les deux voies et range le résultat. **Best-effort absolu** : aucune
    exception ne sort d'ici, aucune valeur n'en revient. Appelée depuis `resolve`,
    après la marche."""
    if not sub or not _enabled():
        return
    try:
        porteur = providers.credential_provider(provider)
        chain, hors_modele = chain_resolution.chain_verdict(sub, porteur, org=org, want=want)
        classe = classify(legacy, chain, hors_modele=hors_modele)
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


# ── L'INVERSION : qui décide, et comment on revient en arrière ────────────────
# `OTO_L7_DECIDE=chain` retourne l'autorité — la chaîne décide, l'ancien chemin
# calcule et se compare. Le retour arrière est le drapeau, pas un revert : `legacy`
# (le défaut) rend le comportement d'aujourd'hui à l'octet près, et un redémarrage
# suffit. Par-process, comme le registre des tenants : basculer la préprod ne bascule
# pas la prod.
#
# ⚠️ Ce drapeau ne se met à `chain` **qu'après** deux conditions MESURÉES, pas
# décidées : une fenêtre de shadow sans divergence `inconnu` en PROD (le trafic de
# préprod ne compte pas), et la classe `free_tier_hors_modele` retombée à zéro — ce
# qui n'arrive qu'une fois la commande `scripts/seed_everyone_edges.py` passée.
DECIDE_LEGACY, DECIDE_CHAIN = "legacy", "chain"


def decide_mode() -> str:
    """Qui décide dans CE process. Toute valeur autre que `chain` vaut `legacy` : un
    drapeau mal orthographié doit laisser le comportement d'aujourd'hui, jamais
    basculer une autorité par accident."""
    return (DECIDE_CHAIN
            if (os.environ.get("OTO_L7_DECIDE", "") or "").strip().lower() == DECIDE_CHAIN
            else DECIDE_LEGACY)


def chain_decides() -> bool:
    return decide_mode() == DECIDE_CHAIN


def resolution_rungs(sub, provider: str, *, org, group, probe, want="auto"):
    """Traversée servie, commune aux appels et aux diagnostics sans déchiffrement.

    Aucune observation, consommation ou tolérance aux erreurs ici. L'anonyme garde
    sa politique org-only existante ; L7 ne change que la résolution identifiée.
    Un contexte explicite (fiche d'un tiers) ne relit jamais celui du requérant.
    """
    from . import cascade
    if sub is not None and chain_decides():
        porteur = providers.credential_provider(provider)
        yield from chain_resolution.rungs_for_picks(
            chain_resolution.chain_paliers(sub, porteur, org=org, want=want, group=group),
            probe, sub, porteur, org)
    else:
        yield from cascade.walk_cascade(sub, provider, org=org, group=group,
                                        probe=probe, want=want)


def decide(provider: str, sub: str, org: Optional[int], *, probe, want: str = "auto",
           group=scope._UNSET):
    """La chaîne DÉCIDE, l'ancien chemin calcule et se compare — le miroir exact de la
    PR 1, l'autorité retournée.

    Rend le barreau servi, de la même forme que `cascade.cascade_winner`, pour que la
    suite de `resolve` (garde du compte nommé, quota, `ResolvedCredential`) ne change
    pas d'une ligne. Ne lève jamais **pour observer** ; les McpError de la SONDE (un
    compte nommé introuvable, une ambiguïté multi-comptes), elles, remontent comme
    avant — ce sont des erreurs servies, pas de l'observation."""
    porteur = providers.credential_provider(provider)
    # Le FETCH garde le nom que le walker lui passait — la traversée change, la
    # lecture non.
    #
    # ⚠️ La lecture PARCOURT les paliers, elle ne lit pas celui que `pick` désigne
    # (#673) : la désignation se fait sur la PRÉSENCE d'un credential, la lecture au
    # FETCH, et les deux divergent sur un compte nommé. S'arrêter au premier désigné
    # rendait un refus sec là où le chemin historique passait au palier suivant.
    # `pick` reste la DÉSIGNATION servie au relevé de fenêtre — lui donner autre chose
    # ferait bouger ce qu'il mesure au moment où on corrige la lecture.
    rung = next(resolution_rungs(sub, provider, org=org, group=group,
                                 probe=probe, want=want), None)
    _observe_inverse(porteur, sub, org, want=want)
    return rung


def _observe_inverse(porteur: str, sub: str, org: Optional[int], *, want: str) -> None:
    """Sous l'autorité de la chaîne, c'est l'ANCIEN chemin qu'on relève — et à la
    sonde de PRÉSENCE, pas de fetch : la question posée est « quel barreau
    gagnerait », et y répondre ne doit pas déchiffrer une seconde clé par appel.

    Les classes sont les MÊMES qu'à l'aller : c'est ce qui permet de lire une seule
    série avant et après la bascule, au lieu de deux mesures qu'on ne pourrait pas
    comparer."""
    if not _enabled():
        return
    try:
        from . import cascade, scope as _scope
        chain, hors_modele = chain_resolution.chain_verdict(sub, porteur, org=org, want=want)
        legacy = cascade.cascade_winner(
            sub, porteur, org=org, group=lambda: _scope.current_group(sub),
            probe=cascade.PRESENCE_PROBE, want=want)
        classe = classify(legacy, chain, hors_modele=hors_modele)
        if classe == ACCORD:
            _compte_accord(porteur, int(org or 0))
            return
        db_shadow.bump_shadow(porteur, int(org or 0), classe, 1,
                              _sample(sub, legacy, chain))
        if classe == INCONNU:
            logger.warning(
                "L7 (chaîne aux commandes) : divergence INCONNUE sur %s (org=%s) — "
                "ancien=%s chaîne=%s", porteur, org, _key(legacy), _key(chain))
    except Exception:  # noqa: BLE001
        logger.warning("L7 : relevé inverse échoué (%s) — la résolution SERVIE par la "
                       "chaîne n'est PAS affectée", porteur, exc_info=True)


# ── Le seam que `resolve` appelle, et qui porte tout le lot ──────────────────
# Il vit ICI et pas dans `resolve` pour une raison de sujet : le chemin de
# résolution n'a pas à savoir qu'un drapeau existe, ni comment il s'écrit. Il demande
# « quel barreau gagne ? » ; ce module répond, et c'est lui qu'on lit le jour où l'on
# retire l'ancien chemin.

def barreau_gagnant(provider: str, sub: str, org: Optional[int], *, probe,
                    group, want: str = "auto"):
    """Le barreau qui gagne — **et c'est un drapeau qui dit laquelle des deux voies
    l'a désigné.**

    `legacy` (le défaut) : le walker décide, la chaîne calcule à côté et se compare.
    `chain` : l'inverse, à l'identique — même sonde, mêmes gardes en aval, seule la
    TRAVERSÉE change. Dans les deux sens la voie non retenue est relevée, avec les
    mêmes classes, pour qu'une seule série de mesures se lise avant ET après la
    bascule. Le retour arrière est le drapeau et un redémarrage, jamais un revert.

    L'observation ne lève jamais et ne rend rien : quoi qu'il arrive, ce qui est
    servi est le barreau, pas la mesure."""
    from . import cascade
    if chain_decides():
        return decide(provider, sub, org, probe=probe, want=want, group=group)
    win = cascade.cascade_winner(sub, provider, org=org, group=group, probe=probe,
                                 want=want)
    observe(provider, sub, org, win, want=want)
    return win
