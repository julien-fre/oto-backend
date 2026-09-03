"""La file d'exécutions du runner — les gardes que le worker ne doit jamais contourner.

Ce que ces tests verrouillent : le scope org du claim (un worker ne voit que SA
file), les exigences par kind (un `continue` sans run est inexécutable, autant le
refuser à l'entrée), le refus-sans-oracle sur les verbes de prise (conclure le job
d'un autre = job inconnu), et la borne du bail. Le comportement SQL (backoff,
`failed` au plafond, SKIP LOCKED) est porté par `db/runner_jobs.py` et se vérifie
au déploiement — ici on stubbe, on teste la capacité.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_jobs as RJ
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


def _ctx(sub="worker-campagne", org_id=226):
    return ResolvedCtx(sub=sub, org_id=org_id)


def _appel(ctx, **kw):
    return RJ._jobs(ctx, RJ.JobsInput(**kw))


@pytest.fixture
def espion(monkeypatch):
    vu = {}
    # ⚠️ La doublure suit la SIGNATURE SERVIE : `fleet_id` est entré avec le
    # rattachement d'un travail à sa flotte (#791). Une doublure figée sur une
    # ancienne signature ne protège plus rien — elle éclate en `TypeError`, ce qui
    # est le bon comportement : c'est le contrat qui a bougé, pas le test.
    monkeypatch.setattr(RJ.db, "enqueue_job",
                        # ⚠️ `**_` : une doublure qui fige la signature de son
                        # original casse au premier champ ajouté — et l'échec
                        # accuse le test, pas le manque.
                        lambda org_id, kind, payload=None, run_id=None,
                        max_attempts=3, fleet_id=None, sub=None, **_:
                        vu.update(org=org_id, kind=kind, fleet=fleet_id,
                                  sub=sub) or
                        {"id": 7, "status": "pending", "due_at": "2026-08-13",
                         "fleet_id": fleet_id})
    monkeypatch.setattr(RJ.db, "claim_next_job",
                        lambda org_id, sub, lease_seconds=600:
                        vu.update(claim=(org_id, sub, lease_seconds)) or None)
    monkeypatch.setattr(RJ.db, "complete_job",
                        lambda job_id, sub, ok, error=None, run_id=None, result=None:
                        vu.update(result=result) or
                        ({"status": "done"} if sub == "worker-campagne" else None))
    monkeypatch.setattr(RJ.db, "bind_job_run", lambda j, s, r: s == "worker-campagne")
    monkeypatch.setattr(RJ.db, "extend_job_lease", lambda j, s, lease_seconds=600: False)
    monkeypatch.setattr(RJ.db, "get_job", lambda j, org: None)
    return vu


# ── le scope, sans lequel tout le reste est faux ──────────────────────────────

def test_le_claim_porte_lorg_et_le_sub_de_lappelant(espion):
    _appel(_ctx(), op="claim")
    org, sub, _ = espion["claim"]
    assert (org, sub) == (226, "worker-campagne"), \
        "le claim ne peut servir QUE la file de l'org du jeton, au nom du worker"


def test_sans_org_active_la_file_refuse(espion):
    with pytest.raises(AuthzDenied) as e:
        _appel(ResolvedCtx(sub="w", org_id=None), op="claim")
    assert e.value.code == "org_required"


def test_le_bail_est_borne(espion):
    _appel(_ctx(), op="claim", lease_seconds=999_999)
    assert espion["claim"][2] == 3600, "un bail d'une journée n'est pas un heartbeat"
    _appel(_ctx(), op="claim", lease_seconds=1)
    assert espion["claim"][2] == 30, "un bail d'une seconde est un claim jetable"


# ── les exigences par kind, refusées à l'entrée ───────────────────────────────

def test_un_continue_sans_run_est_refuse(espion):
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="enqueue", kind="continue")
    assert e.value.code == "missing_fields" and "run_id" in str(e.value.message)


def test_un_start_sans_payload_est_refuse(espion):
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="enqueue", kind="start")
    assert e.value.code == "missing_fields"


def test_enfiler_un_continue_sur_le_run_dautrui_rend_run_inconnu(espion, monkeypatch):
    """Le gate propriétaire tient CÔTÉ SERVEUR, pas dans le séquencement de l'UI :
    un enqueue direct (sans append préalable) sur le run d'un autre ferait
    continuer son fil par le worker, avec les droits du run. Même 404 sans
    oracle que l'append du fil (R1)."""
    monkeypatch.setattr(RJ.db, "get_run_head",
                        lambda run_id: {"sub": "proprietaire", "org_id": 226}
                        if run_id == "run-X" else None)
    monkeypatch.setattr(RJ.db, "enqueue_job",
                        lambda *a, **k: pytest.fail("rien ne s'enfile sans propriété"))
    with pytest.raises(AuthzDenied) as a:
        _appel(_ctx(sub="intrus"), op="enqueue", kind="continue", run_id="run-X")
    with pytest.raises(AuthzDenied) as b:
        _appel(_ctx(sub="intrus"), op="enqueue", kind="continue", run_id="run-INEXISTANT")
    assert (a.value.status, a.value.code) == (b.value.status, b.value.code) == \
        (404, "run_not_found")


