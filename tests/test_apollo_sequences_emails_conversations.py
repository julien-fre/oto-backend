"""Sequences / one-off emails / conversations — extension du connecteur Apollo
(client oto-core, tools oto-backend). Ce que ces tests figent :

1. Écriture BYO-ONLY (`access.resolve_credential(..., want="byo")`), jamais
   `resolve_api_key` (qui admet la clé plateforme) : un enrôlement ou un envoi
   sur la clé plateforme partirait depuis la boîte/les contacts de quelqu'un
   d'autre. Même verrou que Lightfield `send_email` (oto-core 97c53ce).
2. `dry_run` valide identiquement mais saute l'appel mutant final.
3. Une `op` inconnue est refusée AVANT toute résolution de clé.

Mock la CLASSE client (jamais `requests`) — cf. `tests/test_apollo_location_filters.py`.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from oto_mcp.mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS


def _mount(monkeypatch, client=None, *, byo_key="byo-key"):
    """Monte apollo.py sur un FastMCP nu, client mocké, résolution byo tracée.

    Renvoie `(get_tool, calls)` où `calls` note ce qui a été résolu
    (`api_key` = `resolve_api_key`/platform-eligible, `byo` = `resolve_credential`
    want=byo) — sert à vérifier qu'un tool d'écriture n'utilise JAMAIS le premier.
    """
    import oto.tools.apollo.client as apollo_client
    from fastmcp import FastMCP
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool

    client = client or MagicMock()
    calls: list[str] = []

    def _resolve_api_key(provider, *a, **k):
        calls.append("api_key")
        return ("platform-key", False)

    class _RC:
        def __init__(self, key):
            self.key = key

    def _resolve_credential(provider, want="auto", *a, **k):
        assert want == "byo", "un write Apollo doit résoudre want=byo, jamais auto/platform"
        calls.append("byo")
        return _RC(byo_key)

    monkeypatch.setattr(access, "resolve_api_key", _resolve_api_key)
    monkeypatch.setattr(access, "resolve_credential", _resolve_credential)
    monkeypatch.setattr(apollo_client, "ApolloClient", lambda **kw: client)

    m = FastMCP("t")
    apollo_tool.register(m)

    def get_tool(name):
        return asyncio.run(m.get_tool(name)).fn

    return get_tool, calls, client


# ----------------------------------------------------------------------
# _client_byo error message — Apollo is the first connector mixing platform-
# eligible tools (search/enrich) and byo-only tools in the SAME module: the
# generic "aucun credential configuré" would read as "apollo isn't set up at
# all" to a user who already sees apollo_search_organizations working via the
# platform key. The clarifying suffix must attach ONLY to that specific
# message — never to unrelated resolution errors (multi-account ambiguity,
# unknown account), which are already precise on their own.
# ----------------------------------------------------------------------

def test_byo_missing_credential_error_clarifies_the_platform_split(monkeypatch):
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool
    from fastmcp import FastMCP

    def _raise(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message=(
            "Aucun credential `apollo` configuré pour toi. Renseigne-le "
            "sur https://manage.oto.cx/account (section Apollo).")))

    monkeypatch.setattr(access, "resolve_credential", _raise)
    m = FastMCP("t")
    apollo_tool.register(m)
    fn = asyncio.run(m.get_tool("apollo_email_accounts")).fn

    with pytest.raises(McpError) as e:
        fn()
    assert "Aucun credential" in e.value.error.message
    assert "recherche" in e.value.error.message  # la clarification a été ajoutée


def test_byo_other_resolution_errors_pass_through_unchanged(monkeypatch):
    """Une ambiguïté multi-compte n'a rien à voir avec platform-vs-byo — le
    message ne doit PAS recevoir la clarification d'Apollo."""
    from oto_mcp import access
    from oto_mcp.tools import apollo as apollo_tool
    from fastmcp import FastMCP

    original_msg = ("Plusieurs comptes `apollo` configurés dans cette org, aucun "
                    "marqué par défaut — précise lequel.")

    def _raise(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message=original_msg))

    monkeypatch.setattr(access, "resolve_credential", _raise)
    m = FastMCP("t")
    apollo_tool.register(m)
    fn = asyncio.run(m.get_tool("apollo_email_accounts")).fn

    with pytest.raises(McpError) as e:
        fn()
    assert e.value.error.message == original_msg


