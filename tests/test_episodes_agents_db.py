"""Les ÉPISODES de panne d'un agent, en base (`db/episodes_agents.py`).

Ce qu'une doublure ne peut pas prouver : le relevé dès le PREMIER travail mort depuis le
dernier succès ; un seul épisode vivant par agent (l'index unique partiel, rafraîchi au
lieu d'être doublé) ; la garde d'une réparation (plafond par action, rien sur un épisode
clos) ; la guérison au succès suivant ; et le silence de l'alerte borné au côté
plateforme. Joué sur une vraie base.
"""
from __future__ import annotations

import json

import pytest

ORG = 8410


def _travail(tid, status, il_y_a_h, erreur=None, org=ORG):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute(
            "INSERT INTO runner_jobs (org_id, kind, payload, status, last_error, sub, "
            "finished_at) VALUES (%s, 'start', %s::jsonb, %s, %s, 'jane', "
            "NOW() - make_interval(hours => %s)) RETURNING id",
            (org, json.dumps({"trigger_id": tid}), status, erreur, il_y_a_h)
        ).fetchone()["id"]


def _pannes():
    from oto_mcp.db import episodes_agents
    return {p["trigger_id"]: p for p in episodes_agents.pannes_en_cours()}


@pytest.fixture()
def agent(live):
    from oto_mcp.db import runner_triggers as rt
    t = rt.create_trigger(ORG, "jane", procedure="veille", tz="UTC", tools=["a"],
                          cron="0 6 * * *", next_due="2999-01-01 00:00:00+00",
                          label="Veille des leads")
    return t["id"]


def _ouvrir(agent, job_id, classe="refus_passager", cote="plateforme"):
    from oto_mcp.db import episodes_agents
    return episodes_agents.ouvrir_ou_rafraichir(
        org_id=ORG, trigger_id=agent, classe=classe, cote=cote, motif="HTTP 529",
        job_id=job_id, travaux=1)


def test_un_seul_travail_mort_suffit_et_le_dernier_est_rendu(agent):
    _travail(agent, "done", 30)
    _travail(agent, "failed", 20, "ancien")
    dernier = _travail(agent, "failed", 10, "HTTP 529 overloaded")
    p = _pannes()[agent]
    assert (p["travaux"], p["job_id"], p["motif"], p["sub"]) == (
        2, dernier, "HTTP 529 overloaded", "jane")
    assert p["payload"]["trigger_id"] == agent


def test_un_agent_eteint_ou_gueri_n_est_pas_en_panne(agent):
    from oto_mcp.db import runner_triggers as rt
    _travail(agent, "failed", 20)
    _travail(agent, "done", 10)
    assert agent not in _pannes()
    _travail(agent, "failed", 5)
    rt.update_trigger(agent, ORG, {"enabled": False})
    assert agent not in _pannes()


def test_un_seul_episode_vivant_par_agent_rafraichi(agent):
    a = _ouvrir(agent, 1)
    b = _ouvrir(agent, 2, classe="famille_non_servie")
    assert a["id"] == b["id"]
    assert (b["dernier_job_id"], b["classe"], b["statut"]) == (2, "famille_non_servie",
                                                               "ouvert")


def test_la_garde_d_une_reparation(agent):
    from oto_mcp.db import episodes_agents
    ep = _ouvrir(agent, 1)
    for job in (1, 2):
        r = episodes_agents.noter_reparation(ep["id"], action="relance", acteur="medecin",
                                             job_id=job, maximum=2)
        assert r is not None and r["statut"] == "en_soin"
    assert episodes_agents.noter_reparation(ep["id"], action="relance", acteur="medecin",
                                            job_id=3, maximum=2) is None, "plafond"
    gestes = episodes_agents.lire(ep["id"])["reparations"]
    assert [(g["action"], g["job_id"]) for g in gestes] == [("relance", 1), ("relance", 2)]
    assert all(g["at"] for g in gestes)
    episodes_agents.poser(ep["id"], statut="clos")
    assert episodes_agents.noter_reparation(ep["id"], action="note", acteur="x") is None


def test_le_succes_suivant_clot_et_un_nouvel_episode_peut_s_ouvrir(agent):
    from oto_mcp.db import episodes_agents
    from oto_mcp.db._conn import _connect
    ep = _ouvrir(agent, 1)
    with _connect() as conn:
        conn.execute("UPDATE agent_episodes SET ouvert_at = NOW() - interval '2 hours' "
                     "WHERE id = %s", (ep["id"],))
    assert [e["id"] for e in episodes_agents.clore_les_gueris()] == []
    _travail(agent, "done", 1)
    clos = episodes_agents.clore_les_gueris()
    assert [(e["id"], e["resolution"]) for e in clos] == [(ep["id"], "gueri")]
    assert _ouvrir(agent, 9)["id"] != ep["id"]


def test_un_agent_eteint_clot_son_episode(agent):
    from oto_mcp.db import episodes_agents
    from oto_mcp.db import runner_triggers as rt
    ep = _ouvrir(agent, 1)
    rt.update_trigger(agent, ORG, {"enabled": False})
    assert [(e["id"], e["resolution"]) for e in episodes_agents.clore_les_gueris()] == [
        (ep["id"], "agent_eteint")]


def test_l_alerte_ne_se_tait_que_pour_le_cote_plateforme(agent, live):
    from oto_mcp.db import episodes_agents
    from oto_mcp.db import runner_triggers as rt
    autre = rt.create_trigger(ORG, "jane", procedure="veille", tz="UTC", tools=["a"],
                              cron="0 6 * * *", next_due="2999-01-01 00:00:00+00")["id"]
    ep = _ouvrir(agent, 1)
    _ouvrir(autre, 2, classe="cle_refusee", cote="proprietaire")
    assert episodes_agents.agents_soignes_par_nous([agent, autre]) == {agent}
    episodes_agents.poser(ep["id"], statut="escalade")
    assert episodes_agents.agents_soignes_par_nous([agent]) == {agent}, \
        "escaladé chez nous : toujours pas au titulaire"
    episodes_agents.poser(ep["id"], statut="clos")
    assert episodes_agents.agents_soignes_par_nous([agent]) == set()
