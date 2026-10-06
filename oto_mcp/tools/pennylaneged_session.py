"""Pennylane GED — the SESSION: login in the Live View, and verification probe.

Sister of `pennylaneged.py`, which carries the GED tools themselves. This module
deals with ONE thing only: establishing and verifying the user's login on
`app.pennylane.com`, then persisting their session to the vault via `browser_session`. The
two modules are mounted together (`providers/pennylaneged.py`, `modules=`); this one
imports the origin and the error seams from its elder, never the reverse.

⚠️ **The login probe NEVER hits a business route.** On 2026-09-03 it probed
the portfolio view (`/crm/flow_companies`); Pennylane moved it under
`/portfolio/`, it answered 404, the verification concluded "not logged in" and
`browser_session.finalize` returned before persisting: no client could
connect her GED any more, while three of the four tools worked. A session
route (`/users/me`) does not move with the product — that is the one we probe.
"""
from __future__ import annotations

from fastmcp import Context, FastMCP
from mcp.types import INTERNAL_ERROR

from .. import browser_session, browserbase
from .pennylaneged import _ORIGIN, _err, _sub


# Route PROBED for login — a SESSION route, never a business one. `/users/me` is what the SPA
# calls on load to know WHO is logged in (chunk `CurrentUserContext-*.js`):
# it answers **200 in both cases** and carries the verdict in its BODY (`user: null`
# = anonymous, object = logged in). It therefore follows neither the splitting of views nor
# namespace renames — unlike the PORTFOLIO route the code probed
# until 2026-09-03: moved under `/portfolio/`, it answered 404 and nobody
# could connect their GED any more.
_PROBE_PATH = "/users/me"

_PROBE_JS = r"""async (path) => {
    try {
        const r = await fetch(path, {credentials: "include",
            headers: {"accept": "application/json",
                      "x-requested-with": "XMLHttpRequest"}});
        const txt = await r.text();
        let data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
        return {status: r.status,
                login_page: /\/(auth\/)?login/.test(r.url || ""),
                json: data !== null && typeof data === "object",
                logged_in: !!(data && data.user && data.user.id)};
    } catch (e) { return {status: 0, error: String(e).slice(0, 200)}; }
}"""


def _read_probe(res: dict) -> browser_session.Verdict:
    """Verdict of the probe, from its raw response. PURE function (testable without a
    browser): it carries the rule. Four outcomes, not two —

    - **logged in**: 200 + non-null `user`;
    - **not logged in yet**: 200 + `user: null` → the human has not finished in the window;
    - **rejected**: 401, 403, landing on the login page → redo the login;
    - **no verdict**: 404 (the probed endpoint moved) or other → `ProbeUnavailable`,
      which is NOT an authentication signal (see `browser_session`).

    A failed `fetch` (status 0) is a retryable "not logged in": it says nothing about
    authentication, but retrying costs one click and may be enough."""
    V, st = browser_session.Verdict, res.get("status")
    if st in (401, 403):
        return V(False, browser_session.AUTH_REJECTED,
                 f"Pennylane refused the session ({st}): log in again in the Live "
                 "View, then rerun `pennylaneged_connect_status`.")
    if res.get("login_page"):
        return V(False, browser_session.AUTH_REJECTED,
                 "The session landed on the Pennylane login page: the login did not "
                 "complete (or it expired). Redo it in the Live View.")
    if st == 0:
        return V(False, browser_session.NO_SESSION,
                 "The probe could not reach Pennylane from the remote browser "
                 f"(GET {_PROBE_PATH} failed on the network). Rerun `pennylaneged_connect_status`.")
    if st == 200 and res.get("json"):
        if res.get("logged_in"):
            return V(True, browser_session.LOGGED_IN)
        return V(False, browser_session.NO_SESSION,
                 "Pennylane still sees you as anonymous: finish logging in in the Live "
                 "View window (email, password, 2FA), THEN rerun "
                 "`pennylaneged_connect_status` with the same session identifiers.")
    bouge = (": this endpoint no longer exists (route moved by Pennylane)"
             if st == 404 else "")
    raise browser_session.ProbeUnavailable(
        f"the Pennylane login probe could not reach a verdict — GET {_PROBE_PATH} "
        f"answered {st}{bouge}. Your session was saved anyway, WITHOUT login "
        "confirmation. Do not retry the connection: the problem is on our side, not on "
        "yours. Try a GED call directly — if it answers, all is well.")


