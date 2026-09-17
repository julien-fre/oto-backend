"""Les workers de PLATEFORME — déclarés en base, authentifiés par un secret de machine.

Un worker n'est pas un compte. Il n'a ni ligne dans `users`, ni org active, ni
appartenance : rien vers quoi un repli implicite pourrait se rabattre. Ce qu'il
sait faire vient entièrement du travail que le backend lui commande. Ici ne
vivent que trois gestes : le déclarer (et lui remettre son secret, une fois),
le reconnaître, le révoquer.

⚠️ Pourquoi pas une ligne de `user_api_tokens` avec `kind='worker'` : sa clé
étrangère `sub → users` impose un compte, et `verify_api_token` rend ce `sub`
sans regarder `kind` — le worker serait authentifié comme un utilisateur
ordinaire, c'est-à-dire exactement le modèle qu'on retire (un compte personnel
marqué worker, et une flotte qui sonde l'org active de ce compte).
"""
from __future__ import annotations

import secrets
from typing import Optional

from ._conn import _connect
from .tokens import _hash_token

#: Distinct de `oto_` par construction : `"otow_".startswith("oto_")` est faux,
#: et l'adaptateur REST teste ce préfixe-ci AVANT l'autre.
WORKER_SECRET_PREFIX = "otow_"


def create_platform_worker(label: str) -> dict:
    """Déclare un worker et rend son secret — LA seule fois où il est lisible.

    `last_seen_at` part à l'époque zéro : « jamais vu », et non « vu à la
    déclaration ». Sinon `runner_arme` compterait présent, pendant sa fenêtre,
    un worker qui n'a encore jamais sondé."""
    if not (label or "").strip():
        raise ValueError("un worker se déclare avec un `label` — c'est son seul nom lisible")
    secret = WORKER_SECRET_PREFIX + secrets.token_urlsafe(32)
    worker_sub = "worker:" + secrets.token_hex(4)
    with _connect() as conn:
        row = conn.execute(
            """
            INSERT INTO runner_platform_workers
                        (worker_sub, label, secret_hash, last_seen_at)
                 VALUES (%s, %s, %s, TIMESTAMPTZ 'epoch')
              RETURNING worker_sub, label, created_at
            """,
            (worker_sub, label.strip(), _hash_token(secret)),
        ).fetchone()
    return {**dict(row), "secret": secret}


#: Granularité de la marque de présence d'un worker de PLATEFORME — bien en
#: dessous d'`ARME_FENETRE_S` (15 min, `runner_arme`) : un lecteur de cette
#: fenêtre ne voit jamais la différence entre « vu il y a 3 s » et « vu il y a
#: 28 s ». Sœur exacte de la granularité posée côté `claim_next_job`
#: (`runner_jobs.py::_PRESENCE_GRANULARITE_S`) — même seuil, deux points
#: d'écriture distincts sur la même ligne.
_PRESENCE_GRANULARITE_S = 30


def _touch_worker_presence(worker_sub: str) -> None:
    """Marque `last_seen_at`, borné : n'écrit — donc ne verrouille — que si la
    dernière marque a plus de `_PRESENCE_GRANULARITE_S`. Sa propre connexion,
    courte, jamais dans la transaction de l'appelant."""
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE runner_platform_workers
               SET last_seen_at = NOW()
             WHERE worker_sub = %s
               AND last_seen_at < NOW() - interval '{_PRESENCE_GRANULARITE_S} seconds'
            """,
            (worker_sub,),
        )


def verify_worker_secret(secret: str) -> Optional[dict]:
    """Le secret → le worker, ou None. Un worker révoqué n'existe plus pour
    l'authentification, quelle que soit la ligne qu'il garde en base.

    ⚠️ LECTURE PURE (oto-backend, lot perf 17/09/2026, mesuré par oto cd) :
    authentifie CHAQUE appel d'un worker (`take`/`beat`/`complete`/le sondage
    `claim`…), donc s'exécutait jusqu'ici comme un `UPDATE … RETURNING`
    synchrone sur l'UNIQUE ligne que partagent toutes les unités d'une même
    machine (12 `oto-runner@N` sur un seul secret) — 31 attentes de verrou
    mesurées sur 150 instantanés de 30 s, indépendamment du lot déjà posé sur
    `claim_next_job`. La décision d'authentifier ne dépend plus du nombre de
    lignes écrites : un `SELECT`, puis une marque de présence BORNÉE et
    séparée (`_touch_worker_presence`) qui n'écrit — donc ne verrouille — que
    si la dernière marque date de plus de 30 s."""
    if not secret or not secret.startswith(WORKER_SECRET_PREFIX):
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT worker_sub, label
              FROM runner_platform_workers
             WHERE secret_hash = %s AND revoked_at IS NULL
            """,
            (_hash_token(secret),),
        ).fetchone()
    if not row:
        return None
    _touch_worker_presence(row["worker_sub"])
    return dict(row)


def list_platform_workers() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT worker_sub, label, created_at, last_seen_at, revoked_at,
                   secret_hash IS NOT NULL AS declared
              FROM runner_platform_workers
          ORDER BY created_at, worker_sub
            """
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_platform_worker(worker_sub: str) -> bool:
    """Vrai si la révocation a eu lieu MAINTENANT ; faux si le worker est inconnu
    ou déjà révoqué — les deux cas sont rendus, pas confondus avec un succès."""
    with _connect() as conn:
        row = conn.execute(
            """
            UPDATE runner_platform_workers
               SET revoked_at = NOW()
             WHERE worker_sub = %s AND revoked_at IS NULL
         RETURNING worker_sub
            """,
            (worker_sub,),
        ).fetchone()
    return row is not None
