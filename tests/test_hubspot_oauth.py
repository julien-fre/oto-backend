"""« Connect HubSpot » — the OAuth flow next to the pasted key (`auth/hubspot.py`).

Checked here: the consent asks for the scopes of the reference (and the app config
declares the same), the state is bound to its flow and single-use, the return writes
the refresh token with `meta.auth = "oauth"`, `bearer()` hands the tools a pasted key
as is and mints (then caches) an access token for an OAuth row, a rotated refresh
token is written back, a dead grant marks the row, and the probe of an OAuth row
introspects instead of calling the pasted-key endpoints.

⚠️ No real call to HubSpot: `_post` and `oauth_flow.exchange_code` are faked.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import types
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

ORG = 42
SUB = "user-de-test"
MEMBRE = f"{ORG}:{SUB}"
ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Reponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Coffre:
    def __init__(self):
        self.lignes: dict[tuple, dict] = {}
        self.rejets: list[tuple] = []

    def get(self, entity_type, entity_id, connector, account=""):
        assert connector == "hubspot"
        ligne = self.lignes.get((entity_type, entity_id, account))
        return dict(ligne) if ligne else None

    def set(self, entity_type, entity_id, connector, secret, set_by=None,
            meta=None, conn=None, account="", expected_version=None):
        assert connector == "hubspot"
        self.lignes[(entity_type, entity_id, account)] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": set_by}

    def marquer(self, entity_type, entity_id, provider, account, error, verdict=None):
        self.rejets.append((entity_type, entity_id, account, verdict))


@pytest.fixture
def env(monkeypatch):
    from oto_mcp import credentials_store
    from oto_mcp.auth import hubspot as hs
    from oto_mcp.connectors import health as connector_health
    from oto_mcp.db import connector_settings as store

    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [
                            {"scope_type": "platform", "scope_id": "platform",
                             "connector": "hubspot", "key": k, "value": v}
                            for k, v in (("client_id", "cid"), ("client_secret", "csec"))])
    coffre = _Coffre()
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", coffre.get)
    monkeypatch.setattr(credentials_store, "set_credential", coffre.set)
    monkeypatch.setattr(connector_health, "mark_rejected", coffre.marquer)
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: ORG)
    monkeypatch.setattr("oto_mcp.access.current_group", lambda sub: None)
    monkeypatch.setattr("oto_mcp.roles.is_org_admin", lambda sub, org: False)
    monkeypatch.setattr("oto_mcp.auth.flow.consume_state", lambda aud, data: True)
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.exemple.test")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-signature-de-test")
    appels: list[tuple[str, dict]] = []
    reponses: dict[str, list] = {}

    def faux_post(url, data):
        appels.append((url, dict(data)))
        file = reponses.get(url) or []
        assert file, f"no fake answer for {url}"
        return file.pop(0)

    monkeypatch.setattr(hs, "_post", faux_post)
    hs._TOKENS.clear()
    # `bearer()` records which shape served the call in a ContextVar: in production a
    # call's context ends with it; a test run shares one, so it is put back here.
    jeton = hs._SERVED_BY.set("")
    yield types.SimpleNamespace(hs=hs, coffre=coffre, appels=appels, reponses=reponses)
    hs._SERVED_BY.reset(jeton)


def _introspection(**extra):
    return _Reponse(200, {"active": True, "hub_id": 123, "hub_domain": "portal.example",
                          "user": "someone@example.test", "token_use": "refresh_token",
                          "scopes": ["crm.objects.contacts.read", "crm.lists.read"],
                          **extra})


# ── Scopes ──────────────────────────────────────────────────────────────────

def test_les_scopes_viennent_de_la_reference():
    from oto_mcp.auth import hubspot as hs
    from oto_mcp.tools.hubspot_scopes import SCOPES, known_scope

    for fam in ("contacts", "companies", "deals", "lists"):
        read, write = SCOPES[fam]
        assert read[0] in hs.REQUIRED_SCOPES and write[0] in hs.REQUIRED_SCOPES
    assert "crm.objects.owners.read" in hs.REQUIRED_SCOPES
    assert not set(hs.REQUIRED_SCOPES) & set(hs.OPTIONAL_SCOPES)
    assert all(known_scope(s) for s in (*hs.REQUIRED_SCOPES, *hs.OPTIONAL_SCOPES))
    # The events API and tickets are optional: a portal without them still installs.
    assert "business-intelligence" in hs.OPTIONAL_SCOPES
    assert "crm.objects.tickets.read" in hs.OPTIONAL_SCOPES


def test_la_config_de_l_app_declare_les_memes_scopes():
    """HubSpot refuses an authorize URL whose required scopes the app does not
    declare: the uploaded config and the code must be the same list."""
    from oto_mcp.auth import hubspot as hs

    cfg = json.loads((ROOT / "docs/hubspot-app/src/app/app-hsmeta.json").read_text())
    auth = cfg["config"]["auth"]
    assert auth["type"] == "oauth"
    assert auth["requiredScopes"] == list(hs.REQUIRED_SCOPES)
    assert auth["optionalScopes"] == list(hs.OPTIONAL_SCOPES)
    assert all(u.endswith(hs.CALLBACK_PATH) for u in auth["redirectUrls"])


# ── Start ───────────────────────────────────────────────────────────────────

def test_le_consentement_part_avec_l_app_et_les_scopes(env):
    url = urlparse(env.hs.build_auth_url(SUB, "member"))
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == env.hs.AUTHORIZE_URL
    assert q["client_id"] == "cid"
    assert q["redirect_uri"] == "https://mcp.exemple.test/api/hubspot/oauth/callback"
    assert q["scope"].split() == list(env.hs.REQUIRED_SCOPES)
    assert q["optional_scope"].split() == list(env.hs.OPTIONAL_SCOPES)
    data = env.hs.read_state(q["state"])
    assert data["sub"] == SUB and data["org"] == ORG and data["scope"] == "member"
    assert data["jti"]
    assert "csec" not in url.query   # the secret never travels in a URL


def test_le_state_ne_vaut_que_pour_ce_flux(env):
    from oto_mcp.auth import flow as oauth_flow

    autre = oauth_flow.sign_state("meta_ads", {"sub": SUB, "org": ORG, "scope": "member"})
    assert env.hs.read_state(autre) is None
    assert env.hs.read_state("faux") is None


def test_pour_l_org_il_faut_etre_admin(env):
    with pytest.raises(PermissionError):
        env.hs.build_auth_url(SUB, "org")


def test_sans_app_le_refus_nomme_le_reglage(env, monkeypatch):
    from oto_mcp.db import connector_settings as store

    monkeypatch.setattr(store, "list_connector_settings", lambda key=None, conn=None: [])
    with pytest.raises(env.hs.HubSpotOAuthError, match="client_id, client_secret"):
        env.hs.build_auth_url(SUB, "member")
    assert env.hs.app_ready(SUB) is False


def test_le_flux_est_declare_avec_sa_promesse(env):
    from oto_mcp.connectors import flow as connector_flow

    assert connector_flow.supports("hubspot")
    assert connector_flow.entries()["hubspot"].app_ready(SUB) is True


# ── Return ──────────────────────────────────────────────────────────────────

def _callback(query: dict):
    from starlette.requests import Request

    from oto_mcp.api import hubspot as api_hubspot

    route = api_hubspot.make_routes(None, None, None, None, None)[0]
    req = Request({"type": "http", "method": "GET", "path": route.path,
                   "query_string": urlencode(query).encode(), "headers": []})
    return asyncio.run(route.endpoint(req))


def test_le_retour_range_le_refresh_token_et_le_portail(env, monkeypatch):
    from oto_mcp.auth import flow as oauth_flow

    monkeypatch.setattr(oauth_flow, "exchange_code", lambda url, **k: {
        "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800})
    env.reponses[env.hs.INTROSPECT_URL] = [_introspection()]
    state = env.hs.make_state(SUB, ORG, "member")
    resp = _callback({"code": "c0de", "state": state})
    assert resp.headers["location"].endswith("?connector=hubspot&connect=connected")
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "rt-1"
    assert ligne["meta"]["auth"] == "oauth" and ligne["meta"]["hub_id"] == 123
    assert ligne["meta"]["scopes"] == "crm.lists.read crm.objects.contacts.read"
    # The introspection carried the client secret in the BODY.
    url, data = env.appels[0]
    assert url == env.hs.INTROSPECT_URL and data["client_secret"] == "csec"


def test_un_state_rejoue_n_ecrit_rien(env, monkeypatch):
    monkeypatch.setattr("oto_mcp.auth.flow.consume_state", lambda aud, data: False)
    resp = _callback({"code": "c0de", "state": env.hs.make_state(SUB, ORG, "member")})
    assert resp.headers["location"].endswith("connect=error")
    assert not env.coffre.lignes


def test_un_role_perdu_entre_le_clic_et_le_retour_n_ecrit_rien(env):
    resp = _callback({"code": "c0de", "state": env.hs.make_state(SUB, ORG, "org")})
    assert resp.headers["location"].endswith("connect=forbidden")
    assert not env.coffre.lignes


def test_un_refus_chez_hubspot_revient_en_denied(env):
    resp = _callback({"error": "access_denied",
                      "state": env.hs.make_state(SUB, ORG, "member")})
    assert resp.headers["location"].endswith("connect=denied")


# ── The Bearer the tools use ───────────────────────────────────────────────

def _rc(key, meta=None):
    return types.SimpleNamespace(key=key, config=dict(meta or {}), entity_type="member",
                                 entity_id=MEMBRE, account="")


def test_une_cle_collee_passe_telle_quelle(env, monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_credential",
                        lambda *a, **k: _rc("pat-eu1-xyz"))
    assert env.hs.bearer() == "pat-eu1-xyz"
    assert not env.appels


def test_une_connexion_oauth_mint_un_jeton_puis_le_garde(env, monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_credential",
                        lambda *a, **k: _rc("rt-1", {"auth": "oauth"}))
    env.reponses[env.hs.TOKEN_URL] = [_Reponse(200, {
        "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800})]
    assert env.hs.bearer() == "at-1"
    assert env.hs.bearer() == "at-1"          # cached: one refresh only
    assert len(env.appels) == 1
    assert env.appels[0][1]["grant_type"] == "refresh_token"


def test_un_refresh_token_qui_tourne_est_reecrit(env, monkeypatch):
    env.coffre.lignes[("member", MEMBRE, "")] = {
        "secret": "rt-1", "meta": {"auth": "oauth", "hub_id": 123}, "set_by": SUB}
    monkeypatch.setattr("oto_mcp.access.resolve_credential",
                        lambda *a, **k: _rc("rt-1", {"auth": "oauth"}))
    env.reponses[env.hs.TOKEN_URL] = [_Reponse(200, {
        "access_token": "at-2", "refresh_token": "rt-2", "expires_in": 1800})]
    assert env.hs.bearer() == "at-2"
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "rt-2"
    assert ligne["meta"] == {"auth": "oauth", "hub_id": 123}   # meta passed back


def test_un_grant_mort_marque_la_ligne_et_demande_de_reconnecter(env, monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_credential",
                        lambda *a, **k: _rc("rt-1", {"auth": "oauth"}))
    env.reponses[env.hs.TOKEN_URL] = [_Reponse(400, {"error": "invalid_grant"})]
    with pytest.raises(env.hs.HubSpotReauthRequired, match="Reconnect HubSpot"):
        env.hs.bearer()
    assert env.coffre.rejets == [("member", MEMBRE, "", "unauthorized")]


def test_une_config_fausse_ne_marque_pas_la_ligne(env, monkeypatch):
    monkeypatch.setattr("oto_mcp.access.resolve_credential",
                        lambda *a, **k: _rc("rt-1", {"auth": "oauth"}))
    env.reponses[env.hs.TOKEN_URL] = [_Reponse(400, {"error": "invalid_client"})]
    with pytest.raises(env.hs.HubSpotOAuthError) as e:
        env.hs.bearer()
    assert not isinstance(e.value, env.hs.HubSpotReauthRequired)
    assert not env.coffre.rejets


# ── Probe ───────────────────────────────────────────────────────────────────

def test_la_sonde_d_une_connexion_oauth_introspecte(env):
    from oto_mcp.tools import hubspot as tools_hubspot

    env.reponses[env.hs.INTROSPECT_URL] = [_introspection()]
    out = tools_hubspot._verify({"key": "rt-1"}, {"auth": "oauth"})
    assert out["identity"] == {"portal_id": 123, "ui_domain": "portal.example",
                               "connected_as": "someone@example.test"}
    assert out["scopes"]["method"] == "introspection"
    assert out["scopes"]["families"]["contacts"] == "granted"
    assert out["scopes"]["families"]["tickets"] == "missing"
    assert [u for u, _ in env.appels] == [env.hs.INTROSPECT_URL]   # no token minted


def test_la_sonde_d_une_connexion_desinstallee_echoue(env):
    from oto_mcp.tools import hubspot as tools_hubspot

    env.reponses[env.hs.INTROSPECT_URL] = [_Reponse(200, {"active": False})]
    with pytest.raises(env.hs.HubSpotReauthRequired):
        tools_hubspot._verify({"key": "rt-1"}, {"auth": "oauth"})


def test_un_scope_manquant_sur_une_connexion_oauth_dit_de_reconnecter(env, monkeypatch):
    from oto.tools.common.errors import UpstreamHTTPError

    from oto_mcp.tools.hubspot_scopes import missing_scopes_refusal

    monkeypatch.setattr("oto_mcp.access.resolve_credential",
                        lambda *a, **k: _rc("rt-1", {"auth": "oauth"}))
    env.reponses[env.hs.TOKEN_URL] = [_Reponse(200, {
        "access_token": "at-1", "expires_in": 1800})]

    def appel():
        env.hs.bearer()
        e = UpstreamHTTPError(403, "missing", service="hubspot")
        e.body = {"category": "MISSING_SCOPES", "message": "x"}
        return missing_scopes_refusal(e, object_type="tickets")

    import contextvars
    refus = contextvars.copy_context().run(appel)
    assert refus is not None and "reconnect it from the connector card" in str(refus)
