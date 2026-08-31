"""Dispatch `op=` de `gmail_message` (ADR 0047 §Amendement, appliqué au produit
gmail le 2026-08-11 : 7 tools → 3).

Ce que ce fichier verrouille, et que `test_gmail_attachments.py` ne couvrait PAS :
celui-ci exerce un helper pur (`_resolve_attachments`), aucun ne touchait la
SURFACE. Une consolidation par `op=` déplace précisément le risque là — et ici le
risque n'est pas cosmétique : **`archive` retire de la boîte, `trash` met à la
corbeille**. Une op mal câblée appellerait silencieusement la mauvaise méthode du
client sur la vraie boîte de l'utilisateur, et rien ne casserait au boot.

D'où, pour chaque op : la méthode client appelée, **et l'absence d'appel des
voisines dangereuses** ; le refus d'une op inconnue (avant même la résolution du
credential) ; les arguments obligatoires ; et le fait que le défaut est une
LECTURE — un appel sans `op` ne peut ni écrire ni supprimer.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
# Méthodes du GmailClient qui MUTENT la boîte : aucune ne doit être atteinte par
# une lecture, ni par une op inconnue, ni par un appel sans `op`.
_DESTRUCTIVE = ("archive_messages", "trash_message")


def _tool(name: str):
    from fastmcp import FastMCP
    from oto_mcp.tools import gmail as G

    m = FastMCP("t")
    G.register(m)
    return asyncio.run(m.get_tool(name)).fn


def _call(name: str, **kwargs):
    """Les tools gmail sont `async` (I/O Google en threadpool) → on les déroule."""
    return asyncio.run(_tool(name)(**kwargs))


@pytest.fixture
def client(monkeypatch):
    """Faux GmailClient + `sub` résolu. La factory est un MagicMock pour pouvoir
    prouver qu'une op refusée n'atteint JAMAIS la résolution de credential."""
    from oto_mcp.tools import gmail as G

    inst = MagicMock()
    factory = MagicMock(return_value=inst)
    monkeypatch.setattr(G, "_client_for_user", factory)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    inst._factory = factory
    return inst


# --- routage op → méthode client ----------------------------------------------

@pytest.mark.parametrize("op,kwargs,method", [
    ("search", {"query": "is:unread"}, "search"),
    ("get", {"message_id": "m1"}, "get_message"),
    ("attachment", {"message_id": "m1", "filename": "Contrat.pdf"}, "get_attachment"),
    ("drafts", {}, "list_drafts"),
    ("archive", {"message_ids": ["m1"]}, "archive_messages"),
    ("trash", {"message_ids": ["m1"]}, "trash_message"),
])
def test_ops_route_to_the_right_client_method(client, monkeypatch, op, kwargs, method):
    if op == "attachment":
        _stub_attachment(client, monkeypatch)
    _call("gmail_message", op=op, **kwargs)
    getattr(client, method).assert_called_once()


@pytest.mark.parametrize("op,kwargs", [
    ("search", {"query": "is:unread"}),
    ("get", {"message_id": "m1"}),
    ("attachment", {"message_id": "m1", "filename": "Contrat.pdf"}),
    ("drafts", {}),
])
def test_read_ops_never_touch_a_destructive_method(client, monkeypatch, op, kwargs):
    """Le cœur du filet : une LECTURE ne doit atteindre aucune méthode qui mute la
    boîte. Un `op='attachment'` qui tomberait sur `trash_message` est un incident,
    pas un bug d'affichage."""
    if op == "attachment":
        _stub_attachment(client, monkeypatch)
    _call("gmail_message", op=op, **kwargs)
    for m in _DESTRUCTIVE:
        getattr(client, m).assert_not_called()


# --- lectures : forme de réponse et arguments passés --------------------------

def test_search_passes_query_and_max_results_and_counts(client):
    client.search.return_value = [{"id": "a"}, {"id": "b"}]
    out = _call("gmail_message", op="search", query="from:x is:unread", max_results=5)
    assert client.search.call_args.args == ("from:x is:unread", 5)
    assert out == {"messages": [{"id": "a"}, {"id": "b"}], "count": 2}


def test_get_returns_the_raw_message(client):
    client.get_message.return_value = {"id": "m1", "subject": "Devis"}
    assert _call("gmail_message", op="get", message_id="m1") == {"id": "m1",
                                                                 "subject": "Devis"}


def test_drafts_returns_drafts_and_count(client):
    client.list_drafts.return_value = [{"id": "d1"}]
    out = _call("gmail_message", op="drafts", max_results=7)
    assert client.list_drafts.call_args.args == (7,)
    assert out == {"drafts": [{"id": "d1"}], "count": 1}


