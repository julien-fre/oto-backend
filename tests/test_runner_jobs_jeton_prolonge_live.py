"""Prolonger le bail prolonge le jeton délégué du travail (#1333), sur une vraie base.

Le jeton est émis par le VRAI chemin du claim (`capabilities.runner_jobs._delegue`) et
jugé par la VRAIE vérification (`db.verify_api_token`). Le temps ne s'attend pas : on le
fait passer en reculant `expires_at` d'une durée qui dépasse le TTL initial — un jeton
prolongé y survit, un jeton laissé à son TTL d'émission non.
"""
from __future__ import annotations

import uuid

import pytest

PORTEUR = "sub-bail-porteur"
WORKER = "sub-bail-worker"
PAIR = "sub-bail-pair"
BAIL_S = 60
# Plus que le TTL d'émission (`BAIL_S + marge`) : le jeton non prolongé est mort.
ECOULE_S = BAIL_S + 300


@pytest.fixture(scope="module")
def org(live):
    from oto_mcp import db, org_store
    db.upsert_user(PORTEUR, email=f"{PORTEUR}@acme.test", name=PORTEUR)
    o = org_store.create_org(f"Acme bail {uuid.uuid4().hex[:4]}", created_by=PORTEUR)
    org_store.add_org_member(o, PORTEUR, "org_admin")
    return o


@pytest.fixture
def travail(org):
    """Un travail `claimed` par WORKER, et son jeton émis par le chemin du claim."""
    from oto_mcp.capabilities import runner_jobs
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        job_id = conn.execute(
            "INSERT INTO runner_jobs (org_id, kind, status, attempts, claimed_by, "
            "lease_until, sub) VALUES (%s, 'start', 'claimed', 1, %s, "
            "NOW() + make_interval(secs => %s), %s) RETURNING id",
            (org, WORKER, BAIL_S, PORTEUR)).fetchone()["id"]
    job = runner_jobs._delegue({"id": job_id, "sub": PORTEUR, "org_id": org},
                               bail_s=BAIL_S, claimant=WORKER)
    return job_id, job["delegated_token"]


def _vieillir(job_id: int, secondes: int) -> None:
    """Fait passer `secondes` sur le jeton du travail, sans attendre."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "UPDATE user_api_tokens SET expires_at = expires_at - make_interval(secs => %s) "
            "WHERE kind = 'delegation' AND job_id = %s", (secondes, job_id))


def _bail(job_id: int):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT lease_until FROM runner_jobs WHERE id = %s",
                            (job_id,)).fetchone()["lease_until"]


def test_le_jeton_non_prolonge_meurt_apres_son_ttl_initial(travail):
    """Le témoin : sans `extend`, le vieillissement tue le jeton — le banc mesure bien."""
    from oto_mcp import db
    job_id, jeton = travail
    assert db.verify_api_token(jeton) is not None
    _vieillir(job_id, ECOULE_S)
    assert db.verify_api_token(jeton) is None


def test_extend_prolonge_le_jeton_au_dela_de_son_ttl_initial(travail):
    from oto_mcp import db
    job_id, jeton = travail
    assert db.extend_job_lease(job_id, WORKER, lease_seconds=ECOULE_S + 600) is True
    _vieillir(job_id, ECOULE_S)
    row = db.verify_api_token(jeton)
    assert row is not None and row["job_id"] == job_id


def test_extend_ne_raccourcit_jamais_le_jeton(travail):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    job_id, _ = travail
    with _connect() as conn:
        conn.execute("UPDATE user_api_tokens SET expires_at = NOW() + interval '1 day' "
                     "WHERE kind = 'delegation' AND job_id = %s", (job_id,))
    assert db.extend_job_lease(job_id, WORKER, lease_seconds=30) is True
    with _connect() as conn:
        reste = conn.execute(
            "SELECT expires_at > NOW() + interval '23 hours' AS long FROM user_api_tokens "
            "WHERE kind = 'delegation' AND job_id = %s", (job_id,)).fetchone()["long"]
    assert reste is True


def test_un_pair_ne_prolonge_ni_le_bail_ni_le_jeton(travail):
    from oto_mcp import db
    job_id, jeton = travail
    bail_avant = _bail(job_id)
    assert db.extend_job_lease(job_id, PAIR, lease_seconds=ECOULE_S + 600) is False
    assert _bail(job_id) == bail_avant
    _vieillir(job_id, ECOULE_S)
    assert db.verify_api_token(jeton) is None


def test_un_jeton_revoque_n_est_pas_prolonge(travail):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    job_id, _ = travail
    with _connect() as conn:
        avant = conn.execute(
            "UPDATE user_api_tokens SET revoked_at = NOW() "
            "WHERE kind = 'delegation' AND job_id = %s RETURNING expires_at",
            (job_id,)).fetchone()["expires_at"]
    assert db.extend_job_lease(job_id, WORKER, lease_seconds=ECOULE_S + 600) is True
    with _connect() as conn:
        apres = conn.execute(
            "SELECT expires_at FROM user_api_tokens "
            "WHERE kind = 'delegation' AND job_id = %s", (job_id,)).fetchone()["expires_at"]
    assert apres == avant


def test_la_marge_est_une_seule_constante():
    """Émission et prolongation lisent la même marge : aucune copie à faire diverger."""
    import inspect

    from oto_mcp.capabilities import runner_jobs as cap
    from oto_mcp.db import runner_jobs as RJ
    assert "MARGE_JETON_DELEGATION_S" in inspect.getsource(cap._delegue)
    assert "MARGE_JETON_DELEGATION_S" in inspect.getsource(RJ.extend_job_lease)
    assert not hasattr(cap, "_MARGE_JETON_S")
