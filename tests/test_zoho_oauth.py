"""OAuth Zoho server-based — le SECOND mode d'acquisition (le Self Client reste).

L'invariant qui rend la cohabitation sûre : **les deux modes produisent le même
credential** (client_id + client_secret + refresh_token + data_center). Le client
oto-core, la résolution et les outils ne connaissent pas le mode d'acquisition.
"""
from __future__ import annotations

import time

import pytest

from oto_mcp import zoho_oauth as z

DC = "eu"
APP = {"client_id": "1000.ORGAPP", "client_secret": "org-secret"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "test-state-secret")
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.oto.cx")


# --- l'app vient du COFFRE (cascade), jamais de l'environnement --------------

def test_app_comes_from_the_resolved_credential():
    """client_id/secret sont posés sur la carte comme n'importe quel credential —
    membre, équipe, org ou plateforme : la cascade tranche, pas une variable d'env."""
    assert z.resolve_app(APP) == ("1000.ORGAPP", "org-secret")


def test_no_app_yet_is_actionable():
    with pytest.raises(z.ZohoOAuthError, match="client id"):
        z.resolve_app({})


class _Resolved:
    def __init__(self, fields): self.fields = fields


def test_app_fields_reads_the_cascade(monkeypatch):
    seen = {}
    monkeypatch.setattr(z.access, "resolve_credential",
                        lambda con, want=None, sub=None, emit_on_failure=True, **kw:
                        seen.update(con=con, want=want, sub=sub,
                                    emit=emit_on_failure) or _Resolved(APP))
    assert z.app_fields("zohodesk", "u1") == APP
    # sub EXPLICITE (route REST, hors contexte MCP) + sonde silencieuse
    assert seen == {"con": "zohodesk", "want": "byo", "sub": "u1", "emit": False}


def test_app_fields_is_empty_before_any_credential(monkeypatch):
    """Première connexion : rien de posé — état NOMINAL, pas une erreur."""
    monkeypatch.setattr(z.access, "resolve_credential",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("none")))
    assert z.app_fields("zoho", "u1") == {}
    assert z.has_app("zoho", "u1") is False


def test_we_call_an_api_that_actually_accepts_sub():
    """GARDE-FOU. Un stub de test accepte n'importe quelle signature — il a masqué
    le vrai bug (28/07) : `resolve_credential_fields` n'a PAS de paramètre `sub`,
    l'appel levait un TypeError avalé par le except, et la connexion server-based
    était muette. On vérifie donc la signature RÉELLE de la fonction appelée."""
    import inspect
    params = inspect.signature(z.access.resolve_credential).parameters
    assert "sub" in params, "resolve_credential doit accepter un sub explicite (route REST)"
    assert "emit_on_failure" in params
    # et la fonction SANS sub reste bien inadaptée à notre usage
    assert "sub" not in inspect.signature(z.access.resolve_credential_fields).parameters


# --- state signé -------------------------------------------------------------

def test_state_roundtrip():
    st = z.make_state("u1", 35, "zohodesk", DC)
    assert z.verify_state(st) == {"sub": "u1", "org": 35,
                                  "connector": "zohodesk", "data_center": DC}


def test_forged_state_is_rejected():
    st = z.make_state("u1", 35, "zohodesk", DC)
    payload, _sig = st.split(".", 1)
    assert z.verify_state(f"{payload}.{'A' * 43}") is None


def test_state_from_another_secret_is_rejected(monkeypatch):
    st = z.make_state("u1", 35, "zohodesk", DC)
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "autre-secret")
    assert z.verify_state(st) is None


def test_expired_state_is_rejected(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_000_000)
    st = z.make_state("u1", 35, "zohodesk", DC)
    monkeypatch.setattr(time, "time", lambda: 1_000_000 + z._STATE_TTL + 1)
    assert z.verify_state(st) is None


@pytest.mark.parametrize("bad", ["", "abc", "a.b", None])
def test_malformed_state_is_rejected(bad):
    assert z.verify_state(bad) is None


# --- URL d'autorisation ------------------------------------------------------

def test_auth_url_declares_our_scopes():
    """LE bénéfice du mode : les scopes viennent de NOUS, plus de l'utilisateur —
    trois incidents de scope venaient de là (#190, #202, Desk articles-only)."""
    url = z.build_auth_url("u1", 35, "zohodesk", DC, app=APP)
    assert "accounts.zoho.eu/oauth/v2/auth" in url
    assert "Desk.search.READ" in url and "Desk.articles.READ" in url


def test_auth_url_asks_for_offline_access():
    """Sans `access_type=offline` + `prompt=consent`, Zoho ne renvoie PAS de
    refresh_token → la connexion mourrait au bout d'une heure."""
    url = z.build_auth_url("u1", 35, "zoho", DC, app=APP)
    assert "access_type=offline" in url and "prompt=consent" in url