def test_le_proprietaire_enfile_son_continue(espion, monkeypatch):
    monkeypatch.setattr(RJ.db, "get_run_head",
                        lambda run_id: {"sub": "worker-campagne", "org_id": 226})
    out = _appel(_ctx(), op="enqueue", kind="continue", run_id="run-X")
    assert out["id"] == 7


def test_enqueue_scope_lorg_de_lappel(espion):
    out = _appel(_ctx(org_id=42), op="enqueue", kind="start",
                 payload={"procedure": "veille-linkedin"})
    assert espion["org"] == 42 and out["id"] == 7


# ── conclure ce qui ne nous appartient pas = job inconnu (pas d'oracle) ───────

def test_conclure_le_job_dun_autre_rend_job_inconnu(espion):
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(sub="autre-worker"), op="complete", job_id=7, ok=True)
    assert (e.value.status, e.value.code) == (404, "job_not_found")


def test_le_claimant_conclut(espion):
    out = _appel(_ctx(), op="complete", job_id=7, ok=True)
    # Sans run connu, la libération des baux n'est pas tentée et la réponse le DIT
    # (#633) : null + raison, jamais un 0 fabriqué.
    assert out == {"ok": True, "status": "done",
                   "run_id": None, "rows_released": None, "release": "no_run"}


def test_prolonger_un_bail_perdu_rend_job_inconnu(espion):
    # Le bail a expiré, un autre worker a re-claimé : extend rend rowcount 0.
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="extend", job_id=7)
    assert e.value.status == 404, \
        "un worker dont le bail est mort ne garde aucune prise sur le job"


# ── le résultat déclaré (R5, garde budget de flotte) ─────────────────────────

def test_complete_transporte_le_resultat_declare(espion):
    _appel(_ctx(), op="complete", job_id=7, ok=True,
           result={"usage_tokens": 31500, "stopped": "end_turn", "steps": 18})
    assert espion["result"] == {"usage_tokens": 31500, "stopped": "end_turn",
                                "steps": 18}, \
        "le coût d'un job doit être LISIBLE par l'ordonnanceur de flotte"


def test_un_resultat_obese_est_refuse(espion):
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="complete", job_id=7, ok=True,
               result={"note": "x" * 5000})
    assert e.value.code == "result_too_large", \
        "result est un résumé, jamais un contenu de fil"


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os
    import uuid as _uuid

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_rjobs_" + _uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def test_la_liste_est_scopee_a_lorg_et_filtrable(live):
    """La surveillance (page Automatisations) : la file de MON org seulement,
    du plus récent au plus ancien, filtrable par statut."""
    from oto_mcp import db as d

    a = d.enqueue_job(310, "start", payload={"procedure": "p1"})
    d.enqueue_job(311, "start", payload={"procedure": "autrui"})
    job = d.claim_next_job(310, "w-list", lease_seconds=60)
    d.complete_job(job["id"], "w-list", False, error="boom")   # pending, attempt 1

    jobs = d.list_jobs(310)
    assert all("autrui" not in str(j.get("payload")) for j in jobs), \
        "la file d'une autre org ne doit JAMAIS apparaître"
    assert any(j["id"] == a["id"] for j in jobs)
    en_attente = d.list_jobs(310, status="pending")
    assert {j["status"] for j in en_attente} == {"pending"}


def test_le_resultat_fait_l_aller_retour_en_base(live):
    """Le round-trip RÉEL : complete écrit `result`, get le rend — c'est ce que
    l'ordonnanceur de flotte lira pour sa garde budget. Un stub ne prouve ni la
    colonne, ni le COALESCE, ni le SELECT."""
    from oto_mcp import db as d

    j = d.enqueue_job(226, "start", payload={"procedure": "p"})
    job = d.claim_next_job(226, "worker-live", lease_seconds=60)
    assert job and job["id"] == j["id"]
    out = d.complete_job(job["id"], "worker-live", True,
                         result={"usage_tokens": 12345, "stopped": "end_turn"})
    assert out == {"status": "done", "run_id": None}, \
        "complete rend le run connu du job (#633) — aucun ici"
    relu = d.get_job(job["id"], 226)
    assert relu["result"] == {"usage_tokens": 12345, "stopped": "end_turn"}
    assert relu["status"] == "done"