# ----------------------------------------------------------------------
# Prerequisites — BYO-only: these list the KEY OWNER's own mailboxes/schedules,
# not Apollo's shared database, so a platform key must never resolve them.
# ----------------------------------------------------------------------

def test_email_accounts_and_schedules_resolve_byo_only(monkeypatch):
    client = MagicMock()
    client.list_email_accounts.return_value = {"email_accounts": []}
    client.list_email_schedules.return_value = {"emailer_schedules": []}
    get_tool, calls, _ = _mount(monkeypatch, client)

    get_tool("apollo_email_accounts")()
    get_tool("apollo_email_schedules")()
    assert calls == ["byo", "byo"]


# ----------------------------------------------------------------------
# apollo_sequence — byo-only on every op, including `search`: it lists the
# key owner's OWN sequences (names + open/reply rates), not a shared dataset.
# ----------------------------------------------------------------------

def test_sequence_search_resolves_byo_only(monkeypatch):
    client = MagicMock()
    client.search_sequences.return_value = {"emailer_campaigns": []}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_sequence")(op="search", name="Q3")
    assert calls == ["byo"]
    assert client.search_sequences.call_args.kwargs["name"] == "Q3"


def test_sequence_create_resolves_byo_only(monkeypatch):
    client = MagicMock()
    client.create_sequence.return_value = {"emailer_campaign": {}}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_sequence")(op="create", name="Q3", emailer_schedule_id="sched1")
    assert calls == ["byo"]
    assert client.create_sequence.call_args.kwargs["name"] == "Q3"


