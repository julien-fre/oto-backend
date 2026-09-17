"""Le bail d'une ligne — qui la tient, jusqu'à quand, et par quoi elle se libère.

Extrait de `db/datastore.py` sans un changement de comportement (#325). La file de
travail (ADR 0046 D) est une préoccupation entière : réserver, prolonger, libérer, et
savoir qui détient quoi.

Trois défauts constatés en PRODUCTION au premier essai réel valent d'être rappelés là
où ils se sont produits :

- le lien entre une ligne et le traitement en cours n'était pas enregistré, donc rien
  ne se libérait à la fin — et le TITULAIRE lui-même se voyait refuser l'écriture ;
- ce refus, non traduit en surface, ressortait en « erreur interne » ;
- la protection du chemin par LOT n'avait jamais rien protégé : un fail-open sur les
  horodatages « rendus en texte », alors que le row factory du dépôt les rend TOUS en
  texte — le cas cru marginal était le cas normal.

D'où la règle qui gouverne ce module : **une date de bail illisible REFUSE l'écriture
au lieu de l'ouvrir.** Un bail dont on ne sait pas s'il court protège peut-être encore
quelqu'un ; l'ignorance ne se résout pas en faveur de l'écrivain.

Le bail dit qui tient la ligne. Il ne dit pas combien de fois on l'a tenue pour rien :
c'est le plafond de reprises (`rowabandon`, #433), armé aux deux réservations et jugé
à chaque relâchement.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import psycopg

from ._conn import _connect
from .query import _ds_filter_clauses
from .rowabandon import abandonner_les_lignes_a_bout, plafond_de

logger = logging.getLogger(__name__)

# Les colonnes rendues avec une ligne réservée : son bail — À QUI, JUSQU'À QUAND et
# POUR QUEL RUN —, et ce que la file sait d'elle : combien de fois elle a été prise
# sans être écrite, et le motif si elle en est sortie (#433).
#
# ⚠️ `claimed_run` est dans TOUTE projection qui porte `claimed_by`, et pas seulement
# ici : `_row_to_dict` le lit par CLÉ (pas par `.get`) précisément pour qu'un chemin
# qui l'oublierait lève au lieu de servir un `null` — « ce bail n'a pas de run » est
# un fait, pas un défaut de SELECT. La garde mécanique est
# `tests/datastore/test_claimed_run_projection.py::test_toute_projection_de_ligne_porte_claimed_run`.
#
# `rev` (12/09/2026) : la révision de la ligne, servie `_revision` — une réservation,
# un renouvellement ou une libération la font avancer (déclencheur, `db/revision.py`).
_RENDU = ("RETURNING row_id, created_at, updated_at, data, rev, claimed_by, "
          "claimed_until, claimed_run, claims, abandon_reason, "
          "(claimed_until IS NOT NULL AND claimed_until > NOW())"
          "    AS claim_active")


# Ce que le tableau a le DROIT de servir, quel que soit le filtre — partagé par le pick
# et par le comptage de l'ordonnanceur, qui ne doivent jamais diverger.
_LIBRE_ET_EN_FILE = ("ns_id = %s AND abandon_reason IS NULL "
                     "AND (claimed_until IS NULL OR claimed_until < NOW())")


def _perimetre_reclamable(ns_id: int, filters: Optional[list]) -> tuple:
    """Le périmètre réclamable de `claim_next`.

    `abandon_reason IS NULL` est un filet de PLATEFORME, indépendant du filtre du
    client : une ligne sortie de la file ne se sert plus, même à un appelant qui ne
    filtre sur rien. Le filtre dit ce que l'appelant VEUT ; ceci dit ce que le tableau
    a le DROIT de servir.

    ⚠️ Un comptage « ce que la file servirait » a existé du 09/09 au 13/09/2026, jamais
    branché, et retiré sur décision : « la plateforme ne compte pas à la place de
    l'agent ». **Renversé le 14/09/2026** pour l'ordonnanceur des campagnes hébergées,
    qui tirait au hasard entre une file de 171 lignes et une file vide
    (`datastore_compter_reservables`, juste en dessous), puis pour le superviseur d'une
    campagne (`oto_fleet op=state`). Il ne part jamais avec un travail ni dans une
    consigne : l'agent qui réserve découvre toujours la file en réservant.
    """
    fclauses, fparams = _ds_filter_clauses(filters)
    where = "WHERE " + _LIBRE_ET_EN_FILE
    params: list = [ns_id, *fparams]
    for c in fclauses:
        where += f" AND {c}"
    return where, params


def datastore_compter_reservables(ns_id: int, perimetres: dict) -> dict:
    """`{clé: lignes que claim_next servirait}` pour plusieurs périmètres d'UN tableau,
    en UN scan. Ne réserve rien, n'écrit rien.

    `perimetres` = `{clé: filtres}`, les filtres étant ceux que `claim_next` passerait
    (`datastore/file_de_travail.perimetre_de_reservation`).

    ⚠️ **La passe d'abandon n'est pas appelée — un comptage qui écrit serait un piège —
    mais son effet est reproduit** : `claim_next` verse d'abord dans l'état d'abandon
    les lignes libres à `claims ≥ plafond`, qui sortent alors de la file. Les compter
    sur-estimerait exactement ce qu'on veut savoir. D'où `claims < plafond`, avec le
    plafond DÉCLARÉ au schéma : le paramètre `max_claims` d'un appel ne peut que
    l'assouplir (`rowabandon.plafond_de`), le compte est donc un plancher dans ce cas.
    Un schéma au plafond mal déclaré LÈVE ici comme il lève à la réservation.

    ⚠️ **Un scan par tableau, et `data` détoasté UNE fois par ligne.** Mesuré sur 8 910
    lignes larges, base au schéma servi : un compte par campagne coûte ~150 ms (et la
    file VIDE est la plus chère, le scan va au bout). Grouper les campagnes d'un tableau
    en `count(*) FILTER` ne suffit pas : chaque `data ->> champ` de chaque filtre
    redétoaste le JSONB — 6 campagnes, 406 ms. La sous-requête le matérialise une fois
    (`data || '{}'`, `OFFSET 0` pour qu'elle ne soit pas aplatie) : 67 ms, mêmes
    comptes. Elle expose aussi les colonnes réelles que les filtres méta visent
    (`row_id`, `created_at`, `updated_at` — `query._DS_META_*_COLS`).

    Le chiffre est une PHOTO : entre le comptage et la réservation, un autre worker
    peut prendre la dernière ligne."""
    if not perimetres:
        return {}
    cles = list(perimetres)
    colonnes, params = [], []
    for i, cle in enumerate(cles):
        fclauses, fparams = _ds_filter_clauses(perimetres[cle])
        colonnes.append(f"count(*) FILTER (WHERE {' AND '.join(fclauses) or 'TRUE'}) AS n{i}")
        params.extend(fparams)
    where = _LIBRE_ET_EN_FILE
    params.append(ns_id)
    politique = plafond_de(ns_id)
    if politique is not None:
        where += " AND claims < %s"
        params.append(politique.valeur)
    with _connect() as conn:
        # `rev` n'est lu par aucun filtre : il suit l'invariant « une projection de
        # ligne porte sa révision » (`test_revision_de_ligne.py`), plus simple à tenir
        # sans exception qu'avec une.
        row = conn.execute(
            f"SELECT {', '.join(colonnes)} FROM ("
            "  SELECT row_id, created_at, updated_at, data || '{}'::jsonb AS data, rev"
            f"    FROM datastore_rows WHERE {where} OFFSET 0) lignes",
            tuple(params)).fetchone()
    return {cle: int(row[f"n{i}"]) for i, cle in enumerate(cles)}


# Nombre d'essais sur `DeadlockDetected` — même idiome que `init_db()`
# (`_init.py::init_db`), symétrique côté trafic vivant plutôt que côté migration :
# un `ALTER`/`ADD CONSTRAINT` de boot tient un `AccessExclusiveLock` sur des dizaines
# de tables dans une seule transaction (commentaire d'`init_db`, incident Sentry du
# 2026-07-30) ; si PG choisit pour VICTIME un `claim_next` en vol plutôt que la
# migration, l'agent reçoit `deadlock detected` en pleine face pour une contention
# purement liée au déploiement, pas à son propre travail. `init_db` rejoue déjà sa
# propre transaction ; ceci fait le symétrique côté appelant, sur la SEULE ressource
# perdue par un rejeu (le pick lui-même — jamais posé faute de gagner la course), pas
# sur tout l'outil MCP.
_CLAIM_DEADLOCK_ATTEMPTS = "OTO_MCP_CLAIM_DEADLOCK_ATTEMPTS"


def datastore_claim_next(ns_id: int, *, worker: str, lease_seconds: int = 900,
                         filters: Optional[list] = None,
                         run_id: Optional[str] = None,
                         max_claims: Optional[int] = None) -> Optional[dict]:
    """Claim atomique de la prochaine row claimable du namespace (ordre de
    création — row_id uuid7 monotone). `filters` = mêmes filtres whitelistés que
    la lecture (`_ds_filter_clauses`), typiquement `[{field:'status',op:'eq',…}]`.
    Renvoie la row (avec bail posé) ou None si plus rien à traiter.

    `max_claims` surcharge le plafond de reprises déclaré au schéma (#433) pour
    cette passe. La réservation INCRÉMENTE le compteur de la ligne : c'est
    l'écriture qui le remet à zéro, jamais le fait de la reprendre.

    ⚠️ La passe d'abandon tourne AVANT le pick, hors de sa transaction : elle
    ramasse les lignes à bout que personne n'a relâchées (agent mort, bail
    expiré) — le relâchement, lui, s'occupe du cas nominal.

    Rejoue sur `DeadlockDetected` (oto-backend, Sentry PYTHON-STARLETTE-8Z) : la
    victime d'un cycle contre une migration de boot n'a RIEN posé — un rejeu est
    aussi sûr qu'un premier essai, jamais un double claim."""
    attempts = max(1, int(os.environ.get(_CLAIM_DEADLOCK_ATTEMPTS, "3")))
    for attempt in range(1, attempts + 1):
        try:
            return _datastore_claim_next_once(
                ns_id, worker=worker, lease_seconds=lease_seconds,
                filters=filters, run_id=run_id, max_claims=max_claims)
        except psycopg.errors.DeadlockDetected:
            if attempt == attempts:
                raise
            delay = 0.2 * attempt
            logger.warning(
                "datastore_claim_next(ns_id=%s): deadlock (tentative %d/%d) — "
                "nouvel essai dans %.1fs", ns_id, attempt, attempts, delay)
            time.sleep(delay)
    return None  # pragma: no cover — inatteignable (la boucle raise ou return)


def _datastore_claim_next_once(ns_id: int, *, worker: str, lease_seconds: int,
                               filters: Optional[list], run_id: Optional[str],
                               max_claims: Optional[int]) -> Optional[dict]:
    abandonner_les_lignes_a_bout(ns_id, max_claims=max_claims)
    where, params = _perimetre_reclamable(ns_id, filters)
    with _connect() as conn:
        picked = conn.execute(
            f"SELECT row_id FROM datastore_rows {where} "
            "ORDER BY row_id ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
            tuple(params),
        ).fetchone()
        if not picked:
            return None
        row = conn.execute(
            "UPDATE datastore_rows SET claimed_by = %s, "
            "claimed_until = NOW() + (%s || ' seconds')::interval, claimed_run = %s, "
            "claims = claims + 1 "
            "WHERE ns_id = %s AND row_id = %s "
            + _RENDU,
            (str(worker), int(lease_seconds), run_id, ns_id, picked["row_id"]),
        ).fetchone()
        if row is not None and run_id:
            # Ce que la FILE a rendu à ce run (oto#243) — compté ICI, par la plateforme
            # qui sert la ligne : le worker ne sait pas ce qu'est une ligne (oto-runner
            # f082336). `+ 1` SANS COALESCE : `NULL + 1` reste NULL, donc un run ouvert
            # avant la colonne reste « non mesuré » au lieu d'un compte partiel qui se
            # lirait complet. `datastore_claim_row` ne compte pas : il RENOUVELLE aussi
            # un bail, et c'est la file pilotée à la main.
            # Ordre des verrous : ligne du tableau PUIS ligne du run. Aucune transaction
            # ne prend un run puis une ligne de tableau — `finish_run` n'écrit que le
            # run, la libération que les lignes, la purge des runs ni l'un ni l'autre
            # d'un tableau —, donc aucun cycle possible.
            conn.execute("UPDATE runs SET lignes_reservees = lignes_reservees + 1 "
                         "WHERE run_id = %s", (run_id,))
        return dict(row) if row else None


def datastore_claim_row(ns_id: int, row_id: str, *, worker: str,
                        lease_seconds: int = 900,
                        run_id: Optional[str] = None,
                        filters: Optional[list] = None) -> Optional[dict]:
    """Claim d'une row **nommée** (≠ pick de la suivante) — la file pilotée par un
    humain, qui choisit la ligne qu'il traite et à qui le serveur la réserve.

    Même condition d'éligibilité que `datastore_claim_next` (bail NULL ou expiré),
    plus le RENOUVELLEMENT par le même worker : rafraîchir son écran ne doit pas
    coûter sa propre ligne. L'UPDATE conditionnel EST l'atomicité — deux appels
    concurrents sur la même row, un seul repart avec le bail.

    `filters` = le périmètre déclaré au tableau (#517), dans la clause de l'UPDATE :
    une ligne hors périmètre n'est pas réservable, même nommée, même par son
    titulaire — sinon la réservation ciblée serait la porte de côté du périmètre.

    None = row absente, hors périmètre, OU sous bail actif d'un AUTRE worker ; les
    distinguer coûte une relecture, laissée à l'appelant (chemin d'échec seulement).

    ⚠️ Le compteur de reprises monte ici aussi (#433) — mais sur une PRISE, pas sur
    un renouvellement : reprendre une ligne dont le bail a lâché compte, la garder
    non. `claim_next`, lui, n'a pas la nuance à porter : sa clause d'éligibilité
    exclut déjà le bail actif, donc il ne renouvelle jamais rien."""
    fclauses, fparams = _ds_filter_clauses(filters)
    perimetre = "".join(f" AND {c}" for c in fclauses)
    with _connect() as conn:
        row = conn.execute(
            "UPDATE datastore_rows SET claimed_by = %s, "
            "claimed_until = NOW() + (%s || ' seconds')::interval, claimed_run = %s, "
            # RÉSERVER, c'est PRENDRE une ligne : un nouveau titulaire, ou une ligne
            # dont le bail a lâché. Le titulaire qui renouvelle ne la prend pas — elle
            # ne lui a jamais échappé — donc son geste ne consomme pas le plafond
            # (#433) : sur une file pilotée à la main, rafraîchir son écran est le
            # geste le plus banal, et le compter la viderait de ses lignes.
            # ⚠️ Les colonnes lues dans le SET sont celles d'AVANT l'UPDATE (PG) :
            # `claimed_until` désigne bien le bail que cet appel remplace.
            "claims = claims + CASE WHEN claimed_until IS NULL "
            "                       OR claimed_until < NOW() THEN 1 ELSE 0 END "
            "WHERE ns_id = %s AND row_id = %s AND (claimed_until IS NULL "
            "OR claimed_until < NOW() OR claimed_by = %s)"
            + perimetre + " " + _RENDU,
            (str(worker), int(lease_seconds), run_id, ns_id, row_id, str(worker),
             *fparams),
        ).fetchone()
        return dict(row) if row else None


def datastore_row_within(ns_id: int, row_id: str, filters: list) -> bool:
    """Cette ligne est-elle DANS le périmètre (#517) ? Le chemin d'échec de
    `datastore_claim_row` : un None y couvre trois situations, et « hors périmètre »
    se dit autrement que « prise par un autre » — l'une s'attend, l'autre s'instruit.
    Jugé par le MÊME moteur que le pick, jamais par une évaluation Python du filtre
    qui divergerait de lui."""
    fclauses, fparams = _ds_filter_clauses(filters)
    where = "WHERE ns_id = %s AND row_id = %s" + "".join(f" AND {c}" for c in fclauses)
    with _connect() as conn:
        hit = conn.execute(
            f"SELECT 1 AS ok FROM datastore_rows {where}",
            (ns_id, row_id, *fparams)).fetchone()
        return hit is not None


def datastore_release_by_run(run_id: str) -> int:
    """Libère toutes les lignes réservées sous ce run — la TROISIÈME voie du verrou.

    Appelée à la fermeture d'un run, quel que soit son issue (`done`, `failed`,
    `blocked`) : un run qui se termine ne travaille plus, donc ne tient plus rien.

    ⚠️ **Ce qu'elle NE couvre PAS, contrairement à ce que ce commentaire affirmait :
    l'agent qui MEURT.** Un agent mort n'appelle pas `run_finish` — c'est la
    définition. `stale` est par ailleurs DÉRIVÉ (`run_status.is_stale`), jamais posé :
    rien ne ferme un run abandonné, donc rien ne libère ses lignes. Le seul filet pour
    ce cas reste l'expiration du bail, celui qui a mis **18 jours** à jouer sur la
    seule ligne réservée qu'ait portée la production. Le ramassage des runs abandonnés
    est une décision à part (#324).

    Ce qu'elle couvre réellement : l'agent qui TERMINE son run en oubliant de relâcher
    ses lignes. Plus petit que promis, et probablement le cas fréquent.

    Depuis #633 (29/08/2026), un second appelant : la conclusion d'un job du runner
    (`runner.jobs` op=complete) — le WORKER survit à l'agent mort et libère le run
    que le job connaît. L'agent conversationnel (hors runner) qui meurt reste au bail.

    ⚠️ Aucune garde de worker ici, et c'est voulu : la garde du release protège d'un
    agent qui libérerait la ligne d'un AUTRE. Ici c'est le run lui-même qui se ferme —
    il ne peut libérer que ce qu'il tenait, la clause `claimed_run = %s` s'en charge.

    Rend le nombre de lignes libérées (0 = le cas normal, un run qui n'a rien
    réservé)."""
    if not run_id:
        return 0
    with _connect() as conn:
        liberees = conn.execute(
            "UPDATE datastore_rows SET claimed_by = NULL, claimed_until = NULL, "
            "claimed_run = NULL WHERE claimed_run = %s "
            "RETURNING ns_id, row_id", (str(run_id),)).fetchall()
    # Un run qui se ferme sans avoir écrit est LE geste que le plafond mesure : le
    # traitement s'est conclu, la ligne revient intacte. L'évaluation suit la
    # libération, jamais l'inverse — une ligne encore sous bail n'est pas à bout.
    par_tableau: dict = {}
    for r in liberees:
        par_tableau.setdefault(int(r["ns_id"]), []).append(r["row_id"])
    for ns_id, ids in par_tableau.items():
        abandonner_les_lignes_a_bout(ns_id, row_ids=ids)
    return len(liberees)


def datastore_active_lease(ns_id: int, row_id: str) -> Optional[dict]:
    """Le bail ACTIF d'une ligne, ou None — expiré compte pour libre.

    ⚠️ « Actif » est la nuance qui empêche la protection en écriture de devenir un
    mur : un bail expiré ne protège rien (son titulaire est mort), sinon le zombie de
    18 jours mesuré en production aurait bloqué cette ligne pendant 18 jours."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT claimed_by, claimed_until, claimed_run FROM datastore_rows "
            "WHERE ns_id = %s AND row_id = %s AND claimed_by IS NOT NULL "
            "  AND claimed_until IS NOT NULL AND claimed_until > NOW()",
            (ns_id, row_id)).fetchone()
        return dict(row) if row else None


def datastore_claimed_rows(ns_id: int) -> list[dict]:
    """Rows sous bail de file de travail (ADR 0046 D) — la vue « en cours » du
    dashboard. Bail actif OU expiré confondus (le consommateur tranche sur
    `claimed_until`), plus ancien bail d'abord."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT row_id, created_at, updated_at, data, rev, claimed_by, claimed_until, "
            "       claimed_run, claims, abandon_reason, "
            "       (claimed_until IS NOT NULL AND claimed_until > NOW())"
            "           AS claim_active "
            "FROM datastore_rows WHERE ns_id = %s AND claimed_by IS NOT NULL "
            "ORDER BY claimed_until ASC",
            (ns_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def datastore_active_leases_of(*, run_id: Optional[str] = None,
                               worker: Optional[str] = None) -> list[dict]:
    """Les lignes qu'un run (et, si donné, un worker) tient EN CE MOMENT — la
    réservation lue comme une adresse (#517).

    ⚠️ Baux ACTIFS seulement, contrairement à `datastore_claimed_rows` qui rend aussi
    les baux échus pour la vue du dashboard : ici la réponse sert à DÉSIGNER une ligne
    où écrire. Un bail échu ne désigne plus rien — la ligne est peut-être déjà repartie
    à quelqu'un d'autre, et écrire dessus au nom d'un bail mort est exactement ce que
    le verrou natif interdit.

    Sans `run_id`, aucune ligne : l'appartenance se prouve par le jeton de run, jamais
    par le seul nom de worker — celui-ci est une étiquette que l'appelant choisit, donc
    qu'un autre peut porter. Il ne sert qu'à RESTREINDRE.

    Rendu volontairement étroit (`ns_id`, `row_id`) : l'appelant nomme les tableaux
    lui-même, et rapatrier ici la jointure obligerait ce module — qui ne connaît que
    le bail — à connaître le catalogue."""
    if not run_id:
        return []
    garde = "" if worker is None else " AND claimed_by = %s"
    params: tuple = (run_id,) if worker is None else (run_id, str(worker))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ns_id, row_id, claimed_by, claimed_until FROM datastore_rows "
            "WHERE claimed_run = %s AND claimed_until IS NOT NULL "
            f"AND claimed_until > NOW(){garde} ORDER BY claimed_until ASC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def datastore_release_claim(ns_id: int, row_id: str, worker: Optional[str]) -> bool:
    """Libère le bail d'une row. `worker` non-None = gardé (on ne libère pas le
    claim d'un autre) ; None = libération inconditionnelle (chemin interne : entrée
    en état terminal). Renvoie False si rien n'a été libéré (pas de bail, ou bail
    d'un autre worker)."""
    guard = "" if worker is None else " AND claimed_by = %s"
    params: tuple = (ns_id, row_id) if worker is None else (ns_id, row_id, str(worker))
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE datastore_rows SET claimed_by = NULL, claimed_until = NULL "
            f"WHERE ns_id = %s AND row_id = %s AND claimed_by IS NOT NULL{guard}",
            params,
        )
        libere = (cur.rowcount or 0) > 0
    if libere:
        # Rendre la ligne sans l'avoir écrite est le cas NOMINAL du faux départ :
        # c'est ici, à la ligne qu'on vient de relâcher, que le plafond se juge.
        abandonner_les_lignes_a_bout(ns_id, row_ids=[row_id])
    return libere
