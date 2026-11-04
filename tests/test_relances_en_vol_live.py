"""Une relance (`runner.triggers op=rerun`) se lit en vol par le couple `trigger_id` +
`rerun_of` de sa charge (`db.relances_en_vol`) : en base, seuls les statuts qui attendent
ou tournent comptent, et un `rerun_of` posé dans une charge enfilée à la main (sans
`trigger_id`, que l'enfilage à la main refuse) ne bloque la relance de personne."""
from __future__ import annotations

import pytest

ORG = 9442


@pytest.fixture(autouse=True)
def _file_vide(live):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM runner_jobs")


def _relance(statut="pending", trigger_id=5, rerun_of=30, org=ORG):
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    charge = {"procedure": "p", "rerun_of": rerun_of}
    if trigger_id is not None:
        charge["trigger_id"] = trigger_id
    j = db.enqueue_job(org, "start", payload=charge)["id"]
    with _connect() as conn:
        conn.execute("UPDATE runner_jobs SET status = %s WHERE id = %s", (statut, j))
    return j


@pytest.mark.parametrize("statut", ["pending", "held", "claimed"])
def test_une_relance_qui_attend_ou_tourne_compte(live, statut):
    from oto_mcp import db
    _relance(statut)
    assert db.relances_en_vol(30, 5, ORG) == 1


@pytest.mark.parametrize("statut", ["done", "failed", "expired"])
def test_une_relance_finie_ne_compte_pas(live, statut):
    from oto_mcp import db
    _relance(statut)
    assert db.relances_en_vol(30, 5, ORG) == 0


def test_seule_la_relance_de_ce_travail_de_cet_agent_dans_cette_org_compte(live):
    from oto_mcp import db
    _relance(rerun_of=31)
    _relance(trigger_id=6)
    _relance(org=ORG + 1)
    # Enfilée à la main : `rerun_of` sans `trigger_id` ne bloque rien.
    _relance(trigger_id=None)
    assert db.relances_en_vol(30, 5, ORG) == 0
