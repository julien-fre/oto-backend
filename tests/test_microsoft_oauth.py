"""La connexion Microsoft du connecteur `sharepoint` — OAuth délégué, par personne.

⚠️ Le cœur est MOQUÉ à sa frontière (`oto.tools.microsoft` posé dans
`sys.modules`) : aucun appel réel à Microsoft n'est joué ici. Vérifié : le
registre, le flux hébergé (state, URL de retour, coordonnées de l'instance), le
retour de connexion, le coffre (refresh token au palier membre, identité en meta),
le renouvellement (cache, rotation du refresh token) et l'autorisation morte.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from oto_mcp import providers

CONNECTEUR = "sharepoint"
ORG = 42
SUB = "user-de-test"
MEMBRE = f"{ORG}:{SUB}"
_COORDONNEES = {"client_id": "app-id-fictif", "client_secret": "secret-fictif"}
_RETOUR = "https://mcp.exemple.test/api/microsoft/oauth/callback"


def _grant(access="AT1", refresh="RT1", expires_in=3600):
    return types.SimpleNamespace(access_token=access, refresh_token=refresh,
                                 expires_in=expires_in, scope="Files.ReadWrite.All")


def _faux_coeur():
    mod = types.ModuleType("oto.tools.microsoft")
    auth = types.ModuleType("oto.tools.microsoft.auth")

    class MicrosoftAuthError(ValueError):
        status_code = 401
        code = None

    class MicrosoftGrantExpired(MicrosoftAuthError):
        pass

    auth.authorize_url = MagicMock(return_value="https://login.example/authorize?x=1")
    auth.exchange_code = MagicMock(return_value=_grant())
    auth.refresh = MagicMock(return_value=_grant("AT2", "RT2"))
    auth.MicrosoftAuthError = MicrosoftAuthError
    auth.MicrosoftGrantExpired = MicrosoftGrantExpired
    mod.auth = auth
    mod.MicrosoftAuthError = MicrosoftAuthError
    mod.MicrosoftGrantExpired = MicrosoftGrantExpired
    client = MagicMock(name="GraphClient")
    client.return_value.get_me.return_value = {
        "displayName": "Jane Doe", "mail": "jane@contoso.example",
        "userPrincipalName": "jane@contoso.example"}
    mod.GraphClient = client
    return mod


class _Coffre:
    def __init__(self):
        self.lignes: dict[tuple, dict] = {}
        self.rejets: list[tuple] = []

    def poser(self, secret, meta=None):
        self.lignes[("member", MEMBRE, "")] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": SUB,
            "set_at": "2026-10-05T00:00:00Z"}

    def get(self, entity_type, entity_id, connector, account=""):
        assert connector == CONNECTEUR
        ligne = self.lignes.get((entity_type, entity_id, account))
        return dict(ligne) if ligne else None

    def set(self, entity_type, entity_id, connector, secret, set_by=None,
            meta=None, conn=None, account="", expected_version=None):
        assert connector == CONNECTEUR
        self.lignes[(entity_type, entity_id, account)] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": set_by}

    def marquer(self, entity_type, entity_id, provider, account, error):
        self.rejets.append((entity_id, account, error))


@pytest.fixture
def env(monkeypatch):
    from oto_mcp import credentials_store
    from oto_mcp.auth import microsoft as ms_auth
    from oto_mcp.connectors import health as connector_health
    from oto_mcp.db import connector_settings as store

    mod = _faux_coeur()
    monkeypatch.setitem(sys.modules, "oto.tools.microsoft", mod)
    monkeypatch.setitem(sys.modules, "oto.tools.microsoft.auth", mod.auth)
    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [
                            {"scope_type": "platform", "scope_id": "platform",
                             "connector": CONNECTEUR, "key": k, "value": v}
                            for k, v in _COORDONNEES.items()])
    coffre = _Coffre()
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", coffre.get)
    monkeypatch.setattr(credentials_store, "set_credential", coffre.set)
    monkeypatch.setattr(connector_health, "mark_rejected", coffre.marquer)
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: ORG)
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.exemple.test")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-signature-de-test")
    monkeypatch.setattr(ms_auth, "_JETONS", {})
    return types.SimpleNamespace(coeur=mod, coffre=coffre, auth=ms_auth)


# ── Registre ────────────────────────────────────────────────────────────────

def test_registre():
    c = providers.REGISTRY[CONNECTEUR]
    assert c.secret_kind == "oauth" and c.auth_modes == frozenset({"byo_user"})
    assert c.publisher_name == "Microsoft"
    assert not c.credential_fields
    assert c.doc_sections, "la fiche doit être servie depuis son markdown"


# ── Flux hébergé ────────────────────────────────────────────────────────────

def test_url_de_retour_derivee_de_l_environnement(env):
    from oto_mcp.connectors import flow as connector_flow

    assert connector_flow.supports(CONNECTEUR)
    assert connector_flow.callback_url(CONNECTEUR) == _RETOUR


def test_state_ne_vaut_que_pour_ce_flux(env):
    from oto_mcp.auth import flow as oauth_flow

    etat = env.auth.make_state(SUB, ORG, "")
    assert env.auth.verify_state(etat) == (SUB, ORG, "")
    assert env.auth.verify_state(oauth_flow.sign_state(
        "meta_ads", {"sub": SUB, "org": ORG})) is None


def test_sans_reglages_le_refus_nomme_la_cle(env, monkeypatch):
    from oto_mcp.db import connector_settings as store

    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [])
    assert env.auth.coordonnees_manquantes() == ["client_id", "client_secret"]
    assert env.auth.app_disponible(SUB) is False
    with pytest.raises(RuntimeError) as e:
        env.auth.app()
    assert "client_secret" in str(e.value) and "oto_admin_connector_setting" in str(e.value)


def test_le_dialogue_part_avec_l_application_de_l_instance(env):
    env.auth.build_auth_url(SUB, "")
    client_id, retour, etat = env.coeur.auth.authorize_url.call_args.args
    assert client_id == "app-id-fictif" and retour == _RETOUR
    assert env.auth.verify_state(etat) == (SUB, ORG, "")


def test_seul_client_secret_est_secret_et_suit_la_convention():
    from oto_mcp.auth import microsoft as ms_auth
    from oto_mcp.capabilities import platform_connectors as pc

    assert [k for k in ms_auth._REGLAGES if "secret" in k] == ["client_secret"]
    lignes = pc._sans_les_secrets([
        {"connector": CONNECTEUR, "key": k, "value": v} for k, v in _COORDONNEES.items()])
    assert "secret-fictif" not in str(lignes)


# ── Retour de connexion ─────────────────────────────────────────────────────

def _callback(query: dict):
    from starlette.requests import Request

    from oto_mcp.api import microsoft as api_ms

    route = api_ms.make_routes(None, None, None, None, None)[0]
    qs = "&".join(f"{k}={v}" for k, v in query.items()).encode()
    req = Request({"type": "http", "method": "GET", "path": route.path,
                   "query_string": qs, "headers": []})
    return asyncio.run(route.endpoint(req))


def test_retour_echange_le_code_et_range_le_refresh_token(env):
    etat = env.auth.make_state(SUB, ORG, "")
    resp = _callback({"code": "le-code", "state": etat})
    assert resp.status_code == 302 and "connected" in resp.headers["location"]
    args = env.coeur.auth.exchange_code.call_args.args
    assert args == ("app-id-fictif", "secret-fictif", "le-code", _RETOUR)
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "RT1"
    assert ligne["meta"]["email"] == "jane@contoso.example"
    assert "AT1" not in str(ligne), "le jeton d'accès ne va jamais en base"


def test_retour_refuse_sans_state_ou_sur_refus(env):
    assert "error" in _callback({"code": "c", "state": "faux"}).headers["location"]
    etat = env.auth.make_state(SUB, ORG, "")
    resp = _callback({"error": "access_denied", "state": etat})
    assert "forbidden" in resp.headers["location"]
    env.coeur.auth.exchange_code.assert_not_called()
    assert not env.coffre.lignes


# ── Renouvellement ──────────────────────────────────────────────────────────

def test_sans_compte_le_refus_dit_le_geste(env, monkeypatch):
    monkeypatch.setattr("oto_mcp.config.dashboard_url_for", lambda sub: "https://app")
    with pytest.raises(RuntimeError, match="No Microsoft account connected"):
        env.auth.access_token_for(SUB)


def test_renouvelle_range_le_refresh_token_tourne_puis_sert_du_cache(env):
    env.coffre.poser("RT1", {"email": "jane@contoso.example", "health_ko": True})
    assert env.auth.access_token_for(SUB) == "AT2"
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "RT2"
    assert ligne["meta"] == {"email": "jane@contoso.example"}, \
        "l'identité reste, la marque de santé tombe"
    assert env.auth.access_token_for(SUB) == "AT2"
    assert env.coeur.auth.refresh.call_count == 1, "le second appel sert du cache"


def test_autorisation_morte_marque_la_ligne(env):
    env.coffre.poser("RT1")
    env.coeur.auth.refresh.side_effect = env.coeur.MicrosoftGrantExpired("expiré")
    with pytest.raises(env.auth.MicrosoftReauthRequired, match="Reconnect"):
        env.auth.access_token_for(SUB)
    assert env.coffre.rejets and env.coffre.rejets[0][0] == MEMBRE
    assert env.coffre.lignes[("member", MEMBRE, "")]["secret"] == "RT1"


def test_secret_d_application_faux_ne_marque_pas_la_personne(env):
    env.coffre.poser("RT1")
    env.coeur.auth.refresh.side_effect = env.coeur.MicrosoftAuthError("invalid_client")
    with pytest.raises(env.coeur.MicrosoftAuthError):
        env.auth.access_token_for(SUB)
    assert not env.coffre.rejets


def test_statut_de_la_fiche(env):
    assert env.auth._etape_manquante(SUB, None, None, {}) == "Sign in with Microsoft"
    env.coffre.poser("RT1", {"health_ko": True})
    assert "reconnect" in env.auth._etape_manquante(SUB, None, None, {})