def test_auth_url_uses_the_single_redirect_uri():
    """Une seule URI pour les 3 connecteurs (le connecteur voyage dans le state) :
    une URI doit être enregistrée au byte près côté Zoho."""
    a = z.build_auth_url("u1", 35, "zoho", DC, app=APP)
    b = z.build_auth_url("u1", 35, "zohodesk", DC, app=APP)
    assert z.redirect_uri() == "https://mcp.oto.cx/api/zoho/oauth/callback"
    for u in (a, b):
        assert "mcp.oto.cx%2Fapi%2Fzoho%2Foauth%2Fcallback" in u


def test_unknown_region_is_refused():
    with pytest.raises(z.ZohoOAuthError, match="Data center"):
        z.build_auth_url("u1", 35, "zoho", "xx", app=APP)


def test_unknown_connector_is_refused():
    with pytest.raises(z.ZohoOAuthError):
        z.build_auth_url("u1", 35, "salesforce", DC, app=APP)


# --- échange du code ---------------------------------------------------------

class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p = status, payload

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def test_exchange_sends_secrets_in_body_not_url(monkeypatch):
    """Incident #284 : en `params=`, les secrets partent dans l'URL, donc dans
    tout message d'erreur et les access logs."""
    seen = {}
    monkeypatch.setattr(z.requests, "post", lambda url, **kw: (
        seen.update(url=url, kw=kw),
        _Resp(200, {"refresh_token": "1000.rt", "access_token": "at"}))[1])
    z.exchange_code("code123", DC, app=APP)
    assert "params" not in seen["kw"]
    assert seen["kw"]["data"]["client_secret"] == "org-secret"
    assert "org-secret" not in seen["url"]


def test_expired_code_gives_an_actionable_message(monkeypatch):
    monkeypatch.setattr(z.requests, "post",
                        lambda url, **kw: _Resp(200, {"error": "invalid_code"}))
    with pytest.raises(z.ZohoOAuthError, match="expire"):
        z.exchange_code("vieux", DC, app=APP)


def test_missing_refresh_token_is_explained(monkeypatch):
    """Piège Zoho : une app déjà autorisée ne renvoie plus de refresh_token —
    silencieux et incompréhensible sans ce message."""
    monkeypatch.setattr(z.requests, "post",
                        lambda url, **kw: _Resp(200, {"access_token": "at"}))
    with pytest.raises(z.ZohoOAuthError, match="Applications connectées"):
        z.exchange_code("code", DC, app=APP)


def test_error_message_carries_no_secret(monkeypatch):
    monkeypatch.setattr(z.requests, "post",
                        lambda url, **kw: _Resp(400, {"error": "invalid_client"}))
    with pytest.raises(z.ZohoOAuthError) as e:
        z.exchange_code("code", DC, app=APP)
    assert "org-secret" not in str(e.value)


# --- persistance : MÊME forme que le Self Client -----------------------------

def test_persist_writes_the_same_fields_as_self_client(monkeypatch):
    """L'invariant de cohabitation : après un OAuth, le credential est
    indistinguable de celui posé à la main → client et résolution inchangés."""
    written = {}
    monkeypatch.setattr(z.credentials_store, "set_credential",
                        lambda et, eid, con, secret, **kw: written.update(
                            entity_type=et, entity_id=eid, connector=con,
                            secret=secret, meta=kw.get("meta")))
    z.persist("u1", 35, "zohodesk", DC,
              {"refresh_token": "1000.rt"}, app=APP)
    fields = z.credentials_store.unpack_secret("zohodesk", written["secret"])
    assert fields == {"client_id": "1000.ORGAPP", "client_secret": "org-secret",
                      "refresh_token": "1000.rt", "data_center": "eu"}
    assert written["entity_type"] == "member" and written["entity_id"] == "u1:35"
    assert written["meta"]["acquired_via"] == "oauth"


def test_supports_only_the_three_zoho_connectors():
    assert z.supports("zoho") and z.supports("zohodesk") and z.supports("zohoanalytics")
    assert not z.supports("salesforce")


# --- l'étape manquante remonte au front (seam status_hints) ------------------

def test_pending_action_when_app_posted_but_no_consent(monkeypatch):
    """Connexion en deux temps : app posée, consentement pas donné → le front doit
    pouvoir afficher l'étape suivante. Sans ça la carte paraît configurée et
    échoue au premier appel (et l'encart OAuth disparaîtrait de l'écran)."""
    from oto_mcp import status_hints
    from oto_mcp.tools import zoho as zoho_tools  # enregistre les hooks à l'import
    assert status_hints.has_hook("zohodesk")

    monkeypatch.setattr(zoho_tools.access, "resolve_credential",
                        lambda *a, **k: _Resolved({"client_id": "1000.X",
                                                   "client_secret": "s",
                                                   "data_center": "eu"}))
    action = status_hints.pending_action("zohodesk", "u1", 35, None, {"mode": "user"})
    assert action == "Autorise oto chez Zoho"


