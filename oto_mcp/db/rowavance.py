"""L'AVANCE d'une ligne d'une passe à la suivante — la contrepartie de l'abandon (oto#95).

Une campagne en plusieurs passes (`a_traiter → societe → dirigeant → email`) traite
chaque ligne passe après passe. L'agent lit l'état de la ligne quand il la réserve ;
il ne l'écrit pas — mesuré : zéro écriture sur deux passages. Ce n'est pas une
négligence, c'est ce qui arrive à un état qui n'existe que si quelqu'un pense à le
poser. Personne ne pouvait donc reprendre une passe, ni savoir qu'elle était finie.

La plateforme savait déjà faire RECULER une ligne : réservée N fois sans écriture, elle
la verse dans `abandon_state` (`rowabandon`). Elle la fait désormais AVANCER : relâchée
après une écriture, une ligne passe à l'état que `lifecycle.advance` déclare pour son
état courant. L'agent réserve par état (`filter`, ou `claimable`) ; il n'a pas à savoir
où en est la ligne.

**La preuve d'écriture est le compteur de reprises, pas le journal.** `claims` monte à
chaque prise et retombe à zéro à chaque écriture de la ligne, dans la même transaction
que l'écriture (`datastore_merge_row_locked`). Relâchée avec `claims = 0`, la ligne a
donc été écrite depuis sa dernière prise — et, sous bail actif, seul le run titulaire
peut l'écrire (`_lease_guard`). Trois raisons de le préférer au journal des révisions :

- c'est LE critère de l'abandon, lu à l'envers : un relâchement finit en avance
  (`claims = 0`) ou compte vers le plafond (`claims ≥ 1`), jamais les deux, jamais
  aucun des deux ;
- le journal n'écrit pas une écriture SANS EFFET (même valeur renvoyée) : une passe
  qui confirme ce qui était là remet le compteur à zéro sans laisser de révision — la
  ligne ne serait alors ni abandonnée ni avancée, et tournerait sur place ;
- le journal peut être coupé (`OTO_JOURNAL_REVISIONS=off`) et purgé ; le compteur vit
  dans la ligne, sous le verrou qu'on tient déjà.

**Le journal décide d'une seule chose : l'agent a-t-il écrit l'ÉTAT lui-même ?** Si une
révision depuis la prise change la colonne d'état, son écriture fait foi — on n'avance
pas une seconde fois. Le compteur ne sait pas QUOI a été écrit ; le journal, si. Sans
journal (coupé), cette question n'a pas de réponse sûre : l'avance ne se fait pas, et
le dit au journal applicatif — avancer à l'aveugle pourrait sauter une passe, et la
révision de l'avance elle-même ne serait ni lisible ni réversible.

Ce que l'avance n'est PAS : une garde. Rien n'est refusé ; un relâchement qui ne remplit
pas les conditions libère la ligne exactement comme avant. Elle ne se fait qu'au
RELÂCHEMENT — `data_release`, `run_finish`, la conclusion d'un job, la libération forcée
d'un superviseur —, jamais à l'expiration d'un bail : un bail qui expire est un agent
qui n'a pas rendu son verdict, et la passe se refait.

Ce module DÉCIDE ; l'écriture vit dans `rowlock._relacher`, dans la transaction qui
libère, pour qu'aucune réservation ne s'intercale entre la libération et l'avance.
"""
from __future__ import annotations

import json
import logging
from typing import NamedTuple, Optional

from ..datastore.couches import VALUE_LAYER, unwrap
from ..datastore.schema import avance_of, status_field
from . import journal_revisions

logger = logging.getLogger(__name__)

#: L'acteur des écritures de la file — le même que l'abandon (`rowabandon`).
ACTEUR = "file-de-travail"


class Avance(NamedTuple):
    ns_id: int
    row_id: str
    champ: str
    depuis: str
    vers: str
    #: La cellule d'état réécrite : la valeur nue, ou ses couches gardées.
    cellule: object
    #: Le run qui tenait la ligne — celui auquel la révision de l'avance est rattachée.
    run_id: Optional[str]

    def rendu(self) -> dict:
        return {"field": self.champ, "from": self.depuis, "to": self.vers}


def _cellule(actuelle, vers: str):
    """La cellule d'état après l'avance : une valeur à couches garde ses couches."""
    if isinstance(actuelle, dict) and VALUE_LAYER in actuelle:
        return {**actuelle, VALUE_LAYER: vers}
    return vers


def avances(conn, lignes: list[dict]) -> list[Avance]:
    """Les avances à poser parmi les `lignes` qu'on relâche (verrouillées, encore sous
    leur bail : `ns_id`, `row_id`, `data`, `claims`, `claimed_at`, `claimed_run`).
    Lit les schémas et le journal ; n'écrit rien."""
    par_tableau: dict[int, list[dict]] = {}
    for ligne in lignes:
        par_tableau.setdefault(int(ligne["ns_id"]), []).append(ligne)
    schemas = {int(r["id"]): r["schema"] for r in conn.execute(
        "SELECT id, schema FROM user_datastores WHERE id = ANY(%s)",
        (list(par_tableau),)).fetchall()}
    out: list[Avance] = []
    for ns_id, du_tableau in par_tableau.items():
        schema = schemas.get(ns_id)
        suite = avance_of(schema)
        if not suite:
            continue
        champ = str((status_field(schema) or {}).get("key"))
        for ligne in du_tableau:
            a = _avance_de(conn, ns_id, ligne, champ, suite)
            if a is not None:
                out.append(a)
    return out


def _avance_de(conn, ns_id: int, ligne: dict, champ: str,
               suite: dict) -> Optional[Avance]:
    # Aucune écriture depuis la prise : la passe a échoué, le plafond s'en charge.
    if int(ligne["claims"] or 0) != 0 or ligne["claimed_at"] is None:
        return None
    data = ligne["data"] if isinstance(ligne["data"], dict) else json.loads(
        ligne["data"] or "{}")
    actuelle = data.get(champ)
    etat = unwrap(actuelle)
    if etat is None or str(etat) not in suite:
        return None
    if journal_revisions.journal_coupe():
        logger.error(
            "datastore: avance non faite, journal des révisions coupé (%s=off) — "
            "impossible de savoir si la passe a écrit l'état elle-même. tableau=%s "
            "ligne=%s état=%s", journal_revisions.VARIABLE, ns_id, ligne["row_id"], etat)
        return None
    # L'agent a-t-il écrit l'ÉTAT pendant sa passe ? Alors son écriture fait foi.
    # ⚠️ `claimed_at` comparé EN BASE, jamais relu en Python : le row factory rend les
    # instants en texte, et un arrondi à la seconde ferait entrer dans la fenêtre la
    # révision de l'avance PRÉCÉDENTE quand la ligne est reprise dans la même seconde.
    ecrit = conn.execute(
        f"SELECT 1 AS oui FROM {journal_revisions.TABLE} v "
        "JOIN datastore_rows r ON r.ns_id = v.ns_id AND r.row_id = v.row_id "
        "WHERE v.ns_id = %s AND v.row_id = %s AND v.at >= r.claimed_at "
        "  AND v.diff ? %s LIMIT 1",
        (ns_id, ligne["row_id"], champ)).fetchone()
    if ecrit:
        return None
    vers = suite[str(etat)]
    return Avance(ns_id, str(ligne["row_id"]), champ, str(etat), vers,
                  _cellule(actuelle, vers), ligne["claimed_run"])
