"""Les gestes SENSIBLES refusent un jeton d'API, sur les deux faces (CASA 2.4.1).

Deux crans (`capabilities/_session_exigee.py`), testés là où ils s'APPLIQUENT :

1. `RestBinding.allow_api_token=False` : on JOUE la route et on lit ce qui part vers
   `authenticate` (même instrument que `test_api_tokens_capability.py`) — relire le
   descripteur prouverait que le champ est posé, pas qu'il est appliqué. Et un témoin :
   les routes laissées ouvertes à dessein le restent (`GET /api/orgs/{id}`, que lit un
   service tiers ; ajouter ou inviter un simple membre ; le partage) ;
2. la garde de HANDLER : sous un jeton (geste REST `api`, secret de worker, jeton `oto_`
   sur la face MCP), les handlers de domaine refusent en 403 `api_token_forbidden` — par
   les consoles MCP comme par REST — et laissent passer la session (geste `console`,
   session OAuth MCP) et l'appel interne (aucun geste, aucun jeton).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from _datastore_rest import cap

from oto_mcp import geste
from oto_mcp.auth import hooks, platform_worker
from oto_mcp.capabilities import _rest_adapter, _session_exigee
from oto_mcp.capabilities._types import AuthzDenied


# ── 1. Le binding REST ───────────────────────────────────────────────────────

def _kwargs_d_authentification(cle: str, index: int) -> dict:
    vus: dict = {}

    async def _auth(_req, _verifier, **kw):
        vus.update(kw)
        return None, JSONResponse({"error": "stop"}, status_code=401)

    c = cap(cle)
    binding = c.rest_bindings()[index]
    handler = _rest_adapter._make_handler(
        c, binding, None, _auth,
        lambda _r, p, status=200: JSONResponse(p, status_code=status),
        lambda _r, s, code, d=None: JSONResponse({"error": code}, status_code=s))

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(handler(Request({"type": "http", "method": binding.verb,
                                 "path": binding.path, "query_string": b"",
                                 "headers": [], "path_params": {}}, receive=_receive)))
    return vus


_SESSION_SEULE = [
    ("org.archive", 0), ("org.admin.archive", 0),
    ("org.member.set_role", 0), ("org.member.set_role", 1),
    ("org.member.remove", 0), ("org.member.remove", 1),
    ("group.member.set_role", 0), ("group.member.remove", 0),
    ("org.mfa.set", 0), ("platform.user.set_role", 0), ("platform.user.reset_mfa", 0),
    ("billing.subscribe", 0), ("billing.confirm", 0), ("billing.cancel", 0),
    ("billing.resume", 0), ("billing.method_change", 0),
    ("billing.method_change_confirm", 0),
    # Déjà là avant ce lot — gardés ici pour que la liste dise TOUT ce qui est fermé.
    ("org.api_key.create", 0), ("org.api_key.delete", 0),
    ("platform.token.create", 0), ("platform.token.delete", 0),
]


@pytest.mark.parametrize("cle,index", _SESSION_SEULE)
def test_la_route_sensible_refuse_le_porteur_de_jeton(cle, index):
    assert _kwargs_d_authentification(cle, index).get("allow_api_token") is False, cle
    assert not _kwargs_d_authentification(cle, index).get("allow_issuer_token"), (
        f"{cle} : le jeton émetteur ne franchit que les routes de SES jetons")


@pytest.mark.parametrize("cle", [
    "org.get", "org.member.add", "org.invite.create", "group.member.add",
    "platform.org.grant_key", "platform.org.revoke_key"])
def test_les_routes_laissees_ouvertes_le_restent(cle):
    """Lecture générale, ajout/invitation d'un simple membre (la garde de handler juge le
    rang), et les routes d'administration qu'un service tiers appelle avec son jeton."""
    assert "allow_api_token" not in _kwargs_d_authentification(cle, 0), cle


# ── 2. La garde de handler ───────────────────────────────────────────────────

@pytest.fixture()
def sous_jeton_rest():
    jeton = geste.poser(geste.API, "u-jeton")
    yield
    geste.retirer(jeton)


@pytest.fixture()
def sous_session_rest():
    jeton = geste.poser(geste.CONSOLE, "u-session")
    yield
    geste.retirer(jeton)


@pytest.fixture()
def sous_jeton_mcp(monkeypatch):
    """Ce que `server._verify_api_token` pose sur une requête MCP portée par `oto_`."""
    monkeypatch.setattr(hooks, "current_token_axes",
                        lambda: {"token_id": 7, "token_kind": "user"})


def test_la_garde_reconnait_chaque_porteur(monkeypatch):
    assert _session_exigee.porteur_de_jeton() is False          # appel interne
    with geste.portee(geste.CONSOLE, "u"):
        assert _session_exigee.porteur_de_jeton() is False      # dashboard (JWT)
    with geste.portee(geste.AGENT, "u"):
        assert _session_exigee.porteur_de_jeton() is False      # MCP sous OAuth
    with geste.portee(geste.API, "u"):
        assert _session_exigee.porteur_de_jeton() is True       # REST sous `oto_`
    platform_worker.set_current({"worker_sub": "w"})
    try:
        assert _session_exigee.porteur_de_jeton() is True       # secret de worker
    finally:
        platform_worker.set_current(None)
    monkeypatch.setattr(hooks, "current_token_axes", lambda: {"token_id": 3})
    with geste.portee(geste.AGENT, "u"):
        assert _session_exigee.porteur_de_jeton() is True       # MCP sous `oto_`


def test_un_jeton_mcp_se_reconnait_aussi_a_son_client(monkeypatch):
    import fastmcp.server.dependencies as dep
    monkeypatch.setattr(hooks, "current_token_axes", lambda: {})
    monkeypatch.setattr(dep, "get_access_token",
                        lambda: SimpleNamespace(client_id="oto_api_token", claims={}))
    assert _session_exigee.porteur_de_jeton() is True
    monkeypatch.setattr(dep, "get_access_token",
                        lambda: SimpleNamespace(client_id="claude.ai", claims={}))
    assert _session_exigee.porteur_de_jeton() is False


def _refus(fn, *a) -> tuple[int, str]:
    with pytest.raises(AuthzDenied) as e:
        fn(*a)
    return e.value.status, e.value.code


_REFUS = (403, "api_token_forbidden")
_CTX = SimpleNamespace(sub="u-admin", org_id=5, role="org_admin", group_id=None,
                       channel=None)


def _gestes():
    """Chaque geste sensible, appelé par SON handler de domaine — celui que servent la
    route REST ET la console MCP. La garde tombe avant toute lecture en base."""
    from oto_mcp.capabilities import admin_console, org_console, resources, users_admin
    from oto_mcp.capabilities.groups import members as gm
    from oto_mcp.capabilities.orgs import admin as oa, members as om, mfa, update
    return {
        "org.archive": lambda: update._archive_org(_CTX, update.OrgIdInput(org_id=5)),
        "oto_org op=archive": lambda: org_console._org(
            _CTX, org_console.OrgInput(op="archive", org_id=5)),
        "org.admin.archive": lambda: oa._archive_org(_CTX, oa.OrgIdInput(org_id=5)),
        "oto_admin_org op=archive": lambda: admin_console._org(
            _CTX, admin_console.OrgAdminInput(op="archive", org_id=5)),
        "org.member.remove": lambda: om._remove_member(
            _CTX, om.RemoveMemberInput(org_id=5, target="u-x")),
        "oto_admin_org_member op=remove": lambda: admin_console._org_member(
            _CTX, admin_console.OrgMemberAdminInput(op="remove", org_id=5, target="u-x")),
        "group.member.remove": lambda: gm._remove_member(
            _CTX, gm.RemoveGroupMemberInput(group_id=9, target="u-x")),
        "org.mfa.set": lambda: mfa._set_org_mfa(
            _CTX, mfa.SetOrgMfaInput(org_id=5, require=False)),
        "platform.user.set_role": lambda: users_admin._set_role(
            _CTX, users_admin.SetRoleInput(target="u-x", role="super_admin")),
        "platform.user.reset_mfa": lambda: users_admin._reset_mfa(
            _CTX, users_admin.ResetMfaInput(target="u-x")),
        "oto_resource op=transfer": lambda: resources._resources(
            _CTX, resources.ResourceInput(op="transfer", resource_type="project",
                                          resource_id="12", new_owner_org=5)),
    }


@pytest.fixture()
def rien_en_base(monkeypatch):
    """Toute lecture en base lève : la garde doit refuser AVANT d'en faire une."""
    from oto_mcp import db, org_store

    def _boum(*a, **k):
        raise AssertionError("lecture en base avant la garde")
    for nom in ("get_org", "get_org_role", "list_org_members", "remove_org_member"):
        monkeypatch.setattr(org_store, nom, _boum)
    monkeypatch.setattr(db, "get_user", _boum)


