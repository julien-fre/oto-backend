"""Bascule de tenant : ce que la commande déclenchait, et ce qu'elle déclenche encore.

`OTO_MCP_TENANT_MIGRATION_ISS` commandait **deux mécanismes de nature opposée**, et un
troisième qui voyageait en passager clandestin :

1. **Le rapprochement d'identités** (`reconcile_tenant_migration`) — au login,
   fusionnait l'ancien compte de même email dans le nouveau. Il lisait la commande par
   sa VALEUR (elle devait égaler l'`iss` du jeton). **RETIRÉ du chemin de login le
   2026-09-03** : le tableau de vérité qui l'armait est conservé ci-dessous, une
   colonne retournée — le diff dit alors exactement ce qui a cessé.
2. **Le drain d'alias** (`db.resolve_sub`, sur les DEUX portes — REST `_authenticate`
   et MCP `current_user_sub_from_token`) — redirige un ancien identifiant vers le
   compte actuel. Il lit la commande **par sa PRÉSENCE**, sans regarder sa valeur.
3. *(passager)* sur la porte MCP seulement, `db.upsert_user` est lui aussi sous la
   commande du drain — donc un compte MCP-only n'est rafraîchi que commande posée.

⚠️ **Ce fichier a d'abord été un relevé** : écrit AVANT le découplage, vert APRÈS à
l'identique (31/31), il a fait la preuve que séparer les deux commandes ne changeait
aucun comportement. Il est depuis le garde du DÉSARMEMENT : les cas du rapprochement
attendent tous un non, ceux du drain sont inchangés — donc toujours la preuve, à chaque
run, que retirer l'un n'a pas emporté l'autre.

⚠️ La table du drain inclut volontairement le cas qu'un refactoring « propre »
normaliserait sans le dire : **la commande blanche** (`"   "`) l'arme, parce que le
drain lit la présence et ne strippe pas. Le rapprochement, lui, strippait — les deux
lectures n'ont jamais été équivalentes. Figer ce cas est ce qui empêche de le
« corriger » par inadvertance, ce qui serait un changement de comportement sur le
seul mécanisme encore armé, et le seul dont l'arrêt ressuscite des comptes.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import types

import pytest

from oto_mcp.api import routes as api_routes
from oto_mcp.auth import hooks as auth_hooks
from oto_mcp.db import users as db_users

_COMMANDE = "OTO_MCP_TENANT_MIGRATION_ISS"
_NOTRE_ISS = "https://oto.logto.app/oidc"


def _poser(monkeypatch, valeur):
    """Pose (ou retire) la commande. `None` = variable absente, ≠ variable vide."""
    if valeur is None:
        monkeypatch.delenv(_COMMANDE, raising=False)
    else:
        monkeypatch.setenv(_COMMANDE, valeur)


# ── 1. Le rapprochement, observé à sa porte (`upsert_user`) ──────────────────

class _ConnDoublure:
    """La ligne `users` est hors sujet ici : on la rend en UPDATE (`inserted` faux)
    pour que les effets de première inscription ne s'invitent pas dans la mesure."""

    def execute(self, sql, params=()):
        return self

    def fetchone(self):
        return {"inserted": False}


@pytest.fixture
def rapprochements(monkeypatch):
    """Réduit `upsert_user` à sa seule décision observable : a-t-il appelé le
    rapprochement ? La liste rendue est le journal des appels."""
    @contextlib.contextmanager
    def _connect():
        yield _ConnDoublure()

    journal: list = []
    monkeypatch.setattr(db_users, "_connect", _connect)
    monkeypatch.setattr(db_users, "reconcile_tenant_migration",
                        lambda sub, email_hint=None: journal.append((sub, email_hint)))
    return journal


# Les commandes qui ARMAIENT le rapprochement jusqu'au 2026-09-03 — les quatre qui
# suivaient l'égalité d'émetteur y compris. Aucune ne le déclenche désormais.
_COMMANDES_QUI_ARMAIENT = [
    (None,                     "aucune commande posée"),
    ("",                       "commande vide"),
    ("   ",                    "commande blanche"),
    (_NOTRE_ISS,               "notre émetteur : LE cas qui armait, en permanence"),
    (_NOTRE_ISS + "/",         "notre émetteur, slash final"),
    ("  " + _NOTRE_ISS + "  ", "notre émetteur, espaces autour"),
    ("https://tiers/oidc",     "un autre émetteur"),
]


