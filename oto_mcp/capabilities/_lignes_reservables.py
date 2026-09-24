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


class TableauAmbigu(LookupError):
    """L'adresse d'un tableau désigne plusieurs tableaux dans la portée du déclarant —
    rien n'est retenu (#1067). Le message nomme le cas et le geste qui le lève."""


def _portee_du_declarant(sub: str, org_id: int) -> dict:
    """La portée où une campagne désigne son tableau : celle de QUI l'a déclarée, dans
    l'org de la campagne — son perso, ses équipes de cette org, l'org, ses partages.
    C'est aussi la portée sous laquelle ses agents travaillent (`fleet["sub"]`)."""
    from .. import group_store
    groupes = [int(g["group_id"])
               for g in group_store.list_groups_for_user(sub, int(org_id))]
    return {"sub": sub, "org_ids": [int(org_id)], "group_ids": groupes}


def cle_a_la_declaration(adresse: str, *, sub: str, org_id: int) -> Optional[int]:
    """L'IDENTIFIANT du tableau qu'une campagne désigne, résolu UNE fois, à sa
    déclaration, dans la portée de son déclarant (#1067) — `None` si rien ne répond à
    cette adresse. LÈVE `TableauAmbigu` quand elle en désigne plusieurs.

    ⚠️ **Le nom d'un tableau n'est pas une adresse** (#365). La campagne gardait le NOM
    et le résolvait à chaque lecture : un homonyme apparu ensuite dans la portée (un
    « vivier » perso devant celui de l'org) captait le compte, l'état et la file, sans
    erreur. Résolue ici une fois, la campagne garde l'identifiant, et ce qui la lit
    ensuite passe par lui (`tableau_vise`).

    Même règle que les liens de projet (`resolve_datastore_ids_by_name`) : le rang le
    plus proche du déclarant gagne (perso, équipe, org, partage), deux candidats au
    même rang sont ambigus. Des chiffres désignent l'identifiant — ou un tableau ainsi
    NOMMÉ, et les deux à la fois sont ambigus (`db.AdresseAmbigue`)."""
    portee = _portee_du_declarant(sub, org_id)
    if adresse.isdigit():
        try:
            t = db.resolve_datastore_ns(adresse, **portee)
        except db.AdresseAmbigue as e:
            raise TableauAmbigu(
                f"`{adresse}` est l'identifiant d'un tableau ({e.par_id}) et le NOM d'un "
                f"autre ({e.par_nom}) : désigne-le par l'autre voie, ou renomme celui qui "
                "prête à confusion (`data_rename_datastore`).") from None
        return int(t["id"]) if t else None
    resolus, ambigus = db.resolve_datastore_ids_by_name([adresse], **portee)
    if adresse in ambigus:
        raise TableauAmbigu(
            f"plusieurs tableaux s'appellent « {adresse} » au même rang de ta portée : "
            "désigne le tien par son identifiant (`data_list_datastores`).")
    return resolus.get(adresse)


def _cle_heritee(f: dict) -> Optional[int]:
    """La clé d'une campagne déclarée AVANT #1067, qui ne garde que le NOM de son
    tableau — `None` s'il ne résout plus. LÈVE `TableauAmbigu`.

    ⚠️ **Jamais deviner.** Le nom est résolu comme à la déclaration
    (`cle_a_la_declaration`) ET comme la campagne le résolvait jusqu'ici
    (`resolve_datastore_ns`, perso > org > le reste) : si les deux règles ne désignent
    pas le même tableau, la campagne ne sait plus lequel elle visait, et elle est
    refusée plutôt que tranchée. Les chemins d'écriture fixent ensuite cette clé
    (`fixer_le_tableau`). À retirer quand plus aucune campagne ne garde un nom."""
    nom = f["namespace"].strip()
    cle = cle_a_la_declaration(nom, sub=f["sub"], org_id=f["org_id"])
    if cle is None:
        return None
    servi = db.resolve_datastore_ns(nom, **_portee_du_declarant(f["sub"], f["org_id"]))
    if servi is None or int(servi["id"]) != cle:
        raise TableauAmbigu(
            f"cette automatisation désigne son tableau par le nom « {nom} », qui ne "
            "désigne plus un seul tableau de façon sûre : déclares-en une autre sur "
            "l'identifiant du bon tableau.")
    return cle


def cle_de_campagne(f: dict) -> Optional[int]:
    """L'identifiant que la campagne garde (`runner_fleets.namespace`), sans lecture :
    `None` si elle ne vise aucun tableau, ou si elle ne garde encore qu'un NOM."""
    ns = (f.get("namespace") or "").strip()
    return int(ns) if ns.isdigit() else None


def tableau_vise(f: dict) -> Optional[dict]:
    """La ligne `user_datastores` que cette campagne vise, lue par son IDENTIFIANT —
    `None` si elle ne vise aucun tableau, ou si ce tableau n'est plus visible de son
    déclarant dans l'org de la campagne. LÈVE `TableauAmbigu` (campagne d'avant #1067,
    `_cle_heritee`) et sur une panne de lecture : l'appelant choisit s'il s'en passe.

    ⚠️ Aucune résolution par nom ici : un homonyme apparu dans la portée de qui que ce
    soit ne change pas le tableau d'une campagne déclarée."""
    ns = (f.get("namespace") or "").strip()
    if not ns or not f.get("sub"):
        return None
    ns_id = cle_de_campagne(f) if ns.isdigit() else _cle_heritee(f)
    if ns_id is None:
        return None
    from .. import ownership
    if not ownership.visible_in_org(f["sub"], int(f["org_id"]),
                                    ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id)):
        return None
    return db.get_datastore_by_id(ns_id)


def fixer_le_tableau(f: dict) -> dict:
    """Pose l'identifiant d'une campagne déclarée avant #1067 — sur un chemin
    d'ÉCRITURE seulement (armement, production d'un travail), jamais sur une lecture.
    Rend la campagne telle qu'elle est en base ; inchangée si elle garde déjà son
    identifiant, ou si son nom ne résout plus (son compte échoue, elle n'est pas
    servie). LÈVE `TableauAmbigu`.

    La consigne composée par la plateforme (`_instruction.de_file`) suit : elle nommait
    la file par son nom, l'agent la réservait donc dans SA portée. Une consigne écrite
    à la main n'est pas réécrite — son marqueur `{namespace}` rend l'identifiant."""
    ns = (f.get("namespace") or "").strip()
    if not ns or ns.isdigit() or not f.get("sub"):
        return f
    cle = _cle_heritee(f)
    if cle is None:
        return f
    from . import _instruction
    composee = _instruction.de_file(f.get("procedure") or "", ns, f.get("row_filter"))
    fixee = db.fixer_tableau_de_campagne(
        int(f["id"]), ancien=f["namespace"], cle=cle, consigne_composee=composee,
        consigne=_instruction.de_file(f.get("procedure") or "", str(cle),
                                      f.get("row_filter")))
    # `None` : un sondage concurrent l'a fixée entre-temps — on relit ce qu'il a posé.
    relue = fixee or db.get_fleet(int(f["id"]), int(f["org_id"]))
    if relue is None:
        raise LookupError(f"automatisation {f['id']} disparue pendant sa reprise")
    return relue


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