@pytest.mark.parametrize("nom", list(_gestes()))
def test_le_geste_sensible_refuse_un_jeton_rest(nom, sous_jeton_rest, rien_en_base):
    assert _refus(_gestes()[nom]) == _REFUS, nom


@pytest.mark.parametrize("nom", list(_gestes()))
def test_le_geste_sensible_refuse_un_jeton_mcp(nom, sous_jeton_mcp, rien_en_base):
    with geste.portee(geste.AGENT, "u-jeton"):
        assert _refus(_gestes()[nom]) == _REFUS, nom


def test_la_session_passe_la_garde(sous_session_rest, monkeypatch):
    """La garde ne refuse que le jeton : sous une session, le handler va plus loin — ici
    jusqu'à sa propre lecture de l'org, doublée pour répondre « inconnue »."""
    from oto_mcp import org_store
    from oto_mcp.capabilities.orgs import update
    monkeypatch.setattr(org_store, "get_org", lambda oid: None)
    assert _refus(update._archive_org, _CTX, update.OrgIdInput(org_id=5)) == (
        404, "unknown_org")


# ── Les gestes conditionnels ─────────────────────────────────────────────────

@pytest.fixture()
def membres(monkeypatch):
    """`_write_member_role` sans base : le rôle courant est lu d'un dict, l'écriture
    est enregistrée."""
    from oto_mcp import org_store
    from oto_mcp.capabilities.orgs import members as om
    roles = {"u-membre": "org_member", "u-admin2": "org_admin"}
    ecrits: list = []
    monkeypatch.setattr(org_store, "get_org_role", lambda org, sub: roles.get(sub))
    monkeypatch.setattr(org_store, "add_org_member",
                        lambda org, sub, role, actor=None: ecrits.append((sub, role)))
    monkeypatch.setattr(om, "_count_org_admins", lambda org: 2)
    return ecrits


