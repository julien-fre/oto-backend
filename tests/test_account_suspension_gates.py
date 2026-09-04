"""Les gardes d'un compte en pause, exercées CHACUNE là où elle vit.

Un compte en pause ne doit plus rien pouvoir faire **dès la requête suivante**, jeton
déjà émis compris. C'est le point qui fait ou défait le mécanisme : une pause vérifiée
au login ne protège de rien pendant la durée de vie du jeton — une heure pour un JWT,
sans limite pour un jeton `oto_`, qui n'a pas de login du tout. Les quatre portes
d'entrée sont donc testées séparément, parce qu'elles ne partagent que le prédicat :

  1. face REST, jeton `oto_`   → `api.base._authenticate`, branche haute
  2. face REST, JWT            → `api.base._authenticate`, branche basse
  3. face REST, ANCIEN sub     → la levée d'`upsert_user`, traduite en refus nommé
  4. face MCP, toute requête   → `AccountSuspendedMiddleware.on_request`

⚠️ **Chaque garde est aussi éprouvée à l'envers** : le compte vivant doit passer. Une
garde qui refuse tout le monde protège aussi bien qu'une garde qui ne refuse
personne, et se remarque beaucoup plus tard.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from oto_mcp import account_suspension
from oto_mcp.api import routes as api_routes
from oto_mcp.auth import token_scopes

PAUSE = {"sub": "u-1", "suspended_at": "2026-09-03T10:00:00+00:00",
         "suspended_by": "op-9", "suspended_reason": "double du partenaire"}


def _req(method: str, path: str, token: str = "oto_deadbeef"):
    from starlette.requests import Request
    return Request({
        "type": "http", "method": method, "path": path, "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "http_version": "1.1",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


class _FakeVerifier:
    async def verify_token(self, token):
        return types.SimpleNamespace(claims={"sub": "u-jwt", "email": "a@b.test"})


def _auth(request, **kw):
    return asyncio.run(api_routes._authenticate(request, verifier=_FakeVerifier(), **kw))


def _body(response) -> dict:
    return json.loads(bytes(response.body).decode())


@pytest.fixture
def base(monkeypatch):
    """Le socle : un jeton `oto_` valide, un `upsert_user` inerte, personne en pause."""
    box = {"pause": None, "upsert": None}
    monkeypatch.setattr(api_routes.db, "verify_api_token",
                        lambda t: {"sub": "u-1", "scopes": None})
    monkeypatch.setattr(api_routes.db, "get_suspension", lambda sub: box["pause"])

    def _upsert(*a, **k):
        if box["upsert"]:
            raise box["upsert"]
    monkeypatch.setattr(api_routes.db, "upsert_user", _upsert)
    yield box
    token_scopes.set_current(None)


# ── 1. Jeton `oto_` : le chemin SANS login, celui qui compte le plus ─────────

def test_un_jeton_api_dun_compte_en_pause_est_refuse(base):
    base["pause"] = PAUSE
    sub, err = _auth(_req("GET", "/api/me"))
    assert sub is None
    assert err.status_code == 403
    assert _body(err)["error"] == "account_suspended"
    # Le motif écrit par l'opérateur voyage jusqu'au porteur : c'est ce qui rend le
    # refus actionnable au lieu d'être un mur.
    assert "double du partenaire" in _body(err)["detail"]


def test_un_jeton_api_dun_compte_vivant_passe(base):
    assert _auth(_req("GET", "/api/me")) == ("u-1", None)


# ── 2. JWT : le chemin du tableau de bord ────────────────────────────────────

def test_un_jwt_dun_compte_en_pause_est_refuse(base):
    base["pause"] = PAUSE
    sub, err = _auth(_req("GET", "/api/me", token="eyJ-un-jwt"))
    assert sub is None
    assert err.status_code == 403 and _body(err)["error"] == "account_suspended"


def test_un_jwt_dun_compte_vivant_passe(base):
    assert _auth(_req("GET", "/api/me", token="eyJ-un-jwt")) == ("u-jwt", None)


def test_le_compte_en_pause_ncrit_plus_rien(base):
    """La garde tombe AVANT `upsert_user` : un compte neutralisé ne rafraîchit même
    plus son adresse. Éprouvé en faisant lever l'upsert — s'il était atteint, le test
    verrait cette exception plutôt qu'un refus propre."""
    base["pause"] = PAUSE
    base["upsert"] = AssertionError("upsert_user ne doit pas être atteint")
    _, err = _auth(_req("GET", "/api/me", token="eyJ-un-jwt"))
    assert err.status_code == 403