def test_sequence_create_requires_a_schedule_id(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_sequence")(op="create", name="Q3")
    assert not client.create_sequence.called


def test_sequence_create_dry_run_does_not_call_the_client(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    result = get_tool("apollo_sequence")(
        op="create", name="Q3", emailer_schedule_id="sched1", dry_run=True)
    assert result["dry_run"] is True
    assert not client.create_sequence.called
    # la résolution byo a quand même lieu (verrou existe même en dry_run)
    assert calls == ["byo"]


def test_sequence_archive_dry_run_skips_the_call(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    result = get_tool("apollo_sequence")(op="archive", sequence_id="seq1", dry_run=True)
    assert result["dry_run"] is True
    assert not client.archive_sequence.called


def test_sequence_unknown_op_is_refused_before_any_key_resolution(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_sequence")(op="delete", sequence_id="seq1")
    assert not calls


# ----------------------------------------------------------------------
# apollo_sequence_contacts — l'appel le plus à risque
# ----------------------------------------------------------------------

def test_sequence_contacts_add_resolves_byo_only(monkeypatch):
    client = MagicMock()
    client.add_contacts_to_sequence.return_value = {"contacts": []}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_sequence_contacts")(
        op="add", sequence_id="seq1", send_email_from_email_account_id="acc1",
        contact_ids=["c1"])
    assert calls == ["byo"]
    assert client.add_contacts_to_sequence.call_args.args[0] == "seq1"
    assert client.add_contacts_to_sequence.call_args.args[1] == "acc1"


def test_sequence_contacts_add_dry_run_refuses_without_mailbox(monkeypatch):
    """Le verrou boîte connectée est vérifié MÊME en dry_run — dry_run valide
    identiquement, il ne relâche aucune garde."""
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_sequence_contacts")(
            op="add", sequence_id="seq1", contact_ids=["c1"], dry_run=True)
    assert not client.add_contacts_to_sequence.called


def test_sequence_contacts_add_dry_run_skips_the_call(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    result = get_tool("apollo_sequence_contacts")(
        op="add", sequence_id="seq1", send_email_from_email_account_id="acc1",
        contact_ids=["c1"], dry_run=True)
    assert result["dry_run"] is True
    assert not client.add_contacts_to_sequence.called


def test_sequence_contacts_update_status_validates_mode(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_sequence_contacts")(
            op="update_status", emailer_campaign_ids=["s1"], contact_ids=["c1"],
            mode="delete")
    assert not client.update_sequence_contact_status.called


def test_sequence_contacts_activity_resolves_byo_only(monkeypatch):
    """Lecture seule, 0 crédit, mais BYO quand même : les événements sont ceux
    des séquences DU PROPRIÉTAIRE de la clé, pas une base partagée."""
    client = MagicMock()
    client.get_contact_sequence_activity.return_value = {"events": []}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_sequence_contacts")(op="activity", contact_id="c1")
    assert calls == ["byo"]


def test_sequence_contacts_unknown_op(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_sequence_contacts")(op="delete", sequence_id="s1")


# ----------------------------------------------------------------------
# apollo_email — draft/send byo-only, le reste platform-eligible
# ----------------------------------------------------------------------

def test_email_draft_resolves_byo_only(monkeypatch):
    client = MagicMock()
    client.create_email_draft.return_value = {"emailer_message": {}}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_email")(op="draft", contact_id="c1", subject="hi")
    assert calls == ["byo"]


def test_email_send_resolves_byo_only(monkeypatch):
    client = MagicMock()
    client.send_email_now.return_value = {"emailer_message": {}}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_email")(op="send", message_id="msg1")
    assert calls == ["byo"]
    assert client.send_email_now.call_args.args[0] == "msg1"


def test_email_send_dry_run_skips_the_call(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    result = get_tool("apollo_email")(op="send", message_id="msg1", dry_run=True)
    assert result["dry_run"] is True
    assert not client.send_email_now.called


def test_email_send_requires_a_message_id(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_email")(op="send")
    assert not client.send_email_now.called


def test_email_search_status_content_stats_resolve_byo_only(monkeypatch):
    """`search`/`content` rendent les emails ENVOYÉS PAR le propriétaire de la clé
    (corps inclus) — jamais la clé plateforme, ce serait la boîte de quelqu'un
    d'autre exposée à n'importe quel user."""
    client = MagicMock()
    client.search_emails.return_value = {"emailer_messages": []}
    client.check_email_send_status.return_value = {"status": "completed"}
    client.get_email_content.return_value = {"emailer_messages": []}
    client.get_email_stats.return_value = {"emailer_message": {}}
    get_tool, calls, _ = _mount(monkeypatch, client)

    get_tool("apollo_email")(op="search")
    get_tool("apollo_email")(op="status", message_id="m1")
    get_tool("apollo_email")(op="content", ids=["m1"])
    get_tool("apollo_email")(op="stats", message_id="m1")
    assert calls == ["byo"] * 4


def test_email_unknown_op(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_email")(op="forward", message_id="m1")


# ----------------------------------------------------------------------
# apollo_conversation — byo-only sur TOUS les ops
# ----------------------------------------------------------------------

@pytest.mark.parametrize("op, kwargs", [
    ("search", {}),
    ("get", {"conversation_id": "c1"}),
    ("export", {"start_time": "2024-01-01T00:00:00Z", "end_time": "2024-02-01T00:00:00Z",
                "email": "a@b.co"}),
    ("export_status", {"export_id": "exp1"}),
])
def test_conversation_every_op_resolves_byo_only(monkeypatch, op, kwargs):
    client = MagicMock()
    client.search_conversations.return_value = {"conversations": []}
    client.get_conversation.return_value = {"id": "c1"}
    client.export_conversations.return_value = {"export_id": "exp1"}
    client.get_conversations_export.return_value = {"redirect_url": "https://x"}
    get_tool, calls, _ = _mount(monkeypatch, client)
    get_tool("apollo_conversation")(op=op, **kwargs)
    assert calls == ["byo"], f"op={op} doit résoudre byo-only, pas platform-eligible"


def test_conversation_unknown_op(monkeypatch):
    client = MagicMock()
    get_tool, calls, _ = _mount(monkeypatch, client)
    with pytest.raises(McpError):
        get_tool("apollo_conversation")(op="delete")