def test_sous_jeton_on_ajoute_un_simple_membre(sous_jeton_rest, membres):
    from oto_mcp.capabilities.orgs import members as om
    om._write_member_role(5, "u-neuf", "org_member", require_member=False, actor="u")
    om._write_member_role(5, "u-membre", "org_member", require_member=False, actor="u")
    assert membres == [("u-neuf", "org_member"), ("u-membre", "org_member")]


@pytest.mark.parametrize("sub,role", [
    ("u-neuf", "org_admin"),        # nommer un admin, même à l'ajout
    ("u-membre", "org_admin"),      # promouvoir
    ("u-admin2", "org_member")])    # rétrograder par `add` (l'upsert, #273)
def test_sous_jeton_ni_admin_ni_changement_de_role(sous_jeton_rest, membres, sub, role):
    from oto_mcp.capabilities.orgs import members as om
    with pytest.raises(AuthzDenied) as e:
        om._write_member_role(5, sub, role, require_member=False, actor="u")
    assert (e.value.status, e.value.code) == _REFUS
    assert membres == []


def test_sous_session_le_role_change(sous_session_rest, membres):
    from oto_mcp.capabilities.orgs import members as om
    om._write_member_role(5, "u-membre", "org_admin", require_member=True, actor="u")
    assert membres == [("u-membre", "org_admin")]


@pytest.mark.parametrize("role,group_role,refuse", [
    ("org_admin", None, True), ("org_member", "group_admin", True),
    ("org_member", None, False), ("org_member", "group_member", False)])
