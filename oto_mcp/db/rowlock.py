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
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect
from .query import _ds_filter_clauses


def datastore_claim_next(ns_id: int, *, worker: str, lease_seconds: int = 900,
                         filters: Optional[list] = None,
                         run_id: Optional[str] = None) -> Optional[dict]:
    """Claim atomique de la prochaine row claimable du namespace (ordre de
    création — row_id uuid7 monotone). `filters` = mêmes filtres whitelistés que
    la lecture (`_ds_filter_clauses`), typiquement `[{field:'status',op:'eq',…}]`.
    Renvoie la row (avec bail posé) ou None si plus rien à traiter."""
    fclauses, fparams = _ds_filter_clauses(filters)
    where = "WHERE ns_id = %s AND (claimed_until IS NULL OR claimed_until < NOW())"
    params: list = [ns_id, *fparams]
    for c in fclauses:
        where += f" AND {c}"
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
            "claimed_until = NOW() + (%s || ' seconds')::interval, claimed_run = %s "
            "WHERE ns_id = %s AND row_id = %s "
            "RETURNING row_id, created_at, updated_at, data, claimed_by, claimed_until",
            (str(worker), int(lease_seconds), run_id, ns_id, picked["row_id"]),
        ).fetchone()
        return dict(row) if row else None


def datastore_claim_row(ns_id: int, row_id: str, *, worker: str,
                        lease_seconds: int = 900,
                        run_id: Optional[str] = None) -> Optional[dict]:
    """Claim d'une row **nommée** (≠ pick de la suivante) — la file pilotée par un
    humain, qui choisit la ligne qu'il traite et à qui le serveur la réserve.

    Même condition d'éligibilité que `datastore_claim_next` (bail NULL ou expiré),
    plus le RENOUVELLEMENT par le même worker : rafraîchir son écran ne doit pas
    coûter sa propre ligne. L'UPDATE conditionnel EST l'atomicité — deux appels
    concurrents sur la même row, un seul repart avec le bail.

    None = row absente OU sous bail actif d'un AUTRE worker ; les distinguer coûte
    une relecture, laissée à l'appelant (chemin d'échec seulement)."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE datastore_rows SET claimed_by = %s, "
            "claimed_until = NOW() + (%s || ' seconds')::interval, claimed_run = %s "
            "WHERE ns_id = %s AND row_id = %s AND (claimed_until IS NULL "
            "OR claimed_until < NOW() OR claimed_by = %s) "
            "RETURNING row_id, created_at, updated_at, data, claimed_by, claimed_until",
            (str(worker), int(lease_seconds), run_id, ns_id, row_id, str(worker)),
        ).fetchone()
        return dict(row) if row else None


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

    ⚠️ Aucune garde de worker ici, et c'est voulu : la garde du release protège d'un
    agent qui libérerait la ligne d'un AUTRE. Ici c'est le run lui-même qui se ferme —
    il ne peut libérer que ce qu'il tenait, la clause `claimed_run = %s` s'en charge.

    Rend le nombre de lignes libérées (0 = le cas normal, un run qui n'a rien
    réservé)."""
    if not run_id:
        return 0
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE datastore_rows SET claimed_by = NULL, claimed_until = NULL, "
            "claimed_run = NULL WHERE claimed_run = %s", (str(run_id),))
        return cur.rowcount or 0


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
            "SELECT row_id, created_at, updated_at, data, claimed_by, claimed_until "
            "FROM datastore_rows WHERE ns_id = %s AND claimed_by IS NOT NULL "
            "ORDER BY claimed_until ASC",
            (ns_id,),
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
        return (cur.rowcount or 0) > 0
