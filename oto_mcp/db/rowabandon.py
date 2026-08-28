"""Le plafond de reprises d'une ligne — quand la file cesse de tourner à vide (#433).

Le bail répond à « qui tient cette ligne ». Il ne répond pas à « combien de fois
l'a-t-on déjà tenue pour rien » : un agent qui réserve, enquête et conclut SANS
écrire rend sa ligne, et le suivant la reprend pour refaire le même faux départ.
Deux lignes servies deux fois en dix minutes au rodage d'une campagne, aucune
écriture, et rien qui le dise — les traitements se terminent normalement.

**Seul le serveur peut compter ça.** Un ordonnanceur de flotte borne un budget
global ; il ignore quelle ligne l'agent a réservée. Le compteur vit donc dans la
ligne (`datastore_rows.claims`), il monte à chaque réservation et retombe à zéro
à la première écriture réussie — c'est CETTE remise à zéro qui distingue « reprise
après un vrai travail » de « faux départ répété ».

Deux règles gouvernent l'abandon :

- **il ne prend jamais la ligne à quelqu'un** — une ligne sous bail ACTIF est en
  cours de traitement, son titulaire n'a pas encore rendu son verdict ;
- **il ne s'improvise pas** — plafond et état d'abandon se déclarent au cycle de
  vie, validés à la pose. Un plafond sans état où verser la ligne LÈVE au lieu de
  se désarmer tout seul : une garde inerte est pire que pas de garde.
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from ..datastore.schema import (
    abandon_state_of,
    max_claims_of,
    status_field,
    terminal_states,
)
from ._conn import _connect

logger = logging.getLogger(__name__)

# Le motif CITE SES CHIFFRES : le compte et le plafond en vigueur ce jour-là. Sans
# eux, il se lit comme un verdict qu'on ne peut ni vérifier ni rejouer — le plafond
# ayant pu changer depuis.
_MOTIF = "abandonnée après {claims} réservations sans écriture, plafond {plafond}"


class Plafond(NamedTuple):
    """La politique en vigueur sur un tableau : ce qu'il faut pour abandonner."""
    valeur: int
    etat: str
    champ_statut: str
    namespace: str


def plafond_de(ns_id: int, max_claims: Optional[int] = None) -> Optional[Plafond]:
    """La politique d'abandon d'un tableau, ou None = garde inactive.

    `max_claims` (paramètre du claim) l'emporte sur la déclaration du schéma sans
    la modifier : un ordonnanceur peut serrer plus que le tableau pour une passe.
    L'état d'abandon, lui, reste une affaire de SCHÉMA — c'est un état du cycle de
    vie du tableau, pas un choix d'appelant."""
    with _connect() as conn:
        ns = conn.execute(
            "SELECT namespace, schema FROM user_datastores WHERE id = %s",
            (ns_id,)).fetchone()
    if not ns:
        return None
    schema = ns.get("schema")
    if max_claims is None:
        valeur = max_claims_of(schema)
    elif isinstance(max_claims, bool) or not isinstance(max_claims, int) or max_claims < 1:
        raise ValueError(f"max_claims doit être un entier >= 1 (reçu {max_claims!r})")
    else:
        valeur = max_claims
    if valeur is None:
        return None
    etat = abandon_state_of(schema)
    if not etat:
        raise ValueError(
            "un plafond de reprises (`max_claims`) exige `lifecycle.abandon_state` "
            "sur le champ de statut : l'état terminal où verser une ligne réservée "
            f"{valeur} fois sans écriture. Sans lui, la garde serait inerte.")
    if etat not in terminal_states(schema):
        raise ValueError(
            f"`lifecycle.abandon_state` vaut {etat!r}, qui n'est pas un état terminal "
            "déclaré — une ligne abandonnée reviendrait dans la file qu'elle vient "
            "de quitter.")
    champ = (status_field(schema) or {}).get("key")
    if not champ:
        raise ValueError("un plafond de reprises exige un champ `role=\"status\"`")
    return Plafond(valeur, etat, str(champ), str(ns.get("namespace") or ns_id))


def abandonner_les_lignes_a_bout(ns_id: int, *, max_claims: Optional[int] = None,
                                 row_ids: Optional[list] = None) -> list[dict]:
    """Verse dans l'état d'abandon les lignes LIBRES du tableau qui ont atteint le
    plafond, et rend ce qui a été abandonné.

    `row_ids` restreint au relâchement qu'on vient de faire ; sans lui, la passe
    couvre le tableau — c'est le filet des baux expirés que personne n'a relâchés.
    Une ligne sous bail actif est hors d'atteinte : son titulaire travaille encore.

    Le motif est posé dans une colonne de PLATEFORME et non dans un champ du
    schéma : il décrit ce que le serveur a fait de la ligne, pas ce que le métier
    a constaté. Non NULL, il retire la ligne de la file quel que soit le filtre du
    client — un tableau ne peut pas se rendre servable en changeant de filtre."""
    politique = plafond_de(ns_id, max_claims)
    if politique is None:
        return []
    where = ("WHERE ns_id = %s AND claims >= %s AND abandon_reason IS NULL "
             "  AND (claimed_until IS NULL OR claimed_until < NOW())")
    params: list = [ns_id, politique.valeur]
    if row_ids is not None:
        cibles = [str(r) for r in row_ids if r]
        if not cibles:
            return []
        where += " AND row_id = ANY(%s)"
        params.append(cibles)
    abandonnees: list[dict] = []
    with _connect() as conn:
        # Verrouillées avant d'être réécrites : entre le relevé et l'UPDATE, un
        # claim concurrent poserait un bail sur une ligne qu'on s'apprête à sortir
        # de la file — et le travail commencé serait perdu sans un mot.
        lignes = conn.execute(
            f"SELECT row_id, claims FROM datastore_rows {where} FOR UPDATE",
            tuple(params)).fetchall()
        for ligne in lignes:
            motif = _MOTIF.format(claims=ligne["claims"], plafond=politique.valeur)
            conn.execute(
                "UPDATE datastore_rows SET "
                "  data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text), true), "
                "  abandon_reason = %s, claimed_by = NULL, claimed_until = NULL, "
                "  claimed_run = NULL, updated_at = NOW() "
                "WHERE ns_id = %s AND row_id = %s",
                (politique.champ_statut, politique.etat, motif, ns_id, ligne["row_id"]))
            # Bruyant par construction : une ligne qui sort de la file sans que
            # personne ne l'ait demandé est exactement ce qu'on veut voir passer.
            logger.warning(
                "datastore: ligne abandonnée (plafond de reprises) — tableau=%s "
                "ligne=%s réservations=%s plafond=%s état=%s",
                politique.namespace, ligne["row_id"], ligne["claims"],
                politique.valeur, politique.etat)
            abandonnees.append({"row_id": ligne["row_id"], "claims": ligne["claims"],
                                "reason": motif})
    return abandonnees
