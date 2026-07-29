"""salesforce_oauth — live "Connect" flow for the per-customer Salesforce
Connected App. THE only way to obtain refresh_token now — providers.py no
longer declares a refresh_token field at all (full replacement, not
coexistence with a manual-paste path).

Unlike folk_oauth/atlassian_oauth (one Otomata-owned client, no DB lookup to
build the authorize URL), Salesforce's client_id/client_secret/login_url are
per-customer — `build_auth_url` reads them from an already-saved partial
credential. Locks in: state round-trip (now carrying org_id + scope + an
optional group_id, unlike folk's sub-only state), that the authorize URL is
built from the CALLER's own saved fields (not a module constant), the
read-merge-write persistence (refresh_token merged in without disturbing the
other 3 fields), and the `scope="org"`/`scope="group"` admin gates.
"""
import os
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("OTO_MCP_OAUTH_STATE_SECRET", "test-secret")
os.environ.setdefault("OTO_MCP_PUBLIC_URL", "https://mcp.oto.ninja")

from oto_mcp import salesforce_oauth  # noqa: E402


# --- state round-trip ---------------------------------------------------------

def test_state_roundtrip_recovers_sub_org_scope_and_verifier():
    state = salesforce_oauth.make_state("sub-xyz", 42, "member", "verifier-abc")
    got = salesforce_oauth.verify_state(state)
    assert got == ("sub-xyz", 42, "member", "verifier-abc", None)


def test_state_roundtrip_carries_org_scope():
    state = salesforce_oauth.make_state("sub-1", 7, "org", "v")
    got = salesforce_oauth.verify_state(state)
    assert got == ("sub-1", 7, "org", "v", None)


def test_state_roundtrip_carries_group_scope_and_group_id():
    state = salesforce_oauth.make_state("sub-1", 7, "group", "v", group_id=99)
    got = salesforce_oauth.verify_state(state)
    assert got == ("sub-1", 7, "group", "v", 99)


def test_group_scope_state_without_group_id_is_rejected():
    # make_state pose toujours group_id en scope "group" (build_auth_url est son seul
    # appelant), mais verify_state ne doit pas faire confiance à un payload qui l'omet.
    from oto_mcp import oauth_flow
    forged = oauth_flow.sign_state("salesforce",
                                   {"sub": "s", "org": 1, "scope": "group", "v": "x"})
    assert salesforce_oauth.verify_state(forged) is None


def test_state_of_another_flow_is_rejected():
    """Un state VALIDEMENT signé mais émis pour un AUTRE flux ne passe pas ici —
    c'est la raison d'être de l'audience (`oauth_flow`) : avant elle, tous les flux
    signaient au même format avec le même secret."""
    from oto_mcp import oauth_flow
    zoho_state = oauth_flow.sign_state("zoho",
                                       {"sub": "s", "org": 1, "scope": "member", "v": "x"})
    assert salesforce_oauth.verify_state(zoho_state) is None


def test_tampered_state_is_rejected():
    state = salesforce_oauth.make_state("sub-1", 1, "member", "v")
    payload, sig = state.split(".", 1)
    tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + sig
    assert salesforce_oauth.verify_state(tampered) is None


def test_expired_state_is_rejected(monkeypatch):
    state = salesforce_oauth.make_state("sub-1", 1, "member", "v")
    # Fast-forward past the TTL by patching time.time for verify_state's check.
    import time as time_mod
    from oto_mcp import oauth_flow
    real_time = time_mod.time
    monkeypatch.setattr(time_mod, "time", lambda: real_time() + oauth_flow.DEFAULT_STATE_TTL + 1)
    assert salesforce_oauth.verify_state(state) is None


def test_invalid_scope_in_payload_is_rejected():
    # Un state signé avec un scope inventé (impossible via make_state, mais
    # verify_state ne doit pas faire confiance au payload les yeux fermés).
    from oto_mcp import oauth_flow
    forged = oauth_flow.sign_state("salesforce",
                                   {"sub": "s", "org": 1, "scope": "bogus", "v": "x"})
    assert salesforce_oauth.verify_state(forged) is None


# --- build_auth_url reads PER-CUSTOMER fields, never a global constant --------

def _stub_saved_fields(monkeypatch, fields):
    """Simulates a credential already saved via the static-fields form:
    client_id/client_secret/login_url present, refresh_token absent (that's
    the whole point of required=False)."""
    from oto_mcp import credentials_store
    monkeypatch.setattr(credentials_store, "get_credential_with_meta",
                        lambda *a, **k: {"secret": "packed", "meta": {}, "set_at": None})
    monkeypatch.setattr(credentials_store, "unpack_secret", lambda connector, secret: fields)


