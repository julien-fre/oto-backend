"""Une identité de SERVICE : un client machine de l'annuaire, jamais un compte (#1068).

Le commerce parle au cœur sous son propre nom (ADR 0070 §7.4(b)). Ce banc prouve les
deux moitiés du contrat : ce que le service OBTIENT (une règle qui le lit, un journal
à son nom, aucune ligne `users`) et ce qu'il N'OBTIENT PAS (aucune autre route, aucune
autre règle, pas même celles qu'un compte sans droit passe).

Les jetons sont de VRAIS JWT ES384 signés, vérifiés par le vrai verifier des services :
seul le JWKS est servi localement.
"""
from __future__ import annotations

import asyncio
import json
import time
import types

import pytest
from authlib.jose import JsonWebKey, jwt as authlib_jwt
from starlette.requests import Request

from oto_mcp import access, db
from oto_mcp.api import base as ab
from oto_mcp.auth import platform_worker, service_identity, token_scopes
from oto_mcp.capabilities import _authz
from oto_mcp.capabilities._types import AuthzDenied, RawCtx

_LOGTO = "https://annuaire.exemple.test"
_ISSUER = f"{_LOGTO}/oidc"
_AUD_SERVICES = "https://mcp.exemple.test/api/service"   # conftest : adresse publique
_CLIENT = "m2m-commerce-1"


@pytest.fixture(scope="module")
def cle():
    return JsonWebKey.generate_key("EC", "P-384", is_private=True)


@pytest.fixture(autouse=True)
def annuaire(monkeypatch, cle):
    """L'émetteur primaire et son JWKS, servis sans réseau."""
    monkeypatch.setenv("LOGTO_ENDPOINT", _LOGTO)
    service_identity._verifier_pour.cache_clear()
    pem = JsonWebKey.import_key(cle.as_dict(is_private=False)).as_pem().decode()

    # Le verifier récupère sa clé par `jwks_uri` : on remplace la RÉCUPÉRATION, pas
    # la vérification (signature, émetteur, audience, expiration restent réels).
    async def cle_servie(self, token):
        return pem
    from fastmcp.server.auth.providers.jwt import JWTVerifier
    monkeypatch.setattr(JWTVerifier, "_get_verification_key", cle_servie)
    yield
    service_identity.set_current(None)
    platform_worker.set_current(None)
    token_scopes.set_current(None)
    service_identity._verifier_pour.cache_clear()


def _jeton(cle, *, aud=_AUD_SERVICES, sub=_CLIENT, client_id=_CLIENT,
           scope="commerce", iss=_ISSUER, exp_dans=3600) -> str:
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub, "iat": now, "exp": now + exp_dans}
    if client_id is not None:
        claims["client_id"] = client_id
    if scope is not None:
        claims["scope"] = scope
    return authlib_jwt.encode({"alg": "ES384", "kid": "k1"}, claims, cle).decode()


def _req(token: str, path: str = "/api/admin/orgs") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80), "http_version": "1.1",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    })


class _VerifierDesPersonnes:
    """Le verifier MCP : il ne doit JAMAIS être consulté pour un jeton de service."""
    async def verify_token(self, token):
        raise AssertionError("un jeton de service ne passe pas par le verifier des personnes")


def _auth(token, **kw):
    async def scenario():
        req = _req(token)
        sub, err = await ab._authenticate(req, _VerifierDesPersonnes(), **kw)
        return sub, err, service_identity.current(), req.scope.get(ab.CLE_PRINCIPAL)
    return asyncio.run(scenario())


def _erreur(err) -> str:
    return json.loads(bytes(err.body))["error"]


@pytest.fixture
def sans_compte(monkeypatch):
    def jamais(*a, **k):
        raise AssertionError("une identité de service ne touche pas à `users`")
    monkeypatch.setattr(ab.db, "upsert_user", jamais)
    monkeypatch.setattr(ab.account_suspension, "refus", jamais)


# ── L'authentification ───────────────────────────────────────────────────────

def test_un_client_commerce_est_authentifie_sans_compte_et_journalise(cle, sans_compte):
    sub, err, pose, principal = _auth(_jeton(cle), allow_service=True)
    assert err is None and sub == f"service:{_CLIENT}"
    assert pose["roles"] == frozenset({"commerce"})
    assert principal == {"sub": f"service:{_CLIENT}", "token_id": None,
                         "token_kind": "service"}


def test_une_route_qui_ne_le_declare_pas_refuse_le_service(cle, sans_compte):
    sub, err, pose, _ = _auth(_jeton(cle))
    assert sub is None and err.status_code == 403
    assert _erreur(err) == "service_forbidden"
    assert pose is None


