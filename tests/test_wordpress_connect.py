"""Flux « Connecter » WordPress — ce qui protège un credential posé depuis un
retour de navigateur non authentifié : state signé, lié à son audience et à usage
unique, droits re-vérifiés au retour, HTTPS, site renvoyé = site demandé,
vérification AVANT la pose, compte nommé par l'hôte, et le mot de passe (en query)
retiré du journal d'accès et de Sentry."""
import asyncio
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
    def fake(site, **kw):
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


def _nothing_saved(*a, **k):
    from mcp.types import ErrorData, INVALID_PARAMS
    from oto_mcp.access import CredentialUnavailable

    raise CredentialUnavailable(ErrorData(code=INVALID_PARAMS, message="aucun"))


def test_start_refuses_empty_site_and_foreign_auth_host(ctx, monkeypatch):
    from oto_mcp import access

    monkeypatch.setattr(access, "resolve_credential", _nothing_saved)
    with pytest.raises(wp_auth.ConnectRefused, match="enter your WordPress site's URL"):
        wp_auth.start(ctx, {"site_url": ""})
    monkeypatch.setattr(wp_auth, "_authorization_endpoint",
                        _index_with("https://evil.example.net/authorize"))
    with pytest.raises(wp_auth.ConnectRefused, match="another host"):
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


def _parsed(**over):
    return {"sub": "user-1", "org": 12, "scope": "member", "site": "https://blog.example.com",
            "app": "", "jti": "j-1", **over}


def test_finish_refuses_substituted_site(monkeypatch):
    with pytest.raises(wp_auth.ConnectRefused, match="does not match"):
        wp_auth.finish(_parsed(), "https://other.example.com", "editor", "pw")


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
    account = wp_auth.finish(_parsed(), "https://blog.example.com", "editor", "ab cd")
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
        wp_auth.finish(_parsed(), "", "editor", "bad")


@pytest.mark.parametrize("path", ["/api/wordpress/connect/callback",
                                  "/api/wordpress/connect/callback/"])
def test_access_log_redacts_password(path):
    # Le filtre COMMUN du journal d'accès (posé par `server.main`), pas un filtre de plus.
    from oto_mcp.journal_secrets import MasqueCheminAcces

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", f"{path}?state=s&password=SECRET",
                             "1.1", 302), None)
    MasqueCheminAcces().filter(rec)
    assert "SECRET" not in rec.getMessage() and "[redacted]" in rec.getMessage()
    other = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %d",
                              ("c", "GET", "/api/x?y=1", "1.1", 200), None)
    MasqueCheminAcces().filter(other)
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


def test_state_without_jti_is_rejected():
    legacy = oauth_flow.sign_state(wp_auth.AUD, {"sub": "u", "org": 1, "scope": "member",
                                                 "site": "https://x", "app": ""})
    assert wp_auth.read_state(legacy) is None


def test_reconnect_with_nothing_saved_is_named(ctx, monkeypatch):
    from oto_mcp import access

    monkeypatch.setattr(access, "resolve_credential", _nothing_saved)
    with pytest.raises(wp_auth.ConnectRefused, match="enter your WordPress site's URL"):
        wp_auth.start(ctx, {})


def test_reconnect_other_refusal_is_said_not_masked(ctx, monkeypatch):
    # Plusieurs sites posés : le refus de la cascade dit lequel choisir — jamais
    # « indique l'URL », qui ferait croire que rien n'est posé.
    from mcp.types import ErrorData, INVALID_PARAMS
    from oto_mcp import access
    from oto_mcp.mcp_errors import McpError

    def ambiguous(*a, **k):
        assert k.get("check_usage") is False      # configurer n'use aucun quota
        raise McpError(ErrorData(code=INVALID_PARAMS, message="plusieurs sites : précise"))

    monkeypatch.setattr(access, "resolve_credential", ambiguous)
    with pytest.raises(wp_auth.ConnectRefused, match="plusieurs sites"):
        wp_auth.start(ctx, {})


def test_reconnect_db_failure_propagates(ctx, monkeypatch):
    from oto_mcp import access

    def down(*a, **k):
        raise RuntimeError("pool down")

    monkeypatch.setattr(access, "resolve_credential", down)
    with pytest.raises(RuntimeError, match="pool down"):
        wp_auth.start(ctx, {})


# --- HTTPS ---------------------------------------------------------------------

def test_http_site_is_refused(monkeypatch):
    from oto_mcp import egress

    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)
    monkeypatch.setattr(egress, "resolved_addresses", lambda host, port: {"93.184.215.14"})
    monkeypatch.delenv(egress.ALLOW_VAR, raising=False)
    with pytest.raises(wp_auth.ConnectRefused, match="clear text"):
        wp_auth.check_site("http://blog.example.com")
    assert wp_auth.check_site("https://blog.example.com") is False