def test_build_auth_url_uses_this_customers_own_client_id(monkeypatch):
    from oto_mcp import access
    monkeypatch.setattr(access, "current_org", lambda sub: 99)
    _stub_saved_fields(monkeypatch, {
        "client_id": "customer-a-client-id",
        "client_secret": "customer-a-secret",
        "login_url": "https://acme.my.salesforce.com",
    })
    url = salesforce_oauth.build_auth_url("sub-1", "member")
    u, q = urlparse(url), parse_qs(urlparse(url).query)
    assert u.netloc == "acme.my.salesforce.com"
    assert u.path == "/services/oauth2/authorize"
    assert q["client_id"][0] == "customer-a-client-id"
    assert q["redirect_uri"][0] == "https://mcp.oto.ninja/api/salesforce/oauth/callback"
    assert q["code_challenge_method"][0] == "S256"
    assert q["scope"][0] == "api refresh_token"


def test_build_auth_url_different_customers_get_different_urls(monkeypatch):
    """Contrasts with google_oauth.py/folk_oauth.py, whose authorize URL is
    identical for every caller (one Otomata-owned client) — Salesforce's must
    vary per saved credential."""
    from oto_mcp import access
    monkeypatch.setattr(access, "current_org", lambda sub: 1)

    _stub_saved_fields(monkeypatch, {
        "client_id": "client-a", "client_secret": "secret-a",
        "login_url": "https://a.my.salesforce.com",
    })
    url_a = salesforce_oauth.build_auth_url("sub-a", "member")

    _stub_saved_fields(monkeypatch, {
        "client_id": "client-b", "client_secret": "secret-b",
        "login_url": "https://b.my.salesforce.com",
    })
    url_b = salesforce_oauth.build_auth_url("sub-b", "member")

    assert url_a != url_b
    assert "client-a" in url_a and "client-b" not in url_a
    assert "client-b" in url_b and "client-a" not in url_b


def test_build_auth_url_raises_when_fields_not_saved_yet(monkeypatch):
    from oto_mcp import access, credentials_store
    monkeypatch.setattr(access, "current_org", lambda sub: 1)
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", lambda *a, **k: None)
    with pytest.raises(LookupError, match="Consumer Key"):
        salesforce_oauth.build_auth_url("sub-1", "member")


# --- scope="org" admin gate ----------------------------------------------------

def test_org_scope_requires_org_admin(monkeypatch):
    from oto_mcp import access, roles
    monkeypatch.setattr(access, "current_org", lambda sub: 5)
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org_id: False)
    with pytest.raises(PermissionError):
        salesforce_oauth.build_auth_url("sub-1", "org")


def test_org_scope_succeeds_for_org_admin(monkeypatch):
    from oto_mcp import access, roles
    monkeypatch.setattr(access, "current_org", lambda sub: 5)
    monkeypatch.setattr(roles, "is_org_admin", lambda sub, org_id: True)
    _stub_saved_fields(monkeypatch, {
        "client_id": "org-client", "client_secret": "org-secret",
        "login_url": "https://org.my.salesforce.com",
    })
    url = salesforce_oauth.build_auth_url("sub-1", "org")
    assert "org-client" in url


def test_invalid_scope_value_rejected():
    with pytest.raises(ValueError):
        salesforce_oauth.build_auth_url("sub-1", "bogus")


# --- scope="group" admin gate --------------------------------------------------

def test_group_scope_requires_group_admin(monkeypatch):
    from oto_mcp import access, roles
    monkeypatch.setattr(access, "current_org", lambda sub: 5)
    monkeypatch.setattr(access, "current_group", lambda sub: 77)
    monkeypatch.setattr(roles, "can_admin_group", lambda sub, group_id: False)
    with pytest.raises(PermissionError):
        salesforce_oauth.build_auth_url("sub-1", "group")


def test_group_scope_succeeds_for_group_admin(monkeypatch):
    from oto_mcp import access, roles
    monkeypatch.setattr(access, "current_org", lambda sub: 5)
    monkeypatch.setattr(access, "current_group", lambda sub: 77)
    monkeypatch.setattr(roles, "can_admin_group", lambda sub, group_id: True)
    _stub_saved_fields(monkeypatch, {
        "client_id": "team-client", "client_secret": "team-secret",
        "login_url": "https://team.my.salesforce.com",
    })
    url = salesforce_oauth.build_auth_url("sub-1", "group")
    assert "team-client" in url


# --- persist_token: read-merge-write, never disturbing the other 3 fields -----

