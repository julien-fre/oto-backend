"""L'ÉPISODE de blocage d'un agent, en base (`db/alertes_agents.py`, signal 1168).

Ce qu'une doublure ne peut pas prouver, c'est le SQL qui découpe les épisodes : des
travaux morts CONSÉCUTIFS depuis le dernier succès, annoncés une fois, rouverts par le
succès suivant — et rien pour un agent éteint ou sous le seuil. Joué sur une vraie base.
"""
from __future__ import annotations

import json

import pytest

ORG = 8400
SEUIL = 3


def _travail(tid, status, il_y_a_h, erreur=None, org=ORG):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute(
            "INSERT INTO runner_jobs (org_id, kind, payload, status, last_error, "
            "finished_at) VALUES (%s, 'start', %s::jsonb, %s, %s, "
            "NOW() - make_interval(hours => %s))",
            (org, json.dumps({"trigger_id": tid, "label": "x"}), status, erreur, il_y_a_h))


def _bloques():
    from oto_mcp.db import alertes_agents
    return {a["trigger_id"]: a for a in alertes_agents.agents_bloques(SEUIL)}


@pytest.fixture()
def agent(live):
    from oto_mcp.db import runner_triggers as rt
    t = rt.create_trigger(ORG, "jane", procedure="veille", tz="UTC", tools=["a"],
                          cron="0 6 * * *", next_due="2999-01-01 00:00:00+00",
                          label="Veille des leads")
    return t["id"]


def test_sous_le_seuil_rien(agent):
    _travail(agent, "failed", 30, "403")
    _travail(agent, "failed", 20, "403")
    assert agent not in _bloques()


def test_au_seuil_l_episode_se_releve_avec_son_dernier_motif(agent):
    for h, e in ((30, "ancien"), (20, "403 Access denied"), (10, "403 Access denied N")):
        _travail(agent, "failed", h, e)
    a = _bloques()[agent]
    assert (a["org_id"], a["libelle"], a["travaux"]) == (ORG, "Veille des leads", 3)
    assert a["motif"] == "403 Access denied N"


def test_un_succes_coupe_la_serie(agent):
    _travail(agent, "failed", 40)
    _travail(agent, "failed", 35)
    _travail(agent, "done", 30)
    _travail(agent, "failed", 20)
    _travail(agent, "failed", 10)
    assert agent not in _bloques(), "deux morts depuis le succès : sous le seuil"


def test_annonce_une_fois_puis_rouvert_par_le_succes_suivant(agent):
    from oto_mcp.db import alertes_agents
    for h in (30, 20, 10):
        _travail(agent, "failed", h)
    assert alertes_agents.marquer_annonces([agent]) == 1
    _travail(agent, "failed", 0)
    assert agent not in _bloques(), "même épisode : déjà annoncé"
    _travail(agent, "done", 0)
    for _ in range(3):
        _travail(agent, "failed", 0)
    # Le succès est postérieur à l'annonce : épisode neuf. (Même heure : on recule
    # l'annonce pour que l'ordre se lise sans ambiguïté.)
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE runner_triggers SET echec_annonce_at = NOW() - interval "
                     "'1 hour' WHERE id = %s", (agent,))
    assert agent in _bloques()


def test_un_agent_eteint_ne_s_annonce_pas(agent):
    from oto_mcp.db import runner_triggers as rt
    for h in (30, 20, 10):
        _travail(agent, "failed", h)
    rt.update_trigger(agent, ORG, {"enabled": False})
    assert agent not in _bloques()


def test_hors_fenetre_rien(agent):
    for h in (24 * 9, 24 * 8, 24 * 7 + 1):
        _travail(agent, "failed", h)
    assert agent not in _bloques()


def test_la_marque_ne_date_pas_l_agent(agent):
    from oto_mcp.db import alertes_agents
    from oto_mcp.db import runner_triggers as rt
    alertes_agents.marquer_annonces([agent])
    assert rt.get_trigger(agent, ORG)["updated_at"] is None


def test_destinataires_sur_une_vraie_base_ecarte_l_operateur_et_le_suspendu(live):
    """Sur les VRAIES lignes `users` (`role`, `suspended_at`) : la doublure sans base ne
    prouve pas que la fiche lue porte ces colonnes."""
    from oto_mcp import alerte_agents_bloques as M
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, 'o') ON CONFLICT (id) "
                     "DO NOTHING", (ORG,))
        for i, (sub, role, suspendu) in enumerate((
                ("op", "super_admin", False), ("adm", "member", False),
                ("susp", "member", True), ("prop", "member", False))):
            conn.execute(
                "INSERT INTO users (sub, email, role, suspended_at, suspended_by, "
                "suspended_reason) VALUES (%s, %s, %s, "
                "CASE WHEN %s THEN NOW() END, CASE WHEN %s THEN 'op' END, "
                "CASE WHEN %s THEN 'test' END)",
                (sub, f"{sub}@acme.test", role, suspendu, suspendu, suspendu))
            conn.execute(
                "INSERT INTO org_members (org_id, sub, org_role, joined_at) VALUES "
                "(%s, %s, %s, NOW() - make_interval(days => %s))",
                (ORG, sub, "org_member" if sub == "prop" else "org_admin", 10 - i))
    assert M.destinataires(ORG, proprietaires=["prop"]) == [
        ("prop", "prop@acme.test"), ("adm", "adm@acme.test")]
    assert M.destinataire_admin(ORG) == ("adm", "adm@acme.test")
