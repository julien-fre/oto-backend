"""La lignée d'un travail enchaîné (`_chaine` : `parent_job`, `root_job`, `depth`) n'a
qu'un écrivain, le paramètre `chaine` d'`enqueue_job` — jamais la charge reçue. Une
charge enfilée à la main (`runner.jobs op=enqueue`, payload libre) qui porterait une
profondeur remise à zéro rouvrirait la boucle que `CHAINE_MAX` borne
(`runner.triggers op=run_now`, 21/10/2026).

Ses deux lectures (`enfants_du_travail`, `runs_de_la_chaine`) passent par l'index des
travaux d'un déclencheur, bornées par la naissance de l'ancêtre."""
from __future__ import annotations

import pytest

ORG = 9441


@pytest.fixture(autouse=True)
def _file_vide(live):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM runner_jobs")


def _agent(trigger_id, chaine=None, org=ORG):
    from oto_mcp import db
    return db.enqueue_job(org, "start", payload={"procedure": "p", "trigger_id": trigger_id},
                          chaine=chaine)["id"]


def test_la_lignee_de_la_charge_recue_est_retiree(live):
    from oto_mcp import db
    j = db.enqueue_job(ORG, "start", payload={
        "procedure": "p", "_chaine": {"parent_job": 1, "root_job": 1, "depth": 0}})
    assert "_chaine" not in (db.get_job(j["id"], ORG)["payload"] or {})


def test_la_lignee_du_serveur_est_posee(live):
    from oto_mcp import db
    lignee = {"parent_job": 7, "root_job": 3, "depth": 2}
    j = db.enqueue_job(ORG, "start", payload={"procedure": "p"}, chaine=lignee)
    assert db.get_job(j["id"], ORG)["payload"]["_chaine"] == lignee


def test_les_enfants_d_un_travail_se_lisent_par_la_lignee(live):
    from oto_mcp import db
    racine = _agent(4)
    autre = _agent(9)
    _agent(5, {"parent_job": racine, "root_job": racine, "depth": 1})
    _agent(6, {"parent_job": autre, "root_job": autre, "depth": 1})
    # Une lignée glissée dans la charge reçue ne compte pas : elle est retirée.
    db.enqueue_job(ORG, "start", payload={"procedure": "p", "trigger_id": 7,
                                          "_chaine": {"parent_job": racine}})
    assert db.enfants_du_travail(racine, ORG) == ["5"]
    assert db.enfants_du_travail(autre, ORG) == ["6"]


def test_un_ancetre_introuvable_leve_plutot_que_compter_zero(live):
    """Compter zéro enfant d'un ancêtre absent — ou d'une autre org — rouvrirait les
    bornes : la lecture lève."""
    from oto_mcp import db
    racine = _agent(4)
    with pytest.raises(LookupError):
        db.enfants_du_travail(racine, ORG + 1)
    with pytest.raises(LookupError):
        db.runs_de_la_chaine(racine + 1000, ORG)


def test_une_chaine_compte_tous_ses_runs_depart_compris(live):
    """Le budget compte la lignée ENTIÈRE par `root_job` — enfants, petits-enfants, quel
    que soit leur statut —, plus le départ ; pas les runs d'une autre chaîne."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    racine = _agent(4)
    assert db.runs_de_la_chaine(racine, ORG) == 1
    b = _agent(5, {"parent_job": racine, "root_job": racine, "depth": 1})
    _agent(6, {"parent_job": b, "root_job": racine, "depth": 2})
    c = _agent(7, {"parent_job": racine, "root_job": racine, "depth": 1})
    with _connect() as conn:  # un run terminé a été payé : il compte
        conn.execute("UPDATE runner_jobs SET status = 'done' WHERE id = %s", (c,))
    autre = _agent(8)
    _agent(9, {"parent_job": autre, "root_job": autre, "depth": 1})
    assert db.runs_de_la_chaine(racine, ORG) == 4
    assert db.runs_de_la_chaine(autre, ORG) == 2


def test_un_enfant_reporte_reste_compte(live):
    """La borne basse est la NAISSANCE de l'ancêtre, pas son `due_at` : un parent
    reporté (nouvelle tentative) voit encore les enfants de sa tentative d'avant."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    racine = _agent(4)
    _agent(5, {"parent_job": racine, "root_job": racine, "depth": 1})
    with _connect() as conn:
        conn.execute("UPDATE runner_jobs SET due_at = NOW() + interval '1 hour' "
                     "WHERE id = %s", (racine,))
    assert db.enfants_du_travail(racine, ORG) == ["5"]
    assert db.runs_de_la_chaine(racine, ORG) == 2


def test_les_lectures_de_la_lignee_passent_par_l_index(live, monkeypatch):
    """Contre une vraie base chargée (20 000 travaux de flotte, sans clé) : les deux
    lectures — capturées telles que le code les écrit, puis expliquées — prennent
    `idx_runner_jobs_declencheurs` au lieu de balayer la file."""
    from contextlib import contextmanager

    from oto_mcp.db import index_concurrent as ic
    from oto_mcp.db import runner_jobs as RJ
    from oto_mcp.db._conn import _connect

    racine = _agent(4)
    lues: list = []

    rendu: dict = {}

    class _Capture:
        def execute(self, sql, params):
            lues.append((sql, params))
            return type("C", (), {"fetchone": lambda _s: dict(rendu)})()

    @contextmanager
    def _capture():
        yield _Capture()

    monkeypatch.setattr(RJ, "_connect", _capture)
    rendu["v"] = []
    RJ.enfants_du_travail(racine, ORG)
    rendu["v"] = 0
    RJ.runs_de_la_chaine(racine, ORG)
    monkeypatch.undo()
    assert len(lues) == 2

    with _connect() as c:
        assert ic.scalaire_de(c)(RJ.INDEX_DECLENCHEURS.sql_validite) is True
        c.execute(
            "INSERT INTO runner_jobs (org_id, kind, status, due_at, attempts, max_attempts, payload) "
            "SELECT %s, 'start', 'done', NOW() - g * interval '1 minute', 1, 3, '{\"fleet\": 1}'::jsonb "
            "FROM generate_series(1, 20000) g", (ORG,))
        c.execute("ANALYZE runner_jobs")
        for sql, params in lues:
            plan = "\n".join(r["QUERY PLAN"] for r in c.execute("EXPLAIN " + sql, params).fetchall())
            assert RJ.INDEX_DECLENCHEURS.nom in plan and "Seq Scan" not in plan, plan
        c.rollback()
