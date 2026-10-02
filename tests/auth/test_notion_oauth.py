"""Notion « Connecter avec Notion » — le consentement à côté du jeton collé.

L'invariant qui rend la cohabitation sûre : le consentement produit **le même
credential** qu'un jeton collé (un Bearer rangé comme la clé). Les outils et la
cascade ne connaissent pas le mode d'acquisition.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from oto_mcp import credentials_store, crypto
from oto_mcp.access.resolved_credential import ResolvedCredential
from oto_mcp.auth import flow as oauth_flow
from oto_mcp.auth import notion as n
from oto_mcp.connectors import flow as connector_flow

PLATFORM_APP = {"client_id": "plat-cid", "client_secret": "plat-secret"}
TENANT_APP = {"client_id": "ten-cid", "client_secret": "ten-secret"}
TOKENS = {"access_token": "ntn_access", "refresh_token": "ntn_refresh",
          "workspace_id": "ws-1", "workspace_name": "Acme", "bot_id": "bot-1",
          "workspace_icon": "https://icon"}


class _Ctx:
    sub, org_id = "u1", 7


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "test-state-secret")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.example.test")


@pytest.fixture
def apps(monkeypatch):
    """Apps d'éditeur posées, par clé — modifiables par le test."""
    posees = {n.PLATFORM_APP_KEY: PLATFORM_APP}
    monkeypatch.setattr(credentials_store, "get_editor_app",
                        lambda connector, key: posees.get(key) if connector == "notion" else None)
    monkeypatch.setattr(n, "_tenant_slug", lambda sub: None)
    return posees


@pytest.fixture
def coffre(monkeypatch):
    """Coffre en mémoire : (entity_type, entity_id, account) → {secret, meta}."""
    lignes: dict = {}

    def set_credential(et, eid, connector, secret, set_by=None, meta=None, account=""):
        lignes[(et, eid, account)] = {"secret": secret, "meta": dict(meta or {})}

    def list_accounts(et, eid, connector):
        return [{"account": a, "meta": credentials_store._public_meta(v["meta"])}
                for (e, i, a), v in sorted(lignes.items()) if (e, i) == (et, eid)]

    def update_meta(et, eid, connector, account, patch):
        lignes[(et, eid, account)]["meta"].update(patch)
        return True

    def get_with_meta(et, eid, connector, account=""):
        v = lignes.get((et, eid, account))
        return {"secret": v["secret"], "meta": v["meta"]} if v else None

    monkeypatch.setattr(credentials_store, "set_credential", set_credential)
    monkeypatch.setattr(credentials_store, "list_accounts", list_accounts)
    monkeypatch.setattr(credentials_store, "update_meta", update_meta)
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", get_with_meta)
    monkeypatch.setattr(credentials_store, "guard_account_write", lambda *a, **k: None)
    monkeypatch.setattr(crypto, "encrypt", lambda txt, aad: f"enc[{aad}]{txt}")
    monkeypatch.setattr(crypto, "decrypt",
                        lambda env, aad: env.removeprefix(f"enc[{aad}]"))
    return lignes


MEMBRE = (credentials_store.MEMBER, credentials_store.member_id(7, "u1"))


# --- le descripteur : une AUTRE voie que le formulaire --------------------------

def test_notion_declare_un_flux_autonome_et_zoho_reste_en_deux_temps():
    """Le front offre d'emblée un flux `standalone` ; un flux qui complète le
    formulaire (zoho, salesforce) garde « enregistrer puis autoriser »."""
    from oto_mcp.tools import zoho  # noqa: F401 — déclare le flux zoho
    assert connector_flow.describe("notion")["standalone"] is True
    assert connector_flow.describe("zoho")["standalone"] is False


# --- l'URL de consentement ------------------------------------------------------

def test_url_de_consentement_porte_owner_user_et_un_state_de_ce_flux(apps):
    url = n.build_auth_url("u1", 7)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    assert q["owner"] == ["user"] and q["response_type"] == ["code"]
    assert q["client_id"] == ["plat-cid"]
    assert q["redirect_uri"] == ["https://mcp.example.test/api/notion/oauth/callback"]
    st = n.verify_state(q["state"][0])
    assert st["sub"] == "u1" and st["org"] == 7 and st["scope"] == "member"
    assert st["app_key"] == n.PLATFORM_APP_KEY


def test_un_state_d_un_autre_flux_est_refuse():
    autre = oauth_flow.sign_state("zoho", {"sub": "u1", "org": 7, "s": "member", "k": "x"})
    assert n.verify_state(autre) is None


