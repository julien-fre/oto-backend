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

**Et sans déclaration, un plafond de PLATEFORME** (oto#101). La garde était opt-in :
sans `lifecycle.max_claims`, une ligne relâchée sans écriture revenait en tête de la
file indéfiniment — livelock reproduit trois fois sur trois, chaque appel réussissant.
Le plafond par défaut (`OTO_MCP_CLAIM_DEFAULT_MAX_CLAIMS`, 3 : la valeur que les
tableaux déclarent) met alors la ligne DE CÔTÉ par la seule colonne de plateforme
(`abandon_reason`, motif compris) : il ne touche pas aux données du métier — ni au
statut, qu'aucun état déclaré ne lui désigne. Une écriture la remet dans la file,
comme pour l'abandon déclaré.

**Le motif dit la CAUSE, pas seulement le symptôme** (oto-backend#491). « Abandonnée
après 3 réservations sans écriture » réunissait quatre causes à quatre gestes
différents — un redémarrage qui a coupé les sessions, un modèle qui dégénère, un outil
en panne, une ligne impossible. Au moment d'abandonner, le serveur joint donc au motif
ce que la DERNIÈRE tentative a rencontré (`_cause_de_l_abandon`), et garde le run de
cette tentative dans une colonne à part (`abandon_run`, servie `_abandon_run`) : le
rapprochement avec le journal se fait sans relire une phrase.
"""
from __future__ import annotations

import logging
import os
from typing import NamedTuple, Optional

from ..datastore.schema import (
    abandon_state_of,
    max_claims_of,
    status_field,
    terminal_states,
)
from .. import geste
from ._conn import _connect
from .estampille import ecriture_de_lignes

logger = logging.getLogger(__name__)

# Le motif CITE SES CHIFFRES : le compte et le plafond en vigueur ce jour-là. Sans
# eux, il se lit comme un verdict qu'on ne peut ni vérifier ni rejouer — le plafond
# ayant pu changer depuis.
_MOTIF = "abandonnée après {claims} réservations sans écriture, plafond {plafond}"
# Le plafond de plateforme se NOMME dans le motif : sans ça, le propriétaire lirait
# un plafond qu'il n'a jamais déclaré, et ne saurait pas où le régler.
_MOTIF_DEFAUT = (_MOTIF + " (défaut de la plateforme, aucun `lifecycle.max_claims` "
                 "déclaré) — mise de côté, statut inchangé ; une écriture la remet "
                 "dans la file")
# Et il finit par sa CAUSE (#491) : ce que la dernière tentative a rencontré.
_DERNIERE_TENTATIVE = " — dernière tentative : {cause}"

# Les causes, par ordre de préférence (`_cause_de_l_abandon`). Un texte venu d'ailleurs
# (erreur d'un travail, note d'un run, refus d'écriture) est ramené à une ligne et borné :
# le motif est une colonne qu'on lit d'un coup d'œil et qu'on ventile (`abandon_reasons`).
_CAUSE_TRAVAIL = "erreur du travail : {texte}"
_CAUSE_RUN = "run clos `{outcome}` : {texte}"
_CAUSE_ECRITURE = "écriture refusée : {texte}"
_REFUS_QUI_SE_NOMME = "écriture refusée"
_CAUSE_APPELS = "appels en erreur : {outils}"
_CAUSE_BAIL = "bail expiré sans relâchement"
_CAUSE_RIEN = "aucune écriture tentée"
_CAUSE_HORS_RUN = "cause inconnue : réservée hors run"
_CAUSE_ILLISIBLE = "cause inconnue : journal illisible"
_TEXTE_MAX = 300
# Les issues de run qui disent un échec — `done` et `partial` n'en sont pas
# (`run_status.OUTCOMES`).
_ISSUES_EN_ECHEC = ("failed", "blocked")
# Le journal est lu dans la transaction de l'abandon, donc sur le chemin d'une
# réservation : borné, et jamais au point de la faire échouer.
_BORNE_LECTURE = "2000"
_OUTILS_MAX = 5

# Le plafond quand le tableau n'en déclare aucun (oto#101). Réglage d'instance, lu à
# chaque évaluation ; illisible, il LÈVE — jamais de repli silencieux sur une garde.
_PLAFOND_PAR_DEFAUT = "OTO_MCP_CLAIM_DEFAULT_MAX_CLAIMS"


def plafond_par_defaut() -> int:
    """Le plafond de reprises d'un tableau qui n'en déclare pas (oto#101)."""
    brut = os.environ.get(_PLAFOND_PAR_DEFAUT, "3")
    refus = f"{_PLAFOND_PAR_DEFAUT} doit être un entier >= 1 (lu : {brut!r})"
    try:
        valeur = int(brut)
    except ValueError:
        raise ValueError(refus) from None
    if valeur < 1:
        raise ValueError(refus)
    return valeur


class Plafond(NamedTuple):
    """La politique en vigueur sur un tableau : ce qu'il faut pour abandonner.

    `etat` et `champ_statut` valent None pour le plafond de PLATEFORME (aucun
    `lifecycle.max_claims` déclaré) : la ligne est mise de côté par
    `abandon_reason` seul, ses données restent intactes."""
    valeur: int
    etat: Optional[str]
    champ_statut: Optional[str]
    namespace: str

    @property
    def par_defaut(self) -> bool:
        return self.etat is None


def plafond_de(ns_id: int, max_claims: Optional[int] = None) -> Optional[Plafond]:
    """La politique d'abandon d'un tableau, ou None = tableau introuvable.

    Sans `lifecycle.max_claims`, le plafond de PLATEFORME s'applique
    (`plafond_par_defaut`, oto#101) : une file sans garde tournait à vide pour
    toujours. `max_claims` (paramètre du claim) ne peut qu'ASSOUPLIR le plafond en
    vigueur — déclaré ou par défaut —, jamais le serrer.

    ⚠️ **Il l'emportait, et c'était le défaut** (#132). Deux raisons, et la seconde
    est la pire :

    - le paramètre vaut pour l'APPEL, l'abandon vaut pour la LIGNE, définitivement.
      Un ordonnanceur qui « serre pour sa passe » sort des lignes de la file pour
      toujours — la passe finit, l'abandon reste ;
    - sur un tableau SANS plafond déclaré, la garde est inactive. Un appelant qui
      passait une valeur l'ARMAIT donc pour tout le tableau, sur des lignes que
      personne n'avait décidé de plafonner.

    Mesuré : le plafond a été posé 219 fois par le modèle, **219 fois à la valeur
    1** — jamais autre chose. Ni le runner ni la flotte ne le fixaient : c'est le
    schéma de l'outil qui l'offre, et tout paramètre offert au modèle sera réglé
    par lui. Une réservation sans écriture aboutie sortait alors la ligne au
    premier tour, en silence.

    L'état d'abandon, lui, reste une affaire de SCHÉMA — c'est un état du cycle de
    vie du tableau, pas un choix d'appelant."""
    with _connect() as conn:
        ns = conn.execute(
            "SELECT namespace, schema FROM user_datastores WHERE id = %s",
            (ns_id,)).fetchone()
    if not ns:
        return None
    schema = ns.get("schema")
    namespace = str(ns.get("namespace") or ns_id)
    declare = max_claims_of(schema)
    en_vigueur = declare if declare is not None else plafond_par_defaut()
    if max_claims is None:
        valeur = en_vigueur
    elif isinstance(max_claims, bool) or not isinstance(max_claims, int) or max_claims < 1:
        raise ValueError(f"max_claims doit être un entier >= 1 (reçu {max_claims!r})")
    else:
        # Le paramètre assouplit, jamais l'inverse : ce qui est en jeu n'est pas la
        # sévérité d'une passe mais la sortie DÉFINITIVE d'une ligne de la file.
        valeur = max(en_vigueur, max_claims)
    if declare is None:
        # Le plafond de plateforme ne choisit pas d'état à la place du métier : même
        # un `abandon_state` déclaré sans plafond n'est pas lu ici — un schéma posé
        # avant sa garde de pose le rendrait illisible, et chaque réservation lèverait.
        return Plafond(valeur, None, None, namespace)
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
    return Plafond(valeur, etat, str(champ), namespace)


def _court(texte) -> str:
    """Un texte venu d'ailleurs, sur une ligne et borné."""
    plat = " ".join(str(texte).split())
    return plat if len(plat) <= _TEXTE_MAX else plat[:_TEXTE_MAX] + "…"


def _lu_au_journal(conn, ns_id: int, row_id: str, run_id: str) -> Optional[str]:
    """La cause que les journaux du RUN disent, ou None s'ils ne disent rien.

    Trois lectures bornées : `runs` par sa clé, puis `tool_calls` par
    `idx_tool_calls_run (run_id, created_at)`, depuis la dernière PRISE de la ligne —
    `claimed_at`, comparé EN BASE (le row factory rend les instants en texte). C'est
    la fenêtre de la tentative : ce que le même run a fait avant de prendre cette
    ligne ne la concerne pas."""
    run = conn.execute("SELECT outcome, note FROM runs WHERE run_id = %s",
                       (run_id,)).fetchone()
    if run and run["outcome"] in _ISSUES_EN_ECHEC:
        return _CAUSE_RUN.format(outcome=run["outcome"],
                                 texte=_court(run["note"] or "sans note"))
    fenetre = ("FROM tool_calls WHERE run_id = %(run)s AND kind = 'mcp' "
               "  AND created_at >= (SELECT claimed_at FROM datastore_rows "
               "                      WHERE ns_id = %(ns)s AND row_id = %(row)s) ")
    cle = {"run": run_id, "ns": ns_id, "row": row_id}
    # La DERNIÈRE écriture tentée, sur cette ligne ou sans ligne lisible au journal
    # (un lot, ou un `oto_call` dont les arguments sont journalisés en texte).
    ecriture = conn.execute(
        "SELECT ok, error " + fenetre +
        "  AND ((tool = 'data_write' AND COALESCE(args->>'id', %(row)s) = %(row)s) "
        "       OR (tool = 'oto_call' AND args->>'name' = 'data_write')) "
        "ORDER BY created_at DESC LIMIT 1", cle).fetchone()
    if ecriture and not ecriture["ok"]:
        refus = _court(ecriture["error"] or "sans motif")
        # Le refus du schéma se nomme déjà (« écriture refusée par le schéma : … ») :
        # le préfixer une seconde fois ne dirait rien de plus.
        return refus if refus.startswith(_REFUS_QUI_SE_NOMME) \
            else _CAUSE_ECRITURE.format(texte=refus)
    # `oto_call` se nomme par sa CIBLE : c'est elle qui a échoué (#784).
    outils = [r["outil"] for r in conn.execute(
        "SELECT outil FROM (SELECT CASE WHEN tool = 'oto_call' "
        "                          THEN COALESCE(args->>'name', tool) ELSE tool END "
        "                          AS outil, created_at "
        + fenetre + " AND NOT ok ORDER BY created_at DESC LIMIT 50) e "
        "GROUP BY outil ORDER BY max(created_at) DESC LIMIT %(n)s",
        {**cle, "n": _OUTILS_MAX}).fetchall()]
    if outils:
        return _CAUSE_APPELS.format(outils=", ".join(outils))
    return None


def _cause_de_l_abandon(conn, *, ns_id: int, row_id: str, run_id: Optional[str],
                        erreur: Optional[str], bail_expire: bool) -> str:
    """Ce que la DERNIÈRE tentative a rencontré (#491), par ordre de préférence :
    l'erreur du travail qui a relâché la ligne, l'échec déclaré de son run, sa
    dernière écriture refusée, ses appels en erreur — à défaut, son bail expiré sans
    relâchement (l'agent a disparu), ou rien tenté du tout.

    Lu dans la transaction de l'abandon, sous un POINT DE SAUVEGARDE et une borne de
    durée : une lecture qui échoue laisse l'abandon se faire, avec « cause inconnue »,
    et le DIT au journal applicatif — jamais une réservation refusée pour un motif."""
    if erreur and erreur.strip():
        return _CAUSE_TRAVAIL.format(texte=_court(erreur))
    if run_id:
        try:
            with conn.transaction():
                avant = conn.execute(
                    "SELECT current_setting('statement_timeout') AS v").fetchone()["v"]
                conn.execute("SELECT set_config('statement_timeout', %s, true)",
                             (_BORNE_LECTURE,))
                lue = _lu_au_journal(conn, ns_id, row_id, run_id)
                conn.execute("SELECT set_config('statement_timeout', %s, true)", (avant,))
        except Exception:  # noqa: BLE001 — journalisé ; l'abandon ne dépend pas de sa cause
            logger.error(
                "datastore: cause d'abandon illisible — tableau=%s ligne=%s run=%s",
                ns_id, row_id, run_id, exc_info=True)
            return _CAUSE_ILLISIBLE
        if lue:
            return lue
    if bail_expire:
        return _CAUSE_BAIL
    return _CAUSE_RIEN if run_id else _CAUSE_HORS_RUN


def abandonner_les_lignes_a_bout(ns_id: int, *, max_claims: Optional[int] = None,
                                 relachees: Optional[dict] = None,
                                 erreur: Optional[str] = None) -> list[dict]:
    """Verse dans l'état d'abandon les lignes LIBRES du tableau qui ont atteint le
    plafond, et rend ce qui a été abandonné.

    `relachees` = `{row_id: run}`, les lignes qu'on vient de relâcher et le run qui
    les tenait (le relâchement l'a déjà effacé de la ligne) ; sans lui, la passe
    couvre le tableau — c'est le filet des baux expirés que personne n'a relâchés,
    dont le run est encore sur la ligne. `erreur` = ce que le relâcheur sait de
    l'échec (la conclusion d'un travail) : la première des causes (#491).
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
    if relachees is not None:
        cibles = [str(r) for r in relachees if r]
        if not cibles:
            return []
        where += " AND row_id = ANY(%s)"
        params.append(cibles)
    abandonnees: list[dict] = []
    # L'abandon est une décision du SERVEUR, pas de l'appel qui l'a déclenchée (une
    # réservation, un relâchement, la fermeture d'un run) : même geste, pour qu'on
    # retrouve l'appel, mais `system` et l'acteur de la file (oto#273).
    with geste.comme(geste.SYSTEM, acteur=geste.service("file-de-travail")), \
            ecriture_de_lignes() as conn:
        # Verrouillées avant d'être réécrites : entre le relevé et l'UPDATE, un
        # claim concurrent poserait un bail sur une ligne qu'on s'apprête à sortir
        # de la file — et le travail commencé serait perdu sans un mot.
        # `claimed_by` encore posé = un bail EXPIRÉ que personne n'a relâché : son run
        # est toujours sur la ligne, et c'est un fait de la tentative (#491).
        lignes = conn.execute(
            "SELECT row_id, claims, claimed_run, claimed_by IS NOT NULL AS bail_expire "
            f"FROM datastore_rows {where} FOR UPDATE",
            tuple(params)).fetchall()
        gabarit = (_MOTIF_DEFAUT if politique.par_defaut else _MOTIF) + _DERNIERE_TENTATIVE
        for ligne in lignes:
            run_id = (relachees.get(ligne["row_id"]) if relachees is not None
                      else ligne["claimed_run"])
            cause = _cause_de_l_abandon(
                conn, ns_id=ns_id, row_id=ligne["row_id"], run_id=run_id,
                erreur=erreur, bail_expire=bool(ligne["bail_expire"]))
            motif = gabarit.format(claims=ligne["claims"], plafond=politique.valeur,
                                   cause=cause)
            if politique.par_defaut:
                # Mise de côté : la colonne de plateforme seule, `data` intacte.
                conn.execute(
                    "UPDATE datastore_rows SET abandon_reason = %s, abandon_run = %s, "
                    "  claimed_by = NULL, claimed_until = NULL, claimed_run = NULL, "
                    "  updated_at = NOW() "
                    "WHERE ns_id = %s AND row_id = %s",
                    (motif, run_id, ns_id, ligne["row_id"]))
            else:
                conn.execute(
                    "UPDATE datastore_rows SET "
                    "  data = jsonb_set(data, ARRAY[%s], to_jsonb(%s::text), true), "
                    "  abandon_reason = %s, abandon_run = %s, claimed_by = NULL, "
                    "  claimed_until = NULL, claimed_run = NULL, updated_at = NOW() "
                    "WHERE ns_id = %s AND row_id = %s",
                    (politique.champ_statut, politique.etat, motif, run_id, ns_id,
                     ligne["row_id"]))
            # Bruyant par construction : une ligne qui sort de la file sans que
            # personne ne l'ait demandé est exactement ce qu'on veut voir passer.
            logger.warning(
                "datastore: ligne abandonnée (plafond de reprises) — tableau=%s "
                "ligne=%s réservations=%s plafond=%s état=%s run=%s cause=%s",
                politique.namespace, ligne["row_id"], ligne["claims"],
                politique.valeur, politique.etat or "(inchangé, plafond par défaut)",
                run_id, cause)
            abandonnees.append({"row_id": ligne["row_id"], "claims": ligne["claims"],
                                "reason": motif, "run_id": run_id})
    return abandonnees
