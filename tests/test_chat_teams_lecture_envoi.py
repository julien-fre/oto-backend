"""Google Chat et Teams : lire et agir sont deux outils, `chat`/`chat_act` et
`teams`/`teams_act` (21/10/2026).

Un client MCP (claude.ai, ChatGPT) autorise des OUTILS, pas des opérations : « toujours
autoriser » la lecture d'un espace Google Chat ou d'un canal Teams autorisait aussi d'y
poster en ton nom. Le découpage de #1231 (13/10/2026) servait encore l'ancienne op
d'envoi du lecteur jusqu'au 10/12/2026 ; depuis le 21/10/2026, chaque connecteur sert sa
paire et les anciens noms refusent (`outils_retires.RENOMMAGES`). Ce fichier tient :

- l'outil qui agit déclare `ACTE` et vit sous le connecteur de son lecteur (même
  activation, même compte opéré `_account=`) ;
- le lecteur est `LECTURE` et ne poste jamais : une op d'envoi y est une valeur refusée,
  avant toute résolution de compte ;
- l'outil qui agit refuse un envoi incomplet avant toute résolution de compte.
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from oto_mcp import call_axes, error_taxonomy
from oto_mcp.tool_visibility import namespace_of
from oto_mcp.tools import chat as C
from oto_mcp.tools import teams as T
from oto_mcp.tools.lecture import ACTE, en_lecture

#: lecteur → (acteur, ses ops).
PAIRES = {"chat": ("chat_act", ("send",)), "teams": ("teams_act", ("post", "reply"))}


def _monte() -> FastMCP:
    m = FastMCP("t")
    C.register(m)
    T.register(m)
    return m


@pytest.fixture
def m(clients):
    """Monté APRÈS les faux clients : `teams.register` lie `TeamsClient` à son appel."""
    return _monte()


@pytest.fixture
def clients(monkeypatch):
    """Les faux clients Google Chat et Teams, et la trace de chaque résolution de
    compte (Chat : `account` ; Teams : le jeton demandé)."""
    import oto.tools.microsoft as coeur

    etat = types.SimpleNamespace(chat=MagicMock(), teams=MagicMock(), comptes=[])
    monkeypatch.setattr(C, "_client_for_user",
                        lambda account=None: (etat.comptes.append(("chat", account)),
                                              etat.chat)[1])
    monkeypatch.setattr(coeur, "TeamsClient",
                        lambda jeton, *, session: (
                            etat.comptes.append(("teams", jeton)), etat.teams)[1])
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    monkeypatch.setattr("oto_mcp.auth.microsoft.access_token_for",
                        lambda sub, service, renew=False: "AT")
    return etat


def _outil(m, nom):
    return asyncio.run(m.get_tool(nom))


@pytest.mark.parametrize("lecteur", sorted(PAIRES))
def test_l_acteur_declare_qu_il_agit_chez_un_tiers(m, lecteur):
    acteur, ops = PAIRES[lecteur]
    t = _outil(m, acteur)
    assert t.annotations == ACTE
    assert t.annotations.destructiveHint is True and t.annotations.openWorldHint is True
    op = t.parameters["properties"]["op"]
    assert sorted(op.get("enum") or [op["const"]]) == sorted(ops)


@pytest.mark.parametrize("lecteur", sorted(PAIRES))
def test_l_acteur_vit_sous_le_connecteur_de_son_lecteur(lecteur):
    """Même namespace ⟹ même activation, même ACL, même sélection, et le même axe
    `_account=` : poster sous un AUTRE compte que la lecture serait pire qu'avant."""
    acteur = PAIRES[lecteur][0]
    assert namespace_of(acteur) == namespace_of(lecteur) == lecteur
    assert call_axes.accepts_account_axis(acteur) == call_axes.accepts_account_axis(
        lecteur), acteur


@pytest.mark.parametrize("lecteur", sorted(PAIRES))
def test_le_lecteur_est_en_lecture_et_ne_porte_aucune_op_d_envoi(m, lecteur):
    t = _outil(m, lecteur)
    assert en_lecture(t), lecteur
    assert not set(PAIRES[lecteur][1]) & set(t.parameters["properties"]["op"]["enum"])
    assert not {"text", "body", "user"} & set(t.parameters["properties"]), lecteur


@pytest.mark.parametrize("lecteur,args", [
    ("chat", {"op": "send", "space": "spaces/A", "text": "bonjour"}),
    ("teams", {"op": "post", "chat_id": "c1", "body": "ok"}),
    ("teams", {"op": "reply", "team_id": "t1", "channel_id": "ch", "message_id": "1",
               "body": "vu"}),
])
def test_le_lecteur_ne_poste_jamais(m, clients, lecteur, args):
    with pytest.raises(Exception):
        asyncio.run(m.call_tool(lecteur, args))
    assert clients.comptes == []
    clients.chat.send.assert_not_called()
    clients.teams.post_chat_message.assert_not_called()
    clients.teams.reply_channel_message.assert_not_called()


@pytest.mark.parametrize("acteur,args,manque", [
    ("chat_act", {"text": "bonjour"}, "space"),
    ("chat_act", {"space": "spaces/A", "user": "marie@exemple.fr", "text": "x"}, "space"),
    ("chat_act", {"space": "spaces/A"}, "text"),
    ("teams_act", {"chat_id": "c1"}, "body"),
    ("teams_act", {"body": "ok"}, "team_id"),
    ("teams_act", {"op": "reply", "team_id": "t1", "channel_id": "ch", "body": "vu"},
     "message_id"),
])
def test_un_envoi_incomplet_est_refuse_avant_toute_resolution_de_compte(
        m, clients, acteur, args, manque):
    with pytest.raises(Exception) as e:
        asyncio.run(m.call_tool(acteur, args))
    assert manque in error_taxonomy.classify(e.value).message
    assert clients.comptes == []
