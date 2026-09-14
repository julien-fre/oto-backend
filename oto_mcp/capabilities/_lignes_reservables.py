"""Combien de lignes chaque campagne candidate peut encore réserver — pour l'ordonnanceur.

Jusqu'au 14/09/2026, `campagne_a_servir` tirait à chance ÉGALE entre les campagnes
éligibles sans regarder leur file : une passe à 171 lignes réservables recevait un
tirage sur cinq, comme une passe à 2 lignes — ou une passe VIDE, qui fabriquait alors
des travaux à vide. Mesuré sur une chaîne de passes : la file du milieu grossissait
(148 → 171 lignes en 26 minutes), l'amont s'y déversant plus vite qu'elle n'était tirée.

⚠️ **Jamais à l'agent qui travaille — c'est ce qui le sépare de la décision du
13/09/2026** (« la plateforme ne compte pas à la place de l'agent ») : ce compte n'entre
dans aucune consigne et ne part avec aucun travail. Il sert l'ordonnanceur et, depuis
le 14/09/2026 (décision produit), le SUPERVISEUR d'une campagne, qui le lit dans
`oto_fleet op=state` (`pour_le_superviseur`). Sans lui, une campagne dont le filtre ne
recoupe jamais le tableau restait `armed` sans travail ni signal, puisque sautée à
chaque sondage. Un passage ne le lirait que s'il déclarait lui-même `oto_fleet` dans
ses outils.

⚠️ **Le même périmètre que la réservation, à la clause près** : les clauses viennent de
`perimetre_de_reservation`, que `claim_next` appelle aussi, et
`db.datastore_compter_reservables` partage la base réclamable du pick. Le comptage du
09/09 avait divergé de la réservation pour l'avoir recomposée.

⚠️ **Péremption assumée : 15 s, par PROCESSUS.** Un compte coûte un scan du tableau
(~150 ms sur 8 910 lignes larges) et l'ordonnanceur est sondé par chaque worker — après
chaque travail, et toutes les 15 s à vide (`_POLL_S` du runner). Le résultat se garde
donc 15 s : une campagne qui passe de 0 à N lignes attend au plus ce délai, et une file
qui se vide peut encore produire un travail à vide dans la fenêtre. Plusieurs processus
servent la même base (la couleur de prod active, la préprod, le canari) : chacun garde
SA vue, périmée d'au plus 15 s. Un cache partagé a été écarté — une dépendance de plus
pour gagner une fenêtre que le système tolère déjà. Ne pas le « corriger » sans
rouvrir cet arbitrage.

Une campagne dont le compte ÉCHOUE (tableau introuvable, schéma illisible) n'est pas
servie, et la cause est journalisée : la servir fabriquerait des travaux dont la
réservation échouerait de la même façon.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

from .. import db
from ..datastore.file_de_travail import perimetre_de_reservation

logger = logging.getLogger(__name__)

#: Durée de vie d'un compte — la respiration d'un worker à vide (`_POLL_S` du runner).
TTL_S = 15.0

#: Une panne qui dure se rejournalise après ce délai, pour rester visible sans noyer.
_RAPPEL_S = 900.0

#: `{fleet_id: (échéance, signature, lignes)}` — `lignes` vaut `_ECHEC` si le compte a échoué.
_CACHE: dict[int, tuple[float, str, object]] = {}
_ECHEC = object()

#: `{fleet_id: (cause, instant)}` — la dernière cause journalisée par campagne.
_SIGNALE: dict[int, tuple[str, float]] = {}


def tableau_vise(f: dict) -> Optional[dict]:
    """La ligne `user_datastores` que ce passage vise, résolue au nom de QUI l'a déclaré
    (`f["sub"]`) — None s'il ne vise aucun tableau ou si le nom ne résout pas. LÈVE sur
    une panne de lecture : l'appelant choisit s'il s'en passe."""
    ns = (f.get("namespace") or "").strip()
    if not ns or not f.get("sub"):
        return None
    from .. import group_store
    org = int(f["org_id"])
    groupes = [int(g["group_id"])
               for g in group_store.list_groups_for_user(f["sub"], org)]
    return db.resolve_datastore_ns(ns, sub=f["sub"], org_ids=[org], group_ids=groupes)