def test_no_pending_action_once_consent_given(monkeypatch):
    from oto_mcp import status_hints
    from oto_mcp.tools import zoho as zoho_tools
    monkeypatch.setattr(zoho_tools.access, "resolve_credential",
                        lambda *a, **k: _Resolved({"client_id": "1000.X",
                                                   "client_secret": "s",
                                                   "refresh_token": "1000.rt",
                                                   "data_center": "eu"}))
    assert status_hints.pending_action("zohodesk", "u1", 35, None, {"mode": "user"}) is None


def test_no_pending_action_when_nothing_posted():
    """Rien de posé → le verdict « à connecter » suffit, pas de double message."""
    from oto_mcp import status_hints
    from oto_mcp.tools import zoho  # noqa: F401
    assert status_hints.pending_action(
        "zohodesk", "u1", 35, None, {"mode": "forbidden"}) is None


# --- la sonde « tester la connexion » connaît l'état intermédiaire ------------

@pytest.mark.parametrize("module_name", ["zoho", "zohodesk"])
def test_verify_names_the_pending_consent(module_name):
    """Sans garde, la sonde tente un refresh sans refresh_token → Zoho renvoie une
    erreur de grant traduite en « refresh token périmé, régénère-le », qui envoie
    l'utilisateur régénérer ce qui n'existe pas encore (vécu au 1er test réel)."""
    import importlib
    mod = importlib.import_module(f"oto_mcp.tools.{module_name}")
    with pytest.raises(ValueError, match="autorisation n'a pas encore été donnée"):
        mod._verify({"client_id": "1000.X", "client_secret": "s", "data_center": "eu"})


def test_verify_still_runs_for_a_complete_self_client(monkeypatch):
    """Un credential complet ne doit PAS être arrêté par le garde — il va bien
    jusqu'au vrai test de connexion."""
    from oto_mcp.tools import zoho as zoho_tools
    calls = []
    monkeypatch.setattr(zoho_tools.requests, "post",
                        lambda *a, **k: calls.append(1) or _Resp(200, {"error": "x"}))
    with pytest.raises(ValueError):
        zoho_tools._verify({"client_id": "1000.X", "client_secret": "s",
                            "refresh_token": "1000.rt", "data_center": "eu"})
    assert calls, "la sonde doit avoir tenté le refresh (garde non déclenché)"


# --- A : UN calcul d'état, plusieurs surfaces --------------------------------

def test_state_is_declared_once_for_the_three_connectors():
    from oto_mcp import status_hints
    from oto_mcp.tools import zoho  # noqa: F401 — enregistre à l'import
    app_only = {"client_id": "1000.X", "client_secret": "s", "data_center": "eu"}
    for con in ("zoho", "zohodesk", "zohoanalytics"):
        st = status_hints.credential_state(con, app_only)
        assert st is not None and not st.complete
        assert st.missing == ("refresh_token",)
        assert "Autoriser oto chez Zoho" in st.next_action


def test_complete_credential_is_complete():
    from oto_mcp import status_hints
    from oto_mcp.tools import zoho  # noqa: F401
    st = status_hints.credential_state("zoho", {"client_id": "1000.X",
                                                "client_secret": "s",
                                                "refresh_token": "1000.rt"})
    assert st is not None and st.complete


def test_probe_and_verdict_share_the_same_text():
    """LE point de A : la sonde et le verdict ne re-dérivent plus chacun leur
    diagnostic — ils rendent le MÊME `next_action`. C'est la divergence entre ces
    deux textes qui a produit « refresh token périmé » face à un consentement
    simplement pas encore donné."""
    import pytest as _pytest
    from oto_mcp import status_hints
    from oto_mcp.tools import zoho as zoho_tools
    app_only = {"client_id": "1000.X", "client_secret": "s", "data_center": "eu"}
    expected = status_hints.credential_state("zoho", app_only).next_action
    with _pytest.raises(ValueError) as e:
        zoho_tools._verify(app_only)
    assert str(e.value) == expected


def test_require_complete_is_a_noop_without_declared_state():
    """Un connecteur qui ne déclare pas d'état n'est pas gêné par le seam."""
    from oto_mcp import status_hints
    status_hints.require_complete("serper", {})   # ne lève pas
    assert status_hints.credential_state("serper", {}) is None
