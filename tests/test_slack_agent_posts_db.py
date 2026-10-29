"""Les messages Slack notés sous un agent, en base (`db/slack_agent_posts.py`).

Ce qu'une doublure ne peut pas prouver : la clé `(channel, ts)` (une retentative garde la
première écriture), le texte borné, SEUL le message de l'agent (l'app, hors fil) reçoit
des réponses quand tous disent « à nous », chaque partie d'un texte découpé notée, la
lecture qui ne sert plus rien au-delà de la rétention, la purge qui retire exactement ce
que la lecture ne sert plus, la ligne qui part avec son agent (ON DELETE CASCADE), le
lien du travail de la requête à son agent (`db.agent_du_travail`), qui décide sous qui un
message est noté — et la file des webhooks qui enfile la réponse pour un agent
PROGRAMMÉ, pas seulement un webhook. Joué sur une vraie base.
"""
from __future__ import annotations

import json

import pytest

ORG = 8420


@pytest.fixture()
def agent(live):
    from oto_mcp.db import runner_triggers as rt
    t = rt.create_trigger(ORG, "bob", procedure="replies", tz="UTC", tools=["a"],
                          cron="0 6 * * *", next_due="2999-01-01 00:00:00+00",
                          label="Lemlist replies")
    return t["id"]


def _vieillir(channel, ts, jours):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE slack_agent_posts SET created_at = NOW() - "
                     "make_interval(days => %s) WHERE channel = %s AND ts = %s",
                     (jours, channel, ts))


def test_noter_puis_lire_et_une_retentative_garde_la_PREMIERE_ecriture(agent):
    from oto_mcp.db import slack_agent_posts as P
    P.noter(ORG, agent, 41, "C1", ["1690.00"], "Acme said yes", "app")
    P.noter(ORG, agent, 42, "C1", ["1690.00"], "autre chose", "app")
    lu = P.lire("C1", "1690.00")
    assert (lu["org_id"], lu["trigger_id"], lu["job_id"], lu["text"]) == (
        ORG, agent, 41, "Acme said yes")
    assert P.lire("C1", "1690.01") is None and P.lire("C2", "1690.00") is None


def test_le_texte_est_BORNE(agent):
    from oto_mcp.db import slack_agent_posts as P
    P.noter(ORG, agent, None, "C1", ["1691.00"], "x" * (P.TEXTE_MAX + 50), "app")
    assert len(P.lire("C1", "1691.00")["text"]) == P.TEXTE_MAX


def test_au_dela_de_la_retention_la_lecture_ne_sert_plus_et_la_purge_retire(agent):
    from oto_mcp.db import slack_agent_posts as P
    P.noter(ORG, agent, 1, "C9", ["1.0"], "vieux", "app")
    P.noter(ORG, agent, 2, "C9", ["2.0"], "récent", "app")
    _vieillir("C9", "1.0", P.RETENTION_JOURS + 1)
    _vieillir("C9", "2.0", P.RETENTION_JOURS - 1)
    assert P.lire("C9", "1.0") is None and P.lire("C9", "2.0") is not None
    avant = P.compter_purgeables()
    assert avant >= 1
    assert P.purger() == avant
    assert P.compter_purgeables() == 0 and P.lire("C9", "2.0") is not None


def test_l_agent_SUPPRIME_emporte_ses_messages(agent):
    from oto_mcp.db import runner_triggers as rt, slack_agent_posts as P
    P.noter(ORG, agent, 1, "C7", ["7.0"], "x", "app")
    rt.delete_trigger(agent, ORG)
    assert P.lire("C7", "7.0") is None


def test_le_travail_d_un_run_heberge_nomme_son_agent(agent):
    """Ce que `slack_post_message` lit pour savoir sous qui noter : le `trigger_id` que
    le serveur a posé dans la charge du travail."""
    from oto_mcp import db
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        job = conn.execute(
            "INSERT INTO runner_jobs (org_id, kind, payload, status, sub) "
            "VALUES (%s, 'start', %s::jsonb, 'claimed', 'bob') RETURNING id",
            (ORG, json.dumps({"trigger_id": agent}))).fetchone()["id"]
    assert db.agent_du_travail(job, ORG) == agent
    assert db.agent_du_travail(job, ORG + 1) is None


def test_la_maintenance_est_dans_le_timer_quotidien(live):
    from oto_mcp import maintenance
    assert "slack-agent-posts" in maintenance._ALL
    assert set(maintenance.slack_agent_posts(dry_run=True)) == {"purgeables"}


def test_SEUL_le_message_de_l_agent_recoit_des_reponses_tous_disent_a_nous(agent):
    from oto_mcp.db import slack_agent_posts as P
    P.noter(ORG, agent, 1, "C3", ["3.0"], "en ton nom", "me")
    P.noter(ORG, agent, 1, "C3", ["3.1"], "dans un fil", "app", "2.9")
    P.noter(ORG, agent, 1, "C3", ["3.2", "3.3", "3.4"], "long", "app")
    assert P.lire("C3", "3.0") is None and P.lire("C3", "3.1") is None
    assert P.lire("C3", "3.2")["text"] == "long"
    assert P.lire("C3", "3.3") is None, "une partie est une réponse dans le fil du premier"
    assert all(P.est_note("C3", ts) for ts in ("3.0", "3.1", "3.2", "3.3", "3.4"))
    assert not P.est_note("C3", "3.5")


def test_une_reponse_ENFILE_un_agent_PROGRAMME_sous_son_proprietaire(live):
    """`slack_events` passe à `runner_hook.enfiler` la ligne d'un agent programmé
    (`kind = schedule`), pas seulement celle d'un webhook : le travail part, sous son
    propriétaire, avec l'instruction de la réponse, et la livraison est journalisée."""
    from oto_mcp import runner_hook
    from oto_mcp.db import runner_triggers as rt
    from oto_mcp.db._conn import _connect
    t = rt.create_trigger(ORG, "bob", procedure="replies", tz="UTC", tools=["a"],
                          cron="0 6 * * *", next_due="2999-01-01 00:00:00+00",
                          label="Lemlist replies", model="claude-sonnet-5")
    t = rt.get_trigger(t["id"], ORG)
    assert t["kind"] == "schedule"
    res = runner_hook.enfiler(t, "EvLive1", "slack", entree="REPLY BLOCK",
                              outils_en_plus=("slack_post_message",))
    with _connect() as conn:
        job = conn.execute("SELECT sub, payload FROM runner_jobs WHERE id = %s",
                           (res["job_id"],)).fetchone()
        livraison = conn.execute(
            "SELECT outcome, source, external_id FROM runner_hook_deliveries "
            "WHERE trigger_id = %s", (t["id"],)).fetchone()
    assert job["sub"] == "bob"
    assert job["payload"]["input"].endswith("REPLY BLOCK")
    assert job["payload"]["trigger_id"] == t["id"]
    assert "slack_post_message" in job["payload"]["tools"]
    assert (livraison["outcome"], livraison["source"], livraison["external_id"]) == (
        "queued", "slack", "EvLive1")
    again = runner_hook.enfiler(t, "EvLive1", "slack", entree="REPLY BLOCK")
    assert again["duplicate"] is True and again["job_id"] == res["job_id"]