def test_l_invitation_n_est_sensible_que_si_elle_donne_le_rang_d_admin(
        sous_jeton_rest, monkeypatch, role, group_role, refuse):
    """Les trois émetteurs (org, équipe, plateforme) passent par `emit_invitation`."""
    from oto_mcp.capabilities.orgs import invites as oi
    monkeypatch.setattr(oi.org_store, "org_front", lambda org: (None, None))
    monkeypatch.setattr(oi.org_store, "create_invitation", lambda *a, **k: (1, "inv_T"))
    monkeypatch.setattr(oi.config, "invite_base_url", lambda: "https://front.test")
    appel = lambda: oi.emit_invitation(  # noqa: E731
        _CTX, org_id=5, email=None, send_email=False, source="org_admin", role=role,
        target_name="acme", group_id=9 if group_role else None, group_role=group_role)
    if refuse:
        assert _refus(appel) == _REFUS
    else:
        assert appel()["invite_url"] == "https://front.test/invitation/inv_T"


# ── 3. Bout en bout REST : la VRAIE authentification jusqu'au geste conditionnel ──
#
# Les tests ci-dessus posent le geste à la main ; celui-ci passe par
# `api.base._authenticate` (un jeton `oto_` tel que la base le rend, `token_kind`
# compris ou ABSENT) puis l'adaptateur, jusqu'à une invitation d'admin — un geste sans
# cran de binding, que seule la garde de handler tient.

def _jouer_avec_la_vraie_auth(monkeypatch, ligne: dict, role: str) -> tuple[int, dict]:
    import json

    from oto_mcp.api import routes as api_routes
    from oto_mcp.auth import token_scopes
    from oto_mcp.capabilities import _authz
    from oto_mcp.capabilities.orgs import invites as oi
    monkeypatch.setattr(api_routes.db, "verify_api_token", lambda t: dict(ligne))
    monkeypatch.setattr(api_routes.db, "upsert_user", lambda *a, **k: None)
    monkeypatch.setattr(api_routes.db, "get_suspension", lambda sub: None)
    from oto_mcp import garde_identite
    monkeypatch.setattr(garde_identite, "refus", lambda sub: None)
    monkeypatch.setattr(_authz.roles, "is_org_admin", lambda sub, org: True)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: "member")
    monkeypatch.setattr(oi.org_store, "get_org", lambda oid: {"id": oid, "name": "acme"})
    monkeypatch.setattr(oi.org_store, "org_front", lambda org: (None, None))
    monkeypatch.setattr(oi.org_store, "create_invitation", lambda *a, **k: (1, "inv_T"))
    monkeypatch.setattr(oi.config, "invite_base_url", lambda: "https://front.test")

    c = cap("org.invite.create")
    binding = c.rest_bindings()[0]
    handler = _rest_adapter._make_handler(
        c, binding, None, api_routes._authenticate,
        lambda _r, p, status=200: JSONResponse(p, status_code=status),
        lambda _r, s, code, d=None, details=None: JSONResponse({"error": code},
                                                                status_code=s))
    corps = json.dumps({"role": role, "send_email": False}).encode()

    async def _receive():
        return {"type": "http.request", "body": corps, "more_body": False}

    async def _jouer():
        try:
            rep = await handler(Request({
                "type": "http", "method": "POST", "path": "/api/orgs/5/invitations",
                "query_string": b"", "path_params": {"id": "5"},
                "headers": [(b"authorization", b"Bearer oto_deadbeef"),
                            (b"content-type", b"application/json")]}, _receive))
        finally:
            token_scopes.set_current(None)
        return rep.status_code, json.loads(bytes(rep.body))
    return asyncio.run(_jouer())


@pytest.mark.parametrize("ligne", [
    {"sub": "u-jeton", "scopes": None, "token_id": 7, "token_kind": "user"},
    {"sub": "u-jeton", "scopes": None, "token_id": 7}],     # `token_kind` absent
    ids=["kind-user", "kind-absent"])
def test_bout_en_bout_un_jeton_n_invite_pas_un_admin(monkeypatch, ligne):
    code, corps = _jouer_avec_la_vraie_auth(monkeypatch, ligne, "org_admin")
    assert (code, corps.get("error")) == _REFUS, corps


def test_bout_en_bout_un_jeton_invite_un_simple_membre(monkeypatch):
    code, corps = _jouer_avec_la_vraie_auth(
        monkeypatch, {"sub": "u-jeton", "scopes": None, "token_id": 7,
                      "token_kind": "user"}, "org_member")
    assert code == 200 and corps["invite_url"] == "https://front.test/invitation/inv_T", corps
