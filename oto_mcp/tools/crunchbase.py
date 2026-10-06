"""Crunchbase — company / person records via the frontend's PRIVATE API.

⚠️ Private API (the one used by the web UI `www.crunchbase.com/v4/data/*`), auth = **live
browser session** (login cookies). It mirrors the documented schema of the v4 public
API (`api.crunchbase.com/v4/data/*`: same `field_ids`/`card_ids`,
endpoints `entities/organizations|people/{permalink}`, `searches/*`,
`autocompletes`) but without `user_key` — the logged-in session stands in for auth. May
break without notice on Crunchbase's side.

Execution — **Browserbase** (`oto_mcp/browserbase.py`), same substrate as `brevo`:
the token is only accepted from a **live browser session** (a raw `httpx`
is rejected, a session cannot be transplanted by cookie export, and an
in-process browser on the box = OOM + dependency on a local Chrome). So we rent a
remote Chrome: the user logs in ONCE via the **Live View**
(`crunchbase_connect_start`, they handle SSO/captcha/2FA), their session persists in a
Browserbase **Context** (= the per-user credential, `crunchbase` vault), and each
`/v4/data` call runs as a `fetch()` INSIDE an ephemeral session of the Context
(`browserbase.run_fetch`, same-origin `www.crunchbase.com`). Platform creds = env
`BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID`.

Replaces the old in-process DOM scraping (o-browser `CrunchbaseClient`), silently
broken on the box without a browser binary (see ADR 0026).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote, urlencode

from fastmcp import Context, FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR
from starlette.concurrency import run_in_threadpool

from .. import access, browser_session, browserbase
from ..auth.hooks import current_user_sub_from_token
from ..access import ResolvedCredential
from ..connectors import health as connector_health

# (Private API, origin page) pair specific to Crunchbase. The `fetch` is
# same-origin with the app (www.crunchbase.com) → it carries the session cookies;
# the `/v4/data` API lives under the SAME host (not a separate subdomain like brevo).
_API = "https://www.crunchbase.com/v4/data"
_APP = "https://www.crunchbase.com/"


async def _verify_session(session_id: str) -> browser_session.Verdict:
    """Crunchbase login confirmed? Probes the private API FROM the live session
    (same-origin): it only answers 200 when logged in. Shared by the two connection
    surfaces (REST dashboard + MCP) via `browser_session`.

    ⚠️ **Known debt, named on 2026-09-03**: this probe hits a BUSINESS route (a
    specific entity's record, `organizations/crunchbase`). This is EXACTLY the
    coupling that made `pennylaneged` unconnectable when Pennylane moved the
    probed route. Here it is no longer fatal — a status that is not an authentication
    signal raises `ProbeUnavailable` and `finalize` persists while saying so —
    but the probe remains to be moved to a SESSION route (profile / current
    user), to be found in the SPA bundle."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(browserbase.connect_url(session_id))
        try:
            c = b.contexts[0] if b.contexts else await b.new_context()
            pg = c.pages[0] if c.pages else await c.new_page()
            await pg.goto(_APP, wait_until="domcontentloaded", timeout=40000)
            res = await pg.evaluate(
                """async (base) => {
                    try {
                        const r = await fetch(base + "/entities/organizations/crunchbase"
                            + "?field_ids=identifier", {credentials: "include",
                            headers: {"content-type": "application/json"}});
                        return r.status;
                    } catch (e) { return 0; }
                }""", _API)
        finally:
            await b.close()
    if res == 200:
        return browser_session.Verdict(True, browser_session.LOGGED_IN)
    if res in (401, 403, 0):
        return browser_session.Verdict(
            False, browser_session.AUTH_REJECTED if res else browser_session.NO_SESSION,
            f"Crunchbase did not recognize the session (probe → {res}): finish logging in "
            "in the Live View, then re-run `crunchbase_connect_status`.")
    raise browser_session.ProbeUnavailable(
        f"the Crunchbase login probe could not decide (it answered {res}) — "
        "its route has probably moved. Your session was saved anyway, WITHOUT "
        "login confirmation: do not restart the connection, try a call.")


# Declares Crunchbase as a browser-session connector (generic start + this
# verify) — feeds the REST (dashboard) AND MCP connection flows. At import.
browser_session.register("crunchbase", _verify_session, login_url=f"{_APP}login")


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _sub() -> str:
    # An identity failure BUBBLES UP (the seam logs it with its reason, #464): only
    # a call genuinely without a token is "unauthenticated".
    sub = current_user_sub_from_token()
    if not sub:
        raise _err("Auth required — this tool only works over the authenticated HTTP transport.")
    return sub


# Expired session: reconnection is HUMAN (login in the Live View, SSO/captcha/
# 2FA handled by the person) — no automatic renewal possible. The message
# tells the agent so it continues without this source instead of retrying.
SESSION_EXPIREE = (
    "Crunchbase session expired or disconnected: a person must reconnect it "
    "(`crunchbase_connect_start`, login in the Live View) — no automatic "
    "reconnection is possible. Until then, every Crunchbase call will fail: "
    "continue without this source and report it as unreachable. The connector's "
    "card now shows it as \"to reconnect\".")


def _session() -> ResolvedCredential:
    """The user's Crunchbase credential: their Browserbase Context (= their logged-in
    Crunchbase session), resolved from the vault, WITH the row that served it (to mark it
    rejected). Raises an actionable McpError if Crunchbase is not connected. SQL:
    call outside the event loop."""
    try:
        return access.resolve_credential("crunchbase", want="byo")
    except McpError:
        raise _err("Crunchbase not connected. Run `crunchbase_connect_start` to "
                   "log in (once) via the Live View.")


def _permalink(value: str, kind: str) -> str:
    """Extract the permalink (slug) from a value that may be a full URL.
    `kind` = 'organization' | 'person'."""
    v = (value or "").strip()
    marker = f"/{kind}/"
    if marker in v:
        v = v.split(marker, 1)[1]
    return v.split("/")[0].split("?")[0]


async def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    """Run a `/v4/data` call in the user's Browserbase session. Returns the decoded
    `data`. Raises an actionable McpError otherwise."""
    if not browserbase.is_configured():
        raise _err("Browserbase not configured on the platform side "
                   "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
    rc = await run_in_threadpool(_session)
    try:
        res = await browserbase.run_fetch(rc.key, method, path, body, base=_API, app=_APP)
    except browserbase.BrowserbaseError as e:
        raise _err(f"Browserbase execution failed: {e}", code=INTERNAL_ERROR)
    st = res.get("status")
    if st in (401, 403):
        # Signals #1070/#1076/#1149/#1163: the expiry only showed up to
        # agents, run after run, for six days. Marking the row that ACTUALLY
        # served makes the card "to reconnect" for the person who can act;
        # reconnecting rewrites the row and clears the mark.
        await run_in_threadpool(connector_health.mark_rejected, rc.entity_type,
                                rc.entity_id, "crunchbase", rc.account,
                                f"session expired (HTTP {st})")
        raise _err(SESSION_EXPIREE)
    if not (200 <= (st or 0) < 300):
        raise _err(f"Crunchbase returned {st}: {str(res.get('data'))[:200]}", code=INTERNAL_ERROR)
    return res["data"]


def register(mcp: FastMCP) -> None:

    # --- Onboarding (Live View) --------------------------------------------
    @mcp.tool()
    def crunchbase_connect_start(ctx: Context) -> dict:
        """Start the Crunchbase connection. Opens a remote browser and returns
        a **`live_view_url`**: open it, log in to Crunchbase as usual
        (email/password, SSO, captcha — you handle everything in that window). Then
        call `crunchbase_connect_status(context_id, session_id)` with the returned
        values to finalize (your session is saved; only redo this
        when it expires).
        """
        sub = _sub()
        try:
            out = browser_session.start(sub, "crunchbase")
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["instructions"] = ("Open `live_view_url`, log in to Crunchbase, then "
                               "call `crunchbase_connect_status` with context_id + session_id.")
        return out

    @mcp.tool()
    async def crunchbase_connect_status(ctx: Context, context_id: str,
                                        session_id: str) -> dict:
        """Finalize the Crunchbase connection. Checks that you actually logged in
        in the Live View (by calling the private API from your session); if so,
        **saves** your session (the Context) for subsequent calls. Returns
        `{connected}`. Call it again if `connected=false` (not logged in yet)."""
        sub = _sub()
        try:
            res = await browser_session.finalize(sub, "crunchbase", context_id, session_id)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not res.connected:
            return {"connected": False, "reason": res.reason, "retry": res.retry,
                    "hint": res.detail or "Not logged in yet — log in in the Live "
                                          "View then retry."}
        out = {"connected": True, "context_id": context_id, "reason": res.reason,
               "login_verified": not res.warning}
        if res.warning:
            out["warning"], out["retry"] = res.warning, False
        return out

    # --- Lecture ------------------------------------------------------------
    @mcp.tool()
    async def crunchbase_get_company(slug: str) -> dict:
        """Crunchbase company record by permalink (slug).

        Returns the raw `/v4/data` response: `properties` (firmographics: name,
        description, founding, location, headcount, funding…) + `cards`
        (`founders`, `raised_funding_rounds`). Structured data, to use as
        is.

        Args:
            slug: the organization's permalink (e.g. "anthropic" from
                `crunchbase.com/organization/anthropic`) — a full URL is also
                accepted (the slug is extracted from it).
        """
        permalink = _permalink(slug, "organization")
        qs = urlencode({"card_ids": "founders,raised_funding_rounds"})
        return await _api("GET", f"/entities/organizations/{quote(permalink)}?{qs}")

    @mcp.tool()
    async def crunchbase_get_person(slug: str) -> dict:
        """Crunchbase person record by permalink (slug).

        Returns the raw `/v4/data` response: the person's `properties` (name, bio,
        social links…).

        Args:
            slug: permalink from `crunchbase.com/person/<slug>` (full URL
                accepted).
        """
        permalink = _permalink(slug, "person")
        return await _api("GET", f"/entities/people/{quote(permalink)}")

    @mcp.tool()
    async def crunchbase_search_companies(query: str, limit: int = 10) -> dict:
        """Search organizations by free text (Crunchbase autocomplete).

        Returns `{entities}` (raw): each entry carries an `identifier` with
        `permalink` (→ slug for `crunchbase_get_company`), `value` (name) and
        `entity_def_id`.
        """
        qs = urlencode({"query": query, "collection_ids": "organization.companies",
                        "limit": max(1, min(int(limit), 25))})
        return await _api("GET", f"/autocompletes?{qs}")

    @mcp.tool()
    async def crunchbase_search_people(query: str, limit: int = 10) -> dict:
        """Search people by free text (Crunchbase autocomplete).

        Returns `{entities}` (raw): each entry carries an `identifier` with
        `permalink` (→ slug for `crunchbase_get_person`), `value` (name) and
        `entity_def_id`.
        """
        qs = urlencode({"query": query, "collection_ids": "person.people",
                        "limit": max(1, min(int(limit), 25))})
        return await _api("GET", f"/autocompletes?{qs}")

    @mcp.tool()
    async def crunchbase_get_funding_rounds(slug: str) -> dict:
        """Funding rounds of an organization.

        Returns the raw `raised_funding_rounds` card (date, type, amount,
        investors) from `/v4/data`.

        Args:
            slug: the organization's permalink (full URL accepted).
        """
        permalink = _permalink(slug, "organization")
        qs = urlencode({"card_ids": "raised_funding_rounds"})
        return await _api("GET", f"/entities/organizations/{quote(permalink)}?{qs}")
