"""Tools `teams_*` du connecteur Teams (service du porteur `microsoft`).

Ce que ce fichier verrouille :
- la SURFACE (2 tools), le jeton du service `teams`, le refus nommé d'un compte qui
  ne l'a pas autorisé ;
- le dispatch vers les VRAIES méthodes de `TeamsClient` (autospec), le lieu : un canal
  (`team_id` + `channel_id`) OU une conversation (`chat_id`), jamais les deux ;
- le corps markdown → HTML, la vue resserrée (texte du message), le brut sur `full` ;
- ⚠️ le PALIER ADMIN : lire les messages d'un canal exige `scopes.TEAMS_ADMIN`. Sans
  lui : un renouvellement forcé (l'approbation n'est vue qu'au renouvellement), puis
  refus qui dit « requires your Microsoft admin's approval » et rend le lien
  d'approbation du porteur (services=["teams"], annuaire du compte) ; avec lui mais un
  403 de Graph (jeton d'avant l'approbation) : UN renouvellement forcé, puis une seule
  nouvelle tentative — jamais de boucle. Les conversations et la publication n'en
  ont pas besoin.
"""
import asyncio
from unittest.mock import create_autospec

import pytest
from oto_mcp.mcp_errors import McpError

ADMIN = ("ChannelMessage.Read.All Team.ReadBasic.All Channel.ReadBasic.All "
         "ChannelMessage.Send Chat.ReadWrite User.Read offline_access")
SANS_ADMIN = ("Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Send Chat.ReadWrite "
              "User.Read offline_access")


@pytest.fixture
def env(monkeypatch):
    """Le faux `TeamsClient` (autospec), les jetons demandés (`renew` compris), le meta
    du compte que l'appel désigne — que le renouvellement peut faire évoluer, comme le
    fait `access_token_for` en relisant `grant.scope`."""
    import types

    import oto.tools.microsoft as coeur

    inst = create_autospec(coeur.TeamsClient, instance=True)
    etat = types.SimpleNamespace(client=inst, jetons=[], meta={"scopes": SANS_ADMIN},
                                 apres_renouvellement=None, liens=[])
    monkeypatch.setattr(coeur, "TeamsClient", lambda jeton: etat.jetons.append(jeton) or inst)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")

    def jeton(sub, service, renew=False):
        assert service == "teams"
        if renew and etat.apres_renouvellement is not None:
            etat.meta = {"scopes": etat.apres_renouvellement, **{
                k: v for k, v in etat.meta.items() if k != "scopes"}}
        return "AT-neuf" if renew else "AT-cache"

    def compte(sub, service, account=None):
        assert service == "teams"
        return object(), dict(etat.meta)

    def lien(sub, services, tenant=None, return_app="", connector=None):
        etat.liens.append((services, tenant, connector))
        return "https://login.example/adminconsent?x=1"

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", jeton)
    monkeypatch.setattr("oto_mcp.auth.microsoft.resolve_account", compte)
    monkeypatch.setattr("oto_mcp.auth.microsoft.admin_consent_url", lien)
    return etat


def _mcp():
    from fastmcp import FastMCP
    from oto_mcp.tools import teams as X

    m = FastMCP("t")
    X.register(m)
    return m


def _tool(name):
    return asyncio.run(_mcp().get_tool(name)).fn


def _upstream(status):
    from oto.tools.common.errors import UpstreamHTTPError
    return UpstreamHTTPError(status, {"error": {"message": "Forbidden"}}, service="microsoft")


MSG = {"id": "1700", "from": {"user": {"displayName": "Jane"}},
       "createdDateTime": "2026-10-08T09:00:00Z",
       "body": {"contentType": "html", "content": "<p>Bonjour &amp; <b>merci</b></p>"},
       "attachments": [{}], "reactions": ["lourd"]}


# --- surface, jeton, lieux ------------------------------------------------------

def test_surface(env):
    assert {t.name for t in asyncio.run(_mcp().list_tools())} == {
        "teams_spaces", "teams_message"}


def test_un_compte_sans_teams_recoit_le_refus_du_porteur(env, monkeypatch):
    def refus(sub, service, renew=False):
        raise RuntimeError("The Microsoft account j@x has not yet authorized Teams")

    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for", refus)
    with pytest.raises(McpError, match="has not yet authorized Teams"):
        _tool("teams_spaces")()
    assert env.jetons == []


def test_espaces(env):
    c = env.client
    c.list_joined_teams.return_value = [{"id": "t1", "displayName": "Ventes", "x": 1}]
    assert _tool("teams_spaces")()["teams"] == [{"id": "t1", "displayName": "Ventes",
                                                 "description": None}]
    c.list_channels.return_value = [{"id": "19:a@thread.tacv2", "displayName": "Général"}]
    out = _tool("teams_spaces")(op="channels", team_id="t1")
    c.list_channels.assert_called_once_with("t1")
    assert out["channels"][0]["id"] == "19:a@thread.tacv2"
    c.list_chats.return_value = []
    _tool("teams_spaces")(op="chats", limit=7)
    c.list_chats.assert_called_once_with(limit=7)
    with pytest.raises(McpError, match="requires `team_id`"):
        _tool("teams_spaces")(op="channels")


@pytest.mark.parametrize("lieu", [{}, {"team_id": "t1", "chat_id": "c1"},
                                  {"team_id": "t1", "channel_id": "ch", "chat_id": "c1"}])
def test_un_lieu_et_un_seul(env, lieu):
    with pytest.raises(McpError, match="exactly one"):
        _tool("teams_message")(**lieu)


