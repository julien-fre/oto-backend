"""oto#42, entrée 11 du lot 1 — un credential RETIRÉ était indiscernable d'un
credential jamais posé.

`resolve.py` servait mot pour mot le même refus dans les deux cas
(« Aucun credential `{provider}` configuré pour toi »), alors que la base SAIT :
une instance retirée n'est pas supprimée, elle est marquée `revoked_at`/
`revoked_reason` (`connector_instances`). Quatre signalements le même jour
(03/09) pour cette seule cause — chacun a mené sa propre enquête pour retrouver
une info déjà en base.

`rbac._revoked_hint` ajoute le second indice, en lecture seule et fail-soft
(comme son voisin `reachable_team_key` : un hoquet DB ne doit jamais remplacer
un refus normal par une 500)."""
from __future__ import annotations

import pytest

from oto_mcp import access
from oto_mcp.access import rbac
from oto_mcp.mcp_errors import McpError


def test_revoked_hint_vide_sans_org():
    assert rbac._revoked_hint("u1", None, "zoho") == ""


def test_revoked_hint_vide_si_jamais_rien_existe(monkeypatch):
    monkeypatch.setattr(rbac.db, "most_recent_revocation", lambda *a, **k: None)
    assert rbac._revoked_hint("u1", 35, "zoho") == ""


def test_revoked_hint_dit_quand_et_pourquoi(monkeypatch):
    monkeypatch.setattr(rbac.db, "most_recent_revocation",
                        lambda owner_type, owner_id, connector: {
                            "revoked_at": "2026-08-20T10:00:00+00:00",
                            "revoked_reason": "credential_removed"})
    hint = rbac._revoked_hint("u1", 35, "zoho")
    assert "2026-08-20" in hint and "retiré" in hint and "clé retirée" in hint


def test_revoked_hint_interroge_le_bon_owner(monkeypatch):
    vu = {}
    monkeypatch.setattr(rbac.db, "most_recent_revocation",
                        lambda owner_type, owner_id, connector: vu.update(
                            owner_type=owner_type, owner_id=owner_id, connector=connector) or None)
    rbac._revoked_hint("u1", 35, "zoho")
    assert vu == {"owner_type": "member", "owner_id": "35:u1", "connector": "zoho"}


def test_revoked_hint_best_effort_on_db_error(monkeypatch):
    """Même contrôle que `test_reachable_team_key_best_effort_on_db_error` : un
    hoquet DB rend une chaîne vide, jamais une exception qui remplacerait le refus
    normal par une 500."""
    monkeypatch.setattr(rbac.db, "most_recent_revocation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert rbac._revoked_hint("u1", 35, "zoho") == ""


# --- intégration : le refus RÉEL de _resolve_credential_impl porte le hint -----

def _wire_no_credential(monkeypatch, *, org=35):
    monkeypatch.setattr(access, "require_connector_access", lambda *a, **k: None)
    monkeypatch.setattr(access.session_org, "current_call_instance", lambda: None)
    monkeypatch.setattr(access, "project_pinned_instance", lambda prov: None)
    monkeypatch.setattr(access, "current_org", lambda sub: org)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access.db, "get_member_api_key", lambda *a, **k: None)
    monkeypatch.setattr(access.credentials_store, "list_accounts", lambda *a, **k: [])
    monkeypatch.setattr(access.org_store, "get_org_secret", lambda oid, prov, account="": None)
    monkeypatch.setattr(access.group_store, "list_groups_for_user", lambda sub, org_id=None: [])
    monkeypatch.setattr(access.org_store, "list_orgs_for_user", lambda sub: [])


def test_le_refus_reel_nomme_la_revocation(monkeypatch):
    _wire_no_credential(monkeypatch)
    monkeypatch.setattr(rbac.db, "most_recent_revocation",
                        lambda owner_type, owner_id, connector: {
                            "revoked_at": "2026-08-20T10:00:00+00:00",
                            "revoked_reason": "credential_removed"})
    with pytest.raises(McpError) as e:
        access._resolve_credential_impl("zoho", "byo", "u1")
    assert "2026-08-20" in str(e.value) and "retiré" in str(e.value)


def test_le_refus_reel_reste_muet_si_jamais_rien_nexistait(monkeypatch):
    _wire_no_credential(monkeypatch)
    monkeypatch.setattr(rbac.db, "most_recent_revocation", lambda *a, **k: None)
    with pytest.raises(McpError) as e:
        access._resolve_credential_impl("zoho", "byo", "u1")
    assert "retiré" not in str(e.value)