def test_http_allowed_only_for_a_declared_internal_destination(monkeypatch):
    from oto_mcp import egress

    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)
    monkeypatch.setattr(egress, "resolved_addresses", lambda host, port: {"127.0.0.1"})
    monkeypatch.setenv(egress.ALLOW_VAR, "wp-local=127.0.0.1:9400")
    assert wp_auth.check_site("http://127.0.0.1:9400") is True     # allow_http du client
    with pytest.raises(wp_auth.ConnectRefused):
        wp_auth.check_site("http://127.0.0.1:9401")


def test_finish_refuses_http(monkeypatch):
    from oto_mcp import credentials_store, egress

    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)
    monkeypatch.setattr(egress, "resolved_addresses", lambda host, port: {"93.184.215.14"})
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda *a, **k: pytest.fail("posé sur un site en HTTP"))
    with pytest.raises(wp_auth.ConnectRefused):
        wp_auth.finish(_parsed(site="http://blog.example.com"), "", "editor", "pw")


def test_finish_org_scope_writes_the_org_row(monkeypatch):
    from oto_mcp import credentials_store, egress
    from oto.tools.wordpress import client as wp_client

    monkeypatch.setattr(egress, "check_url", lambda *a, **k: None)
    monkeypatch.setattr(wp_client.WordPressClient, "me", lambda self: {"id": 4})
    seen = {}
    monkeypatch.setattr(credentials_store, "guard_account_write",
                        lambda *a, **k: seen.setdefault("guard", a))
    monkeypatch.setattr(credentials_store, "set_credential",
                        lambda *a, **k: seen.setdefault("set", a))
    wp_auth.finish(_parsed(scope="org"), "", "editor", "pw")
    assert seen["guard"][:2] == ("org", "12") and seen["set"][:2] == ("org", "12")


# --- la route de retour ----------------------------------------------------------

CALLBACK = "/api/wordpress/connect/callback"


def _handler():
    from oto_mcp.api import wordpress as wp_routes

    routes = wp_routes.make_routes(None, None, None, None, None)
    return next(r.endpoint for r in routes if r.path == CALLBACK)


def _call(query: str):
    from starlette.requests import Request

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req = Request({"type": "http", "method": "GET", "path": CALLBACK, "headers": [],
                   "query_string": query.encode(), "path_params": {}}, receive=_receive)
    resp = asyncio.run(_handler()(req))
    return parse_qs(urlsplit(resp.headers["location"]).query).get("connect", [None])[0]


@pytest.fixture
def retour(monkeypatch):
    """Un state valide, une table de consommation en mémoire, une pose observée."""
    from oto_mcp import db

    used: set = set()
    monkeypatch.setattr(db, "consume_state_jti",
                        lambda aud, jti: not (f"{aud}:{jti}" in used or used.add(f"{aud}:{jti}")),
                        raising=False)
    monkeypatch.setattr(oauth_flow, "connector_return_url",
                        lambda app, connector, etat, org=None:
                        f"https://front.example.test/c?connector={connector}&connect={etat}")
    posed = []
    monkeypatch.setattr(wp_auth, "finish", lambda parsed, *a: posed.append(parsed) or "acct")
    monkeypatch.setattr(wp_auth, "still_allowed", lambda parsed: True)

    def state(**over):
        return oauth_flow.sign_state(wp_auth.AUD, _parsed(**over))

    return SimpleNamespace(state=state, posed=posed)


def test_callback_unreadable_state_is_error(retour):
    assert _call("state=garbage&password=x") == "error"
    assert not retour.posed


def test_callback_success_then_replay_is_refused(retour):
    st = retour.state()
    assert _call(f"state={st}&site_url=https://blog.example.com&user_login=e&password=p") \
        == "connected"
    # La même `success_url`, rejouée avec un autre identifiant : rien n'est posé.
    assert _call(f"state={st}&site_url=https://blog.example.com&user_login=x&password=q") \
        == "error"
    assert len(retour.posed) == 1


def test_callback_denied(retour):
    assert _call(f"state={retour.state()}&success=false") == "denied"
    assert not retour.posed


def test_callback_rechecks_rights(retour, monkeypatch):
    monkeypatch.setattr(wp_auth, "still_allowed", lambda parsed: False)
    assert _call(f"state={retour.state(scope='org')}&user_login=e&password=p") == "forbidden"
    assert not retour.posed


def test_callback_finish_failure_is_error(retour, monkeypatch):
    def boom(*a):
        raise wp_auth.ConnectRefused("non")

    monkeypatch.setattr(wp_auth, "finish", boom)
    assert _call(f"state={retour.state()}&user_login=e&password=p") == "error"


def test_still_allowed_reads_the_role_of_the_scope(monkeypatch):
    from oto_mcp import roles

    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org: org == 12)
    monkeypatch.setattr(roles, "can_admin_group", lambda sub, gid: gid == 7)
    assert wp_auth.still_allowed(_parsed(scope="org"))
    assert not wp_auth.still_allowed(_parsed(scope="org", org=13))
    assert wp_auth.still_allowed(_parsed(scope="group", group=7))
    assert not wp_auth.still_allowed(_parsed(scope="group", group=8))
    assert wp_auth.still_allowed(_parsed())