async def _verify_session(session_id: str) -> browser_session.Verdict:
    """Pennylane login confirmed? Probes `/users/me` FROM the live session
    (same-origin) and decides on the response BODY, not its HTTP code — see
    `_read_probe`. Shared by the two connection surfaces (REST dashboard + MCP)
    via `browser_session`."""
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp(browserbase.connect_url(session_id))
        try:
            c = b.contexts[0] if b.contexts else await b.new_context()
            pg = c.pages[0] if c.pages else await c.new_page()
            await pg.goto(f"{_ORIGIN}/", wait_until="domcontentloaded", timeout=40000)
            res = await pg.evaluate(_PROBE_JS, _PROBE_PATH)
        finally:
            await b.close()
    return _read_probe(res)


# Declares Pennylane GED as a browser-session connector (generic start + this
# verify) — feeds the REST (dashboard) AND MCP connection flow. At import.
browser_session.register("pennylaneged", _verify_session, login_url=f"{_ORIGIN}/")


def register(mcp: FastMCP) -> None:

    # --- Onboarding (Live View) --------------------------------------------
    @mcp.tool()
    def pennylaneged_connect_start(ctx: Context) -> dict:
        """Starts the connection to the Pennylane GED. Opens a remote browser and
        returns a **`live_view_url`**: open it, log in to Pennylane normally
        (email/password, SSO, 2FA — you handle everything in that window). Then call
        `pennylaneged_connect_status(context_id, session_id)` with the returned values
        to finalize (your session is saved; redo only when it expires).
        """
        sub = _sub()
        try:
            out = browser_session.start(sub, "pennylaneged")
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        out["instructions"] = ("Open `live_view_url`, log in to Pennylane, then "
                               "call `pennylaneged_connect_status` with context_id + session_id.")
        return out

    @mcp.tool()
    async def pennylaneged_connect_status(ctx: Context, context_id: str,
                                          session_id: str,
                                          force: bool = False) -> dict:
        """Finalizes the connection to the Pennylane GED. Checks that you actually logged in in
        the Live View (`/users/me` probe from your session); if so, **saves** your
        session (the Context) for subsequent calls.

        Returns `{connected, reason, retry, hint}` — **read `reason` before retrying**:

        - `logged_in` → done, nothing to redo;
        - `no_session` → you have not (yet) finished logging in in the window: go
          through to the end, THEN call this tool again with the same `context_id`/`session_id`;
        - `auth_rejected` → Pennylane refused the session: redo the login;
        - `probe_unavailable` → your session IS saved (`connected: true`) but the
          probe could not confirm it, because it is broken on OUR side.
          `retry: false`: **do not retry**, try a GED call directly.

        ⚠️ `retry: false` means "retrying cannot succeed" — the problem
        is not on the user's side. Do not loop: say so and move on.

        Args:
            context_id: `context_id` returned by `pennylaneged_connect_start`.
            session_id: `session_id` returned by `pennylaneged_connect_start`.
            force: saves the session WITHOUT verifying the login. Escape hatch to avoid
                getting stuck when the probe is wrong or down. Use it only if you
                did log in and the verification still refuses: it may
                put a dead session in the vault, which you will only discover at the first
                GED call (401 → "session expired").
        """
        sub = _sub()
        try:
            res = await browser_session.finalize(sub, "pennylaneged", context_id,
                                                 session_id, force=bool(force))
        except browser_session.SessionError as e:
            raise _err(str(e), code=INTERNAL_ERROR)
        if not res.connected:
            return {"connected": False, "reason": res.reason, "retry": res.retry,
                    "hint": res.detail or "Not logged in yet — log in in the Live "
                                          "View then rerun."}
        out = {"connected": True, "context_id": context_id, "reason": res.reason,
               "login_verified": not res.warning}
        if res.warning:
            out["warning"] = res.warning
            out["retry"] = False
        return out
