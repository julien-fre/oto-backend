"""oto-backend#409 — Slack sert plusieurs workspaces dans une même org.

Un token Slack (`xoxb-` comme `xoxp-`) est émis par INSTALLATION de l'app dans un
workspace : N installations = N tokens indépendants, et l'auth est par requête,
sans état. Rien côté fournisseur n'impose un workspace unique — et la lib
`oto.tools.slack` est déjà multi-workspace. Ce qui figeait la cardinalité à 1
était la couche résolution du backend, qui rangeait Slack hors des connecteurs
multi-compte au seul motif que son credential a deux champs (`bot_token` /
`user_token`) au lieu d'un.

On vérifie ici le bout qui compte pour l'appelant : deux workspaces posés, c'est
`_account=` qui choisit — et un credential posé AVANT le lot (ligne mono) résout
exactement comme avant.
"""
import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import access


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(access, "require_connector_access", lambda p, s=None: None)
    monkeypatch.setattr(access, "current_org", lambda sub: 1)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access, "project_pinned_identity", lambda prov: None)
    monkeypatch.setattr(access.org_store, "get_org_secret",
                        lambda oid, prov, account="": None)
    monkeypatch.setattr(access.db, "insert_tool_call", lambda payload: None)
    monkeypatch.setattr(access.db, "member_instance_suspended", lambda *a, **k: False)
    yield


def _workspaces(monkeypatch, tokens: dict):
    """`tokens` : compte → blob de credential (le pack multi-champs Slack)."""
    monkeypatch.setattr(access.credentials_store, "list_accounts",
                        lambda et, eid, con: [{"account": a} for a in tokens])
    monkeypatch.setattr(access.db, "get_member_api_key",
                        lambda sub, org, prov, account="": tokens.get(account))


def test_named_workspace_resolves(monkeypatch):
    _workspaces(monkeypatch, {"otomata": "T-OTOMATA", "client": "T-CLIENT"})
    rc = access.resolve_credential("slack", want="byo", sub="u1", account="client")
    assert rc.key == "T-CLIENT" and rc.account == "client"


def test_two_workspaces_without_a_name_is_an_actionable_error(monkeypatch):
    """Jamais de repli muet sur l'un des deux : agir sous le mauvais workspace
    est une usurpation, pas un défaut d'ergonomie."""
    _workspaces(monkeypatch, {"otomata": "T-OTOMATA", "client": "T-CLIENT"})
    with pytest.raises(McpError) as e:
        access.resolve_credential("slack", want="byo", sub="u1",
                                  emit_on_failure=False)
    # Pour la BONNE raison : l'ambiguïté entre deux workspaces, pas « aucun
    # credential configuré » (ce que rendait le chemin mono-compte). Et dans le
    # VOCABULAIRE de Slack — « plusieurs comptes » obligerait l'agent à traduire.
    msg = str(e.value)
    assert "Plusieurs workspaces `slack`" in msg
    # Le refus doit porter le geste qui débloque, avec le nom EXACT du jeton.
    assert '_account=' in msg and "oto_identity(op='list')" in msg


def test_single_workspace_resolves_without_a_name(monkeypatch):
    _workspaces(monkeypatch, {"otomata": "T-OTOMATA"})
    rc = access.resolve_credential("slack", want="byo", sub="u1")
    assert rc.key == "T-OTOMATA" and rc.account == "otomata"


def test_credential_posed_before_the_change_still_resolves(monkeypatch):
    """Non-régression : un Slack posé quand le connecteur était mono-compte vit
    sur la ligne anonyme — elle reste la clé d'aujourd'hui."""
    _workspaces(monkeypatch, {"": "T-LEGACY"})
    rc = access.resolve_credential("slack", want="byo", sub="u1")
    assert rc.key == "T-LEGACY" and rc.account == ""


def test_unknown_workspace_raises_instead_of_falling_back(monkeypatch):
    _workspaces(monkeypatch, {"otomata": "T-OTOMATA"})
    with pytest.raises(McpError) as e:
        access.resolve_credential("slack", want="byo", sub="u1",
                                  account="jamais-pose", emit_on_failure=False)
    assert "jamais-pose" in str(e.value) and "introuvable" in str(e.value)
