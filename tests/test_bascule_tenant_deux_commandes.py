"""Bascule de tenant : le RELEVÉ de ce que l'unique commande déclenche aujourd'hui.

`OTO_MCP_TENANT_MIGRATION_ISS` commande **deux mécanismes de nature opposée**, et un
troisième qui voyage en passager clandestin :

1. **Le rapprochement d'identités** (`reconcile_tenant_migration`, appelé par
   `upsert_user`) — au login, fusionne l'ancien compte de même email dans le nouveau.
   Il lit la commande **par sa VALEUR** : elle doit égaler l'`iss` du jeton.
2. **Le drain d'alias** (`db.resolve_sub`, sur les DEUX portes — REST `_authenticate`
   et MCP `current_user_sub_from_token`) — redirige un ancien identifiant vers le
   compte actuel. Il lit la commande **par sa PRÉSENCE**, sans regarder sa valeur.
3. *(passager)* sur la porte MCP seulement, `db.upsert_user` est lui aussi sous la
   commande du drain — donc un compte MCP-only n'est rafraîchi que commande posée.

⚠️ **Ce fichier ne juge rien, il constate.** Il est écrit AVANT le découplage et doit
rester vert APRÈS, à l'identique : c'est lui, et rien d'autre, qui fait la preuve que
séparer les deux commandes n'a changé aucun comportement. Les tables de vérité
ci-dessous incluent donc volontairement les cas qu'un refactoring « propre »
normaliserait sans le dire — au premier chef **la commande blanche** (`"   "`), que le
rapprochement `.strip()` (⟹ inerte) et que le drain ne strippe pas (⟹ armé). Cette
asymétrie n'est pas une intention, c'est l'état des lieux ; la figer est justement ce
qui empêche de la « corriger » par inadvertance au milieu d'un lot de découplage.
"""
from __future__ import annotations

import asyncio
import contextlib
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


# (commande, iss du jeton, le rapprochement se déclenche-t-il ?, pourquoi)
_CAS_RAPPROCHEMENT = [
    (None,                    _NOTRE_ISS,             False, "aucune commande posée"),
    ("",                      _NOTRE_ISS,             False, "commande vide"),
    ("   ",                   _NOTRE_ISS,             False, "commande blanche : .strip() la vide"),
    (_NOTRE_ISS,              None,                   False, "jeton sans iss"),
    (_NOTRE_ISS,              "",                     False, "iss vide"),
    (_NOTRE_ISS,              _NOTRE_ISS,             True,  "egalite stricte"),
    (_NOTRE_ISS + "/",        _NOTRE_ISS,             True,  "slash final cote commande"),
    (_NOTRE_ISS,              _NOTRE_ISS + "/",       True,  "slash final cote iss"),
    ("  " + _NOTRE_ISS + "  ", _NOTRE_ISS,            True,  "espaces autour de la commande"),
    (_NOTRE_ISS,              "https://tiers/oidc",   False, "un autre emetteur"),
    (_NOTRE_ISS,              _NOTRE_ISS.upper(),     False, "la comparaison est sensible a la casse"),
]


@pytest.mark.parametrize("commande,iss,attendu,pourquoi", _CAS_RAPPROCHEMENT)
def test_le_rapprochement_lit_la_VALEUR_de_la_commande(
        monkeypatch, rapprochements, commande, iss, attendu, pourquoi):
    _poser(monkeypatch, commande)
    db_users.upsert_user("sub-1", email="a@b.c", iss=iss)
    assert bool(rapprochements) is attendu, pourquoi


def test_le_rapprochement_recoit_le_sub_et_l_email_du_jeton(monkeypatch, rapprochements):
    """Le pré-filtre cheap : l'email du claim est passé en indice, jamais en preuve."""
    _poser(monkeypatch, _NOTRE_ISS)
    db_users.upsert_user("sub-1", email="a@b.c", iss=_NOTRE_ISS)
    assert rapprochements == [("sub-1", "a@b.c")]


def test_un_rapprochement_qui_echoue_ne_casse_pas_le_login(monkeypatch, rapprochements):
    """Le mécanisme est best-effort : son échec est avalé, `upsert_user` rend la main."""
    _poser(monkeypatch, _NOTRE_ISS)
    monkeypatch.setattr(db_users, "reconcile_tenant_migration",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Logto injoignable")))
    db_users.upsert_user("sub-1", email="a@b.c", iss=_NOTRE_ISS)  # ne lève pas


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


# ── 3. Ce que le relevé prouve, mis noir sur blanc ───────────────────────────

def test_les_deux_mecanismes_ne_lisent_PAS_la_commande_de_la_meme_facon():
    """La preuve, en une ligne, qu'il y a bien DEUX commandes dans une seule variable :
    il existe une valeur qui arme l'un et laisse l'autre inerte."""
    blanche = [c for c in _CAS_RAPPROCHEMENT if c[0] == "   "][0]
    blanche_drain = [c for c in _CAS_DRAIN if c[0] == "   "][0]
    assert blanche[2] is False and blanche_drain[1] is True