@pytest.mark.parametrize("commande,pourquoi", _COMMANDES_QUI_ARMAIENT)
def test_le_login_ne_rapproche_plus_JAMAIS(monkeypatch, rapprochements, commande, pourquoi):
    """Le désarmement, éprouvé sur les entrées qui armaient : plus aucun rapprochement
    ne part d'un login, quelle que soit la commande posée."""
    _poser(monkeypatch, commande)
    db_users.upsert_user("sub-1", email="a@b.c")
    assert rapprochements == [], pourquoi


def test_le_login_ne_regarde_MEME_PLUS_l_emetteur():
    """La garde la plus forte du désarmement : `upsert_user` ne reçoit plus l'`iss`.
    L'émetteur du jeton n'était lu que pour décider du rapprochement — le paramètre
    disparu, plus aucun code de login ne peut en faire une décision de fusion sans que
    la signature change, donc sans que ce test rougisse."""
    import inspect
    assert "iss" not in inspect.signature(db_users.upsert_user).parameters


def test_le_rapprochement_reste_joignable_en_acte_d_operateur():
    """Retirer le déclenchement AUTOMATIQUE n'est pas retirer le mécanisme : la fusion
    reste possible là où quelqu'un la décide (`migrate_sub`, ADR 0052 §6)."""
    assert callable(db_users.migrate_sub)
    assert "operator_source" in __import__("inspect").signature(db_users.migrate_sub).parameters


# ── 2. Le drain, observé à ses DEUX portes ───────────────────────────────────

# (commande, le drain se déclenche-t-il ?, pourquoi)
_CAS_DRAIN = [
    (None,        False, "aucune commande posée"),
    ("",          False, "commande vide"),
    ("   ",       True,  "commande blanche : la PRESENCE suffit, pas de .strip() ici"),
    (_NOTRE_ISS,  True,  "commande posée"),
    ("nimporte",  True,  "la valeur n'est jamais regardée"),
]


# -- porte REST : `_authenticate` --

def _requete_jwt():
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": "/api/me", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1",
        "headers": [(b"authorization", b"Bearer eyJ-un-jwt")],
    })


class _VerifieurJWT:
    async def verify_token(self, token):
        return types.SimpleNamespace(claims={"sub": "sub-vieux", "email": "a@b.c",
                                             "iss": _NOTRE_ISS})


@pytest.fixture
def drain_rest(monkeypatch):
    """La porte REST réduite à son observable : `resolve_sub` a-t-il été consulté ?"""
    journal: list = []

    def _resolve(sub):
        journal.append(sub)
        return "sub-canonique"

    monkeypatch.setattr(api_routes.db, "resolve_sub", _resolve)
    monkeypatch.setattr(api_routes.db, "upsert_user", lambda *a, **k: None)
    return journal


@pytest.mark.parametrize("commande,attendu,pourquoi", _CAS_DRAIN)
def test_le_drain_REST_lit_la_PRESENCE_de_la_commande(
        monkeypatch, drain_rest, commande, attendu, pourquoi):
    _poser(monkeypatch, commande)
    sub, err = asyncio.run(api_routes._authenticate(_requete_jwt(), _VerifieurJWT()))
    assert err is None
    assert bool(drain_rest) is attendu, pourquoi
    # Et le sub SERVI est bien celui qu'a rendu le drain — ou le sub brut sans lui.
    assert sub == ("sub-canonique" if attendu else "sub-vieux")


def test_la_porte_REST_rafraichit_le_compte_MEME_commande_absente(monkeypatch):
    """⚠️ C'est ce qui rend le drain porteur : ici `upsert_user` est HORS commande.
    Un ancien identifiant non redirigé y RECRÉE donc la ligne `users` supprimée."""
    _poser(monkeypatch, None)
    vus: list = []
    monkeypatch.setattr(api_routes.db, "upsert_user",
                        lambda sub, **k: vus.append(sub))
    monkeypatch.setattr(api_routes.db, "resolve_sub",
                        lambda s: pytest.fail("le drain ne devait pas être consulté"))
    asyncio.run(api_routes._authenticate(_requete_jwt(), _VerifieurJWT()))
    assert vus == ["sub-vieux"], "le compte est écrit sous l'identifiant NON redirigé"


