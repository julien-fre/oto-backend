"""Sélection marketplace (`connectors.*`) — projection compacte (#109) + guidage
d'activation (#111). Seams de domaine monkeypatchés (pas de DB)."""
import oto_mcp.capabilities.connectors_selection as CS
from oto_mcp.capabilities._types import ResolvedCtx


def _catalog(monkeypatch, entries):
    monkeypatch.setattr(CS, "_visible_catalog", lambda ctx: list(entries))
    monkeypatch.setattr(CS.connector_selection, "list_selection", lambda sub, org: {})
    monkeypatch.setattr(CS.org_store, "get_org_default_connectors", lambda org: [])
    monkeypatch.setattr(CS, "_doctrine_refs_by_ns", lambda org: {})


# ── #109 : projection compacte par défaut, plein sur verbose ──

_FAT = {"name": "serper", "label": "Serper", "help": "recherche web", "family": "api",
        "category": "Prospection", "availability": "self_serve", "logo_url": None,
        "namespaces": ["serper"], "doc_sections": [{"body_md": "x" * 5000}],
        "credential_fields": [{"name": "api_key"}], "auth": {"method": "secret"}}


def test_me_compact_by_default_drops_heavy_fields(monkeypatch):
    _catalog(monkeypatch, [_FAT])
    out = CS._me(ResolvedCtx(sub="u1", org_id=42), CS.MyConnectorsInput())
    c = out["connectors"][0]
    assert out["verbose"] is False
    assert c["name"] == "serper" and c["state"] == "not_selected"
    # Les gros champs ne sont PAS dans la vue compacte.
    for heavy in ("doc_sections", "credential_fields", "auth"):
        assert heavy not in c


def test_me_verbose_keeps_full_card(monkeypatch):
    _catalog(monkeypatch, [_FAT])
    out = CS._me(ResolvedCtx(sub="u1", org_id=42), CS.MyConnectorsInput(verbose=True))
    c = out["connectors"][0]
    assert out["verbose"] is True and c["doc_sections"] and c["auth"]["method"] == "secret"


def test_me_state_filter(monkeypatch):
    _catalog(monkeypatch, [_FAT, {**_FAT, "name": "hunter"}])
    monkeypatch.setattr(CS.connector_selection, "list_selection",
                        lambda sub, org: {"hunter": "active"})
    out = CS._me(ResolvedCtx(sub="u1", org_id=42), CS.MyConnectorsInput(state="active"))
    assert [c["name"] for c in out["connectors"]] == ["hunter"]


# ── #326 : filtre `name` (lecture d'état ciblée, plus d'échec silencieux) ──

def test_me_name_filter_returns_only_that_connector(monkeypatch):
    _catalog(monkeypatch, [_FAT, {**_FAT, "name": "hunter"}])
    out = CS._me(ResolvedCtx(sub="u1", org_id=42), CS.MyConnectorsInput(name="hunter"))
    assert [c["name"] for c in out["connectors"]] == ["hunter"]


def test_me_unknown_name_raises_instead_of_returning_everything(monkeypatch):
    """Le mode d'échec de #326 : `name` ignoré → catalogue entier (~30k tokens en
    verbose) sans warning. Une liste vide serait tout aussi muette → on lève."""
    import pytest
    from oto_mcp.capabilities._types import AuthzDenied
    _catalog(monkeypatch, [_FAT])
    with pytest.raises(AuthzDenied) as e:
        CS._me(ResolvedCtx(sub="u1", org_id=42), CS.MyConnectorsInput(name="zohodesk"))
    assert e.value.code == "unknown_connector"


# ── #111 : guidage d'activation (oto_call comme pont) ──

def test_select_returns_activation_hint(monkeypatch):
    monkeypatch.setattr(CS.connector_activation, "exposed_connectors", lambda org: {"unipile"})
    calls = []
    monkeypatch.setattr(CS.connector_selection, "set_state",
                        lambda sub, name, state, org: calls.append((sub, name, state, org)))
    # #186 : la réponse DONNE les noms d'outils (registre boot, immunisé visibilité).
    import oto_mcp.tool_registry as tr
    monkeypatch.setattr(tr, "boot_tool_names",
                        lambda: ["unipile_me", "unipile_search", "zoho_record"])
    out = CS._select(ResolvedCtx(sub="u1", org_id=42), CS.ConnectorActionInput(name="unipile"))
    assert out["connector"] == "unipile" and out["state"] == "active"
    assert out["tools"] == ["unipile_me", "unipile_search"]   # les siens, pas zoho
    assert "oto_call" in out["hint"] and "unipile_me" in out["hint"]
    assert calls and calls[0][1] == "unipile"


