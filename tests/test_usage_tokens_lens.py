"""`org.usage.tokens` — les jetons que les agents d'une org ont déclarés, lentille ADMIN.

1. **Toutes les voies de paiement** : un travail d'abonnement compte, même sans crédit.
2. **Un poste non déclaré n'est pas un zéro** : la requête ne somme que les nombres et
   compte les travaux dont l'entrée ou la sortie manque (`unmeasured`).
3. **Admin seulement, invisible des agents** (`mcp=None`).
"""
from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from oto_mcp.capabilities import org_monitoring as om
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.registry import CAPABILITIES

CTX = ResolvedCtx(sub="admin", org_id=7)


def test_la_lentille_est_admin_et_pas_un_outil_mcp():
    cap = next(c for c in CAPABILITIES if c.key == "org.usage.tokens")
    assert cap.mcp is None
    assert cap.rest.path == "/api/orgs/{id}/usage/tokens"


def test_la_fenetre_est_bornee():
    assert om.OrgTokensInput(org_id=1).days == 30
    assert om.OrgTokensInput(org_id=1, days=90).days == 90
    for mauvais in (0, 91):
        with pytest.raises(ValidationError):
            om.OrgTokensInput(org_id=1, days=mauvais)


def test_les_cases_sont_rendues_sous_leurs_noms_publics(monkeypatch):
    vu: dict = {}

    def faux(org_id, jours):
        vu.update(org_id=org_id, jours=jours)
        return [{"jour": "2026-11-03", "trigger_id": 5, "paye_par": "abonnement",
                 "modele": "claude-opus-5-5", "travaux": 3, "entree": 10, "sortie": 20,
                 "lecture_cache": 30, "ecriture_cache": 40, "non_mesures": 1}]

    monkeypatch.setattr(om.db, "jetons_des_travaux", faux)
    out = om._tokens(CTX, om.OrgTokensInput(org_id=7, days=7))
    assert vu == {"org_id": 7, "jours": 7}
    assert out == {"days": 7, "rows": [{
        "day": "2026-11-03", "trigger_id": 5, "paid_by": "abonnement",
        "model": "claude-opus-5-5", "jobs": 3, "input": 10, "output": 20,
        "cache_read": 30, "cache_write": 40, "unmeasured": 1}]}


@pytest.fixture
def base(pg_module_dsn):
    psycopg = pytest.importorskip("psycopg")
    url_prec = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        with psycopg.connect(pg_module_dsn, autocommit=True) as c:
            c.execute("DELETE FROM runner_jobs")

            def job(org, payload, result, fini="NOW() - interval '1 hour'"):
                c.execute(
                    "INSERT INTO runner_jobs (org_id, kind, status, due_at, attempts, "
                    "max_attempts, payload, result, finished_at) "
                    f"VALUES (%s, 'start', 'done', NOW() - interval '2 hours', 1, 3, "
                    f"%s::jsonb, %s::jsonb, {fini})", (org, payload, result))

            full = ('{"usage_input": 100, "usage_output": 50, "usage_cache_read": 1000,'
                    ' "usage_cache_write": 10, "paye_par": "abonnement",'
                    ' "model": "claude-opus-5-5"}')
            job(1, '{"trigger_id": "5"}', full)
            job(1, '{"trigger_id": "5"}', full)
            # Sortie non déclarée (run coupé en vol) : pas un zéro, un non mesuré.
            job(1, '{"trigger_id": "5"}',
                '{"usage_input": 7, "paye_par": "abonnement", "model": "claude-opus-5-5"}')
            # Lancé à la main, clé de la plateforme, modèle lu du payload.
            job(1, '{"model": "claude-sonnet-5-5"}',
                '{"usage_input": 1, "usage_output": 2, "paye_par": "cle_plateforme"}')
            # Clé de trigger pas numérique : rangé « à la main », la lecture tient.
            job(1, '{"trigger_id": "pas-un-nombre"}',
                '{"usage_input": 1, "usage_output": 1, "paye_par": "cle_org"}')
            # Une autre org, une conclusion trop ancienne : exclues.
            job(2, '{"trigger_id": "9"}', full)
            job(1, '{"trigger_id": "5"}', full, fini="NOW() - interval '40 days'")
            # En cours (pas de finished_at) : pas de jour, exclu.
            c.execute("INSERT INTO runner_jobs (org_id, kind, status, due_at, attempts, "
                      "max_attempts, payload) VALUES (1,'start','claimed',NOW(),1,3,"
                      "'{\"trigger_id\": \"5\"}'::jsonb)")
        yield
    finally:
        if url_prec is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_prec


def test_la_requete_somme_les_nombres_et_compte_les_non_mesures(base):
    from oto_mcp.db import runner_jobs as RJ
    rows = RJ.jetons_des_travaux(1, 30)
    par = {(r["trigger_id"], r["paye_par"], r["modele"]): r for r in rows}
    assert set(par) == {
        (5, "abonnement", "claude-opus-5-5"),
        (None, "cle_plateforme", "claude-sonnet-5-5"),
        (None, "cle_org", None)}
    a = par[(5, "abonnement", "claude-opus-5-5")]
    assert (a["travaux"], a["entree"], a["sortie"]) == (3, 207, 100)
    assert (a["lecture_cache"], a["ecriture_cache"]) == (2000, 20)
    assert a["non_mesures"] == 1
    assert par[(None, "cle_plateforme", "claude-sonnet-5-5")]["non_mesures"] == 0
    assert all(isinstance(r["jour"], str) and len(r["jour"]) == 10 for r in rows)
    # La fenêtre : à 60 jours, la conclusion de 40 jours entre.
    assert sum(r["travaux"] for r in RJ.jetons_des_travaux(1, 60)) == 6