# ── 3. L'ANCIEN identifiant d'un compte en pause ─────────────────────────────

def test_un_ancien_identifiant_qui_menerait_a_un_compte_en_pause_est_refuse(base):
    """Le cas de la résurrection, vu depuis la porte REST.

    Ce sub-là n'a pas de ligne à lui — la fusion l'a supprimée — donc la garde
    d'entrée ne le voit pas : c'est `upsert_user` qui reconnaît, au moment de le
    RECRÉER, que son alias mène à un compte neutralisé. Le refus doit sortir avec le
    même code que les autres, pas en 500."""
    base["upsert"] = api_routes.db.CompteEnPause("u-1", "double du partenaire",
                                                 "l'identifiant u-vieux y redirige")
    sub, err = _auth(_req("GET", "/api/me", token="eyJ-un-jwt"))
    assert sub is None
    assert err.status_code == 403 and _body(err)["error"] == "account_suspended"


def test_un_ancien_identifiant_ALIASE_vers_un_compte_gele_nest_pas_servi(base, monkeypatch):
    """⚠️ Bout en bout, et c'est le chemin par lequel passera le trafic des comptes
    qu'on va geler.

    Le drain de la chaîne d'alias (`db.resolve_sub`) ne bloque PAS un compte gelé, et
    ce n'est pas son rôle : son prédicat de vivacité est l'existence de la ligne
    `users`, et un compte gelé garde la sienne. Il rend donc le sub canonique, et
    c'est très bien — c'est la pause qui doit refuser, **à sa porte**.

    Ce test le vérifie dans l'ordre réel : jeton portant l'ANCIEN identifiant → drain
    → sub canonique → refus. Un successeur qui déplacerait la garde AVANT le drain le
    verrait rougir, parce qu'elle regarderait alors un sub que personne n'a jamais mis
    en pause."""
    from oto_mcp.api import base as api_base
    monkeypatch.setattr(api_base, "alias_drain_armed", lambda: True)
    monkeypatch.setattr(api_routes.db, "resolve_sub",
                        lambda s: "u-canonique" if s == "u-jwt" else s)
    # ⚠️ La pause n'est posée QUE sur le sub canonique — pas sur celui du jeton. C'est
    # ce qui rend ce test discriminant sur l'ORDRE : si la garde tournait avant le
    # drain, elle interrogerait `u-jwt`, ne trouverait rien, et servirait la requête.
    monkeypatch.setattr(api_routes.db, "get_suspension",
                        lambda s: dict(PAUSE, sub=s) if s == "u-canonique" else None)
    sub, err = _auth(_req("GET", "/api/me", token="eyJ-un-jwt"))
    assert sub is None
    assert err.status_code == 403 and _body(err)["error"] == "account_suspended"


def test_le_meme_chemin_alias_sert_un_compte_qui_nest_PAS_gele(base, monkeypatch):
    """Le contrefactuel : le drain doit continuer de faire son travail. Sans lui, la
    garde ci-dessus serait verte parce que plus rien ne passe."""
    from oto_mcp.api import base as api_base
    monkeypatch.setattr(api_base, "alias_drain_armed", lambda: True)
    monkeypatch.setattr(api_routes.db, "resolve_sub",
                        lambda s: "u-canonique" if s == "u-jwt" else s)
    assert _auth(_req("GET", "/api/me", token="eyJ-un-jwt")) == ("u-canonique", None)


# ── 4. Face MCP : TOUTE requête, pas seulement l'appel d'outil ───────────────

class _Ctx:
    """Le minimum qu'un middleware fastmcp reçoit — on ne teste que le refus."""


