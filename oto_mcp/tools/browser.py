"""Connected browser — read N sites behind a login WITHOUT writing one connector per site.

**Generic** connector on the Browserbase substrate (ADR 0026): where `crunchbase`,
`brevoauto` and `pennylaneged` are three hard-coded connectors for three private APIs
that we exploit in depth, this one serves the opposite need — **reading** an
authenticated page on any site (paid media, intranet, back-office without an API), where
writing a dedicated connector would cost a full dev cycle for a `GET`.

Model (oto-private#79):
- **one site = one vault account** (`account` = the host, see multi-account ADR 0011/0024):
  each site has ITS OWN Browserbase Context, hence its isolated session — never a
  catch-all profile that would mix the credentials of N sites in a single secret. The
  connected sites can be listed (`browser_sites`) and appear in the dashboard's identity
  picker (generic keyed backend of `connector_identities`).
- **connection** = interactive Live View on the requested URL (`browser_connect_start`):
  the user logs in by hand (SSO/2FA/captcha), the session persists in the Context.
- **reading** = `browser_fetch(url)` loads the page in an ephemeral session of the site's
  Context and returns its **full** content (not the 400-character truncated fallback of
  `run_fetch`, which targets JSON APIs).

⚠️ **Login verification: generic, hence fallible.** A dedicated connector probes an
authenticated route it knows (`/crm/flow_companies` on Pennylane); here we know nothing
about the site. The only signal readable everywhere = "does the session carry cookies on
this host?". 0 cookies ⇒ almost surely not logged in; >0 PROVES nothing (an anonymous
cookie is enough). Hence `force=True` on `browser_connect_status` for sites whose login
state lives elsewhere (localStorage) — deliberate and documented, not a silent fallback.

Cost: **1 browser session per call** (inherited from the substrate). Suited to monitoring
deltas (a few pages), not to a backfill of hundreds of articles — reusing one session
for N calls of the same run is left to do if the volume justifies it.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastmcp import Context, FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from .. import access, browser_session, browserbase, url_perimeter
from ..connectors import identities as connector_identities
from ..auth.hooks import current_user_sub_from_token

_CONNECTOR = "browser"

# Cap on rendered content returned to the agent. A whole page can weigh hundreds of
# thousands of characters; we truncate while SAYING so (`truncated`), never silently.
_MAX_CHARS = 100_000


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _sub() -> str:
    # An identity failure BUBBLES UP (the seam logs it with its reason, #464): only
    # a call truly without a token is "unauthenticated".
    sub = current_user_sub_from_token()
    if not sub:
        raise _err("Auth required — this tool only works over the authenticated HTTP transport.")
    return sub


def _site_of(url: str) -> str:
    """Normalized host of a URL = the site's identity in the vault. `www.` removed (same
    cookies, same login: `www.example.fr` and `example.fr` must not produce two
    sessions to maintain), port and case normalized."""
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise _err(f"Invalid URL: {url!r} — expected an absolute URL (https://…).")
    host = p.hostname.lower()
    return host[4:] if host.startswith("www.") else host


def _context_id(site: str) -> str:
    """The user's Browserbase Context FOR THIS SITE, resolved from the vault. Raises an
    actionable McpError if the site is not connected. An explicit account that is not
    found raises on the `access` side (never a silent fallback to the Context of ANOTHER
    site — reading the wrong session would leak between sites)."""
    try:
        return access.resolve_credential(_CONNECTOR, want="byo", account=site).key
    except McpError:
        raise _err(f"`{site}` is not connected. Run `browser_connect_start(\"https://{site}/\")` "
                   "to log in once (session remembered afterwards).")


async def _verify_site(session_id: str, account: str) -> browser_session.Verdict:
    """GENERIC login probe: does the live session carry cookies on the host? (see the
    warning at the top of the module — a signal, not proof.) The refusal states its
    reason AND the escape hatch, otherwise the agent can only loop."""
    if not account:
        return browser_session.Verdict(
            False, browser_session.NO_SESSION,
            "No site targeted: call `browser_connect_start(url)` again first.",
            retry=False)
    if await browserbase.host_cookies(session_id, f"https://{account}/") > 0:
        return browser_session.Verdict(True, browser_session.LOGGED_IN)
    return browser_session.Verdict(
        False, browser_session.NO_SESSION,
        f"No cookies on `{account}`: either the login was not completed (finish it "
        "in the Live View), or this site keeps its session OUTSIDE cookies (localStorage) "
        "— in that case retry with `force=true`.")


# Browser-session connector, GENERIC variant: `login_url` supplied at call time
# (the site comes from the user) and an account-aware `verify`. At import, like the others.
browser_session.register(_CONNECTOR, _verify_site, account_aware=True)


def register(mcp: FastMCP) -> None:

    # --- Connecting a site (Live View) --------------------------------------
    @mcp.tool()
    def browser_connect_start(ctx: Context, url: str) -> dict:
        """Connect a site behind a login (once per site). Opens a remote browser on
        `url` and returns a **`live_view_url`**: open it and log in to the site
        normally (email/password, SSO, 2FA — everything happens in that window). Then
        call `browser_connect_status(context_id, session_id, site)` with the returned
        values to remember the session.

        After that, `browser_fetch(url)` reads any page of that site while logged in.

        Args:
            url: URL of the site's login page (or its home page).
        """
        sub = _sub()
        site = _site_of(url)
        try:
            out = browser_session.start(sub, _CONNECTOR, login_url=url)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["site"] = site
        out["instructions"] = (
            f"Open `live_view_url`, log in to {site}, then call "
            f"`browser_connect_status` with context_id + session_id + site='{site}'.")
        return out

    @mcp.tool()
    async def browser_connect_status(ctx: Context, context_id: str, session_id: str,
                                     site: str, force: bool = False) -> dict:
        """Finalize a site's connection. Checks that you actually logged in in the Live
        View; if so, **remembers** the session for this site. Returns `{connected, site}`.
        Call it again if `connected=false` (not logged in yet).

        Args:
            context_id: value returned by `browser_connect_start`.
            session_id: value returned by `browser_connect_start`.
            site: host returned by `browser_connect_start` (e.g. `le-ticket.fr`).
            force: remember WITHOUT verification. The verification is generic (presence
                of cookies on the host): some sites keep their session elsewhere
                (localStorage) and wrongly answer "not logged in". Only use `force` if
                you did log in and the verification still fails.
        """
        sub = _sub()
        try:
            res = await browser_session.finalize(
                sub, _CONNECTOR, context_id, session_id, account=site, force=force)
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not res.connected:
            return {"connected": False, "site": site, "reason": res.reason,
                    "retry": res.retry,
                    "hint": res.detail or ("Not logged in yet (no cookie on this site) — "
                                           "log in in the Live View then retry.")}
        out = {"connected": True, "site": site, "reason": res.reason,
               "login_verified": not res.warning}
        if res.warning:
            out["warning"], out["retry"] = res.warning, False
        return out

    @mcp.tool()
    def browser_sites() -> dict:
        """List the sites you have connected in this browser (one per remembered login).
        Returns `{sites: [{id, label, is_default}]}` — `id` = the host to pass to the other
        tools. A site missing from here must first go through `browser_connect_start`."""
        sub = _sub()
        return {"sites": connector_identities.list_identities(sub, _CONNECTOR)}

    # --- Reading -------------------------------------------------------------
    @mcp.tool()
    async def browser_fetch(url: str, as_html: bool = False,
                            max_chars: int = _MAX_CHARS) -> dict:
        """Read a page **while logged in** on the site (remembered session, see
        `browser_connect_start`). Loads the URL in the remote browser and returns the
        rendered content — readable text by default, serialized DOM if `as_html`.

        Returns `{site, status, final_url, title, content, truncated}`. `status` = HTTP
        code of the navigation (a login page returned instead of the content signals
        an expired session → reconnect the site).

        Args:
            url: absolute URL of the page to read — refused under the project's
                `excluded_url_prefixes` (requested, or reached through a redirect).
            as_html: True = rendered HTML (to extract specific attributes/links);
                False (default) = readable text, much more compact.
            max_chars: cap on returned characters (truncation flagged by
                `truncated=true`).
        """
        # The perimeter refusal speaks FIRST (#632), before host validation.
        per = await asyncio.to_thread(url_perimeter.perimeter_of_call)
        url_perimeter.refuse_if_excluded(url, per)
        site = _site_of(url)
        if not browserbase.is_configured():
            raise _err("Browserbase not configured on the platform side "
                       "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
        ctx_id = _context_id(site)
        try:
            res = await browserbase.fetch_page(ctx_id, url, as_html=as_html)
        except browserbase.BrowserbaseError as e:
            raise _err(f"Browserbase execution failed: {e}", code=INTERNAL_ERROR)
        url_perimeter.refuse_if_excluded(res.get("final_url"), per)
        content = res.get("content") or ""
        cap = max(1, int(max_chars))
        return {"site": site, "status": res.get("status"),
                "final_url": res.get("final_url"), "title": res.get("title"),
                "content": content[:cap], "truncated": len(content) > cap}

    @mcp.tool()
    async def browser_eval(url: str, js: str) -> dict:
        """Run JavaScript **in the page**, on your logged-in session — an escape hatch
        for what `browser_fetch` does not cover (calling an internal API of the site with
        its rotating CSRF, clicking/expanding before reading, extracting a precise structure).

        `js` = source of an async function **with no argument**, e.g.
        `async () => (await fetch("/api/items", {credentials:"include"})).json()`.
        Its return value is returned as-is under `result` (a list is wrapped under
        `items` — MCP requires an object). The `fetch` is same-origin with
        `url`, so it carries the session cookies.

        Args:
            url: page to load before executing (provides the origin and the cookies) —
                refused under the project's `excluded_url_prefixes`.
            js: source of the async function to run in the page.
        """
        url_perimeter.refuse_if_excluded(
            url, await asyncio.to_thread(url_perimeter.perimeter_of_call))
        site = _site_of(url)
        if not browserbase.is_configured():
            raise _err("Browserbase not configured on the platform side "
                       "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
        ctx_id = _context_id(site)
        try:
            res = await browserbase.run_page_eval(ctx_id, url, js)
        except browserbase.BrowserbaseError as e:
            raise _err(f"Browserbase execution failed: {e}", code=INTERNAL_ERROR)
        if isinstance(res, list):
            return {"site": site, "items": res}
        return {"site": site, "result": res}
