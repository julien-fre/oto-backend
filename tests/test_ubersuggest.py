"""Le connecteur `ubersuggest` — connexion OAuth de la personne (client public, PKCE,
enregistrement dynamique) et les sept outils qui la dépensent.

⚠️ Le cœur est MOQUÉ à sa frontière (`oto.tools.ubersuggest` posé dans
`sys.modules`), le coffre et les réglages sont des faux en mémoire : aucun appel
réel à Ubersuggest ni à la base. La RÉSOLUTION du credential, elle, est la vraie
(`access.resolve_credential`). Vérifié : le registre, le flux hébergé (state porteur
du vérificateur PKCE, client enregistré une fois puis gardé), le retour de connexion,
le renouvellement (cache, rotation, autorisation morte MARQUÉE et jamais purgée), et
côté outils la correspondance op → outil amont, le refus d'un argument que l'amont ne
prend pas, l'argument requis nommé, la vue resserrée, la garde des crédits.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from oto_mcp import providers
from oto_mcp.mcp_errors import McpError

CONNECTEUR = "ubersuggest"
ORG = 42
SUB = "user-de-test"
MEMBRE = f"{ORG}:{SUB}"
_RETOUR = "https://mcp.exemple.test/api/ubersuggest/oauth/callback"


def _grant(access="AT1", refresh="RT1", expires_in=3600):
    return types.SimpleNamespace(access_token=access, refresh_token=refresh,
                                 expires_in=expires_in, scope="keywords domain")


class _UpstreamHTTPError(Exception):
    def __init__(self, status_code, body=None):
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code, self.body = status_code, body


def _faux_coeur():
    mod = types.ModuleType("oto.tools.ubersuggest")
    auth = types.ModuleType("oto.tools.ubersuggest.auth")

    class UbersuggestAuthError(ValueError):
        status_code = 401

    class UbersuggestGrantExpired(UbersuggestAuthError):
        pass

    auth.register_client = MagicMock(return_value="client-enregistre")
    auth.authorize_url = MagicMock(return_value="https://ubersuggest.example/authorize?x=1")
    auth.exchange_code = MagicMock(return_value=_grant())
    auth.refresh = MagicMock(return_value=_grant("AT2", "RT2"))
    mod.auth = auth
    mod.UbersuggestAuthError = UbersuggestAuthError
    mod.UbersuggestGrantExpired = UbersuggestGrantExpired
    mod.UbersuggestClient = MagicMock(name="UbersuggestClient")
    return mod


class _Coffre:
    def __init__(self):
        self.lignes: dict[tuple, dict] = {}

    def poser(self, secret, meta=None):
        self.lignes[("member", MEMBRE, "")] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": SUB,
            "set_at": "2026-10-08T00:00:00Z"}

    def get_with_meta(self, entity_type, entity_id, connector, account=""):
        assert connector == CONNECTEUR
        ligne = self.lignes.get((entity_type, entity_id, account))
        return {**ligne, "meta": dict(ligne["meta"])} if ligne else None

    def get(self, entity_type, entity_id, connector, account=""):
        ligne = self.get_with_meta(entity_type, entity_id, connector, account)
        return ligne["secret"] if ligne else None

    def set(self, entity_type, entity_id, connector, secret, set_by=None,
            meta=None, conn=None, account="", expected_version=None):
        assert connector == CONNECTEUR
        self.lignes[(entity_type, entity_id, account)] = {
            "secret": secret, "meta": dict(meta or {}), "set_by": set_by,
            "set_at": "2026-10-08T01:00:00Z"}

    def list_accounts(self, entity_type, entity_id, connector):
        return [{"account": a, "meta": dict(l["meta"]), "set_at": l["set_at"]}
                for (et, eid, a), l in sorted(self.lignes.items())
                if (et, eid) == (entity_type, entity_id)]

    def update_meta(self, entity_type, entity_id, connector, account, patch, conn=None):
        ligne = self.lignes.get((entity_type, entity_id, account))
        if ligne is None:
            return False
        ligne["meta"].update(patch)
        return True


@pytest.fixture
def env(monkeypatch):
    from oto_mcp import access, credentials_store, db
    from oto_mcp.auth import ubersuggest as ub_auth
    from oto_mcp.connectors import cardinality
    from oto_mcp.db import connector_settings as store

    mod = _faux_coeur()
    monkeypatch.setitem(sys.modules, "oto.tools.ubersuggest", mod)
    monkeypatch.setitem(sys.modules, "oto.tools.ubersuggest.auth", mod.auth)
    reglages: dict = {}
    monkeypatch.setattr(store, "get_connector_setting",
                        lambda st, sid, c, k: reglages.get((st, sid, c, k)))
    monkeypatch.setattr(store, "set_connector_setting",
                        lambda st, sid, c, k, v, set_by=None:
                        reglages.__setitem__((st, sid, c, k), v))
    coffre = _Coffre()
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", coffre.get_with_meta)
    monkeypatch.setattr(credentials_store, "get_credential", coffre.get)
    monkeypatch.setattr(credentials_store, "set_credential", coffre.set)
    monkeypatch.setattr(credentials_store, "list_accounts", coffre.list_accounts)
    monkeypatch.setattr(credentials_store, "update_meta", coffre.update_meta)
    monkeypatch.setattr(db, "member_instance_suspended", lambda *a, **k: False)
    monkeypatch.setattr(db, "insert_tool_call", lambda *a, **k: None)
    monkeypatch.setenv("OTO_L7_SHADOW", "0")
    monkeypatch.setattr(access, "current_org", lambda sub: ORG)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access, "project_pinned_identity", lambda p, project_id=None: None)
    monkeypatch.setattr(access, "current_user_sub_or_raise", lambda: SUB)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.exemple.test")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-signature-de-test")
    monkeypatch.setattr(ub_auth, "_JETONS", {})
    return types.SimpleNamespace(coeur=mod, coffre=coffre, auth=ub_auth, reglages=reglages)


# ── Registre ────────────────────────────────────────────────────────────────

def test_registre():
    c = providers.REGISTRY[CONNECTEUR]
    assert c.secret_kind == "oauth" and c.auth_modes == frozenset({"byo_user"})
    assert c.publisher_name == "Ubersuggest"
    assert not c.credential_fields
    assert c.doc_sections, "la fiche doit être servie depuis son markdown"


# ── Flux hébergé ────────────────────────────────────────────────────────────

def test_url_de_retour_derivee_de_l_environnement(env):
    from oto_mcp.connectors import flow as connector_flow

    assert connector_flow.supports(CONNECTEUR)
    assert connector_flow.callback_url(CONNECTEUR) == _RETOUR


def test_state_porte_le_verificateur_et_ne_vaut_que_pour_ce_flux(env):
    from oto_mcp.auth import flow as oauth_flow

    etat = env.auth.make_state(SUB, ORG, "verif", "")
    assert env.auth.verify_state(etat) == (SUB, ORG, "verif", "")
    assert env.auth.verify_state(oauth_flow.sign_state(
        "microsoft", {"sub": SUB, "org": ORG, "v": "verif"})) is None
    assert env.auth.verify_state(oauth_flow.sign_state(
        "ubersuggest", {"sub": SUB, "org": ORG})) is None


def test_le_client_s_enregistre_une_fois_puis_se_garde(env):
    assert env.auth.client_id() == "client-enregistre"
    assert env.auth.client_id() == "client-enregistre"
    env.coeur.auth.register_client.assert_called_once()
    assert env.coeur.auth.register_client.call_args.args[0] == [_RETOUR]
    assert env.reglages[("platform", "platform", CONNECTEUR, "client_id")] == "client-enregistre"


def test_un_client_pose_par_l_operateur_prime(env):
    env.reglages[("platform", "platform", CONNECTEUR, "client_id")] = "pose-a-la-main"
    assert env.auth.client_id() == "pose-a-la-main"
    env.coeur.auth.register_client.assert_not_called()


def test_le_dialogue_porte_le_defi_du_verificateur_signe(env):
    import base64
    import hashlib

    env.auth.build_auth_url(SUB, "")
    client_id, retour, etat, defi = env.coeur.auth.authorize_url.call_args.args
    assert client_id == "client-enregistre" and retour == _RETOUR
    sub, org, verif, _ = env.auth.verify_state(etat)
    assert (sub, org) == (SUB, ORG)
    attendu = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).rstrip(b"=")
    assert defi == attendu.decode()


# ── Retour de connexion ─────────────────────────────────────────────────────

def _callback(query: dict):
    from starlette.requests import Request

    from oto_mcp.api import ubersuggest as api_ub

    route = api_ub.make_routes(None, None, None, None, None)[0]
    qs = "&".join(f"{k}={v}" for k, v in query.items()).encode()
    req = Request({"type": "http", "method": "GET", "path": route.path,
                   "query_string": qs, "headers": []})
    return asyncio.run(route.endpoint(req))


def test_retour_echange_le_code_avec_le_verificateur_et_range_le_refresh_token(env):
    etat = env.auth.make_state(SUB, ORG, "verif", "")
    resp = _callback({"code": "le-code", "state": etat})
    assert resp.status_code == 302 and "connected" in resp.headers["location"]
    assert env.coeur.auth.exchange_code.call_args.args == (
        "client-enregistre", "le-code", _RETOUR, "verif")
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "RT1"


def test_retour_refuse_par_la_personne(env):
    etat = env.auth.make_state(SUB, ORG, "verif", "")
    resp = _callback({"error": "access_denied", "state": etat})
    assert "forbidden" in resp.headers["location"]
    env.coeur.auth.exchange_code.assert_not_called()


def test_retour_sans_state_lisible(env):
    resp = _callback({"code": "x", "state": "faux.state"})
    assert "error" in resp.headers["location"]
    env.coeur.auth.exchange_code.assert_not_called()


# ── Renouvellement ──────────────────────────────────────────────────────────

def test_renouvelle_garde_en_cache_et_range_la_rotation(env):
    env.coffre.poser("RT1")
    assert env.auth.access_token_for(SUB) == "AT2"
    assert env.coeur.auth.refresh.call_args.args == ("client-enregistre", "RT1")
    assert env.coffre.lignes[("member", MEMBRE, "")]["secret"] == "RT2"
    assert env.auth.access_token_for(SUB) == "AT2"
    assert env.coeur.auth.refresh.call_count == 1


def test_autorisation_morte_marque_la_ligne_sans_la_purger(env):
    env.coffre.poser("RT1")
    env.coeur.auth.refresh.side_effect = env.coeur.UbersuggestGrantExpired("invalid_grant")
    with pytest.raises(env.auth.UbersuggestReauthRequired):
        env.auth.access_token_for(SUB)
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "RT1" and ligne["meta"]["health_ko"] is True
    assert env.auth._etape_manquante(SUB, None, None, {}) == "Sign-in expired — reconnect"


def test_non_connecte_dit_quoi_faire(env):
    assert env.auth._etape_manquante(SUB, None, None, {}) == "Sign in with Ubersuggest"


# ── Outils ──────────────────────────────────────────────────────────────────

@pytest.fixture
def outils(env, monkeypatch):
    from fastmcp import FastMCP
    from oto.tools.common import errors as core_errors

    from oto_mcp.tools import ubersuggest as ub_tools

    monkeypatch.setattr(core_errors, "UpstreamHTTPError", _UpstreamHTTPError)
    env.coffre.poser("RT1")
    m = FastMCP("t")
    ub_tools.register(m)
    client = env.coeur.UbersuggestClient.return_value
    return types.SimpleNamespace(
        fn=lambda name: asyncio.run(m.get_tool(name)).fn, client=client)


def test_op_devient_l_outil_amont_avec_ses_noms(outils):
    outils.client.call.return_value = {"search_volume": 880}
    out = outils.fn("ubersuggest_keywords")(op="overview", keyword="crm", loc_id=2250,
                                            language="fr")
    assert out == {"search_volume": 880}
    assert outils.client.call.call_args.args == (
        "keyword_overview", {"keyword": "crm", "locId": 2250, "language": "fr"})


def test_offset_devient_previous_key_la_ou_l_amont_le_nomme_ainsi(outils):
    outils.client.call.return_value = []
    outils.fn("ubersuggest_domain")(op="keywords", domain="x.com", offset=50)
    assert outils.client.call.call_args.args == (
        "domain_keywords", {"domain": "x.com", "previousKey": 50})


def test_argument_que_l_amont_ne_prend_pas_est_refuse(outils):
    with pytest.raises(McpError, match="does not take `keyword`"):
        outils.fn("ubersuggest_domain")(op="overview", domain="x.com",
                                        params={"keyword": "crm"})
    with pytest.raises(McpError, match="does not take `limit`"):
        outils.fn("ubersuggest_keywords")(op="overview", keyword="crm", limit=5)
    outils.client.call.assert_not_called()


def test_argument_requis_nomme(outils):
    with pytest.raises(McpError, match="requires `lang_locs`"):
        outils.fn("ubersuggest_domain")(op="top_countries", domain="x.com")


def test_vue_resserree_par_defaut_brute_sur_demande(outils):
    lignes = [{"keyword": "crm", "volume": 10, "cpc": None, "monthly_searches": [1, 2]}]
    outils.client.call.return_value = {"suggestions": lignes, "nextKey": 50}
    out = outils.fn("ubersuggest_keywords")(op="match", keywords=["crm"])
    assert out == {"suggestions": [{"keyword": "crm", "volume": 10}], "nextKey": 50}
    outils.client.call.return_value = {"suggestions": lignes, "nextKey": 50}
    brut = outils.fn("ubersuggest_keywords")(op="match", keywords=["crm"], full=True)
    assert brut["suggestions"] == lignes


def test_un_article_exige_l_accord_sur_les_credits(outils):
    with pytest.raises(McpError, match="100 monthly Ubersuggest credits"):
        outils.fn("ubersuggest_projects")(
            op="generate_article", project_id="p1", keyword="crm",
            params={"title": "T", "content_idea": "C"})
    outils.client.call.assert_not_called()
    outils.client.call.return_value = {"article_id": "a1"}
    outils.fn("ubersuggest_projects")(
        op="generate_article", project_id="p1", keyword="crm",
        params={"title": "T", "content_idea": "C"}, confirm_credits=True)
    assert outils.client.call.call_args.args[0] == "generate_article"


def test_suppression_de_liste_non_exposee(outils):
    with pytest.raises(McpError, match="op must be one of"):
        outils.fn("ubersuggest_keyword_lists")(op="delete", list_id="l1")


def test_refus_amont_nomme_et_401_dit_de_reconnecter(outils):
    outils.client.call.side_effect = _UpstreamHTTPError(422, "Plan limit reached")
    with pytest.raises(McpError, match="Plan limit reached"):
        outils.fn("ubersuggest_backlinks")(op="overview", domain="x.com")
    outils.client.call.side_effect = _UpstreamHTTPError(401, "invalid_token")
    with pytest.raises(McpError, match="reconnect"):
        outils.fn("ubersuggest_account")()


def test_renouvellement_refuse_autrement_est_un_refus_nomme(env, outils):
    env.coeur.auth.refresh.side_effect = env.coeur.UbersuggestAuthError("invalid_client")
    with pytest.raises(McpError, match="invalid_client"):
        outils.fn("ubersuggest_account")()
    outils.client.call.assert_not_called()


def test_5xx_reste_ce_qu_il_est(outils):
    outils.client.call.side_effect = _UpstreamHTTPError(503, "down")
    with pytest.raises(_UpstreamHTTPError):
        outils.fn("ubersuggest_account")()


def test_chaque_op_vise_un_outil_amont_decrit():
    from oto_mcp.tools import ubersuggest as ub_tools

    tables = (ub_tools._KEYWORDS_OPS, ub_tools._DOMAIN_OPS, ub_tools._BACKLINKS_OPS,
              ub_tools._AUDIT_OPS, ub_tools._LISTS_OPS, ub_tools._PROJECTS_OPS,
              ub_tools._ACCOUNT_OPS)
    vises = {u for t in tables for u in t.values()}
    assert vises <= set(ub_tools._SPEC)
    assert not {"delete_keyword_list", "onboard_project", "configure_brand"} & vises
