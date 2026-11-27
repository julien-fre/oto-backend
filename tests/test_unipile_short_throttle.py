"""Unipile's short per-account throttle is absorbed on LinkedIn reads.

Unipile answers "429: We only allow 1 requests. Retry in 1 second." when one LinkedIn
account receives several requests at once. A client firing parallel
`linkedin_unipile_profile` calls on the same account had every one of them refused.
What this file holds:

- reads of ONE account are served one after another inside the process (a thread lock
  per Unipile account id: these sync tools run in the server's threadpool), and a read
  never waits more than `_GATE_WAIT_MAX_S` for its turn;
- a 429 whose delay is short (≤ `_SHORT_RETRY_MAX_S`) is waited out ONCE; a long delay,
  an unreadable one, or a second 429 is the `unipile_rate_limited` refusal as before;
- two different accounts never wait for each other;
- a write (an invitation) is never replayed.

The HTTP layer is mocked (`requests.Session.request` on a real oto-core client) and no
test really sleeps.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from oto.tools.unipile import make_unipile_client
from oto.tools.unipile.client import UnipileRateLimited

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import unipile as U

BURST = "We only allow 1 requests. Retry in 1 second."


class _Resp:
    """The few attributes of a `requests.Response` the client reads."""

    def __init__(self, status: int, body: dict, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.reason = "x"
        self.text = "x"

    def json(self):
        return self._body


def _profile(slug: str) -> _Resp:
    return _Resp(200, {"object": "UserProfile", "public_identifier": slug})


def _throttled(detail: str = BURST) -> _Resp:
    return _Resp(429, {"detail": detail})


def _slug_of(url: str) -> str:
    return url.rsplit("/", 1)[-1]


@pytest.fixture
def env(monkeypatch):
    """A real oto-core client on one account, whose HTTP calls the test scripts."""
    U._ACCOUNT_GATES.clear()
    U._RATE_LIMIT_UNTIL.clear()
    slept: list[float] = []
    monkeypatch.setattr(U.time, "sleep", slept.append)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    # The owner's sending limits are a DB concern, not this file's.
    monkeypatch.setattr(U, "_within_limits", lambda client, action, fn: fn())
    client = make_unipile_client(api_key="k", account_id="acc-A", provider="LINKEDIN")
    monkeypatch.setattr(U, "unipile_client", lambda *a, **k: client)
    m = FastMCP("t")
    U.register(m)
    tools = SimpleNamespace(
        profile=asyncio.run(m.get_tool("linkedin_unipile_profile")).fn,
        act=asyncio.run(m.get_tool("linkedin_unipile_act")).fn)
    yield SimpleNamespace(client=client, slept=slept, tools=tools)
    U._ACCOUNT_GATES.clear()
    U._RATE_LIMIT_UNTIL.clear()


def _script(client, *responses):
    """Successive HTTP answers; returns the list of URLs actually requested."""
    it = iter(responses)
    urls: list[str] = []

    def request(method, url, **kw):
        urls.append(url)
        return next(it)

    client.session.request = request
    return urls


def _code(e: McpError):
    return (getattr(e.error, "data", None) or {}).get("code")


def test_the_verbatim_burst_message_reads_as_one_second():
    from oto.tools.unipile.errors import _parse_retry_after
    assert _parse_retry_after(f"Unipile 429: {BURST}") == 1


def test_four_parallel_reads_of_one_account_queue_instead_of_colliding(env):
    """Unipile's side is simulated: a request arriving while another is in flight on the
    account gets the 429. Behind the gate, none collides and all four are served."""
    state = {"in_flight": 0, "max": 0, "throttled": 0}
    guard = threading.Lock()

    def request(method, url, **kw):
        with guard:
            state["in_flight"] += 1
            state["max"] = max(state["max"], state["in_flight"])
            busy = state["in_flight"] > 1
            if busy:
                state["throttled"] += 1
        try:
            if busy:
                return _throttled()
            threading.Event().wait(0.05)  # the upstream call takes a moment
            return _profile(_slug_of(url))
        finally:
            with guard:
                state["in_flight"] -= 1

    env.client.session.request = request
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(env.tools.profile, op="person", identifier=f"member-{i}")
                   for i in range(4)]
        results = [f.result(timeout=10) for f in futures]

    assert sorted(r["public_identifier"] for r in results) == [
        f"member-{i}" for i in range(4)]
    assert state["max"] == 1 and state["throttled"] == 0
    assert env.slept == [] and U._RATE_LIMIT_UNTIL == {}


def test_a_short_throttle_is_waited_out_once_then_served(env):
    urls = _script(env.client, _throttled(), _profile("jane-doe"))
    out = env.tools.profile(op="person", identifier="jane-doe")
    assert out["public_identifier"] == "jane-doe"
    assert len(urls) == 2
    assert env.slept == [1.5]
    assert U._RATE_LIMIT_UNTIL == {}  # absorbed: nothing armed against the next reads


def test_the_retry_after_header_counts_too(env):
    urls = _script(env.client, _Resp(429, {"detail": "Too many requests"},
                                     {"Retry-After": "2"}), _profile("jane-doe"))
    env.tools.profile(op="person", identifier="jane-doe")
    assert len(urls) == 2 and env.slept == [2.5]


def test_a_long_throttle_is_refused_at_once_with_the_existing_refusal(env):
    urls = _script(env.client, _throttled("We only allow 100 requests. Retry in 55 minutes."),
                   _profile("jane-doe"))
    with pytest.raises(McpError) as e:
        env.tools.profile(op="person", identifier="jane-doe")
    assert _code(e.value) == "unipile_rate_limited"
    assert e.value.error.message.startswith("Refused `unipile_rate_limited`: ")
    assert "SLOW DOWN the pace of linkedin_* calls" in e.value.error.message
    assert len(urls) == 1 and env.slept == []
    assert "sub-1" in U._RATE_LIMIT_UNTIL  # the cooldown stops the next reads, as before


def test_a_throttle_without_a_readable_delay_is_not_retried(env):
    urls = _script(env.client, _throttled("Too many requests"), _profile("jane-doe"))
    with pytest.raises(McpError) as e:
        env.tools.profile(op="person", identifier="jane-doe")
    assert _code(e.value) == "unipile_rate_limited"
    assert len(urls) == 1 and env.slept == []


def test_a_second_throttle_is_refused(env):
    urls = _script(env.client, _throttled(), _throttled(), _profile("jane-doe"))
    with pytest.raises(McpError) as e:
        env.tools.profile(op="person", identifier="jane-doe")
    assert _code(e.value) == "unipile_rate_limited"
    assert len(urls) == 2 and env.slept == [1.5]
    assert "sub-1" in U._RATE_LIMIT_UNTIL


def test_company_reads_get_the_same_treatment(env):
    U._COMPANY_CACHE.clear()
    company = _Resp(200, {"object": "CompanyProfile", "public_identifier": "acme"})
    urls = _script(env.client, _throttled(), company)
    out = env.tools.profile(op="company", identifier="acme")
    assert out["public_identifier"] == "acme"
    assert len(urls) == 2 and env.slept == [1.5]
    U._COMPANY_CACHE.clear()


def test_a_write_is_never_retried(env):
    """An invitation met by the same short throttle goes back to the caller after ONE
    request: a send is never replayed."""
    urls = _script(env.client, _throttled(), _Resp(200, {"object": "UserInvitationSent"}))
    with pytest.raises(UnipileRateLimited):
        env.tools.act(op="invite", provider_id="ACoAAprovider")
    assert len(urls) == 1 and env.slept == []


def test_a_retried_profile_read_counts_one_visit(monkeypatch):
    """The owner's visit limit reserves a slot per attempt and gives it back when the
    attempt fails: a read absorbed by one retry counts ONE visit."""
    from oto_mcp import unipile_limites as L

    U._ACCOUNT_GATES.clear()
    U._RATE_LIMIT_UNTIL.clear()
    monkeypatch.setattr(U.time, "sleep", lambda s: None)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise", lambda: "sub-1")
    # The suite's autouse fixture hides the account from the limits without a database:
    # this test plays the limits' store itself.
    monkeypatch.setattr(L, "compte_du_client", lambda client: client._account_id)
    journal: list[int] = []
    monkeypatch.setattr(L.store, "limites", lambda acct: {})
    monkeypatch.setattr(L.store, "journaliser",
                        lambda acct, action: (journal.append(len(journal)), len(journal))[1])
    monkeypatch.setattr(L.store, "retirer", lambda rid: journal.pop())
    client = make_unipile_client(api_key="k", account_id="acc-A", provider="LINKEDIN")
    monkeypatch.setattr(U, "unipile_client", lambda *a, **k: client)
    _script(client, _throttled(), _profile("jane-doe"))
    m = FastMCP("t")
    U.register(m)
    asyncio.run(m.get_tool("linkedin_unipile_profile")).fn(op="person",
                                                           identifier="jane-doe")
    assert len(journal) == 1
    U._ACCOUNT_GATES.clear()


# --- the gate itself ----------------------------------------------------------------

@pytest.fixture
def gate_env(monkeypatch):
    U._ACCOUNT_GATES.clear()
    U._RATE_LIMIT_UNTIL.clear()
    monkeypatch.setattr(U.time, "sleep", lambda s: None)
    yield
    U._ACCOUNT_GATES.clear()
    U._RATE_LIMIT_UNTIL.clear()


def _acct(account_id):
    return SimpleNamespace(_account_id=account_id)


def test_two_accounts_are_not_serialized_against_each_other(gate_env):
    a, b = _acct("acc-A"), _acct("acc-B")
    b_done = threading.Event()

    def read_a():
        # Holds account A's turn until account B has been served.
        return b_done.wait(timeout=5)

    def read_b():
        b_done.set()
        return "b"

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(U._scrape, "sub-1", read_a, a)
        threading.Event().wait(0.05)
        fb = pool.submit(U._scrape, "sub-1", read_b, b)
        assert fb.result(timeout=5) == "b"
        assert fa.result(timeout=5) is True


def test_one_account_is_one_queue_whoever_calls(gate_env):
    """Two actors operating the SAME account (a lent account) share its queue."""
    assert U._account_gate("sub-1", _acct("acc-A")) is U._account_gate("sub-2",
                                                                         _acct("acc-A"))
    assert U._account_gate("sub-1", _acct("acc-A")) is not U._account_gate(
        "sub-1", _acct("acc-B"))
    # No account id on the client (or no client): the actor's own queue.
    assert U._account_gate("sub-1", None) is U._account_gate("sub-1", _acct(None))


def test_the_queue_wait_stays_short():
    # A read waiting for its turn holds a thread of the shared pool (plus the sleep of a
    # short retry): a burst on one account must not hold them long.
    assert U._GATE_WAIT_MAX_S <= 5


def test_a_read_never_waits_past_the_bound(gate_env, monkeypatch):
    monkeypatch.setattr(U, "_GATE_WAIT_MAX_S", 0.05)
    client = _acct("acc-A")
    called = []
    gate = U._account_gate("sub-1", client)
    gate.acquire()
    try:
        with pytest.raises(McpError) as e:
            U._scrape("sub-1", lambda: called.append(1), client)
    finally:
        gate.release()
    assert _code(e.value) == "unipile_rate_limited"
    assert e.value.error.message.startswith("Refused `unipile_rate_limited`: ")
    assert called == []
    assert U._RATE_LIMIT_UNTIL == {}  # a local queue, not Unipile's signal: nothing armed


def test_a_queued_read_meets_the_long_throttle_of_the_one_ahead(gate_env):
    """The read ahead met a long throttle while this one waited: refused without a call."""
    client = _acct("acc-A")
    called = []
    gate = U._account_gate("sub-1", client)
    gate.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        f = pool.submit(U._scrape, "sub-1", lambda: called.append(1), client)
        threading.Event().wait(0.05)
        U._RATE_LIMIT_UNTIL["sub-1"] = U.time.time() + 600
        gate.release()
        with pytest.raises(McpError) as e:
            f.result(timeout=5)
    assert _code(e.value) == "unipile_rate_limited"
    assert called == []


def test_the_turn_is_given_back_after_a_refusal(gate_env):
    client = _acct("acc-A")

    def boom():
        raise UnipileRateLimited("Unipile 429: Retry in 12 hours.", retry_after=43200)

    with pytest.raises(McpError):
        U._scrape("sub-1", boom, client)
    assert not U._account_gate("sub-1", client).locked()
