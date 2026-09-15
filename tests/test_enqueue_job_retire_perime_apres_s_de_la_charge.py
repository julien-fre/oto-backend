"""`enqueue_job` retirait `_plateforme` de la charge fournie par l'appelant, mais
PAS `_perime_apres_s` — trouvé par fleet le 15/09/2026, à la relecture de la
fusion #942 (`d2631270`).

**Pourquoi c'est plus qu'un `500` isolé.** La réservation (`claim_next_job`)
caste ce champ à CHAQUE appel (`(payload->>'_perime_apres_s')::int`), y compris
pour un worker de PLATEFORME (`org_id IS NULL`) qui n'est filtré par AUCUNE org.
Une charge posée par une SEULE org, avec une valeur non castable en `int`
(chaîne, ou nombre hors bornes `int` PostgreSQL), fait lever l'`UPDATE` de
péremption entier — donc `claim_next_job` entier — pour TOUT worker de
plateforme, quelle que soit l'org qu'il sert. Une organisation bloque la
réservation de toutes les autres, sans qu'aucun journal ne la désigne.

Ce banc prouve la PORTÉE du défaut (deux orgs, une réservation de plateforme),
pas seulement le cast : une assertion sur `enqueue_job` seul n'aurait pas montré
qu'un job innocent d'une AUTRE org devient injoignable.
"""
from __future__ import annotations

import os
import uuid

import pytest

ORG_FAUTIVE = 9431
ORG_INNOCENTE = 9432
WORKER_PLATEFORME = "worker-plateforme-942-scope"


@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_942_scope_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        if avant_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = avant_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture(autouse=True)
def _file_vide(live):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM runner_jobs")


def test_une_charge_fautive_dune_org_ne_bloque_pas_la_reservation_dune_autre(live):
    from oto_mcp import db

    # Org fautive : `_perime_apres_s` posé DIRECTEMENT dans la charge, pas via le
    # paramètre de confiance — exactement ce qu'un appelant `runner_jobs op=enqueue`
    # peut envoyer aujourd'hui (payload libre).
    fautif = db.enqueue_job(ORG_FAUTIVE, "start",
                            payload={"procedure": "p-fautif",
                                     "_perime_apres_s": "bientôt"})

    # Org innocente : un travail ordinaire, sans rapport.
    innocent = db.enqueue_job(ORG_INNOCENTE, "start", payload={"procedure": "p-ok"})

    # Un worker de PLATEFORME (org_id=None) sert tout le monde — c'est lui que la
    # ligne fautive, non scopée par org dans l'UPDATE de péremption, peut geler.
    pris = db.claim_next_job(None, WORKER_PLATEFORME, lease_seconds=60)
    assert pris is not None, (
        "la réservation de plateforme n'a rien rendu — la charge fautive de "
        f"l'org {ORG_FAUTIVE} a bloqué TOUTE la file, y compris l'org "
        f"{ORG_INNOCENTE}")

    # La charge fautive ne doit jamais avoir atteint la base : `_perime_apres_s`
    # n'a qu'un seul écrivain légitime, le paramètre `perime_apres_s`, jamais la
    # charge de l'appelant.
    releve = db.get_job(fautif["id"], ORG_FAUTIVE)
    assert "_perime_apres_s" not in (releve["payload"] or {}), (
        f"la charge fautive a été écrite telle quelle : {releve['payload']!r}")

    # Le job innocent reste réservable normalement — servi par CE claim ou par le
    # suivant selon l'ordre `due_at`, mais jamais perdu.
    ids_vus = {pris["id"]}
    suite = db.claim_next_job(None, WORKER_PLATEFORME, lease_seconds=60)
    if suite:
        ids_vus.add(suite["id"])
    assert innocent["id"] in ids_vus, (
        f"le job de l'org innocente {ORG_INNOCENTE} n'a jamais été servi : "
        f"vus={ids_vus!r}")


def test_perime_apres_s_reste_pose_par_le_serveur_quand_legitime(live):
    """Non-régression : le paramètre de confiance continue de marcher — retirer la
    charge fautive ne doit pas retirer la fonctionnalité elle-même."""
    from oto_mcp import db

    j = db.enqueue_job(ORG_FAUTIVE, "start", payload={"procedure": "p-legit"},
                       perime_apres_s=3600)
    releve = db.get_job(j["id"], ORG_FAUTIVE)
    assert releve["payload"].get("_perime_apres_s") == 3600, releve["payload"]
