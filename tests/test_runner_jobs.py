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


def _ctx(sub="worker-audiens", org_id=226):
    return ResolvedCtx(sub=sub, org_id=org_id)


def _appel(ctx, **kw):
    return RJ._jobs(ctx, RJ.JobsInput(**kw))


@pytest.fixture
def espion(monkeypatch):
    vu = {}
    monkeypatch.setattr(RJ.db, "enqueue_job",
                        lambda org_id, kind, payload=None, run_id=None, max_attempts=3:
                        vu.update(org=org_id, kind=kind) or
                        {"id": 7, "status": "pending", "due_at": "2026-08-13"})
    monkeypatch.setattr(RJ.db, "claim_next_job",
                        lambda org_id, sub, lease_seconds=600:
                        vu.update(claim=(org_id, sub, lease_seconds)) or None)
    monkeypatch.setattr(RJ.db, "complete_job",
                        lambda job_id, sub, ok, error=None, run_id=None:
                        {"status": "done"} if sub == "worker-audiens" else None)
    monkeypatch.setattr(RJ.db, "bind_job_run", lambda j, s, r: s == "worker-audiens")
    monkeypatch.setattr(RJ.db, "extend_job_lease", lambda j, s, lease_seconds=600: False)
    monkeypatch.setattr(RJ.db, "get_job", lambda j, org: None)
    return vu


# ── le scope, sans lequel tout le reste est faux ──────────────────────────────

def test_le_claim_porte_lorg_et_le_sub_de_lappelant(espion):
    _appel(_ctx(), op="claim")
    org, sub, _ = espion["claim"]
    assert (org, sub) == (226, "worker-audiens"), \
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
                        lambda run_id: {"sub": "worker-audiens", "org_id": 226})
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
    assert out == {"ok": True, "status": "done"}


def test_prolonger_un_bail_perdu_rend_job_inconnu(espion):
    # Le bail a expiré, un autre worker a re-claimé : extend rend rowcount 0.
    with pytest.raises(AuthzDenied) as e:
        _appel(_ctx(), op="extend", job_id=7)
    assert e.value.status == 404, \
        "un worker dont le bail est mort ne garde aucune prise sur le job"