def test_un_canal_a_besoin_de_son_equipe(env):
    with pytest.raises(McpError, match="requires `team_id`"):
        _tool("teams_message")(channel_id="ch")


# --- conversations et publication : sans palier admin ----------------------------

def test_lire_une_conversation_sans_palier_admin(env):
    env.client.list_chat_messages.return_value = [dict(MSG)]
    out = _tool("teams_message")(chat_id="19:c@unq.gbl.spaces", limit=5)
    env.client.list_chat_messages.assert_called_once_with("19:c@unq.gbl.spaces", limit=5)
    m = out["messages"][0]
    assert m["text"] == "Bonjour & merci" and m["from"] == "Jane" and m["attachments"] == 1
    assert "reactions" not in m
    assert _tool("teams_message")(chat_id="c", full=True)["messages"] == [MSG]
    assert env.jetons == ["AT-cache", "AT-cache"] and env.liens == []


def test_publier_dans_un_canal_et_une_conversation(env):
    c = env.client
    c.post_channel_message.return_value = {"id": "m1"}
    _tool("teams_message")(op="post", team_id="t1", channel_id="ch", body="**Point** à 14 h")
    args, kw = c.post_channel_message.call_args
    assert args == ("t1", "ch") and "<strong>Point</strong>" in kw["html"]
    c.post_chat_message.return_value = {"id": "m2"}
    _tool("teams_message")(op="post", chat_id="c1", body="ok")
    assert c.post_chat_message.call_args.args == ("c1",)
    c.reply_channel_message.return_value = {"id": "m3"}
    _tool("teams_message")(op="reply", team_id="t1", channel_id="ch", message_id="1700",
                           body="vu")
    assert c.reply_channel_message.call_args.args == ("t1", "ch", "1700")
    assert env.liens == [], "publier n'a pas besoin du palier admin"


def test_une_conversation_n_a_pas_de_fil(env):
    for op, extra in (("replies", {"message_id": "1"}), ("reply", {"message_id": "1",
                                                                   "body": "x"})):
        with pytest.raises(McpError, match="no threads"):
            _tool("teams_message")(op=op, chat_id="c1", **extra)
    with pytest.raises(McpError, match="requires `body`"):
        _tool("teams_message")(op="post", chat_id="c1")


# --- le palier admin ------------------------------------------------------------

def test_lire_un_canal_sans_palier_admin_refus_avec_le_lien(env):
    env.meta = {"scopes": SANS_ADMIN, "tenant": "contoso.onmicrosoft.com"}
    with pytest.raises(McpError) as e:
        _tool("teams_message")(team_id="t1", channel_id="ch")
    assert "requires your Microsoft admin's approval" in str(e.value)
    assert "https://login.example/adminconsent?x=1" in str(e.value)
    assert env.liens == [(("teams",), "contoso.onmicrosoft.com", "teams")]
    assert env.jetons == ["AT-neuf"], "UN renouvellement forcé avant de refuser"
    env.client.list_channel_messages.assert_not_called()
    with pytest.raises(McpError, match="admin's approval"):
        _tool("teams_message")(op="replies", team_id="t1", channel_id="ch",
                               message_id="1700")
    env.client.list_replies.assert_not_called()


def test_une_approbation_donnee_depuis_est_vue_au_renouvellement(env):
    env.apres_renouvellement = ADMIN
    env.client.list_channel_messages.return_value = [dict(MSG)]
    out = _tool("teams_message")(team_id="t1", channel_id="ch", limit=3)
    env.client.list_channel_messages.assert_called_once_with("t1", "ch", limit=3)
    assert env.jetons == ["AT-neuf"] and env.liens == [] and out["count"] == 1


def test_palier_accorde_jeton_d_avant_l_approbation_renouvele_une_fois(env):
    env.meta = {"scopes": ADMIN}
    env.client.list_replies.side_effect = [_upstream(403), [dict(MSG)]]
    out = _tool("teams_message")(op="replies", team_id="t1", channel_id="ch",
                                 message_id="1700")
    assert env.jetons == ["AT-cache", "AT-neuf"] and out["count"] == 1
    assert env.client.list_replies.call_count == 2


def test_jamais_plus_d_un_renouvellement(env):
    env.meta = {"scopes": ADMIN}
    env.client.list_channel_messages.side_effect = _upstream(403)
    with pytest.raises(McpError, match="HTTP 403"):
        _tool("teams_message")(team_id="t1", channel_id="ch")
    assert env.jetons == ["AT-cache", "AT-neuf"]
    assert env.client.list_channel_messages.call_count == 2
    # Déjà renouvelé pour lire le palier : un 403 ensuite ne renouvelle plus.
    env.meta, env.apres_renouvellement, env.jetons[:] = {"scopes": SANS_ADMIN}, ADMIN, []
    env.client.list_channel_messages.reset_mock()
    env.client.list_channel_messages.side_effect = _upstream(403)
    with pytest.raises(McpError, match="HTTP 403"):
        _tool("teams_message")(team_id="t1", channel_id="ch")
    assert env.jetons == ["AT-neuf"] and env.client.list_channel_messages.call_count == 1


def test_un_autre_refus_n_est_pas_un_renouvellement(env):
    env.meta = {"scopes": ADMIN}
    env.client.list_channel_messages.side_effect = _upstream(404)
    with pytest.raises(McpError, match="not found"):
        _tool("teams_message")(team_id="t1", channel_id="ch")
    assert env.jetons == ["AT-cache"]