def test_l_app_du_tenant_prime_et_rappelle_sur_son_host(apps, monkeypatch):
    from oto_mcp import tenancy
    apps[credentials_store.tenant_app_key("acme")] = TENANT_APP
    monkeypatch.setattr(n, "_tenant_slug", lambda sub: "acme")

    class _Registre:
        def callback_host(self, slug):
            return "app.acme.test" if slug == "acme" else None
    monkeypatch.setattr(tenancy, "current", lambda: _Registre())
    q = urllib.parse.parse_qs(urllib.parse.urlparse(n.build_auth_url("u1", 7)).query)
    assert q["client_id"] == ["ten-cid"]
    assert q["redirect_uri"] == ["https://app.acme.test/api/notion/oauth/callback"]


def test_sans_app_le_bouton_n_est_pas_pret_et_le_demarrage_le_dit(apps):
    from oto_mcp.capabilities._types import AuthzDenied
    apps.clear()
    assert n.app_disponible("u1") is False
    with pytest.raises(AuthzDenied) as e:
        n._start_flow(_Ctx(), {})
    assert e.value.status == 503 and "jeton" in str(e.value)


def test_connecter_pour_l_org_exige_un_admin(apps, monkeypatch):
    from oto_mcp import detenteurs, roles
    from oto_mcp.capabilities._types import AuthzDenied
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: False)
    monkeypatch.setattr(detenteurs, "admins_de_l_org", lambda sub, org: [])
    with pytest.raises(AuthzDenied) as e:
        n._start_flow(_Ctx(), {"scope": "org"})
    assert e.value.status == 403


# --- l'échange : HTTP Basic + JSON, jamais de secret dans l'URL -----------------

class _Resp:
    def __init__(self, code, payload):
        self.status_code, self._p = code, payload

    def json(self):
        return self._p


def test_echange_en_basic_et_json(apps, monkeypatch):
    vu = {}
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda url, **kw: (vu.update(url=url, kw=kw), _Resp(200, TOKENS))[1])
    assert n.exchange_code("CODE", n.PLATFORM_APP_KEY) == TOKENS
    assert vu["url"] == "https://api.notion.com/v1/oauth/token"
    assert vu["kw"]["auth"] == ("plat-cid", "plat-secret")
    assert vu["kw"]["json"]["code"] == "CODE"
    assert "client_secret" not in vu["kw"]["json"] and "data" not in vu["kw"]
    assert vu["kw"]["json"]["redirect_uri"].endswith("/api/notion/oauth/callback")


def test_la_forme_par_defaut_reste_le_corps_form_encode(monkeypatch):
    vu = {}
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda url, **kw: (vu.update(kw=kw), _Resp(200, {"a": 1}))[1])
    oauth_flow.exchange_code("https://x/token", code="c", client_id="i",
                             client_secret="s", redirect="https://r")
    assert vu["kw"]["data"]["client_secret"] == "s" and "auth" not in vu["kw"]


# --- le rangement ----------------------------------------------------------------

def test_le_jeton_est_range_comme_une_cle_et_le_refresh_chiffre(apps, coffre):
    compte = n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, TOKENS)
    ligne = coffre[(*MEMBRE, compte)]
    assert compte == "Acme"
    assert ligne["secret"] == "ntn_access"           # même forme qu'un jeton collé
    assert ligne["meta"]["workspace_id"] == "ws-1"
    assert ligne["meta"]["is_default"] is True
    assert "ntn_refresh" not in str(credentials_store._public_meta(ligne["meta"]))
    assert "refresh_token" not in ligne["meta"]      # jamais en clair
    assert ligne["meta"]["refresh_token_enc"].endswith("ntn_refresh")
    assert "expires_at" not in ligne["meta"]         # Notion n'en a pas donné


def test_reconnecter_le_meme_espace_remplace_sa_ligne_meme_renomme(apps, coffre):
    n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, TOKENS)
    n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY,
              {**TOKENS, "workspace_name": "Acme Renamed", "access_token": "neuf"})
    lignes = {k: v for k, v in coffre.items() if k[:2] == MEMBRE}
    assert list(lignes) == [(*MEMBRE, "Acme")]
    assert lignes[(*MEMBRE, "Acme")]["secret"] == "neuf"


