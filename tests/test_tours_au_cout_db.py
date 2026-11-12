"""Un tour OpenRouter sur la clé partagée, EN BASE : ni réservé ni réglé au budget
d'unités, journalisé à son coût — une ligne `settlement` que la lentille de facturation
sert, écrite une seule fois quand le worker rejoue son rapport.

Une doublure ne prouve ni la relecture du run par la tentative en cours, ni la requête
qui garde le rejeu, ni que la lentille membre sert la ligne : c'est pourtant ce qui fait
la facture.
"""
from __future__ import annotations

import uuid

import pytest

PORTEUR = "worker:banc"
FLASH = "deepseek/deepseek-v4.1-flash-1017"
TOUR = {"model": FLASH, "usage_input": 152, "usage_output": 81, "usage_cache_write": None,
        "usage_cache_read": 6912, "usage_cost_usd": 6.44952e-05}


@pytest.fixture
def travail(live, monkeypatch):
    from oto_mcp.db._conn import _connect
    monkeypatch.setenv("OTO_CREDITS_MODE", "enforce")
    org = 43
    run_id = f"r-{uuid.uuid4().hex[:8]}"
    with _connect() as c:
        c.execute("INSERT INTO runs (run_id, sub, org_id, label) VALUES (%s, %s, %s, %s)",
                  (run_id, "alexis", org, "banc"))
        jid = c.execute(
            """INSERT INTO runner_jobs (org_id, kind, payload, status, claimed_by, attempts,
                                        sub, run_id)
               VALUES (%s, 'start', %s::jsonb, 'claimed', %s, 1, 'alexis', %s)
               RETURNING id""",
            (org, '{"model": "%s", "model_family": "openrouter"}' % FLASH, PORTEUR,
             run_id)).fetchone()["id"]
    yield jid, run_id, org


def _jobs(op, jid, **champs):
    from oto_mcp.capabilities import runner_jobs as RJ
    from oto_mcp.capabilities._types import ResolvedCtx
    return RJ._jobs(ResolvedCtx(sub=PORTEUR, org_id=None, role="platform_worker",
                                platform_worker=True),
                    RJ.JobsInput(op=op, job_id=jid, **champs))


def test_un_tour_au_cout_est_journalise_une_fois_et_servi_a_la_facturation(travail):
    from oto_mcp import db
    from oto_mcp.capabilities._types import AuthzDenied
    from oto_mcp.db import usage
    from oto_mcp.db._conn import _connect
    jid, run_id, org = travail
    assert db.estampiller_cle_partagee(jid, PORTEUR, "openrouter", "platform")

    assert _jobs("reserve", jid, turn=0, max_tokens=16000) == {"ok": True, "held": False}
    assert _jobs("meter", jid, turn=0, result=TOUR) == {
        "ok": True, "metered": True, "microdollars": 65, "duplicate": False}
    assert _jobs("meter", jid, turn=0, result=TOUR)["duplicate"] is True
    with pytest.raises(AuthzDenied) as e:
        _jobs("meter", jid, turn=1, result={**TOUR, "usage_cost_usd": None})
    assert e.value.status == 422

    with _connect() as c:
        assert c.execute("SELECT count(*) AS n FROM credit_movements WHERE call_uid LIKE %s",
                         (f"runner:{jid}:%",)).fetchone()["n"] == 0, "rien au budget d'unités"
    page = usage.list_billable_calls_for_org(org, run_ids=[run_id])
    assert page["total"] == 1
    (ligne,) = page["calls"]
    assert (ligne["tool"], ligne["quantity"], ligne["key_mode"], ligne["run_id"],
            ligne["call_uid"]) == ("openrouter", 65, "platform", run_id, f"runner:{jid}:1:0")