def _stub_attachment(client, monkeypatch):
    """`op='attachment'` traverse le rendu inline-vs-URL (S3) : on le stubbe pour
    n'observer QUE le dispatch."""
    from oto_mcp.tools import gmail as G

    client.get_attachment.return_value = {
        "data": b"PDF", "filename": "Contrat.pdf", "mimeType": "application/pdf"}
    monkeypatch.setattr(G.file_content, "render_for_agent",
                        lambda *a, **k: {"encoding": "url", "url": "https://signed"})


def test_attachment_passes_filename_and_index_tiebreaker(client, monkeypatch):
    """`index` départage plusieurs pièces jointes de MÊME nom (images inline) —
    il doit arriver au client, pas être avalé par le dispatch."""
    _stub_attachment(client, monkeypatch)
    out = _call("gmail_message", op="attachment", message_id="m1",
                filename="Contrat.pdf", index=2)
    assert client.get_attachment.call_args.args == ("m1", "Contrat.pdf", 2)
    assert out["encoding"] == "url"


def test_attachment_media_unavailable_is_actionable(client, monkeypatch):
    """S3 absent → erreur de tool actionnable, jamais une 500 opaque."""
    from oto_mcp.tools import gmail as G

    _stub_attachment(client, monkeypatch)

    def _boom(*a, **k):
        raise G.file_content.MediaUnavailable("stockage temporaire indisponible")
    monkeypatch.setattr(G.file_content, "render_for_agent", _boom)
    with pytest.raises(McpError, match="stockage temporaire"):
        _call("gmail_message", op="attachment", message_id="m1", filename="a.pdf")


# --- écritures : un cas par op, et les voisines muettes ------------------------

def test_archive_calls_archive_only_and_never_trashes(client):
    """`archive` = retirer le label INBOX. Si cette op atteignait `trash_message`,
    l'utilisateur retrouverait ses messages à la corbeille : cas dédié."""
    client.archive_messages.return_value = [{"id": "m1"}]
    out = _call("gmail_message", op="archive", message_ids=["m1", "m2"])
    assert client.archive_messages.call_args.args == (["m1", "m2"],)
    assert out == {"archived": [{"id": "m1"}]}
    client.trash_message.assert_not_called()
    client.search.assert_not_called()
    client.get_message.assert_not_called()


def test_trash_calls_trash_per_message_and_never_archives(client):
    """`trash` = mise à la corbeille, message par message (pas de batch amont).
    Aucune autre méthode mutante ne doit partir au passage."""
    client.trash_message.side_effect = [{"id": "m1"}, {"id": "m2"}]
    out = _call("gmail_message", op="trash", message_ids=["m1", "m2"])
    assert [c.args[0] for c in client.trash_message.call_args_list] == ["m1", "m2"]
    assert out == {"trashed": ["m1", "m2"]}
    client.archive_messages.assert_not_called()


def test_trash_falls_back_to_the_requested_id_when_the_api_omits_it(client):
    client.trash_message.return_value = {}
    assert _call("gmail_message", op="trash", message_ids=["m9"]) == {"trashed": ["m9"]}


@pytest.mark.parametrize("op", ["archive", "trash"])
def test_write_ops_refuse_an_empty_id_list(client, op):
    """`message_ids=[]` ne doit PAS rendre un succès vide : un no-op silencieux
    laisserait croire à l'agent que l'action a eu lieu."""
    with pytest.raises(McpError, match="message_ids"):
        _call("gmail_message", op=op, message_ids=[])
    with pytest.raises(McpError, match="message_ids"):
        _call("gmail_message", op=op)
    for m in _DESTRUCTIVE:
        getattr(client, m).assert_not_called()


# --- le défaut est une LECTURE ------------------------------------------------

def test_default_op_is_a_read_and_writes_nothing(client):
    """Un appel sans `op` ne doit ni écrire ni supprimer : le défaut est
    `op='search'`, et sans `query` il refuse plutôt que d'agir."""
    with pytest.raises(McpError, match="query"):
        _call("gmail_message")
    for m in _DESTRUCTIVE:
        getattr(client, m).assert_not_called()


def test_the_declared_default_op_is_declared_as_a_read():
    """Contrat lisible dans le module, pas seulement dans ce test : le défaut du
    paramètre `op` doit appartenir aux ops de LECTURE."""
    import inspect

    from fastmcp import FastMCP
    from oto_mcp.tools import gmail as G

    m = FastMCP("t")
    G.register(m)
    default = inspect.signature(asyncio.run(m.get_tool("gmail_message")).fn) \
        .parameters["op"].default
    assert default in G._MESSAGE_READ_OPS
    assert set(G._MESSAGE_WRITE_OPS) == {"archive", "trash"}
    assert set(_DESTRUCTIVE) <= set(dir(_pinned_client_class()))