def _signature(f: dict) -> str:
    """Ce dont le compte dépend dans la déclaration : s'il change, le compte gardé ne vaut plus."""
    return json.dumps([f.get("namespace"), f.get("sub"), f.get("org_id"), f.get("row_filter")],
                      sort_keys=True, default=str)


def _echec(fid: int, sig: str, e: Exception, maintenant: float) -> bool:
    """Garde l'échec pour la fenêtre, et dit s'il faut le journaliser (cause neuve ou rappel)."""
    _CACHE[fid] = (maintenant + TTL_S, sig, _ECHEC)
    cause = f"{type(e).__name__}: {e}"
    vue, quand = _SIGNALE.get(fid, (None, 0.0))
    if cause == vue and maintenant - quand < _RAPPEL_S:
        return False
    _SIGNALE[fid] = (cause, maintenant)
    return True


def pour_le_superviseur(f: dict) -> dict:
    """Le compte que l'ordonnanceur voit pour UNE campagne, rendu à qui la supervise :
    `reservable_rows`, et quand il manque, POURQUOI — `no_table` (elle ne vise aucun
    tableau) ou `count_failed` (le compte a échoué, la cause est au journal). Même
    fenêtre de 15 s que l'ordonnanceur : c'est SA vue, pas une seconde lecture qui
    pourrait la contredire."""
    fid = int(f["id"])
    comptes = lignes_reservables([f])
    if fid not in comptes:
        return {"reservable_rows": None, "reservable_rows_unavailable": "count_failed"}
    if comptes[fid] is None:
        return {"reservable_rows": None, "reservable_rows_unavailable": "no_table"}
    return {"reservable_rows": comptes[fid], "reservable_rows_unavailable": None}


def lignes_reservables(candidates: list[dict], *,
                       horloge: Callable[[], float] = time.monotonic,
                       ) -> dict[int, Optional[int]]:
    """`{fleet_id: lignes réservables}` pour les campagnes candidates.

    - un entier : ce que `claim_next` servirait sous le filtre du passage ;
    - `None` : la campagne ne vise aucun tableau, il n'y a rien à compter ;
    - **absente** : son compte a échoué — elle ne doit pas être servie."""
    maintenant = horloge()
    for fid in [k for k, (echeance, _, _) in _CACHE.items() if echeance <= maintenant]:
        del _CACHE[fid]
    comptes: dict[int, Optional[int]] = {}
    par_tableau: dict[int, dict[int, list]] = {}
    signatures: dict[int, str] = {}
    for f in candidates:
        fid, sig = int(f["id"]), _signature(f)
        garde = _CACHE.get(fid)
        if garde is not None and garde[1] == sig:
            if garde[2] is not _ECHEC:
                comptes[fid] = garde[2]
            continue
        signatures[fid] = sig
        if not (f.get("namespace") or "").strip():
            comptes[fid] = None
            _CACHE[fid] = (maintenant + TTL_S, sig, None)
            continue
        try:
            t = tableau_vise(f)
            if t is None:
                raise LookupError("le tableau visé ne résout pas pour le déclarant")
            _, clauses = perimetre_de_reservation(t.get("schema"), int(t["id"]),
                                                  f.get("row_filter"))
        except Exception as e:  # noqa: BLE001 — une campagne illisible ne prive pas les autres
            if _echec(fid, sig, e, maintenant):
                logger.warning("campagne %s non servie : ses lignes réservables ne se "
                               "comptent pas (%s)", fid, e, exc_info=True)
            continue
        par_tableau.setdefault(int(t["id"]), {})[fid] = clauses
    for ns_id, perimetres in par_tableau.items():
        try:
            lus = db.datastore_compter_reservables(ns_id, perimetres)
        except Exception as e:  # noqa: BLE001 — même parti, pour les campagnes du tableau
            for fid in perimetres:
                if _echec(fid, signatures[fid], e, maintenant):
                    logger.warning("campagne %s non servie : le comptage du tableau %s a "
                                   "échoué (%s)", fid, ns_id, e, exc_info=True)
            continue
        for fid, n in lus.items():
            comptes[fid] = n
            _CACHE[fid] = (maintenant + TTL_S, signatures[fid], n)
    return comptes