def test_le_nouveau_compte_devient_le_defaut_sans_detruire_l_ancien(apps, coffre):
    """Une personne qui avait collé un jeton puis se connecte en un clic : rien
    n'est détruit, mais ses appels passent par le nouveau (sinon : ambiguïté)."""
    coffre[(*MEMBRE, "principal")] = {"secret": "colle", "meta": {"is_default": True}}
    compte = n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, TOKENS)
    assert coffre[(*MEMBRE, "principal")]["secret"] == "colle"
    assert coffre[(*MEMBRE, "principal")]["meta"]["is_default"] is False
    assert coffre[(*MEMBRE, compte)]["meta"]["is_default"] is True


def test_un_nom_d_espace_deja_pris_par_un_autre_espace_est_suffixe(apps, coffre):
    coffre[(*MEMBRE, "Acme")] = {"secret": "x", "meta": {"workspace_id": "ws-autre"}}
    assert n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, TOKENS) == "Acme (2)"


# --- le renouvellement à l'usage --------------------------------------------------

def _rc(account="Acme"):
    return ResolvedCredential(provider="notion", secret="ntn_access", is_platform=False,
                              mode="user", entity_type=MEMBRE[0], entity_id=MEMBRE[1],
                              account=account)


def test_sans_echeance_rien_ne_se_renouvelle(apps, coffre, monkeypatch):
    n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, TOKENS)
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda *a, **k: pytest.fail("aucun appel attendu"))
    assert n.fresh_key(_rc()) == "ntn_access"


def test_un_jeton_colle_ne_lit_que_sa_ligne(coffre):
    coffre[(*MEMBRE, "")] = {"secret": "colle", "meta": {}}
    rc = _rc(account="")
    assert n.fresh_key(rc) == rc.key


def test_une_echeance_proche_renouvelle_et_range_la_paire_rotee(apps, coffre, monkeypatch):
    n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, {**TOKENS, "expires_in": 60})
    vu = {}
    monkeypatch.setattr(oauth_flow.requests, "post", lambda url, **kw: (
        vu.update(kw=kw),
        _Resp(200, {"access_token": "frais", "refresh_token": "rote", "expires_in": 28800}))[1])
    assert n.fresh_key(_rc()) == "frais"
    assert vu["kw"]["json"] == {"grant_type": "refresh_token", "refresh_token": "ntn_refresh"}
    ligne = coffre[(*MEMBRE, "Acme")]
    assert ligne["secret"] == "frais"
    assert ligne["meta"]["refresh_token_enc"].endswith("rote")
    assert ligne["meta"]["workspace_id"] == "ws-1"   # meta re-passé complet
    echeance = datetime.fromisoformat(ligne["meta"]["expires_at"])
    assert echeance - datetime.now(timezone.utc) > timedelta(hours=7)


def test_un_renouvellement_refuse_dit_de_reconnecter(apps, coffre, monkeypatch):
    n.persist("u1", 7, "member", None, n.PLATFORM_APP_KEY, {**TOKENS, "expires_in": 0})
    monkeypatch.setattr(oauth_flow.requests, "post",
                        lambda url, **kw: _Resp(400, {"error": "invalid_grant"}))
    with pytest.raises(n.NotionOAuthError, match="reconnecte Notion"):
        n.fresh_key(_rc())


# --- le retour de consentement ----------------------------------------------------

@pytest.fixture
def client():
    from oto_mcp.api import notion as api_notion
    routes = api_notion.make_routes(None, None, None, None, None)
    return TestClient(Starlette(routes=routes), follow_redirects=False)


def test_un_refus_revient_en_forbidden(apps, client):
    state = n.make_state("u1", 7, "member", None, n.PLATFORM_APP_KEY)
    r = client.get(f"/api/notion/oauth/callback?error=access_denied&state={state}")
    assert r.status_code == 302 and "connect=forbidden" in r.headers["location"]


def test_un_state_illisible_revient_en_erreur(client):
    r = client.get("/api/notion/oauth/callback?code=c&state=faux")
    assert r.status_code == 302 and "connect=error" in r.headers["location"]


def test_un_consentement_abouti_range_et_revient_connecte(apps, coffre, client, monkeypatch):
    monkeypatch.setattr(oauth_flow.requests, "post", lambda url, **kw: _Resp(200, TOKENS))
    state = n.make_state("u1", 7, "member", None, n.PLATFORM_APP_KEY)
    r = client.get(f"/api/notion/oauth/callback?code=c&state={state}")
    assert r.status_code == 302 and "connect=connected" in r.headers["location"]
    assert "connector=notion" in r.headers["location"]
    assert coffre[(*MEMBRE, "Acme")]["secret"] == "ntn_access"
