"""Slack — le message qu'un agent hébergé poste EN TANT QUE L'APP est noté sous lui.

Une réponse dans son fil relance alors cet agent (`slack_events.py`, section « A reply
to an agent's message »). Ce que `slack_post_message` garantit :

1. **L'agent vient du travail de la requête** (`verrou_org.travail_de_la_requete` →
   `db.agent_du_travail`), jamais d'un argument : hors d'un run hébergé, rien n'est noté.
2. **Tout message d'un run hébergé est noté**, avec son auteur et son fil : seul celui de
   l'agent (l'app, hors fil) recevra des réponses ; les autres disent à la route que
   l'événement est À NOUS (en ton nom, il n'a pas de `bot_id` — sans ça, un agent qui
   répond dans son propre fil se relancerait). Un texte DÉCOUPÉ note chaque partie.
3. **Le message est PARTI** : noter qui échoue ne change pas un envoi réussi en erreur.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto_mcp import db, verrou_org
from oto_mcp.tools import slack as slack_tools


@pytest.fixture
def monte(monkeypatch):
    """L'outil monté sur un compte qui porte le bot ; un run hébergé de l'agent 12."""
    class _RC:
        fields = {"bot_token": "xoxb-1", "user_token": "xoxp-1"}
        key = None
        is_platform = False

    monkeypatch.setattr(slack_tools.access, "resolve_credential", lambda *a, **k: _RC())
    vu = {"verrou": verrou_org.Verrou(sub="bob", org_id=77, job_id=4411),
          "agent": 12, "notes": [], "panne": None}
    monkeypatch.setattr(verrou_org, "travail_de_la_requete", lambda: vu["verrou"])
    monkeypatch.setattr(db, "agent_du_travail",
                        lambda job, org: vu["agent"] if (job, org) == (4411, 77) else None)

    def _noter(*a):
        if vu["panne"]:
            raise vu["panne"]
        vu["notes"].append(a)

    monkeypatch.setattr(db.slack_agent_posts, "noter", _noter)
    from fastmcp import FastMCP
    patcher = patch("oto.tools.slack.client.SlackClient")
    cls = patcher.start()
    client = cls.return_value
    # Slack rend l'ID du canal où le message est arrivé (un DM ouvert par user → D…).
    client.post_message.return_value = {"ok": True, "channel": "C0GTM", "ts": "1690.00"}
    client.channel_info.return_value = {"ok": True, "channel": {"id": "C0GTM"}}
    vu["client"] = client
    m = FastMCP("t")
    slack_tools.register(m)
    vu["poster"] = asyncio.run(m.get_tool("slack_post_message")).fn
    yield vu
    patcher.stop()


def test_le_message_de_l_APP_dans_un_run_heberge_est_note_sous_son_agent(monte):
    out = monte["poster"](channel="gtm", text="Acme said yes. Reply ok to send.",
                          author="app")
    assert out["ok"] is True
    assert monte["notes"] == [(77, 12, 4411, "C0GTM", ["1690.00"],
                               "Acme said yes. Reply ok to send.", "app", None)]


@pytest.mark.parametrize("args,auteur,fil", [({"author": "me"}, "me", None),
                                             ({"author": "app", "thread_ts": "1.0"}, "app", "1.0"),
                                             ({"author": "me", "thread_ts": "1.0"}, "me", "1.0")])
def test_en_TON_nom_ou_dans_un_fil_le_message_est_note_avec_son_auteur_et_son_fil(
        monte, args, auteur, fil):
    monte["poster"](channel="C0GTM", text="x", **args)
    assert [(n[6], n[7]) for n in monte["notes"]] == [(auteur, fil)]


def test_un_texte_DECOUPE_note_chacune_de_ses_parties(monte):
    monte["client"].post_message.return_value = {
        "ok": True, "channel": "C0GTM", "ts": "1690.00",
        "ts_all": ["1690.00", "1690.01", "1690.02"], "split_into": 3}
    monte["poster"](channel="C0GTM", text="long", author="app")
    assert monte["notes"][0][4] == ["1690.00", "1690.01", "1690.02"]


def test_hors_d_un_run_heberge_rien_n_est_note(monte):
    monte["verrou"] = None
    monte["poster"](channel="C0GTM", text="x", author="app")
    assert monte["notes"] == []


def test_un_travail_qui_n_est_le_run_d_AUCUN_agent_ne_note_rien(monte):
    """Un travail enfilé à la main, ou dont l'agent a été supprimé."""
    monte["agent"] = None
    monte["poster"](channel="C0GTM", text="x", author="app")
    assert monte["notes"] == []


def test_noter_qui_ECHOUE_ne_change_pas_un_envoi_reussi_en_erreur(monte, caplog):
    monte["panne"] = RuntimeError("base indisponible")
    out = monte["poster"](channel="C0GTM", text="x", author="app")
    assert out["ok"] is True and out["ts"] == "1690.00"
    assert "not noted for replies" in caplog.text
