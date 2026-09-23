"""Slack — QUI écrit se choisit, il ne se déduit pas (décision du 23/09).

Un compte Slack (un workspace) porte jusqu'à deux identités : l'app (jeton de bot
`xoxb-`) et une personne (jeton utilisateur `xoxp-`). Le client routait toute
écriture sur la personne dès que son jeton existait. Conséquences vécues : un
message part en ton nom alors qu'il devait venir de l'app, ou l'inverse devient
impossible — et rien, dans la réponse, ne dit sous quel nom il est parti.

Règle : un seul jeton → il sert ; les deux → l'appelant nomme l'auteur, sinon
refus ; un auteur sans son jeton → refus, jamais un repli sur l'autre. Et la
réponse dit qui a écrit et OÙ (nom du canal, partagé avec l'extérieur ou non) —
le 02/09, une réponse destinée à Tulina est partie sur le canal de JB.
"""
import asyncio
from unittest.mock import patch

import pytest

from oto.tools.slack.client import SlackError

from oto_mcp.tools import slack as slack_tools


# --- la règle, seule ----------------------------------------------------------

@pytest.mark.parametrize("bot,user,author,attendu", [
    (True, False, None, False),     # seul le bot : l'app écrit
    (False, True, None, True),      # seul l'utilisateur : la personne écrit
    (True, True, "me", True),
    (True, True, "app", False),
    (False, True, "me", True),
    (True, False, "app", False),
])
def test_un_auteur_non_ambigu_est_servi(bot, user, author, attendu):
    assert slack_tools._auteur(bot, user, author) is attendu


def test_deux_identites_sans_auteur_est_un_refus_qui_dit_quoi_passer():
    with pytest.raises(ValueError) as e:
        slack_tools._auteur(True, True, None)
    assert 'author="me"' in str(e.value) and 'author="app"' in str(e.value)


@pytest.mark.parametrize("bot,user,author,manque", [
    (True, False, "me", "xoxp-"),
    (False, True, "app", "xoxb-"),
])
def test_un_auteur_sans_son_jeton_est_refuse_jamais_remplace(bot, user, author, manque):
    """Le repli sur l'autre identité ferait parler quelqu'un d'autre que celui
    qu'on a nommé : c'est exactement ce que la décision interdit."""
    with pytest.raises(ValueError) as e:
        slack_tools._auteur(bot, user, author)
    assert manque in str(e.value)


# --- sur l'outil monté --------------------------------------------------------

def _monte(monkeypatch, bot, user):
    class _RC:
        fields = {"bot_token": bot, "user_token": user}
        key = None
        is_platform = False

    monkeypatch.setattr(slack_tools.access, "resolve_credential", lambda *a, **k: _RC())
    from fastmcp import FastMCP
    patcher = patch("oto.tools.slack.client.SlackClient")
    cls = patcher.start()
    m = FastMCP("t")
    slack_tools.register(m)
    return m, cls, patcher


def _fn(m, name):
    return asyncio.run(m.get_tool(name)).fn


def test_poster_tient_l_auteur_choisi_et_dit_qui_et_ou(monkeypatch):
    m, cls, patcher = _monte(monkeypatch, "xoxb-1", "xoxp-1")
    try:
        client = cls.return_value
        client.post_message.return_value = {"ok": True, "ts": "1.2"}
        client.channel_info.return_value = {"ok": True, "channel": {
            "id": "C1", "name": "oto-tulina", "is_im": False, "is_ext_shared": True}}
        out = _fn(m, "slack_post_message")(channel="C1", text="bonjour", author="app")
    finally:
        patcher.stop()
    # Le client est construit pour écrire en tant que l'APP, pas « l'utilisateur
    # parce que son jeton existe ».
    assert cls.call_args.kwargs["default_as_user"] is False
    assert out["_author"] == "app"
    assert out["_channel"] == {"id": "C1", "name": "oto-tulina", "is_dm": False,
                               "shared_externally": True}


def test_poster_ambigu_est_refuse_AVANT_tout_envoi(monkeypatch):
    m, cls, patcher = _monte(monkeypatch, "xoxb-1", "xoxp-1")
    try:
        with pytest.raises(Exception):
            _fn(m, "slack_post_message")(channel="C1", text="bonjour")
        cls.return_value.post_message.assert_not_called()
    finally:
        patcher.stop()


def test_ne_pas_savoir_ou_n_annule_pas_un_message_parti(monkeypatch):
    """Le message est déjà envoyé quand on cherche où il est arrivé : un échec de
    lecture se NOMME dans la réponse, il ne transforme pas un succès en erreur."""
    m, cls, patcher = _monte(monkeypatch, "xoxb-1", None)
    try:
        client = cls.return_value
        client.post_message.return_value = {"ok": True, "ts": "1.2"}
        client.channel_info.side_effect = SlackError("channel_not_found")
        out = _fn(m, "slack_post_message")(channel="G9", text="x")
    finally:
        patcher.stop()
    assert out["ok"] is True and out["_author"] == "app"
    assert out["_channel"] == {"id": "G9", "unknown": "channel_not_found"}


@pytest.mark.parametrize("outil,args", [
    ("slack_delete_message", {"channel": "C1", "ts": "1.2"}),
    ("slack_add_reaction", {"channel": "C1", "ts": "1.2", "name": "wave"}),
])
def test_supprimer_et_reagir_suivent_la_meme_regle(monkeypatch, outil, args):
    m, cls, patcher = _monte(monkeypatch, "xoxb-1", "xoxp-1")
    try:
        cls.return_value.delete_message.return_value = {"ok": True}
        cls.return_value.add_reaction.return_value = {"ok": True}
        with pytest.raises(Exception):
            _fn(m, outil)(**args)
        out = _fn(m, outil)(**args, author="me")
    finally:
        patcher.stop()
    assert out["_author"] == "me"
    assert cls.call_args.kwargs["default_as_user"] is True


def test_les_lectures_ne_demandent_pas_d_auteur(monkeypatch):
    """Le choix ne concerne que ce qui PART : lire reste routé par le client
    (canal → bot, DM → utilisateur), sans rien exiger de l'appelant."""
    m, cls, patcher = _monte(monkeypatch, "xoxb-1", "xoxp-1")
    try:
        cls.return_value.list_channels.return_value = {"ok": True, "channels": []}
        _fn(m, "slack_list_channels")()
    finally:
        patcher.stop()