def _pinned_client_class():
    """La vraie classe oto-core épinglée : les méthodes mutantes surveillées ici
    doivent exister dessus, sinon ce filet garderait un nom mort."""
    from oto.tools.google.gmail.lib.gmail_client import GmailClient
    return GmailClient


def test_default_op_dispatches_to_search(client):
    _call("gmail_message", query="is:unread")
    client.search.assert_called_once()
    for m in _DESTRUCTIVE:
        getattr(client, m).assert_not_called()


# --- refus ---------------------------------------------------------------------

def test_unknown_op_is_refused_with_the_allowed_list(client):
    """Une op inconnue doit lever en NOMMANT les ops valides — jamais retomber
    silencieusement sur le défaut (l'agent croirait sa demande honorée)."""
    with pytest.raises(McpError, match="op doit être"):
        _call("gmail_message", op="nope")
    with pytest.raises(McpError, match=r"'search'.*'archive'.*'trash'"):
        _call("gmail_message", op="delete")


def test_unknown_op_never_reaches_the_client_nor_the_credential(client):
    """La garde est AVANT `_client_for_user` : une op inconnue ne déchiffre même
    pas le credential Google, donc aucun chemin dérivé ne peut agir."""
    with pytest.raises(McpError, match="op doit être"):
        _call("gmail_message", op="metadata", message_ids=["m1"])
    client._factory.assert_not_called()
    assert not client.method_calls


@pytest.mark.parametrize("op,kwargs,missing", [
    ("search", {}, "query"),
    ("get", {}, "message_id"),
    ("attachment", {}, "message_id"),
    ("attachment", {"message_id": "m1"}, "filename"),
    ("archive", {}, "message_ids"),
    ("trash", {}, "message_ids"),
])
def test_missing_required_arg_names_the_op_and_the_arg(client, op, kwargs, missing):
    with pytest.raises(McpError, match=missing) as e:
        _call("gmail_message", op=op, **kwargs)
    assert f"op='{op}'" in str(e.value)


def test_empty_query_is_refused(client):
    """`query=""` ratisserait la boîte entière : une valeur vide compte comme
    absente, pas comme « tout »."""
    with pytest.raises(McpError, match="query"):
        _call("gmail_message", op="search", query="")
    client.search.assert_not_called()


# --- la surface elle-même ------------------------------------------------------

def _tool_names():
    from fastmcp import FastMCP
    from oto_mcp.tools import gmail as G

    m = FastMCP("t")
    G.register(m)
    return {t.name for t in asyncio.run(m.list_tools())}


def test_surface_is_the_consolidated_one():
    """7 → 3. Rupture assumée sans alias (ADR 0047) : les anciens noms ne
    répondent plus, ils n'ont pas été gardés en doublon silencieux."""
    assert _tool_names() == {"gmail_list_accounts", "gmail_message", "gmail_compose"}


def test_list_accounts_stays_alone_because_it_has_no_parameter():
    """Découverte pure : `gmail_list_accounts` ne prend AUCUN paramètre (il énumère
    les `account` que les autres consomment). Le fusionner n'homogénéiserait rien —
    même arbitrage que `zoho_modules`."""
    from fastmcp import FastMCP
    from oto_mcp.tools import gmail as G

    m = FastMCP("t")
    G.register(m)
    schema = asyncio.run(m.get_tool("gmail_list_accounts")).parameters
    assert not (schema.get("properties") or {})


def test_compose_stays_alone_with_its_disjoint_parameters(client):
    """`gmail_compose` garde ses paramètres de rédaction, qui ne recouvrent aucun
    de ceux de `gmail_message` — variante disjointe, laissée seule (sur-fusionner
    est pire que sous-fusionner). Son axe reste `mode`, validé strictement."""
    from fastmcp import FastMCP
    from oto_mcp.tools import gmail as G

    m = FastMCP("t")
    G.register(m)
    params = set(asyncio.run(m.get_tool("gmail_compose")).parameters["properties"])
    msg_params = set(asyncio.run(m.get_tool("gmail_message")).parameters["properties"])
    assert params & msg_params == {"account"}

    with pytest.raises(McpError, match="mode doit être"):
        asyncio.run(_tool("gmail_compose")(body="hi", mode="nope", to="a@b.c"))
    client.send.assert_not_called()
    client.create_draft.assert_not_called()