@pytest.mark.parametrize("jeton, statut, code", [
    (dict(scope=None), 403, "service_role_missing"),
    (dict(scope="lecture autre"), 403, "service_role_missing"),
    (dict(sub="u-humain"), 403, "service_machine_required"),
    (dict(client_id=None), 403, "service_machine_required"),
    (dict(exp_dans=-60), 401, "invalid_token"),
    (dict(iss="https://autre.exemple.test/oidc"), 401, "invalid_token"),
])
def test_un_jeton_adresse_aux_services_mais_refuse_dit_pourquoi(cle, sans_compte, jeton, statut, code):
    sub, err, pose, _ = _auth(_jeton(cle, **jeton), allow_service=True)
    assert sub is None and err.status_code == statut and _erreur(err) == code
    assert pose is None


def test_une_signature_d_une_autre_cle_est_refusee(sans_compte):
    autre = JsonWebKey.generate_key("EC", "P-384", is_private=True)
    sub, err, _, _ = _auth(_jeton(autre), allow_service=True)
    assert sub is None and _erreur(err) == "invalid_token"


def test_un_jeton_d_une_autre_audience_suit_le_chemin_des_personnes(cle, monkeypatch):
    """Le service n'a pas d'autre porte : un jeton d'audience MCP n'est jamais lu
    comme un service, et un jeton de service n'entre pas par le verifier MCP."""
    vu = []

    class _V:
        async def verify_token(self, token):
            vu.append(token)
            return None
    req = _req(_jeton(cle, aud="https://mcp.exemple.test/mcp"))
    sub, err = asyncio.run(ab._authenticate(req, _V(), allow_service=True))
    assert vu and err.status_code == 401 and service_identity.current() is None


def test_le_fait_service_ne_survit_pas_a_la_requete_suivante(cle, sans_compte, monkeypatch):
    monkeypatch.setattr(ab.db, "verify_api_token", lambda t: {"sub": "u-1", "scopes": None})
    monkeypatch.setattr(ab.account_suspension, "refus", lambda sub: None)

    async def scenario():
        await ab._authenticate(_req(_jeton(cle)), _VerifierDesPersonnes(), allow_service=True)
        assert service_identity.current() is not None
        sub, err = await ab._authenticate(_req("oto_ordinaire"), _VerifierDesPersonnes())
        return sub, service_identity.current()
    sub, pose = asyncio.run(scenario())
    assert sub == "u-1" and pose is None


# ── La règle d'autorisation ──────────────────────────────────────────────────

_PRINCIPAL = {"sub": f"service:{_CLIENT}", "client_id": _CLIENT,
              "roles": frozenset({"commerce"})}


def _raw(sub):
    return RawCtx(sub=sub)


def test_la_regle_commerce_lit_le_service_pose():
    service_identity.set_current(dict(_PRINCIPAL))
    ctx = _authz.COMMERCE_SERVICE(_raw(_PRINCIPAL["sub"]))
    assert (ctx.sub, ctx.org_id, ctx.role) == (_PRINCIPAL["sub"], None, "service:commerce")


def test_un_compte_meme_super_admin_ne_passe_pas_la_regle_commerce(monkeypatch):
    monkeypatch.setattr(access, "is_super_admin", lambda sub: True)
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: True)
    with pytest.raises(AuthzDenied) as refus:
        _authz.COMMERCE_SERVICE(_raw("u-super"))
    assert refus.value.code == "service_required"


def test_le_principal_doit_etre_celui_qui_est_pose():
    service_identity.set_current(dict(_PRINCIPAL))
    with pytest.raises(AuthzDenied) as refus:
        _authz.COMMERCE_SERVICE(_raw("service:un-autre"))
    assert refus.value.code == "service_identity_mismatch"


@pytest.mark.parametrize("regle", [
    _authz.SUB_ONLY, _authz.ORG_MEMBER, _authz.PLATFORM_ADMIN, _authz.SUPER_ADMIN,
    _authz.WORKER_OR_ORG_MEMBER,
])
def test_le_service_ne_passe_aucune_regle_de_compte(regle, monkeypatch):
    monkeypatch.setattr(access, "current_org", lambda sub: 42)
    monkeypatch.setattr(access, "get_user_role", lambda sub: "super_admin")
    monkeypatch.setattr(access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(access, "is_super_admin", lambda sub: True)
    service_identity.set_current(dict(_PRINCIPAL))
    with pytest.raises(AuthzDenied) as refus:
        regle(_raw(_PRINCIPAL["sub"]))
    assert refus.value.code == "service_forbidden"


def test_un_role_hors_catalogue_est_refuse_a_la_declaration():
    with pytest.raises(ValueError):
        _authz.SERVICE_ROLE("facturation")


def test_l_adaptateur_n_ouvre_le_service_qu_aux_regles_qui_le_lisent():
    assert _authz.accepts_service(_authz.COMMERCE_SERVICE)
    assert _authz.accepts_service(_authz.BY_OP({"a": _authz.PLATFORM_ADMIN,
                                                "b": _authz.COMMERCE_SERVICE}))
    for regle in (_authz.SUB_ONLY, _authz.PLATFORM_ADMIN, _authz.SUPER_ADMIN,
                  _authz.BY_OP({"a": _authz.PLATFORM_ADMIN})):
        assert not _authz.accepts_service(regle)
