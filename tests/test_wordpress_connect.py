"""Flux « Connecter » WordPress — ce qui protège un credential posé depuis un
retour de navigateur non authentifié : state signé et lié à son audience, site
renvoyé = site demandé, vérification AVANT la pose, compte nommé par l'hôte,
et le mot de passe (en query) retiré du journal d'accès et de Sentry."""
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from oto_mcp.auth import flow as oauth_flow
from oto_mcp.auth import wordpress as wp_auth


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "test-secret")
    monkeypatch.setattr(oauth_flow, "redirect_uri",
                        lambda path, host=None: f"https://mcp.example.test{path}")


@pytest.fixture
def ctx(monkeypatch):
    from oto_mcp import access, egress

    monkeypatch.setattr(access, "current_org", lambda sub: 12)
    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)
    monkeypatch.setattr(wp_auth, "_app_name", lambda org: "Brand")
    return SimpleNamespace(sub="user-1")


def _index_with(endpoint):
    def fake(site):
        return endpoint
    return fake


def test_start_builds_native_authorization_url(ctx, monkeypatch):
    monkeypatch.setattr(wp_auth, "_authorization_endpoint",
                        _index_with("https://blog.example.com/wp-admin/authorize-application.php"))
    out = wp_auth.start(ctx, {"site_url": "blog.example.com/", "app": "unknown-front"})
    u = urlsplit(out.auth_url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert u.path == "/wp-admin/authorize-application.php"
    assert q["app_name"] == "Brand" and q["app_id"] == wp_auth.APP_ID
    assert q["success_url"].startswith("https://mcp.example.test/api/wordpress/connect/callback?state=")
    assert "success=false" in q["reject_url"]
    state = parse_qs(urlsplit(q["success_url"]).query)["state"][0]
    parsed = wp_auth.read_state(state)
    assert parsed["site"] == "https://blog.example.com" and parsed["org"] == 12
    assert parsed["app"] == ""                     # front hors liste fermée → défaut
    assert out.details["account"] == "blog.example.com"


def test_start_refuses_empty_site_and_foreign_auth_host(ctx, monkeypatch):
    with pytest.raises(wp_auth.ConnectRefused):
        wp_auth.start(ctx, {"site_url": ""})
    monkeypatch.setattr(wp_auth, "_authorization_endpoint",
                        _index_with("https://evil.example.net/authorize"))
    with pytest.raises(wp_auth.ConnectRefused, match="autre hôte"):
        wp_auth.start(ctx, {"site_url": "https://blog.example.com"})


def test_org_scope_requires_admin(ctx, monkeypatch):
    from oto_mcp import roles

    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: False)
    with pytest.raises(PermissionError):
        wp_auth.start(ctx, {"site_url": "https://blog.example.com", "scope": "org"})


def test_state_of_another_flow_is_rejected():
    other = oauth_flow.sign_state("zoho", {"sub": "u", "org": 1, "scope": "member",
                                           "site": "https://x"})
    assert wp_auth.read_state(other) is None
    assert wp_auth.read_state("garbage") is None


def _parsed():
    return {"sub": "user-1", "org": 12, "scope": "member", "site": "https://blog.example.com",
            "app": ""}


def test_finish_refuses_substituted_site(monkeypatch):
    with pytest.raises(wp_auth.ConnectRefused, match="ne correspond pas"):
        wp_auth.finish(_parsed(), "https://other.example.com", "julien", "pw")


def test_finish_verifies_then_persists_under_host_account(monkeypatch):
    from oto_mcp import credentials_store, egress
    from oto.tools.wordpress import client as wp_client

    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)
    monkeypatch.setattr(wp_client.WordPressClient, "me", lambda self: {"id": 4})
    seen = {}
    monkeypatch.setattr(credentials_store, "guard_account_write",
                        lambda *a, **k: seen.setdefault("guard", a))
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda *a, **k: seen.setdefault("set", (a, k)))
    account = wp_auth.finish(_parsed(), "https://blog.example.com", "julien", "ab cd")
    assert account == "blog.example.com"
    args, kw = seen["set"]
    assert args[0] == credentials_store.MEMBER and args[2] == "wordpress"
    assert kw["account"] == "blog.example.com" and kw["meta"]["connected_via"]
    # Le coffre retire les espaces (`clean_field_value`) ; WordPress les ignore aussi.
    assert credentials_store.unpack_secret("wordpress", args[3])["application_password"] == "abcd"


