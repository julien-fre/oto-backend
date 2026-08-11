"""Dispatch `op=` des tools `chat_*` (ADR 0047 §Amendement, appliqué au produit
Google Chat le 2026-08-11 : 3 tools → 2).

Ce que ce fichier verrouille : la SURFACE. Le seul test voisin (`test_chat_error.py`)
exerce la normalisation d'un `HttpError` — jamais le câblage tool → méthode du client.
Or ce module **ÉCRIT** : `op="send"` poste pour de bon dans un espace Google Chat réel,
sous l'identité de l'utilisateur. Une op mal câblée n'échoue pas au boot, elle publie.
D'où, pour chaque op : la méthode client appelée ET la preuve qu'aucune des voisines
dangereuses ne l'est, le fait que le défaut soit une LECTURE, le refus explicite d'une
op inconnue (jamais un fallback muet) et le refus nommé d'un argument obligatoire.

Les tools sont `async` (le client Google est sync, poussé hors boucle par
`asyncio.to_thread`) → on les déroule par `asyncio.run`, comme `_tool` les récupère.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import McpError


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import chat as C

    m = FastMCP("t")
    C.register(m)
    fn = asyncio.run(m.get_tool(name)).fn
    return lambda **kw: asyncio.run(fn(**kw))


@pytest.fixture
def client(monkeypatch):
    """Faux ChatClient — le credential OAuth Google n'est pas le sujet ici.

    `_client_for_user` résout le `sub` courant puis les credentials du compte : on
    patche le tout au seam du module, en gardant l'`account` reçu pour vérifier
    qu'il est bien transmis.
    """
    from oto_mcp.tools import chat as C

    inst = MagicMock()
    inst.accounts = []
    monkeypatch.setattr(C, "_client_for_user",
                        lambda account=None: (inst.accounts.append(account), inst)[1])
    return inst


# --- espaces : la découverte, restée seule -------------------------------------

def test_spaces_lists_spaces_and_counts_them(client):
    client.list_spaces.return_value = [{"name": "spaces/A"}, {"name": "spaces/B"}]
    assert _tool("chat_spaces")() == {
        "spaces": [{"name": "spaces/A"}, {"name": "spaces/B"}], "count": 2}
    client.list_spaces.assert_called_once()


def test_spaces_builds_the_chat_api_filter(client):
    """Le tool prend un `space_type` nu et fabrique le filtre attendu par l'API Chat
    (`spaceType = "…"`) — pas de filtre du tout quand il est omis."""
    client.list_spaces.return_value = []
    _tool("chat_spaces")(space_type="DIRECT_MESSAGE", max_results=7)
    assert client.list_spaces.call_args.args == ('spaceType = "DIRECT_MESSAGE"', 7)
    _tool("chat_spaces")()
    assert client.list_spaces.call_args.args == (None, 100)


def test_spaces_never_writes(client):
    """L'inventaire est une lecture : aucune des deux méthodes d'envoi ne bouge."""
    client.list_spaces.return_value = []
    _tool("chat_spaces")()
    client.send.assert_not_called()
    client.send_dm.assert_not_called()


# --- messages : lecture --------------------------------------------------------

def test_message_list_is_the_default_op_and_never_writes(client):
    """Un appel SANS `op` lit. C'est l'invariant qui compte le plus ici : aucun
    chemin par défaut ne doit pouvoir poster dans un espace réel."""
    client.list_messages.return_value = [{"text": "hello"}]
    out = _tool("chat_message")(space="spaces/A")
    assert out == {"messages": [{"text": "hello"}], "count": 1}
    client.list_messages.assert_called_once()
    client.send.assert_not_called()
    client.send_dm.assert_not_called()


def test_message_list_forwards_the_space_and_the_cap(client):
    client.list_messages.return_value = []
    _tool("chat_message")(op="list", space="spaces/A", max_results=5)
    assert client.list_messages.call_args.args == ("spaces/A", 5)


def test_message_list_requires_the_space(client):
    """Pas de repli sur « le premier espace venu » : l'espace est nommé, et le
    message rappelle que `user` ne sert qu'à l'envoi."""
    with pytest.raises(McpError, match="space") as e:
        _tool("chat_message")(op="list", user="a@b.com")
    assert "op='list'" in str(e.value) and "chat_spaces" in str(e.value)
    client.list_messages.assert_not_called()


# --- messages : ÉCRITURE (poste dans un espace Google Chat réel) ---------------

def test_message_send_to_a_space_posts_only_there(client):
    """op="send" + `space` → `send(space, text)` et RIEN d'autre : ni DM (qui
    partirait à un tiers), ni lecture."""
    _tool("chat_message")(op="send", space="spaces/A", text="bonjour")
    client.send.assert_called_once()
    assert client.send.call_args.args == ("spaces/A", "bonjour")
    client.send_dm.assert_not_called()
    client.list_messages.assert_not_called()
    client.list_spaces.assert_not_called()


def test_message_send_to_a_user_resolves_the_dm_and_nothing_else(client):
    """op="send" + `user` → `send_dm(user, text)` (qui résout l'espace DM côté
    client) : `send` ne doit PAS être appelée avec un email en guise d'espace."""
    _tool("chat_message")(op="send", user="marie@exemple.fr", text="bonjour")
    client.send_dm.assert_called_once()
    assert client.send_dm.call_args.args == ("marie@exemple.fr", "bonjour")
    client.send.assert_not_called()
    client.list_messages.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {},                                                   # aucune destination
    {"space": "spaces/A", "user": "marie@exemple.fr"},     # les deux
])
def test_message_send_refuses_an_ambiguous_destination(client, kwargs):
    """Ni deviné, ni « les deux » : un envoi dont la destination est ambiguë est
    refusé AVANT d'atteindre Google — sinon le message part au mauvais endroit."""
    with pytest.raises(McpError, match="soit"):
        _tool("chat_message")(op="send", text="bonjour", **kwargs)
    client.send.assert_not_called()
    client.send_dm.assert_not_called()


