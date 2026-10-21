"""La lignée d'un travail enchaîné (`_chaine` : `parent_job`, `root_job`, `depth`) n'a
qu'un écrivain, le paramètre `chaine` d'`enqueue_job` — jamais la charge reçue. Une
charge enfilée à la main (`runner.jobs op=enqueue`, payload libre) qui porterait une
profondeur remise à zéro rouvrirait la boucle que `CHAINE_MAX` borne
(`runner.triggers op=run_now`, 21/10/2026)."""
from __future__ import annotations

import pytest

ORG = 9441


@pytest.fixture(autouse=True)
def _file_vide(live):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM runner_jobs")


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
    db.enqueue_job(ORG, "start", payload={"procedure": "p", "trigger_id": 5},
                   chaine={"parent_job": 70, "root_job": 70, "depth": 1})
    db.enqueue_job(ORG, "start", payload={"procedure": "p", "trigger_id": 6},
                   chaine={"parent_job": 71, "root_job": 70, "depth": 1})
    # Une lignée glissée dans la charge reçue ne compte pas : elle est retirée.
    db.enqueue_job(ORG, "start", payload={"procedure": "p", "trigger_id": 7,
                                          "_chaine": {"parent_job": 70}})
    assert db.enfants_du_travail(70, ORG) == ["5"]
    assert db.enfants_du_travail(70, ORG + 1) == [], "org-scopé"