def test_finish_does_not_persist_when_password_fails(monkeypatch):
    from oto_mcp import credentials_store, egress
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.wordpress import client as wp_client

    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)

    def refuse(self):
        raise UpstreamHTTPError(401, {"code": "incorrect_password"})

    monkeypatch.setattr(wp_client.WordPressClient, "me", refuse)
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda *a, **k: pytest.fail("posé malgré un refus"))
    with pytest.raises(UpstreamHTTPError):
        wp_auth.finish(_parsed(), "", "julien", "bad")


def test_access_log_redacts_password():
    from oto_mcp.api.wordpress import _RedactSensitiveQuery

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET",
                             "/api/wordpress/connect/callback?state=s&password=SECRET",
                             "1.1", 302), None)
    _RedactSensitiveQuery().filter(rec)
    assert "SECRET" not in rec.getMessage()
    other = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %d",
                              ("c", "GET", "/api/x?y=1", "1.1", 200), None)
    _RedactSensitiveQuery().filter(other)
    assert "y=1" in other.getMessage()


def test_sentry_event_redacts_password():
    from oto_mcp.sentry_setup import _redact_sensitive_query

    ev = {"request": {"url": "https://mcp.example.test/api/wordpress/connect/callback?password=S",
                      "query_string": "password=S"}}
    _redact_sensitive_query(ev)
    assert "S" not in ev["request"]["query_string"] and "?" not in ev["request"]["url"]


@pytest.mark.parametrize("site,account", [
    ("https://blog.example.com", "blog.example.com"),
    ("https://Example.com/blog", "example.com/blog"),
    ("http://127.0.0.1:9400", "127.0.0.1:9400"),
    ("https://example.com:443", "example.com"),
])
def test_account_name_is_host_port_path(site, account):
    assert wp_auth.account_for(site) == account


def test_authorization_endpoint_reads_index_without_credentials(monkeypatch):
    # WordPress vérifie un Basic factice sur l'index public aussi : le démarrage
    # ne doit JAMAIS en envoyer, sinon « Connecter » échoue sur tout site réel.
    from oto.tools.wordpress import client as wp_client

    def no_auth_request(*a, **k):
        raise AssertionError("appel authentifié au démarrage du flux")

    monkeypatch.setattr(wp_client.WordPressClient, "request", no_auth_request)
    monkeypatch.setattr(wp_client.WordPressClient, "public_index", lambda self: {
        "authentication": {"application-passwords": {"endpoints": {
            "authorization": "https://blog.example.com/wp-admin/authorize-application.php"}}}})
    assert wp_auth._authorization_endpoint("https://blog.example.com").endswith(
        "authorize-application.php")


def test_sentry_transaction_redacts_password():
    from oto_mcp.sentry_setup import _before_send_transaction

    ev = {"request": {"url": "https://h/api/wordpress/connect/callback?password=S",
                      "query_string": "password=S"}}
    assert "password" not in str(_before_send_transaction(ev, {}))


def test_reconnect_without_site_reuses_saved_credential(ctx, monkeypatch):
    # Le bouton « Connecter » de la fiche d'accès ne poste pas de site_url.
    from oto_mcp import access

    monkeypatch.setattr(access, "resolve_credential", lambda *a, **k: SimpleNamespace(
        fields={"site_url": "https://blog.example.com"}), raising=False)
    monkeypatch.setattr(wp_auth, "_authorization_endpoint",
                        _index_with("https://blog.example.com/wp-admin/authorize-application.php"))
    assert wp_auth.start(ctx, {}).details["site"] == "https://blog.example.com"