@pytest.mark.parametrize("dest", [{"space": "spaces/A"}, {"user": "marie@exemple.fr"}])
def test_message_send_requires_the_text(client, dest):
    """`text` n'est plus exigé par la signature (il l'était sur l'ex-`chat_send`) :
    c'est ce check runtime qui interdit de poster un message vide."""
    with pytest.raises(McpError, match="text") as e:
        _tool("chat_message")(op="send", **dest)
    assert "op='send'" in str(e.value)
    client.send.assert_not_called()
    client.send_dm.assert_not_called()


# --- refus ---------------------------------------------------------------------

def test_unknown_op_is_refused_with_the_allowed_list(client):
    """Une op inconnue doit lever en nommant les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée), et surtout
    jamais sur l'envoi."""
    with pytest.raises(McpError, match="op doit être") as e:
        _tool("chat_message")(op="metadata", space="spaces/A", text="bonjour")
    assert "'list'" in str(e.value) and "'send'" in str(e.value)
    assert client.mock_calls == []


def test_no_op_reaches_a_write_by_accident(client):
    """Filet global : aucune valeur d'`op` autre que 'send' n'atteint `send`/
    `send_dm`, y compris les quasi-synonymes qu'un agent pourrait inventer."""
    for op in ("metadata", "get", "read", "post", "create", "delete", "SEND", ""):
        with pytest.raises(McpError):
            _tool("chat_message")(op=op, space="spaces/A", text="bonjour",
                                  user=None)
    client.send.assert_not_called()
    client.send_dm.assert_not_called()


# --- compte ciblé ---------------------------------------------------------------

def test_account_is_forwarded_by_both_tools(client):
    """Multi-compte : `account` doit atteindre la résolution de credentials, sinon
    on lirait/posterait depuis le mauvais compte Google."""
    client.list_spaces.return_value = []
    client.list_messages.return_value = []
    _tool("chat_spaces")(account="alexis@otomata.tech")
    _tool("chat_message")(space="spaces/A", account="alexis@otomata.tech")
    assert client.accounts == ["alexis@otomata.tech", "alexis@otomata.tech"]


# --- inventaire de surface ------------------------------------------------------

def test_the_product_exposes_exactly_two_tools(client):
    """3 → 2, zéro capacité perdue : `chat_spaces` (découverte, filtre `space_type`,
    aucun `space`) reste SEUL — ses params ne recouvrent pas ceux de `chat_message`."""
    from fastmcp import FastMCP
    from oto_mcp.tools import chat as C

    m = FastMCP("t")
    C.register(m)
    names = {t.name for t in asyncio.run(m.list_tools())}
    assert names == {"chat_spaces", "chat_message"}


def test_nothing_is_required_by_the_schema_so_op_gates_everything(client):
    """Aucun argument n'est exigé par la SIGNATURE : c'est le dispatch `op` qui
    porte les obligations. Un tool d'écriture sans champ requis n'est sûr que parce
    que son défaut est une lecture — les tests ci-dessus le figent."""
    from fastmcp import FastMCP
    from oto_mcp.tools import chat as C

    m = FastMCP("t")
    C.register(m)
    msg = asyncio.run(m.get_tool("chat_message")).parameters
    assert msg.get("required", []) == []
    assert msg["properties"]["op"]["default"] == "list"
