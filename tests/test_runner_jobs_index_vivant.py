"""`claim_next_job` doit passer par l'index partiel des travaux VIVANTS, jamais
par un parcours complet de `runner_jobs` (mesuré en production le 17/09/2026,
`Seq Scan`, 72 425 lignes `done` écartées, 152 ms, pour zéro résultat).

Cause : l'ancien index (`idx_runner_jobs_claim`, `WHERE status = 'pending'`) ne
couvrait que la moitié de la condition de réservation (`status = 'pending' OR
(status = 'claimed' AND lease_until < NOW())`) — le planificateur ne pouvait pas
s'y limiter face à une disjonction sur DEUX statuts.

⚠️ Ce banc exige un vrai PostgreSQL et une VRAIE volumétrie (des dizaines de
milliers de lignes `done`) — un plan ne se lit pas sur une table vide, où
PostgreSQL choisirait un `Seq Scan` de toute façon (moins cher qu'un index sur
une poignée de pages). Aucun seuil de durée : uniquement le TYPE de nœud du plan.
"""
from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(scope="module")
def base_avec_historique(pg_dsn):
    """Une base migrée (Alembic head), peuplée comme la production : ~70k lignes
    `done`, une poignée `pending`/`claimed` — jamais l'inverse (le défaut du
    17/09 ne se voit QUE sous cette proportion)."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_runner_idx_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom

    url_prec, pool_prec = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(
                "INSERT INTO runner_jobs (org_id, kind, status, due_at, "
                "attempts, max_attempts) "
                "SELECT 1, 'start', 'done', NOW() - (g || ' seconds')::interval, "
                "1, 3 FROM generate_series(1, 70000) g")
            c.execute(
                "INSERT INTO runner_jobs (org_id, kind, status, due_at, "
                "attempts, max_attempts) "
                "SELECT 1, 'start', 'pending', "
                "NOW() - (g || ' seconds')::interval, 0, 3 "
                "FROM generate_series(1, 5) g")
            c.execute(
                "INSERT INTO runner_jobs (org_id, kind, status, due_at, "
                "attempts, max_attempts, lease_until) "
                "SELECT 1, 'start', 'claimed', "
                "NOW() - (g || ' seconds')::interval, 1, 3, "
                "NOW() - interval '1 minute' FROM generate_series(1, 3) g")
            c.execute("ANALYZE runner_jobs")
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_prec
        if url_prec is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_prec
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def _plan(dsn, sql, params):
    import psycopg
    with psycopg.connect(dsn) as c:
        lignes = c.execute(f"EXPLAIN {sql}", params).fetchall()
    return "\n".join(l[0] for l in lignes)


# La requête réelle de `claim_next_job` (oto_mcp/db/runner_jobs.py), forme
# `pris AS (SELECT …)` isolée — c'est CETTE forme que le planificateur doit
# pouvoir servir par l'index, indépendamment du CTE d'écriture qui l'englobe.
_REQUETE_CLAIM = """
    SELECT id FROM runner_jobs
     WHERE (%(org)s::bigint IS NULL OR org_id = %(org)s) AND due_at <= NOW()
       AND status IN ('pending', 'claimed')
       AND (status = 'pending' OR lease_until < NOW())
       AND attempts < max_attempts
     ORDER BY due_at
       FOR UPDATE SKIP LOCKED
     LIMIT 1
"""


def test_le_plan_passe_par_l_index_partiel_pas_un_parcours_complet(
        base_avec_historique):
    plan = _plan(base_avec_historique, _REQUETE_CLAIM, {"org": None})
    assert "idx_runner_jobs_live" in plan, (
        "la réservation n'utilise pas idx_runner_jobs_live :\n" + plan)
    assert "Seq Scan" not in plan, (
        "la réservation redevient un parcours complet de runner_jobs :\n" + plan)


def test_le_plan_reste_bon_avec_un_org_id_explicite(base_avec_historique):
    """Le filtre `(%s::bigint IS NULL OR org_id = %s)` est la forme RÉELLE de
    l'appel côté worker d'org (pas seulement le worker plateforme, org_id=NULL)."""
    plan = _plan(base_avec_historique, _REQUETE_CLAIM, {"org": 1})
    assert "idx_runner_jobs_live" in plan, plan
    assert "Seq Scan" not in plan, plan


def test_les_deux_balayages_du_meme_sondage_en_profitent_aussi(
        base_avec_historique):
    """Épaves (`status='claimed' AND lease_until < NOW()`) et périmés
    (`status='pending' AND …`) — leurs prédicats IMPLIQUENT le prédicat partiel
    de l'index sans qu'aucune réécriture n'ait été nécessaire sur ces deux
    requêtes-là (oto_mcp/db/runner_jobs.py, les deux UPDATE juste avant le CTE
    de réservation)."""
    epaves = _plan(
        base_avec_historique,
        "SELECT id FROM runner_jobs WHERE (%(org)s::bigint IS NULL "
        "OR org_id = %(org)s) AND status = 'claimed' AND lease_until < NOW() "
        "AND attempts >= max_attempts",
        {"org": None})
    assert "idx_runner_jobs_live" in epaves, epaves
    assert "Seq Scan" not in epaves, epaves

    perimes = _plan(
        base_avec_historique,
        "SELECT id FROM runner_jobs WHERE (%(org)s::bigint IS NULL "
        "OR org_id = %(org)s) AND status = 'pending' "
        "AND payload ? '_perime_apres_s'",
        {"org": None})
    assert "idx_runner_jobs_live" in perimes, perimes
    assert "Seq Scan" not in perimes, perimes


def test_l_ancien_index_a_disparu(base_avec_historique):
    """`idx_runner_jobs_claim` est redondant (son prédicat est un sous-ensemble
    de celui du nouveau) — le garder doublerait la maintenance de l'index à
    chaque écriture pour rien."""
    import psycopg
    with psycopg.connect(base_avec_historique) as c:
        noms = {r[0] for r in c.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'runner_jobs'"
        ).fetchall()}
    assert "idx_runner_jobs_live" in noms, noms
    assert "idx_runner_jobs_claim" not in noms, noms