def _mcp(sub, pause, monkeypatch):
    from oto_mcp.middleware import account_suspended as mod
    monkeypatch.setattr(mod, "current_user_sub_from_token", lambda: sub)
    monkeypatch.setattr(account_suspension.db, "get_suspension",
                        lambda s: pause if s == sub else None)
    passe = {"n": 0}

    async def _next(ctx):
        passe["n"] += 1
        return "résultat"

    mw = mod.AccountSuspendedMiddleware()
    return mw, _next, passe


def test_mcp_refuse_toute_requete_dun_compte_en_pause(monkeypatch):
    from oto_mcp.mcp_errors import McpError
    mw, _next, passe = _mcp("u-1", PAUSE, monkeypatch)
    with pytest.raises(McpError) as leve:
        asyncio.run(mw.on_request(_Ctx(), _next))
    # Le refus dit ce qui se passe ET ce qui débloque : un agent qui lit « en pause »
    # cesse de réessayer, là où un résultat vide lui ferait conclure à une panne.
    assert "en pause" in str(leve.value)
    assert passe["n"] == 0, "la chaîne ne doit pas avoir tourné du tout"


def test_mcp_laisse_passer_un_compte_vivant(monkeypatch):
    mw, _next, passe = _mcp("u-1", None, monkeypatch)
    assert asyncio.run(mw.on_request(_Ctx(), _next)) == "résultat"
    assert passe["n"] == 1


def test_mcp_ne_refuse_rien_sans_identite(monkeypatch):
    """Dev local sans Logto : pas de compte, donc rien à neutraliser. Fermer ici
    couperait le run local de tout le monde sans qu'aucune pause n'ait été posée."""
    mw, _next, passe = _mcp(None, PAUSE, monkeypatch)
    assert asyncio.run(mw.on_request(_Ctx(), _next)) == "résultat"


def test_le_middleware_garde_les_requetes_et_pas_seulement_les_appels_doutil():
    """Le handshake injecte les instructions de l'org et `tools/list` révèle la boîte
    composée pour ce compte : garder `on_call_tool` seul laisserait un compte sorti
    continuer à LIRE ce qu'on lui a retiré le droit de faire.

    Épinglé sur le hook DÉCLARÉ plutôt que sur un scénario, parce que c'est le choix
    du hook qui porte la décision — et qu'un successeur qui le déplacerait vers
    `on_call_tool` verrait ce test rougir avec sa raison."""
    from fastmcp.server.middleware import Middleware
    from oto_mcp.middleware.account_suspended import AccountSuspendedMiddleware
    surcharges = {n for n in dir(AccountSuspendedMiddleware)
                  if n.startswith("on_")
                  and getattr(AccountSuspendedMiddleware, n)
                  is not getattr(Middleware, n, None)}
    assert surcharges == {"on_request"}


def test_le_middleware_est_monte_au_dessus_du_contexte_dappel():
    """L'ordre d'ajout dans `server.py` EST l'ordre externe→interne. Le refus doit
    tomber avant que le contexte d'appel, la rédaction, la visibilité et le journal
    n'aient une raison de tourner."""
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "oto_mcp" / "server.py"
    ordre = re.findall(r"instance\.add_middleware\(\s*(\w+)", src.read_text())
    assert ordre.index("AccountSuspendedMiddleware") < ordre.index("CallContextMiddleware")
    assert ordre.index("AccountSuspendedMiddleware") < ordre.index("ErrorEnvelopeMiddleware")


# ── Le prédicat lui-même ────────────────────────────────────────────────────

def test_un_hoquet_de_base_ne_blanchit_pas_un_compte_en_pause(monkeypatch):
    """Le fail-safe d'une neutralisation est le REFUS, pas le laisser-passer : rendre
    `None` sur une panne ferait sauter la garde en silence, et un incident de base
    rouvrirait l'accès de tous les comptes qu'on a fermés."""
    def _boum(sub):
        raise RuntimeError("pool épuisé")
    monkeypatch.setattr(account_suspension.db, "get_suspension", _boum)
    with pytest.raises(RuntimeError):
        account_suspension.refus("u-1")
