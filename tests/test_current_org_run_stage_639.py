"""#639 — l'étage « org du run » du seam `current_org`, et la garde qui le pose.

Le seam résout `jeton d'appel ?? org du run ?? consultation ?? maison` (ADR 0023/0038,
amendées le 30/08/2026). L'org du run n'est PAS relue par le seam : le middleware la
pose une fois par appel (`run_org.pin_for_call`), après les axes — donc seulement quand
aucun `_org=`/`_project=` n'a déjà posé l'org — et après garde d'appartenance. Ici, sans
base : l'ordre du seam, l'invariant groupe ⊂ org, et les six sorties de la pose.
"""
from __future__ import annotations

import uuid

import pytest
from mcp.shared.exceptions import McpError

from oto_mcp import access, call_axes, db, group_store, org_store, roles, session_org


@pytest.fixture(autouse=True)
def _maison(monkeypatch):
    monkeypatch.setattr(org_store, "get_active_org", lambda sub: 2)
    monkeypatch.setattr(session_org, "current_subdomain_candidate", lambda: None)
    monkeypatch.setattr(group_store, "get_active_group", lambda sub: None)


def _pose(fn, *args):
    """Pose une ContextVar et rend la fonction qui la retire."""
    tok = fn(*args)
    reset = getattr(session_org, fn.__name__.replace("set_", "reset_"))
    return lambda: reset(tok)


# ── l'ordre du seam ───────────────────────────────────────────────────────────

def test_l_org_du_run_passe_avant_la_maison():
    undo = _pose(session_org.set_call_run_org, 226)
    try:
        assert access.current_org("u") == 226
    finally:
        undo()
    assert access.current_org("u") == 2


def test_le_jeton_org_explicite_prime_sur_l_org_du_run():
    u1 = _pose(session_org.set_call_run_org, 226)
    u2 = _pose(session_org.set_call_org, 255)
    try:
        assert access.current_org("u") == 255
    finally:
        u2(); u1()


def test_le_groupe_maison_d_une_autre_org_n_est_pas_rendu_sous_l_org_du_run(monkeypatch):
    """Invariant groupe ⊂ org : sous l'org du run, le home_group d'une AUTRE org
    n'est pas rendu (niveau org) — même règle que sous un jeton `_org=`."""
    monkeypatch.setattr(group_store, "get_active_group", lambda sub: 7)
    monkeypatch.setattr(group_store, "get_group", lambda gid: {"id": 7, "org_id": 2})
    undo = _pose(session_org.set_call_run_org, 226)
    try:
        assert access.current_group("u") is None
    finally:
        undo()
    undo = _pose(session_org.set_call_run_org, 2)
    try:
        assert access.current_group("u") == 7
    finally:
        undo()


# ── la pose (`run_org.pin_for_call`) ──────────────────────────────────────────

@pytest.fixture
def garde(monkeypatch):
    from oto_mcp import run_org
    monkeypatch.setattr(call_axes, "current_user_sub_from_token", lambda: "u")
    monkeypatch.setattr(org_store, "get_org", lambda oid: {"id": oid, "name": "Travail"})
    return run_org


def _run_de(monkeypatch, org_id, *, membre=True):
    run = uuid.uuid4().hex
    monkeypatch.setattr(db, "get_run_head",
                        lambda r: {"sub": "u", "org_id": org_id} if r == run else None)
    monkeypatch.setattr(roles, "is_org_member", lambda sub, org: membre)
    return run


async def _dans_le_run(garde, run):
    tok = session_org.set_call_run(run)
    try:
        return await garde.pin_for_call()
    finally:
        session_org.reset_call_run(tok)


@pytest.mark.asyncio
async def test_sans_run_rien_n_est_pose_ni_lu(garde, monkeypatch):
    monkeypatch.setattr(db, "get_run_head", lambda r: pytest.fail("lecture inutile"))
    assert await garde.pin_for_call() == []


@pytest.mark.asyncio
async def test_sous_un_org_explicite_l_org_du_run_n_est_pas_lue(garde, monkeypatch):
    """`_org=`/`_project=` ont déjà posé l'org : ni lecture, ni garde — l'agent
    multi-org ne paie rien et n'est jamais refusé au nom d'un run qu'il ne suit pas."""
    run = _run_de(monkeypatch, 226, membre=False)
    monkeypatch.setattr(db, "get_run_head", lambda r: pytest.fail("lecture inutile"))
    tok = session_org.set_call_org(255)
    try:
        assert await _dans_le_run(garde, run) == []
    finally:
        session_org.reset_call_org(tok)


@pytest.mark.asyncio
async def test_un_run_inconnu_ne_pose_rien(garde, monkeypatch):
    monkeypatch.setattr(db, "get_run_head", lambda r: None)
    assert await _dans_le_run(garde, uuid.uuid4().hex) == []
    assert session_org.current_call_run_org() is None


@pytest.mark.asyncio
async def test_membre_l_org_du_run_est_posee_puis_retiree(garde, monkeypatch):
    run = _run_de(monkeypatch, 226)
    undo = await _dans_le_run(garde, run)
    try:
        assert session_org.current_call_run_org() == 226
    finally:
        for reset, tok in reversed(undo):
            reset(tok)
    assert session_org.current_call_run_org() is None


@pytest.mark.asyncio
async def test_non_membre_refus_nomme_jamais_un_repli(garde, monkeypatch):
    run = _run_de(monkeypatch, 226, membre=False)
    with pytest.raises(McpError) as e:
        await _dans_le_run(garde, run)
    msg = str(e.value)
    assert "226" in msg and "Travail" in msg and "membre" in msg and run in msg, msg
    assert session_org.current_call_run_org() is None


@pytest.mark.asyncio
async def test_une_base_qui_tousse_refuse_proprement(garde, monkeypatch):
    """Comme la garde de `_org=` : une erreur interne devient un McpError lisible,
    jamais un repli silencieux sur la maison (tourner sous une autre org que celle du
    run est pire que se faire rejeter, ADR 0038)."""
    def _boom(r):
        raise RuntimeError("pool timeout")
    monkeypatch.setattr(db, "get_run_head", _boom)
    with pytest.raises(McpError) as e:
        await _dans_le_run(garde, uuid.uuid4().hex)
    assert "interne" in str(e.value).lower()
