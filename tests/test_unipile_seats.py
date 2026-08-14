"""Sièges de la clé plateforme unipile : l'inventaire et l'acte de LIBÉRATION.

Un siège se paie tant qu'il existe chez unipile — le soft-disconnect côté oto ne le
rend pas. La vue doit donc distinguer TROIS états (en service · déconnecté · orphelin),
et la libération refuser le premier : couper la messagerie de quelqu'un qui s'en sert
n'est pas du ménage. On exerce les handlers de capacité (`capabilities/unipile_seats.py`)
et la console MCP qui les réutilise — pas de DB, pas de HTTP réel.
"""
import asyncio

import pytest

from oto_mcp.capabilities import admin_console, unipile_seats as us
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="admin")


class FakeClient:
    """Client unipile de test : enregistre les suppressions demandées."""
    dsn = "api.unipile.com"

    def __init__(self, accounts, deleted):
        self._accounts = accounts
        self.deleted = deleted

    def list_accounts(self):
        return self._accounts

    def delete_account(self, account_id):
        self.deleted.append(account_id)


def _row(account_id, *, sub="u1", email="u1@x.io", org_id=7, org_name="Org7",
         connected_at="2026-07-01 10:00:00", disconnected_at=None):
    return {"account_id": account_id, "provider": "LINKEDIN", "account_name": "N",
            "sub": sub, "email": email, "org_id": org_id, "org_name": org_name,
            "connected_at": connected_at, "disconnected_at": disconnected_at,
            "platform_seat": True}


def _setup(monkeypatch, *, accounts, rows, has_key=True):
    seen = {"include_disconnected": None}

    def owners(include_disconnected=False):
        seen["include_disconnected"] = include_disconnected
        return rows if include_disconnected else [r for r in rows
                                                  if r["disconnected_at"] is None]

    monkeypatch.setattr(us.db, "unipile_account_owners", owners)
    deleted: list[str] = []
    monkeypatch.setattr(us, "_platform_client",
                        lambda: (FakeClient(accounts, deleted) if has_key else None))
    return deleted, seen


def test_inventaire_classe_les_trois_etats(monkeypatch):
    """En service / déconnecté / orphelin — et un déconnecté NOMME encore son
    propriétaire (c'est à lui qu'on écrit avant de libérer)."""
    accounts = [
        {"id": "acc_live", "name": "Vivant", "provider": "linkedin", "status": "running"},
        {"id": "acc_off", "name": "Déconnecté", "provider": "linkedin", "status": "running"},
        {"id": "acc_none", "name": "Personne", "provider": "linkedin", "status": "running"},
    ]
    rows = [
        _row("acc_live"),
        _row("acc_off", sub="u2", email="u2@x.io", disconnected_at="2026-08-01 09:00:00"),
    ]
    _, seen = _setup(monkeypatch, accounts=accounts, rows=rows)
    out = asyncio.run(us._list_seats(CTX, us.SeatsListInput()))

    # La vue lit les bindings MORTS aussi — sans quoi acc_off passerait pour orphelin.
    assert seen["include_disconnected"] is True
    by = {s["account_id"]: s for s in out["seats"]}
    assert by["acc_live"]["state"] == "bound"
    assert by["acc_off"]["state"] == "disconnected"
    assert by["acc_off"]["owner_email"] == "u2@x.io"
    assert by["acc_none"]["state"] == "orphan"
    assert by["acc_none"]["owner_email"] is None
    # `orphan` ne compte que les sièges que PERSONNE ne réclame ; `reclaimable` chiffre
    # l'économie réelle (déconnectés compris).
    assert out["orphan_count"] == 1
    assert out["reclaimable_count"] == 2
    # v2 sert `provider` : la colonne canal ne doit plus être vide.
    assert by["acc_live"]["provider"] == "linkedin" and by["acc_live"]["type"] == "linkedin"


def test_liberer_refuse_un_siege_en_service(monkeypatch):
    """Le garde-fou MORD : un siège avec binding vivant n'est pas supprimé."""
    deleted, _ = _setup(monkeypatch, accounts=[{"id": "acc_live"}], rows=[_row("acc_live")])
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(us._release_seat(CTX, us.SeatReleaseInput(account_id="acc_live")))
    assert (e.value.status, e.value.code) == (409, "seat_in_use")
    assert deleted == []


def test_liberer_un_siege_deconnecte(monkeypatch):
    """Cas d'usage principal : le propriétaire a déconnecté côté oto, le siège courait
    toujours chez unipile."""
    rows = [_row("acc_off", disconnected_at="2026-08-01 09:00:00")]
    deleted, _ = _setup(monkeypatch, accounts=[{"id": "acc_off"}], rows=rows)
    out = asyncio.run(us._release_seat(CTX, us.SeatReleaseInput(account_id="acc_off")))
    assert out["was"] == "disconnected"
    assert deleted == ["acc_off"]


def test_liberer_un_orphelin(monkeypatch):
    deleted, _ = _setup(monkeypatch, accounts=[{"id": "acc_none"}], rows=[])
    out = asyncio.run(us._release_seat(CTX, us.SeatReleaseInput(account_id="acc_none")))
    assert out["was"] == "orphan"
    assert deleted == ["acc_none"]


def test_liberer_sans_cle_plateforme(monkeypatch):
    deleted, _ = _setup(monkeypatch, accounts=[], rows=[], has_key=False)
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(us._release_seat(CTX, us.SeatReleaseInput(account_id="acc_none")))
    assert (e.value.status, e.value.code) == (400, "no_platform_key")
    assert deleted == []


def test_inventaire_sans_cle_plateforme(monkeypatch):
    """Pas de clé = pas de carte affichée, pas une erreur."""
    _setup(monkeypatch, accounts=[], rows=[], has_key=False)
    out = asyncio.run(us._list_seats(CTX, us.SeatsListInput()))
    assert out == {"configured": False, "instance_dsn": None, "seats": [],
                   "orphan_count": 0, "reclaimable_count": 0}


def test_console_mcp_route_les_deux_verbes(monkeypatch):
    """`oto_admin_unipile_seat` réutilise les handlers — pas une seconde implémentation."""
    rows = [_row("acc_off", disconnected_at="2026-08-01 09:00:00")]
    deleted, _ = _setup(monkeypatch, accounts=[{"id": "acc_off"}], rows=rows)

    listed = asyncio.run(admin_console._unipile_seat(
        CTX, admin_console.UnipileSeatAdminInput(op="list")))
    assert listed["reclaimable_count"] == 1

    released = asyncio.run(admin_console._unipile_seat(
        CTX, admin_console.UnipileSeatAdminInput(op="release", account_id="acc_off")))
    assert released["ok"] is True and deleted == ["acc_off"]


def test_console_mcp_release_exige_un_compte(monkeypatch):
    _setup(monkeypatch, accounts=[], rows=[])
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(admin_console._unipile_seat(
            CTX, admin_console.UnipileSeatAdminInput(op="release")))
    assert e.value.code == "missing_account_id"
