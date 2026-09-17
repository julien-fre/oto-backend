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


def verify_worker_secret(secret: str) -> Optional[dict]:
    """Le secret → le worker, ou None. Un worker révoqué n'existe plus pour
    l'authentification, quelle que soit la ligne qu'il garde en base.

    ⚠️ LECTURE PURE (oto-backend, lot perf 17/09/2026, mesuré par oto cd) :
    authentifie CHAQUE appel d'un worker (`take`/`beat`/`complete`/le sondage
    `claim`…), donc s'exécutait jusqu'ici comme un `UPDATE … RETURNING`
    synchrone sur l'UNIQUE ligne que partagent toutes les unités d'une même
    machine (12 `oto-runner@N` sur un seul secret) — 31 attentes de verrou
    mesurées sur 150 instantanés de 30 s, indépendamment du lot déjà posé sur
    `claim_next_job`. La marque de présence n'est PLUS posée ici : un worker
    de plateforme finit toujours par sonder `claim_next_job`
    (`runner_jobs.py::_touch_platform_worker_presence`, seul point d'écriture
    de `runner_platform_workers` depuis ce lot — c'est aussi le seul chemin
    commun à un worker à jeton d'ORG, qui n'appelle jamais cette fonction-ci).
    Poser un second point d'écriture ici referait doublon avec lui."""
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
    return dict(row) if row else None


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