# ── bulk_select : activer un connecteur pour toute l'org, présent + futurs ──

def test_bulk_select_activates_unselected_members_and_persists_default(monkeypatch):
    monkeypatch.setattr(CS.connector_activation, "exposed_connectors", lambda org: {"unipile"})
    monkeypatch.setattr(CS.org_store, "list_org_members",
                        lambda org: [{"sub": "a"}, {"sub": "b"}, {"sub": "c"}])
    states = {"a": None, "b": "active", "c": "paused"}
    monkeypatch.setattr(CS.connector_selection, "state_of",
                        lambda sub, name, org: states[sub])
    calls = []
    monkeypatch.setattr(CS.connector_selection, "set_state",
                        lambda sub, name, state, org: calls.append((sub, name, state, org)))
    monkeypatch.setattr(CS.org_store, "get_org_default_connectors", lambda org: ["hunter"])
    set_defaults_calls = []
    monkeypatch.setattr(CS.org_store, "set_org_default_connectors",
                        lambda org, connectors: set_defaults_calls.append((org, connectors)))
    out = CS._bulk_select(ResolvedCtx(sub="admin", org_id=42), CS.BulkSelectInput(org_id=42, name="unipile"))
    # Seul "a" (jamais choisi) est activé — "b"/"c" ont déjà un choix explicite,
    # jamais réécrit (le paused de "c" en particulier doit survivre).
    assert out["activated"] == 1 and out["skipped"] == 2
    assert calls == [("a", "unipile", "active", 42)]
    # Persisté en défaut d'org (fusion additive, "hunter" préservé) pour que les
    # FUTURS membres le reçoivent pré-activé à leur premier seed.
    assert out["added_to_org_defaults"] is True
    assert set_defaults_calls == [(42, ["hunter", "unipile"])]


def test_bulk_select_does_not_rewrite_org_defaults_if_already_present(monkeypatch):
    monkeypatch.setattr(CS.connector_activation, "exposed_connectors", lambda org: {"unipile"})
    monkeypatch.setattr(CS.org_store, "list_org_members", lambda org: [])
    monkeypatch.setattr(CS.org_store, "get_org_default_connectors", lambda org: ["unipile"])
    set_defaults_calls = []
    monkeypatch.setattr(CS.org_store, "set_org_default_connectors",
                        lambda org, connectors: set_defaults_calls.append((org, connectors)))
    out = CS._bulk_select(ResolvedCtx(sub="admin", org_id=42), CS.BulkSelectInput(org_id=42, name="unipile"))
    assert out["added_to_org_defaults"] is False
    assert set_defaults_calls == []


def test_bulk_select_rejects_connector_not_exposed_to_org(monkeypatch):
    import pytest
    from oto_mcp.capabilities._types import AuthzDenied
    monkeypatch.setattr(CS.connector_activation, "exposed_connectors", lambda org: set())
    with pytest.raises(AuthzDenied) as e:
        CS._bulk_select(ResolvedCtx(sub="admin", org_id=42), CS.BulkSelectInput(org_id=42, name="unipile"))
    assert e.value.code == "org_disabled"


def test_bulk_select_rejects_unknown_connector():
    import pytest
    from oto_mcp.capabilities._types import AuthzDenied
    with pytest.raises(AuthzDenied) as e:
        CS._bulk_select(ResolvedCtx(sub="admin", org_id=42),
                        CS.BulkSelectInput(org_id=42, name="not-a-real-connector"))
    assert e.value.code == "unknown_connector"


# ── unset_default : symétrique soustractif, jamais de masquage catalogue ──

def test_unset_default_removes_only_the_named_connector(monkeypatch):
    monkeypatch.setattr(CS.org_store, "get_org_default_connectors",
                        lambda org: ["hunter", "unipile"])
    calls = []
    monkeypatch.setattr(CS.org_store, "set_org_default_connectors",
                        lambda org, connectors: calls.append((org, connectors)))
    out = CS._unset_default(ResolvedCtx(sub="admin", org_id=42),
                            CS.UnsetDefaultInput(org_id=42, name="unipile"))
    assert out == {"org_id": 42, "connector": "unipile", "removed": True}
    assert calls == [(42, ["hunter"])]


def test_unset_default_noop_if_not_present(monkeypatch):
    monkeypatch.setattr(CS.org_store, "get_org_default_connectors", lambda org: ["hunter"])
    calls = []
    monkeypatch.setattr(CS.org_store, "set_org_default_connectors",
                        lambda org, connectors: calls.append((org, connectors)))
    out = CS._unset_default(ResolvedCtx(sub="admin", org_id=42),
                            CS.UnsetDefaultInput(org_id=42, name="unipile"))
    assert out["removed"] is False
    assert calls == []
