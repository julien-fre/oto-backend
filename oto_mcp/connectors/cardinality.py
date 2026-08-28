"""LA question « ce connecteur porte-t-il plusieurs comptes ? », et son unique réponse.

Trois crans, dans cet ordre — c'est l'arbitrage d'Alexis du 2026-08-27, dans sa lettre :

1. la surcharge de l'**ORG** de contexte, si elle existe ;
2. la surcharge **PLATEFORME**, si elle existe ;
3. le défaut du **CODE** — `Connector.cardinality` déclaré, sinon dérivé du descripteur
   d'auth (`Connector.auth_multi_account`).

**Pourquoi un module et pas une propriété.** Le registre (`providers/`) est PUR : aucun
import `oto_mcp`, aucune base. Il ne peut donc pas connaître une surcharge. Et la
surcharge ne peut pas vivre à moitié : une cardinalité lue par la GARDE D'ÉCRITURE mais
pas par la RÉSOLUTION accepterait un deuxième compte que personne n'irait jamais lire —
c'est très exactement le défaut qu'oto-backend#409 a corrigé le 27/08. D'où une source
unique, ici, que les deux appellent.

⚠️ **Zéro lecture de base sur le chemin chaud.** La cardinalité est consultée jusqu'à
quatre fois par appel d'outil (`access/resolve.py`), sur un serveur MONO-LOOP, contre
une base managée distante : une requête par consultation est le mode de panne que
`docs/event-loop-perf.md` documente. Les surcharges sont donc chargées **au boot** dans
un dictionnaire de process, et rechargées par un **geste explicite**
(`oto_admin_connector_setting op=reload`) — exactement le patron du registre
d'émetteurs (`server.reload_tenant_registry`), avec la même conséquence, à dire dans la
doc : **le rechargement est PAR PROCESS**. Recharger la preprod ne recharge pas la prod.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .. import providers

logger = logging.getLogger(__name__)

# La `key` de cette propriété dans `connector_settings`.
KEY = "cardinality"
MONO, MULTI = "mono", "multi"

# Les surcharges VIVANTES du process : {(scope_type, scope_id, connector): valeur}.
# Remplacé par SWAP de référence (atomique sous CPython) — aucun lecteur ne voit un
# dictionnaire à moitié rempli, et il n'y a donc pas de verrou sur la lecture.
_OVERRIDES: dict = {}
# Le chargement, lui, se sérialise : deux boots concurrents (tests, threadpool) ne
# doivent pas faire deux requêtes pour le même résultat.
_LOCK = threading.Lock()
_LOADED = False


def reload() -> int:
    """Relit les surcharges depuis la base et les installe. Rend leur nombre.

    Échec de lecture ⟹ l'exception REMONTE et rien n'est installé : le process garde
    les surcharges d'avant, entières — jamais un jeu à moitié posé. C'est l'appelant
    (le boot, la capacité admin) qui décide s'il tolère l'échec."""
    global _OVERRIDES, _LOADED
    from ..db import connector_settings as store
    neuf = {}
    for r in store.list_connector_settings(KEY):
        valeur = (r["value"] or "").strip().lower()
        if valeur not in (MONO, MULTI):
            # Compté et journalisé, jamais interprété : inventer un sens à une valeur
            # inconnue, c'est décider à la place de celui qui l'a posée.
            logger.warning(
                "cardinalité : surcharge IGNORÉE, valeur inconnue %r pour %s/%s %s "
                "(attendu %r ou %r)", r["value"], r["scope_type"], r["scope_id"],
                r["connector"], MONO, MULTI)
            continue
        neuf[(r["scope_type"], str(r["scope_id"]), r["connector"])] = valeur
    _OVERRIDES = neuf
    _LOADED = True
    logger.info("cardinalité : %d surcharge(s) chargée(s)%s", len(neuf),
                (" — " + ", ".join(f"{k[2]}@{k[0]}:{k[1]}={v}"
                                   for k, v in sorted(neuf.items()))) if neuf else "")
    return len(neuf)


def _ensure_loaded() -> None:
    """Premier appel : charge. **Fail-open loggé** — une base injoignable laisse le
    dictionnaire VIDE, donc tout le monde retombe sur le défaut du code, qui est le
    comportement d'avant ce lot. La direction du repli est le point : une surcharge
    ÉLARGIT (mono → multi), donc son absence ne peut que resserrer, jamais ouvrir."""
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        try:
            reload()
        except Exception:
            logger.warning("cardinalité : surcharges illisibles — défauts du registre "
                           "seuls (fail-open)", exc_info=True)
            _LOADED = True


def _porteur(connector: str):
    """L'entrée de registre du connecteur qui PORTE le credential de `connector`.

    Deux fonctions, et il faut les deux : `credential_provider` résout la DÉLÉGATION
    (les six canaux unipile pointent `unipile`) — c'est un pur calcul de nom, un seul
    niveau — et `connector_for_provider` est le lookup. Sans la première, surcharger
    `unipile` laisserait ses canaux au défaut du code : deux réponses pour une seule
    clé, exactement la divergence du 2026-07-07 (carte verte à côté d'un « Bloqué »)."""
    return providers.connector_for_provider(providers.credential_provider(connector))


def overrides_snapshot() -> dict:
    """Copie des surcharges vivantes — surface admin et journal. Jamais le dict lui-même
    (l'exposer laisserait un appelant l'éditer sans passer par la base)."""
    _ensure_loaded()
    return dict(_OVERRIDES)


def is_multi_account(connector: str, org: "int | str | None" = None) -> bool:
    """Ce connecteur porte-t-il plusieurs comptes, POUR CETTE ORG ? La seule fonction
    à appeler — la garde d'écriture et la résolution passent toutes deux par ici.

    `connector` est normalisé vers le PORTEUR du credential (délégation
    `Connector.credential_of` : les canaux unipile pointent `unipile`), comme partout
    ailleurs — sans quoi une surcharge posée sur le porteur serait invisible depuis un
    canal, et l'inverse.

    `org` = l'org de CONTEXTE du requérant, jamais son appartenance (même lecture que
    `_platform_grantee_scope`). None ⟹ seule la surcharge plateforme s'applique."""
    con = _porteur(connector)
    if con is None:
        return False
    _ensure_loaded()
    if org is not None:
        valeur = _OVERRIDES.get(("org", str(org), con.name))
        if valeur:
            return valeur == MULTI
    valeur = _OVERRIDES.get(("platform", "platform", con.name))
    if valeur:
        return valeur == MULTI
    return con.auth_multi_account


def accepted_anywhere(connector: str) -> bool:
    """Le connecteur accepte-t-il un `_account=` à l'appel, dans UNE org quelconque ?

    Volontairement org-AGNOSTIQUE, et permissif. L'axe `_account=` est lu très bas dans
    le chemin d'appel (`call_axes.axes_for_call`, appelé par le middleware), là où
    l'org de contexte coûterait une requête à chaque appel. Or l'axe n'autorise rien :
    il ne fait que NOMMER un compte, et c'est la résolution qui refuse, actionnable, si
    ce compte n'existe pas au palier. Accepter le mot là où l'org n'a pas été élargie
    ne donne donc accès à rien — tandis que le refuser rendrait une org élargie
    INCAPABLE de viser son second compte : le défaut d'oto-backend#409, une clé posée
    que rien ne va lire."""
    con = _porteur(connector)
    if con is None:
        return False
    if con.auth_multi_account:
        return True
    _ensure_loaded()
    return any(v == MULTI for (_, _, nom), v in _OVERRIDES.items() if nom == con.name)


def _reset_for_tests() -> None:
    """Vide le cache de process. Réservé aux tests — un test qui pose une surcharge
    doit pouvoir la faire prendre sans redémarrer l'interpréteur."""
    global _OVERRIDES, _LOADED
    _OVERRIDES, _LOADED = {}, False