class _FakeStore:
    """In-memory stand-in for the one row a member/org credential lives in —
    just enough to prove the read→merge→pack→write sequence, without a DB."""

    def __init__(self, initial_fields):
        self.fields = dict(initial_fields)
        self.meta = {}
        self.set_credential_calls = []
        self.update_meta_calls = []

    def get_credential_with_meta(self, *a, **k):
        return {"secret": "packed", "meta": dict(self.meta), "set_at": None}

    def unpack_secret(self, connector, secret):
        return dict(self.fields)

    def pack_secret(self, connector, fields):
        self.fields = dict(fields)  # what would be written, captured for assertions
        return "new-packed-secret"

    def set_credential(self, entity_type, entity_id, connector, secret, set_by=None, meta=None):
        self.set_credential_calls.append(
            {"entity_type": entity_type, "entity_id": entity_id, "meta": meta})

    def update_meta(self, entity_type, entity_id, connector, account, patch):
        self.update_meta_calls.append(patch)
        self.meta.update(patch)
        return True


@pytest.mark.asyncio
async def test_persist_token_merges_refresh_token_without_disturbing_other_fields(monkeypatch):
    store = _FakeStore({
        "client_id": "cid", "client_secret": "csecret",
        "login_url": "https://acme.my.salesforce.com",
    })
    from oto_mcp import credentials_store, connector_verify
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", store.get_credential_with_meta)
    monkeypatch.setattr(credentials_store, "unpack_secret", store.unpack_secret)
    monkeypatch.setattr(credentials_store, "pack_secret", store.pack_secret)
    monkeypatch.setattr(credentials_store, "set_credential", store.set_credential)
    monkeypatch.setattr(credentials_store, "update_meta", store.update_meta)
    monkeypatch.setattr(connector_verify, "run", lambda *a, **k: _ok())

    async def _ok():
        return None

    result = await salesforce_oauth.persist_token(
        "sub-1", 1, "member",
        {"refresh_token": "rt-abc", "instance_url": "https://acme.my.salesforce.com",
         "id": "https://login.salesforce.com/id/00D.../005..."},
    )

    assert store.fields == {
        "client_id": "cid", "client_secret": "csecret",
        "login_url": "https://acme.my.salesforce.com", "refresh_token": "rt-abc",
    }
    assert store.set_credential_calls[0]["meta"]["instance_url"] == "https://acme.my.salesforce.com"
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_persist_token_writes_to_org_scope_when_scope_is_org(monkeypatch):
    store = _FakeStore({
        "client_id": "cid", "client_secret": "csecret", "login_url": "https://acme.my.salesforce.com",
    })
    from oto_mcp import credentials_store, connector_verify, org_store
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", store.get_credential_with_meta)
    monkeypatch.setattr(credentials_store, "unpack_secret", store.unpack_secret)
    monkeypatch.setattr(credentials_store, "pack_secret", store.pack_secret)
    monkeypatch.setattr(credentials_store, "update_meta", store.update_meta)

    org_secret_calls = []
    monkeypatch.setattr(org_store, "set_org_secret",
                        lambda org_id, provider, secret, set_by=None, meta=None:
                        org_secret_calls.append((org_id, provider, secret, meta)))

    async def _ok():
        return None
    monkeypatch.setattr(connector_verify, "run", lambda *a, **k: _ok())

    await salesforce_oauth.persist_token("sub-1", 42, "org", {"refresh_token": "rt-org"})

    assert len(org_secret_calls) == 1
    assert org_secret_calls[0][0] == 42
    assert org_secret_calls[0][1] == "salesforce"


@pytest.mark.asyncio
async def test_persist_token_writes_to_group_scope_when_scope_is_group(monkeypatch):
    store = _FakeStore({
        "client_id": "cid", "client_secret": "csecret", "login_url": "https://acme.my.salesforce.com",
    })
    from oto_mcp import credentials_store, connector_verify, group_store
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", store.get_credential_with_meta)
    monkeypatch.setattr(credentials_store, "unpack_secret", store.unpack_secret)
    monkeypatch.setattr(credentials_store, "pack_secret", store.pack_secret)
    monkeypatch.setattr(credentials_store, "update_meta", store.update_meta)

    group_secret_calls = []
    monkeypatch.setattr(group_store, "set_group_secret",
                        lambda group_id, provider, secret, set_by=None, meta=None:
                        group_secret_calls.append((group_id, provider, secret, meta)))

    async def _ok():
        return None
    monkeypatch.setattr(connector_verify, "run", lambda *a, **k: _ok())

    await salesforce_oauth.persist_token("sub-1", 5, "group", {"refresh_token": "rt-team"},
                                         group_id=77)

    assert len(group_secret_calls) == 1
    assert group_secret_calls[0][0] == 77
    assert group_secret_calls[0][1] == "salesforce"


@pytest.mark.asyncio
async def test_persist_token_requires_refresh_token():
    with pytest.raises(RuntimeError, match="refresh_token"):
        await salesforce_oauth.persist_token(
            "sub-1", 1, "member", {"access_token": "at-only"})
