"""« Continuer » sur un run DÉTACHÉ relit le modèle du travail qui le tenait.

Régression du 13/09/2026 (e903369a) : la réservation détache d'un `start` son run clos
(`runner_jobs.run_id` → NULL, trace `payload._plateforme.runs_detaches`). `modele_du_run`
ne lisait que `runner_jobs.run_id` : un `continue` enfilé sur l'ancien run partait sans
famille de modèle, donc sur le modèle de n'importe quel worker — alors qu'un fil ouvert
sur une voie ne se poursuit pas sur une autre.

Contre un PostgreSQL jetable, par le chemin réel : enfilé, pris, lié, clos (le fait
`run_finish` au journal), conclu en échec, repris. C'est la réservation qui détache,
jamais une trace écrite à la main.
"""
from __future__ import annotations

import uuid

import pytest

SUB = "worker-modele-detache"
OPUS = {"procedure": "p", "model": "claude-opus-5", "model_family": "anthropic"}
MISTRAL = {"procedure": "p", "model": "mistral-large-latest", "model_family": "mistral"}
MISTRAL_MEDIUM = {"procedure": "p", "model": "mistral-medium-2604",
                  "model_family": "mistral", "effort": "high"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_modele_detache_" + uuid.uuid4().hex[:8]
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


# Une org PAR CAS : la réservation prend le plus ancien travail de l'org.

def _sql(requete: str, *args) -> None:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(requete, args)


def _run_ouvert(org: int) -> str:
    """Un run tel que le journal le connaît : l'index `runs` (cible de la FK du
    travail) et le fait `run_start`, stampé de son `run_id`."""
    from oto_mcp import db
    run_id = uuid.uuid4().hex
    db.insert_run(run_id, sub=SUB, org_id=org, label="travail hébergé")
    db.insert_tool_call({"tool": "run_start", "sub": SUB, "org_id": org,
                         "run_id": run_id, "args": {"label": "travail hébergé"},
                         "ok": True})
    return run_id


def _clore(run_id: str, org: int) -> None:
    """Le fait `run_finish` — c'est lui, et lui seul, qui dit « clos »."""
    from oto_mcp import db
    db.insert_tool_call({"tool": "run_finish", "sub": SUB, "org_id": org,
                         "run_id": run_id,
                         "args": {"run_id": run_id, "outcome": "failed"}, "ok": True})


def _prendre(org: int, charge: dict) -> dict:
    from oto_mcp import db
    job = db.claim_next_job(org, SUB, lease_seconds=60,
                            depot=charge.get("model_family"))
    assert job, "la file portait un travail : le claim le rend"
    return job


def _vol_puis_detachement(org: int, charge: dict) -> tuple[int, str, dict]:
    """Enfilé, pris, run lié puis clos, conclu en échec, repris : la réservation
    détache. Rend (travail, run détaché, modèle lu AVANT le détachement)."""
    from oto_mcp import db
    j = db.enqueue_job(org, "start", payload=dict(charge))
    assert _prendre(org, charge)["id"] == j["id"]
    run = _run_ouvert(org)
    assert db.bind_job_run(j["id"], SUB, run)
    avant = db.modele_du_run(run, org)
    _clore(run, org)
    assert db.complete_job(j["id"], SUB, False, error="échec")["status"] == "pending"
    _sql("UPDATE runner_jobs SET due_at = NOW() WHERE id = %s", j["id"])
    reprise = _prendre(org, charge)
    assert reprise["id"] == j["id"] and reprise["run_id"] is None, (
        f"la réservation a détaché le run clos — {reprise!r}")
    return int(j["id"]), run, avant


def _detacher_aussi_dans(org: int, charge: dict, run: str) -> int:
    """Un second `start` enfilé SUR le même run, déjà clos : sa prise le détache."""
    from oto_mcp import db
    j = db.enqueue_job(org, "start", payload=dict(charge), run_id=run)
    pris = _prendre(org, charge)
    assert pris["id"] == j["id"] and pris["run_id"] is None, pris
    return int(j["id"])


# ── ① l'ancien run détaché garde sa voie ────────────────────────────────────────

def test_un_run_detache_rend_le_modele_d_avant_son_detachement(live):
    from oto_mcp import db
    org = 9201
    job_id, run, avant = _vol_puis_detachement(org, OPUS)
    assert avant == {"model": "claude-opus-5", "model_family": "anthropic"}, avant
    assert db.get_job(job_id, org)["run_id"] is None
    assert db.modele_du_run(run, org) == avant, (
        "« Continuer » sur l'ancien run reste sur sa voie — `{}` le lancerait sur le "
        "modèle du worker")


def test_l_effort_du_modele_survit_lui_aussi_au_detachement(live):
    """`effort` (14/09/2026) suit la MÊME règle que `model`/`model_family` — posé
    au `start`, jamais recalculé depuis le catalogue courant. Un `continue` sur
    un run mistral-medium-2604 doit repartir en effort HAUT, pas sur le défaut
    du fournisseur."""
    from oto_mcp import db
    org = 9208
    job_id, run, avant = _vol_puis_detachement(org, MISTRAL_MEDIUM)
    assert avant == {"model": "mistral-medium-2604", "model_family": "mistral",
                     "effort": "high"}, avant
    assert db.modele_du_run(run, org) == avant


# ── ② le run courant est inchangé, et l'association courante prime ─────────────

def test_le_run_courant_est_inchange_et_prime_sur_la_trace(live):
    from oto_mcp import db
    org = 9202
    job_id, ancien, _ = _vol_puis_detachement(org, OPUS)
    neuf = _run_ouvert(org)
    assert db.bind_job_run(job_id, SUB, neuf)
    assert db.modele_du_run(neuf, org) == {"model": "claude-opus-5",
                                           "model_family": "anthropic"}

    # L'ancien run reçoit une association COURANTE : un `start` enfilé dessus, pas pris.
    db.enqueue_job(org, "start", payload=dict(MISTRAL), run_id=ancien)
    assert db.modele_du_run(ancien, org) == {
        "model": "mistral-large-latest", "model_family": "mistral"}, (
        "l'association courante prime, comme avant : la trace n'est lue qu'à défaut")


# ── ③ sans lien : le comportement existant ──────────────────────────────────────

def test_sans_lien_le_comportement_existant(live):
    from oto_mcp import db
    org = 9203
    assert db.modele_du_run("run-inconnu", org) == {}
    _, run, avant = _vol_puis_detachement(org, {"procedure": "p"})
    assert avant == {} and db.modele_du_run(run, org) == {}, (
        "un run démarré sans modèle se poursuit sans modèle")


# ── ④ des modèles contradictoires : aucun choix silencieux ──────────────────────

def test_des_modeles_contradictoires_levent_au_lieu_de_choisir(live):
    from oto_mcp import db
    org = 9204
    a, run, _ = _vol_puis_detachement(org, OPUS)
    b = _detacher_aussi_dans(org, MISTRAL, run)
    with pytest.raises(RuntimeError, match="contradictoires") as e:
        db.modele_du_run(run, org)
    assert str(a) in str(e.value) and str(b) in str(e.value), e.value


def test_plusieurs_travaux_du_meme_modele_ne_se_contredisent_pas(live):
    from oto_mcp import db
    org = 9205
    _, run, avant = _vol_puis_detachement(org, OPUS)
    _detacher_aussi_dans(org, OPUS, run)
    assert db.modele_du_run(run, org) == avant, "un seul couple : rien à choisir"


# ── ⑤ une autre org : rien lu, rien contredit ──────────────────────────────────

def test_une_autre_org_ne_lit_rien_et_ne_contredit_rien(live):
    from oto_mcp import db
    org, autre = 9206, 9207
    _, run, avant = _vol_puis_detachement(org, OPUS)
    assert db.modele_du_run(run, autre) == {}, "le run d'une AUTRE org ne se lit pas"

    _detacher_aussi_dans(autre, MISTRAL, run)   # la même trace chez un voisin, autre modèle
    assert db.modele_du_run(run, org) == avant, (
        "la trace d'une autre org ne crée aucune contradiction")
    assert db.modele_du_run(run, autre) == {"model": "mistral-large-latest",
                                            "model_family": "mistral"}