# -- porte MCP : `current_user_sub_from_token` --

class _JetonMCP:
    claims = {"sub": "sub-vieux", "email": "a@b.c", "name": "Jane", "iss": _NOTRE_ISS}


@pytest.fixture
def drain_mcp(monkeypatch):
    """La porte MCP réduite à ses observables : le drain, ET son passager `upsert_user`."""
    import fastmcp.server.dependencies as deps
    from oto_mcp import db

    monkeypatch.setattr(auth_hooks, "_sub_override",
                        type(auth_hooks._sub_override)("x", default=None))
    monkeypatch.setattr(deps, "get_access_token", lambda: _JetonMCP())
    monkeypatch.setenv("OTO_MCP_DEV_SUB", "sub-de-secours")

    journal: dict = {"drain": [], "upsert": []}
    monkeypatch.setattr(db, "resolve_sub",
                        lambda s: journal["drain"].append(s) or "sub-canonique")
    monkeypatch.setattr(db, "upsert_user",
                        lambda sub, **k: journal["upsert"].append(sub))
    return journal


@pytest.mark.parametrize("commande,attendu,pourquoi", _CAS_DRAIN)
def test_le_drain_MCP_lit_la_PRESENCE_de_la_commande(
        monkeypatch, drain_mcp, commande, attendu, pourquoi):
    _poser(monkeypatch, commande)
    sub = auth_hooks.current_user_sub_from_token()
    assert bool(drain_mcp["drain"]) is attendu, pourquoi
    assert sub == ("sub-canonique" if attendu else "sub-vieux")


@pytest.mark.parametrize("commande,attendu,pourquoi", _CAS_DRAIN)
def test_sur_la_porte_MCP_le_rafraichissement_du_compte_suit_le_drain(
        monkeypatch, drain_mcp, commande, attendu, pourquoi):
    """Le passager clandestin : contrairement à REST, `upsert_user` n'est appelé ici
    QUE si la commande est posée. Le figer empêche le découplage de le déplacer."""
    _poser(monkeypatch, commande)
    auth_hooks.current_user_sub_from_token()
    assert bool(drain_mcp["upsert"]) is attendu, pourquoi
    if attendu:
        assert drain_mcp["upsert"] == ["sub-canonique"], "écrit APRÈS le drain"


def test_la_porte_MCP_laisse_passer_l_echec_du_drain(monkeypatch, drain_mcp):
    """Un échec d'IDENTITÉ ne se dégrade pas en anonymat (site B5 du 27/08)."""
    _poser(monkeypatch, _NOTRE_ISS)
    from oto_mcp import db
    monkeypatch.setattr(db, "resolve_sub",
                        lambda s: (_ for _ in ()).throw(RuntimeError("pool épuisé")))
    with pytest.raises(RuntimeError):
        auth_hooks.current_user_sub_from_token()


# ── 3. Ce que ce fichier prouve, mis noir sur blanc ──────────────────────────

def test_retirer_le_rapprochement_n_a_pas_emporte_le_drain():
    """L'énoncé du lot, en une assertion : la MÊME commande qui n'arme plus rien du
    côté rapprochement arme toujours le drain. C'est ce qui distingue « désarmer un
    mécanisme » de « couper l'interrupteur » — lequel aurait fait renaître les comptes
    fusionnés, la porte REST écrivant `upsert_user` hors commande."""
    from oto_mcp import tenant_migration

    monkeypatched = os.environ.get("OTO_MCP_TENANT_MIGRATION_ISS")
    try:
        os.environ["OTO_MCP_TENANT_MIGRATION_ISS"] = _NOTRE_ISS
        assert tenant_migration.alias_drain_armed() is True
    finally:
        if monkeypatched is None:
            os.environ.pop("OTO_MCP_TENANT_MIGRATION_ISS", None)
        else:
            os.environ["OTO_MCP_TENANT_MIGRATION_ISS"] = monkeypatched
    assert not hasattr(tenant_migration, "email_merge_armed"), (
        "un prédicat d'armement du rapprochement est réapparu : le déclenchement "
        "automatique au login a été retiré le 2026-09-03, le ré-armer est un acte "
        "d'opérateur (`migrate_sub`), pas une variable d'environnement."
    )
