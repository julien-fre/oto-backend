"""La dimension « N comptes par connecteur » est la même pour tous les connecteurs
multi-compte : renommer un compte, la section de doc qui l'explique, et le refus
d'ambiguïté qui nomme les comptes. Rien ici n'est propre à un fournisseur."""
import pytest

from oto_mcp import access, credentials_store, providers
from oto_mcp.access import cascade
from oto_mcp.capabilities.connectors import identities as cap
from oto_mcp.capabilities._types import AuthzDenied
from oto_mcp.connectors import identities as ci
from oto_mcp.mcp_errors import McpError


def _coffre(monkeypatch, comptes, org=1):
    monkeypatch.setattr(access, "current_org", lambda sub: org)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(credentials_store, "member_id", lambda o, sub: f"{o}:{sub}")
    monkeypatch.setattr(credentials_store, "list_accounts",
                        lambda et, eid, con: [dict(c) for c in comptes])
    renames = []
    monkeypatch.setattr(credentials_store, "rename_account",
                        lambda et, eid, con, old, new: renames.append((et, eid, con, old, new)))
    return renames


# ── Renommer ─────────────────────────────────────────────────────────────────

def test_rename_moves_the_vault_row_and_keeps_default(monkeypatch):
    renames = _coffre(monkeypatch, [{"account": "principal", "meta": {"is_default": True}},
                                    {"account": "b", "meta": {}}])
    res = ci.rename_identity("u", "zoho", "principal", "  filiale-a ", "org")
    assert renames == [("org", "1", "zoho", "principal", "filiale-a")]
    assert res == {"id": "filiale-a", "is_default": True}


def test_rename_refuses_a_taken_name(monkeypatch):
    # rename_account fait un upsert : laisser passer écraserait la clé d'arrivée.
    renames = _coffre(monkeypatch, [{"account": "a", "meta": {}}, {"account": "b", "meta": {}}])
    with pytest.raises(ValueError, match="existe déjà"):
        ci.rename_identity("u", "zoho", "a", "b")
    assert renames == []


@pytest.mark.parametrize("ident,nom,motif", [
    ("absent", "x", "inconnu"),
    ("a", "   ", "vide"),
])
def test_rename_refuses_unknown_or_empty(monkeypatch, ident, nom, motif):
    _coffre(monkeypatch, [{"account": "a", "meta": {}}])
    with pytest.raises(ValueError, match=motif):
        ci.rename_identity("u", "zoho", ident, nom)


def test_rename_same_name_is_a_noop(monkeypatch):
    renames = _coffre(monkeypatch, [{"account": "a", "meta": {}}])
    assert ci.rename_identity("u", "zoho", "a", "a")["id"] == "a"
    assert renames == []


def test_rename_refused_on_connector_without_vault_accounts():
    # google/unipile ont leurs propres backends : pas de lignes du coffre à renommer.
    with pytest.raises(ValueError, match="renommables"):
        ci.rename_identity("u", "google", "x@y", "z")


@pytest.mark.asyncio
async def test_rename_capability_maps_refusal_to_400(monkeypatch):
    _coffre(monkeypatch, [{"account": "a", "meta": {}}, {"account": "b", "meta": {}}])
    ctx = type("Ctx", (), {"sub": "u", "org_id": 1})()
    with pytest.raises(AuthzDenied) as e:
        await cap._rename(ctx, cap.RenameIdentityInput(
            connector="zoho", identity_id="a", name="b"))
    assert e.value.status == 400


@pytest.mark.asyncio
async def test_rename_capability_requires_org_admin_for_org_scope(monkeypatch):
    from oto_mcp import roles
    _coffre(monkeypatch, [{"account": "a", "meta": {}}])
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: False)
    ctx = type("Ctx", (), {"sub": "u", "org_id": 1})()
    with pytest.raises(AuthzDenied) as e:
        await cap._rename(ctx, cap.RenameIdentityInput(
            connector="zoho", identity_id="a", name="c", scope="org"))
    assert e.value.status == 403


# ── La doc générée ───────────────────────────────────────────────────────────

def test_every_multi_account_connector_documents_its_accounts():
    multi = [c for c in providers._REGISTRY_LIST if c.auth_multi_account]
    assert len(multi) > 20
    for con in multi:
        noun = con.account_noun or "compte"
        titres = [s.title for s in con.doc_sections]
        assert f"plusieurs {noun}s" in titres, con.name
        corps = next(s.body_md for s in con.doc_sections if s.title == f"plusieurs {noun}s")
        assert f"connector='{con.name}'" in corps and "_account" in corps


def test_single_account_connector_has_no_multi_account_section():
    mono = [c for c in providers._REGISTRY_LIST if not c.auth_multi_account]
    assert mono
    for con in mono:
        assert not any(s.title.startswith("plusieurs ") and "_account" in s.body_md
                       for s in con.doc_sections), con.name


# ── Le refus d'ambiguïté ─────────────────────────────────────────────────────

def test_ambiguity_names_the_accounts_without_gender_agreement(monkeypatch):
    monkeypatch.setattr(credentials_store, "list_accounts", lambda et, eid, con: [
        {"account": "alpha", "meta": {}}, {"account": "beta", "meta": {}}])
    monkeypatch.setattr(cascade, "account_noun", lambda p: "société")
    with pytest.raises(McpError) as e:
        cascade._shared_auto_account("org", "1", "zoho", "pour ton org", scope="org")
    msg = e.value.error.message
    assert msg.startswith("Plusieurs sociétés `zoho` pour ton org (`alpha`, `beta`)")
    assert "configurés" not in msg and "marqué" not in msg
    assert "_account=" in msg and "scope='org'" in msg