@pytest.mark.parametrize("mode,method", [("send", "send"), ("draft", "create_draft")])
def test_compose_modes_route_to_the_right_client_method(client, mode, method):
    """Envoi RÉEL vs brouillon : deux méthodes distinctes, et l'autre reste muette."""
    _call("gmail_compose", body="hi", to="a@b.c", subject="s", mode=mode)
    getattr(client, method).assert_called_once()
    other = "create_draft" if method == "send" else "send"
    getattr(client, other).assert_not_called()


@pytest.mark.parametrize("mode,method", [("send", "reply"), ("draft", "create_draft_reply")])
def test_compose_reply_never_starts_a_new_thread(client, mode, method):
    """Une réponse reste DANS son fil — dans les deux modes.

    Le test passait `mode` implicitement et figeait donc le défaut d'alors (`send`).
    Depuis que le défaut est `draft` (#345 ④, l'oubli d'un paramètre ne doit pas expédier
    un mail dehors), un test qui repose sur le défaut ne prouve plus ce qu'il annonce :
    il aurait suivi le changement au lieu de le contrôler. Les deux modes sont donc
    exercés explicitement — la propriété gardée est le FIL, pas le défaut.
    """
    _call("gmail_compose", body="hi", reply_to="m1", mode=mode)
    getattr(client, method).assert_called_once()
    # Aucun chemin « nouveau message » : c'est ce qui casserait le fil.
    client.send.assert_not_called()
    client.create_draft.assert_not_called()


def test_le_defaut_d_une_reponse_est_lui_aussi_un_brouillon(client):
    _call("gmail_compose", body="hi", reply_to="m1")
    client.create_draft_reply.assert_called_once()
    client.reply.assert_not_called()


def test_compose_refuses_a_new_message_without_a_recipient(client):
    with pytest.raises(McpError, match="`to` requis"):
        _call("gmail_compose", body="hi")
    client.send.assert_not_called()


# --- une écriture PARTIELLE ne se raconte pas comme un échec total ------------
#
# Signal #227 (16/07) : « `gmail_modify` (action=trash sur un draft) : aucun
# retour pendant 300 s → abort côté client, **alors que l'action a bien été
# exécutée** ». L'enquête du 28/08 a écarté la cause telle qu'elle est décrite :
# `gmail_modify` n'existe plus (consolidé en `gmail_message`, ADR 0047), ses 10
# appels journalisés plafonnent à 661 ms, aucun tool `gmail_*` n'a jamais dépassé
# 8,7 s sur 399 appels, et chaque appel client part déjà en `asyncio.to_thread`
# (donc hors de la boucle). Rien ici ne pouvait pendre 300 s.
#
# Reste le défaut DURABLE que le signal décrit bien, lui, et qui vit toujours :
# une écriture appliquée dont l'appelant n'apprend rien. `op="trash"` boucle sur
# `message_ids` ; si le 3ᵉ échoue, les deux premiers SONT à la corbeille et
# l'agent ne voit qu'un refus nu. Il conclut « rien n'est parti » et rejoue —
# le scénario exact de #227, et la même faute que #600 (annoncer un échec sur
# un succès).

def test_une_corbeille_partielle_dit_ce_qui_est_deja_parti(client):
    """Le refus doit nommer les messages DÉJÀ mis à la corbeille : sans ça,
    l'agent croit à un échec total et rejoue une écriture déjà faite (#227)."""
    faits: list = []

    def _trash(mid):
        if mid == "m3":
            raise RuntimeError("Gmail 503")
        faits.append(mid)
        return {"id": mid}

    client.trash_message.side_effect = _trash
    with pytest.raises(McpError) as e:
        _call("gmail_message", op="trash", message_ids=["m1", "m2", "m3", "m4"])
    msg = str(e.value)
    assert faits == ["m1", "m2"], "on s'arrête au premier échec, sans rejouer"
    assert "m1" in msg and "m2" in msg, "ce qui est DÉJÀ à la corbeille est nommé"
    assert "m3" in msg, "celui qui a échoué aussi"
    assert "m4" in msg, "et ceux qui n'ont pas été tentés"
    assert "Gmail 503" in msg, "et la panne d'origine"


def test_une_corbeille_entierement_reussie_ne_change_pas_de_forme(client):
    """Le chemin nominal garde son contrat : `{'trashed': [...]}` — la
    correction de #227 ne doit se voir que sur l'échec partiel."""
    client.trash_message.side_effect = lambda mid: {"id": mid}
    assert _call("gmail_message", op="trash", message_ids=["m1", "m2"]) == \
        {"trashed": ["m1", "m2"]}
