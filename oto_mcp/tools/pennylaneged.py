"""Pennylane GED (DMS) — document tray via the SPA's PRIVATE API.

⚠️ Pennylane's GED is **not exposed by the public API** (the keyed `pennylane`
connector therefore cannot write to it — its token carries no DMS scope).
It is exposed by the **internal API** of `app.pennylane.com` (session cookie + rotating
CSRF), under the company scope `/companies/{cid}/dms/…`. It is a connector
**distinct** from `pennylane`: a credential of a different nature (browser session,
not an API key).

Execution — **Browserbase** (`oto_mcp/browserbase.py`), same substrate as
`crunchbase`/`brevo`: the internal API only accepts calls from a **live browser
session** (a bare `httpx` risks being blocked by Cloudflare, and a session
cannot be transplanted by cookie export). The user logs in ONCE via the
**Live View** (`pennylaneged_connect_start`), their session persists in a Browserbase
**Context** (= the per-user credential, vault `pennylaneged`), and each DMS call
runs as a `fetch()` INSIDE an ephemeral session of the Context, same-origin
`app.pennylane.com`. Platform creds = env `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID`.

**Requirements of the internal API** (handled by the in-page JS `_FETCH_JS`): header
`accept: application/json` (otherwise HTML 404 — Rails constraint), `x-requested-with:
XMLHttpRequest`, and on writes `x-csrf-token` = the **rotating** value of the cookie
`my_csrf_token` (re-read on EVERY call — the `<meta csrf-token>` is stale after the 1st XHR).

**Data-plane split (GDPR)** — a file upload does NOT route the bytes
through Oto (see ADR / issue #31). `pennylaneged_request_upload` (control plane) requests
a **presigned S3 URL**; the LOCAL agent does the `PUT` of the bytes **directly** to
S3 (never through Oto, never via MCP); then `pennylaneged_finalize` (control plane)
creates the DMS entry from the `signed_id`. The bytes go `local → Pennylane S3`, their
destination anyway.

⚠️ **TWO homonymous `company_id` spaces, not interchangeable.** The keyed `pennylane`
connector (public API) returns ids that have NOTHING to do with the ones here.
Mixing them up produces a **401/403**, which this module used to translate as "session expired" — two
agents fell for it on 2026-09-03 and kept reconnecting a
live session. A refusal carrying a `/companies/<id>` therefore blames the ID FIRST. The GED id is read
in the SPA URL (`app.pennylane.com/companies/<id>/…`). ⚠️ And the warning is
placed where one FALLS — in every tool that CONSUMES a `company_id` — not only
in the one that returns it: it was already there since 28/08, and the trap recurred
identically, because nobody reads the description of a tool they are not calling.

**Target GED (one per client)** — the firm manages N client companies, each
with ITS OWN GED. Every tool takes a **mandatory** `company_id`: no stored
default, so as never to risk writing into the wrong client's GED.
`pennylaneged_companies` lists the companies to resolve the target `company_id` —
but it is NOT a required step, and one must not believe it is: the `company_id` is
readable **in the SPA URL** (`app.pennylane.com/companies/<company_id>/…`, visible
as soon as a file is opened), and `pennylaneged_companies(minimal=True)` returns it via an
independent route. When the list is down, the three other tools (tree, record,
upload) still work — on 2026-09-03, a client spent her morning believing the
connector dead because ONLY this list was.

⚠️ **A 404 here says "no endpoint for THIS call"**, never "session expired"
(that is 401/403 — verified on 03/09: a live route answers 401 to an
anonymous session). Two causes give it: the ACCOUNT lacks this scope (firm routes
only exist for an account attached to a firm — measured on 10/09: 200
for one, 404 for the other, at the same hour), or the ROUTE moved (Pennylane renames
without notice; the new one is found in the SPA bundle — the portfolio
migrated from `/crm/flow_companies` to `/portfolio/crm/flow_companies` on 03/09).
Decide without reconnecting anything: call again with `minimal=true`, which takes another
route — if it answers, it is the scope. ⚠️ This text asserted the second cause
ALONE until 10/09: closing a case by engraving ITS cause makes people blame the wrong
part as soon as the other shows up.

The LOGIN (Live View, verification probe, persisting the session to the vault) lives
in the sibling module `pennylaneged_session.py` — here, the session is assumed acquired.

Status: RE flow **manually validated** (18/06, client test account); **still to
smoke-test live** on the Browserbase substrate (in-page CSRF + session longevity).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlencode

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

from .. import access, browserbase
from ..auth.hooks import current_user_sub_from_token

# Origin of the SPA — all internal routes (DMS, direct_uploads, crm) derive
# from it. The page loaded to carry the session is same-origin (a path of
# this origin), so `fetch("/companies/…")` carries the cookies.
_ORIGIN = "https://app.pennylane.com"

# Pennylane-specific in-page JS: reads the rotating CSRF from the `my_csrf_token` cookie at
# call time and sets the expected Rails headers. `path` is an absolute path of the
# `app.pennylane.com` origin (so the `fetch` is same-origin).
_FETCH_JS = """async ({path, method, body}) => {
    const m = document.cookie.match(/(?:^|;\\s*)my_csrf_token=([^;]+)/);
    const headers = {"accept": "application/json", "x-requested-with": "XMLHttpRequest"};
    if (m) headers["x-csrf-token"] = decodeURIComponent(m[1]);
    if (body) headers["content-type"] = "application/json";
    const r = await fetch(path, {
        method, credentials: "include", headers,
        body: body ? JSON.stringify(body) : undefined,
    });
    // A Response body can only be read ONCE: `r.json()` LOCKS the
    // stream even before failing, so a `catch` that calls `r.text()` again throws
    // "body stream already read" and masks the REAL response. Experienced in prod on
    // 27/08 on a GED folder DELETE: the deletion had gone through, the tool
    // answered "Internal server error." (signal #600, tool_calls#1039702).
    // We read the text once, then parse it.
    const txt = await r.text();
    let data;
    try { data = txt ? JSON.parse(txt) : null; }
    catch (e) { data = {raw: txt.slice(0, 400)}; }
    return {status: r.status, data};
}"""


def _err(msg: str, code: int = INVALID_PARAMS) -> McpError:
    return McpError(ErrorData(code=code, message=msg))


def _sub() -> str:
    # An identity failure RISES (the seam logs it with its reason, #464): only
    # a call truly without a token is "unauthenticated".
    sub = current_user_sub_from_token()
    if not sub:
        raise _err("Auth required — this tool only works on the authenticated HTTP transport.")
    return sub


def _context_id() -> str:
    """The user's Browserbase Context (= their logged-in Pennylane session), resolved from the
    vault. Raises an actionable McpError if the GED is not connected."""
    try:
        return access.resolve_credential("pennylaneged", want="byo").key
    except McpError:
        raise _err("Pennylane GED not connected. Run `pennylaneged_connect_start` to "
                   "log in (once) to Pennylane via the Live View.")


def _company_app(company_id: int) -> str:
    """Page to load to prime the company context (the SPA requires a navigation
    to the company's DMS view before `/companies/{cid}/context` answers 200)."""
    return f"{_ORIGIN}/companies/{int(company_id)}/dms/items"



# Ce que `context` porte et qui ne décrit pas le dossier : la plomberie de Pennylane.
# Mesuré le 2026-10-07 : la fiche servait tel quel `pusher_channel_access_token` — un
# jeton — et les identifiants que Pennylane tient chez ses prestataires (temps réel,
# banque, CRM). On écarte par FAMILLE, pas par nom : le prochain jeton ou le prochain
# identifiant du même prestataire tombe sous la même règle.
_PLOMBERIE = ("pusher_", "swan_", "salesforce_")


def _sans_plomberie(company: dict) -> dict:
    return {k: v for k, v in company.items()
            if not k.startswith(_PLOMBERIE) and "token" not in k}


async def _call_raw(app: str, path: str, method: str = "GET",
                    body: Optional[dict] = None) -> dict:
    """The internal API call, returned RAW: `{status, data}`.

    Separate from `_call` because a WRITE needs the `status` to
    pronounce itself: a successful `DELETE` answers `204` **without a body**, and `_call`
    turned it into a `{}` indistinguishable from an empty response (signal #600).

    ⚠️ The `except` cannot be limited to `BrowserbaseError`: the #600 failure
    was a **playwright** error (`Page.evaluate: TypeError…`) raised
    from the page, of a class the substrate does not convert. It
    therefore bubbled up bare to the error taxonomy, which served it to the agent
    as "Internal server error." — a message that says neither what was
    attempted, nor that the write may have gone through. We name both."""
    if not browserbase.is_configured():
        raise _err("Browserbase not configured on the platform side "
                   "(BROWSERBASE_API_KEY / BROWSERBASE_PROJECT_ID).", code=INTERNAL_ERROR)
    ctx_id = _context_id()
    try:
        res = await browserbase.run_page_eval(
            ctx_id, app, _FETCH_JS, {"path": path, "method": method, "body": body})
    except McpError:
        raise
    except Exception as e:  # noqa: BLE001 — re-raised, named, just below
        # The request may have GONE OUT before the failure: on a write, saying
        # "failed" would assert more than we know (#600).
        incertitude = ("" if method.upper() == "GET" else
                       " The call may already have gone out: the write MAY HAVE "
                       "happened — re-read the tree (`pennylaneged_tree`) before "
                       "retrying.")
        raise _err(f"Pennylane GED call failed — {method.upper()} {path}: "
                   f"{type(e).__name__}: {e}.{incertitude}",
                   code=INTERNAL_ERROR) from e
    st = res.get("status")
    if st in (401, 403):
        # ⚠️ THREE causes, THREE courses of action — and the least likely was the only
        # one named. A refusal CARRYING A `company_id` blames that id first, not the
        # session: on 2026-09-03, two independent agents concluded "session
        # expired" and reconnected in a loop, while one held an id from the
        # `pennylane` connector (public API) and the other had a
        # perfectly live session.
        vise = re.search(r"/companies/(\d+)", path)
        if vise:
            raise _err(
                f"Pennylane refused {method.upper()} {path} ({st}). DO NOT CONCLUDE the "
                f"session expired: this call targets company {vise.group(1)}, and three "
                "causes give the same code. (1) WRONG ID SPACE — the most frequent: "
                "does this id come from the `pennylane` connector (public API)? That is NOT "
                "the same space as the GED. The GED id is read in the SPA URL, "
                "`app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`. "
                "(2) OUT OF SCOPE — the id is right but this account has no access to this "
                "company. (3) DEAD SESSION, the only case that justifies "
                "`pennylaneged_connect_start`. To decide between the three WITHOUT "
                "reconnecting: call the same tool on ANOTHER company. If it answers, "
                "your session is fine and the problem is the id.")
        err = _err(
            f"Pennylane refused {method.upper()} {path} ({st}) — this call targets no "
            "company in particular, so the session is indeed at fault: rerun "
            "`pennylaneged_connect_start`.")
        # The status comes from the page's fetch, not from an exception: nothing in
        # the chain carries the 401. Declared here, it marks the served session red
        # (`error_taxonomy.credential_rejected_in_chain`) — and only here: a refusal
        # aimed at a company is far more often the wrong id space (above).
        err.credential_rejected = True
        raise err
    if st == 404:
        # ⚠️ This block asserted a SINGLE cause — "the route no longer exists" — until
        # 2026-09-10, when a 404 on `/portfolio/crm/flow_companies` was measured while
        # the SAME route answered 200 for another account, at the same hour. The 404
        # does not say "the endpoint disappeared", it says "no endpoint FOR THIS CALL":
        # a firm route does not exist for an account that is attached to no
        # firm. Closing the 03/09 case by engraving its cause made people blame the wrong
        # part, and sent them looking for a fix on our side where there was nothing to
        # fix. The message therefore discriminates, and gives the means to decide.
        raise _err(
            f"Pennylane answered 404 on {method.upper()} {path}. This is NOT an "
            "expired session (that is 401/403): DO NOT RERUN "
            "`pennylaneged_connect_start`, your session is good. On this internal API, "
            "a 404 says \"no endpoint for THIS call\", and two causes give it. "
            "(1) YOUR ACCOUNT DOES NOT HAVE THIS SCOPE — the most frequent on firm "
            "routes (the portfolio): they only exist for an account attached to "
            "a firm. An ordinary company account gets 404, and that is normal: "
            "nothing to fix, neither on your side nor ours. (2) THE ROUTE MOVED — Pennylane "
            "renames its internal routes without notice (experienced on 03/09); in that case the fix "
            "is on our side, find the new route in the SPA bundle. "
            "TO DECIDE, without reconnecting anything: call `pennylaneged_companies` again "
            "with `minimal=true` — it is ANOTHER route (the company selector), which "
            "shares nothing with the portfolio. If it answers, your session AND the SPA "
            "are fine: you are in case (1), and `minimal` is precisely the route that "
            "suits you. If it returns 404 too, it is case (2): report it.",
            code=INTERNAL_ERROR)
    if not (200 <= (st or 0) < 300):
        raise _err(f"Pennylane GED returned {st}: {str(res.get('data'))[:200]}",
                   code=INTERNAL_ERROR)
    return {"status": st, "data": res.get("data")}


async def _call(app: str, path: str, method: str = "GET",
                body: Optional[dict] = None) -> dict:
    """`_call_raw` reduced to the decoded BODY — the shape reads expect.

    The formatting for the agent lives here, not in `_call_raw`: a
    write needs the raw `status` to pronounce itself on its own act."""
    data = (await _call_raw(app, path, method, body)).get("data")
    # The internal API sometimes returns a bare ARRAY (e.g. `/dms/items/tree`). MCP requires
    # a tool's structured_content to be an object (dict) or None — NEVER a
    # list (otherwise `ValueError: structured_content must be a dict` → broken tool, seen
    # in prod on `pennylaneged_tree`). Any list is wrapped under `items`: a
    # uniform, serializable shape for the agent.
    if isinstance(data, list):
        return {"items": data}
    return data or {}




def register(mcp: FastMCP) -> None:

    # --- "Where" resolution (control plane) ---------------------------------
    @mcp.tool()
    async def pennylaneged_companies(page: int = 1, minimal: bool = False) -> dict:
        """Lists the portfolio companies (firm side) — resolves the target `company_id`
        of a GED operation, and carries the management record of each file.

        ⚠️ **A firm's portfolio lives HERE**, not in the keyed `pennylane`
        connector: its public API is SINGLE-COMPANY and its "customers" are the
        clients INVOICED by a company, not the managed files. Searched there, the
        portfolio cannot be found — experienced by a client on 2026-08-28.

        ⚠️ **The route moved** (SPA bundle, chunk `list-*.js`,
        `getCRMFlowCompanies`, noted on 2026-09-03): `/crm/flow_companies` →
        `/portfolio/crm/flow_companies`. A 404 here is NOT "logged out" — but not
        "it moved again" for sure either: on a FIRM route, the most frequent
        cause is that **your account is attached to no firm**
        (measured on 10/09: 200 for a firm account, 404 for a company
        account, at the same hour). To decide: call again with `minimal=true`,
        which takes another route — if it answers, it is your scope, not the
        route, and `minimal` is the route that suits you. Returns the RAW response:
        `{companies: [...], pagination: {page, pageSize, pages, totalEntries,
        hasNextPage}}`. **20 companies per page** — a firm's portfolio is therefore
        walked over several calls, driven by `hasNextPage`/`pages`.

        Each company carries FAR MORE than its `id` (= `company_id`) and its `name` —
        it is the complete management record of the file (noted 2026-08-28):

        - **identity**: `legal_form` (legal form, e.g. `fr_sas`), `trade_name`,
          `client_code`, `file_type`, `is_demo`/`is_training`/`is_fake`;
        - **tax**: `vat_regime` + `vat_frequency` (VAT regime and periodicity —
          SEPARATE settings), `current_fiscal_year` (`{start, finish}`),
          `cash_based_accounting`, `number_of_employees`;
        - **file team**: `accountant` (accountant in charge, with email),
          `accounting_supervisor`, `accounting_manager`, `substitute_accountant`,
          `manager`, `legal_manager`, `social_manager`, `legal_collaborator`,
          `social_collaborator`, `external_auditor`;
        - **progress status**: `transactions` (`pending`, `accounting_needed`,
          `validation_needed`…), `supplier_invoices`, `customer_invoices`,
          `document_requests` (documents requested from the client), `bank_accounts`
          (connected / disconnected / imported by hand);
        - **subscription**: `subscription_plan`, `saas_plan`, `churns_on`, `confidential`.

        Enough to build a portfolio dashboard, not just resolve an id.

        ⚠️ Two values NOT to interpret blindly, for lack of Pennylane docs: the
        values of `vat_regime` (`standard` observed; the three FR regimes are franchise
        en base / réel simplifié / réel normal) and the shape of `client_code` (UUID on a
        TEST file, whereas Pennylane documents a "client code" that can be entered in the
        settings). Note the distinct values on a REAL portfolio before making
        them a readable column or a reconciliation key.

        ABSENT from here: the SIREN, and the **tax category** (IS/IR) — Pennylane
        distinguishes it from the "tax regime" and files it under the file's settings. Both
        are to be found elsewhere: `/companies/{id}/context` (`reg_no`) or the file's
        settings page.

        ⚠️ Cost: ONE browser session per call — 350 files = 18 pages = 18
        sessions opened then closed.

        **If this tool is down, the connector is NOT dead.** It is only the required step
        for the management record: the `company_id` alone is read in the SPA URL
        (`app.pennylane.com/companies/<company_id>/…`), and `minimal=True` returns it via a
        route INDEPENDENT of the portfolio's. Tree, company record and upload
        work without going through here.

        Args:
            page: pagination page (1-based).
            minimal: take the LIGHT route — `/navbar/companies`, the SPA's company
                selector route, which returns `{companies: [...]}` without the management
                record. Two uses: resolve a `company_id` at lower cost, and
                above all keep a route open when the portfolio route is
                down (they share nothing). Fields OBSERVED under a logged-in session
                on 2026-09-03: `id` (= the `company_id`), `display_name`, `source_id`,
                `saas_plan`, `uc_exists`, `is_demo`/`is_training`/`is_fake`, `firm`,
                `company_group` — enough to identify a file, NOTHING of the management
                record (no legal form, no VAT, no team, no to-do status).
        """
        if minimal:
            qs = urlencode({"page": max(1, int(page)), "per_page": 20})
            return await _call(f"{_ORIGIN}/", f"/navbar/companies?{qs}")
        qs = urlencode({"page": max(1, int(page))})
        return await _call(f"{_ORIGIN}/", f"/portfolio/crm/flow_companies?{qs}")

    @mcp.tool()
    async def pennylaneged_company(company_id: int) -> dict:
        """Record of ONE company: legal identity + TAX and VAT settings.

        Complements `pennylaneged_companies` (the portfolio) where it stops. It is
        HERE, and nowhere else, that the three settings live that Pennylane
        distinguishes and that NO public API returns (noted 2026-08-28):

        - `fiscal_category` — tax category, e.g. `bic_is` (BIC subject to corporate tax): it is the
          "IS / IR" of the permanent file;
        - `fiscal_regime` — tax regime, e.g. `fr_rn` (réel normal);
        - `vat_frequency`, `vat_day_of_month`, `submitted_to_vat_from`, `vat_number`,
          `default_input_vat_rate` / `default_output_vat_rate` (e.g. `FR_200`) — the VAT.

        ⚠️ **Three fields, three notions — do not merge them into one column.** The
        `vat_regime` returned by `pennylaneged_companies` (e.g. `standard`) is the VAT
        regime; it is DISTINCT from `fiscal_regime` (`fr_rn`) and from `fiscal_category`
        (`bic_is`). Mixing them up produces a wrong export.

        Also carries what the list lacks: `reg_no` (**the SIREN**), `legal_form_code`
        (INSEE legal form code, e.g. `5710` = SAS), `share_capital`,
        `creation_date` / `cessation_date`, `address` / `postal_code` / `city`,
        `business_description`, `invoicing_software`, `cash_based_accounting`,
        `resumption_status`, `dms_activated`.

        Hits `/companies/{cid}/context`. The feature-flag blocks of the
        response (`experiments`, `companyFeaturesAbility`, `userFeaturesAbility`) are
        DISCARDED: voluminous and of no business value, they would drown the record.
        So are Pennylane's own plumbing fields — any access token, and the ids it
        keeps for its real-time channel and its banking and CRM providers
        (`pusher_*`, `swan_*`, `salesforce_*`): they say nothing about the file.

        ⚠️ ONE call = ONE company = ONE browser session. Enriching an entire
        portfolio therefore costs one call PER file — weigh it against the volume.

        Args:
            company_id: GED id of the file — ⚠️ **NOT** that of the `pennylane` connector (public
                API): two homonymous spaces, and getting it wrong returns a 401/403
                that IMITATES an expired session. It is read in the SPA URL,
                `app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`.
        """
        cid = int(company_id)
        res = await _call(_company_app(cid), f"/companies/{cid}/context")
        company = res.get("company")
        if not company:
            raise _err(f"Unexpected `context` response for company {cid}: "
                       f"{str(res)[:200]}", code=INTERNAL_ERROR)
        return {"company": _sans_plomberie(company), "firm": res.get("firm"),
                "user_role": res.get("userRole")}

    # --- Tree / folders ------------------------------------------------------
    @mcp.tool()
    async def pennylaneged_tree(company_id: int,
                                item_type: str = "DmsFolder") -> dict:
        """Reads a company's GED tree.

        Returns `{items: [{id, name, itemable_type, parent_id, folders_count, …}]}`
        (the API returns an array, wrapped under `items`) — use the `id`/`parent_id`
        to target a creation `parent_id` or an item to delete.

        Args:
            company_id: GED id of the file — ⚠️ **NOT** that of the `pennylane` connector (public
                API): two homonymous spaces, and getting it wrong returns a 401/403
                that IMITATES an expired session. It is read in the SPA URL,
                `app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`.
            item_type: type of items listed — `DmsFolder` (folders, default) or `DmsFile`.
        """
        cid = int(company_id)
        qs = urlencode({"item_type": item_type})
        return await _call(_company_app(cid), f"/companies/{cid}/dms/items/tree?{qs}")

    @mcp.tool()
    async def pennylaneged_create_folder(company_id: int, name: str,
                                         parent_id: Optional[int] = None) -> dict:
        """Creates a folder in a company's GED.

        Returns the created `DmsFolder` (including its `id`, to reuse as `parent_id`).

        Args:
            name: folder name (in its final form — no separate rename afterwards).
            company_id: GED id of the file — ⚠️ **NOT** that of the `pennylane` connector (public
                API): two homonymous spaces, and getting it wrong returns a 401/403
                that IMITATES an expired session. It is read in the SPA URL,
                `app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`.
            parent_id: id of the parent folder (None = GED root).
        """
        cid = int(company_id)
        item: dict = {"name": name}
        if parent_id is not None:
            item["parent_id"] = int(parent_id)
        return await _call(_company_app(cid), f"/companies/{cid}/dms/items", "POST",
                           {"dms_items": [item]})

    # --- Upload (control plane; bytes PUT LOCALLY, never through Oto) --------
    @mcp.tool()
    async def pennylaneged_request_upload(
        company_id: int, filename: str, content_type: str,
        byte_size: int, checksum: str,
    ) -> dict:
        """Step 1/2 of a GED upload — requests a **presigned S3 URL** (control plane).

        ⚠️ Does NOT read the file (GDPR: the bytes NEVER transit through Oto).
        Compute LOCALLY, BEFORE this call: `byte_size` (size) and `checksum` (MD5 of the
        file, **base64**-encoded). Hits `direct_uploads` (ActiveStorage) and returns
        `{signed_id, put_url, put_headers}`.

        Then, LOCALLY (not via MCP, not through Oto): **PUT** the file's bytes
        directly to `put_url` passing `put_headers` (Content-Type, Content-MD5).
        Finally call `pennylaneged_finalize(name, signed_id, parent_id)`.

        Args:
            filename: name of the source file.
            content_type: MIME type (e.g. `application/pdf`).
            byte_size: file size in bytes (computed locally).
            checksum: MD5 of the file base64-encoded (computed locally).
            company_id: GED id of the file — ⚠️ **NOT** that of the `pennylane` connector (public
                API): two homonymous spaces, and getting it wrong returns a 401/403
                that IMITATES an expired session. It is read in the SPA URL,
                `app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`.
        """
        cid = int(company_id)
        res = await _call(
            _company_app(cid),
            f"/companies/{cid}/direct_uploads", "POST",
            {"blob": {"filename": filename, "content_type": content_type,
                      "byte_size": int(byte_size), "checksum": checksum}})
        direct = res.get("direct_upload") or {}
        signed_id = res.get("signed_id")
        put_url = direct.get("url")
        if not signed_id or not put_url:
            raise _err(f"Unexpected direct_uploads response: {str(res)[:200]}",
                       code=INTERNAL_ERROR)
        return {"signed_id": signed_id, "put_url": put_url,
                "put_headers": direct.get("headers") or {}}

    @mcp.tool()
    async def pennylaneged_finalize(company_id: int, name: str, signed_id: str,
                                    parent_id: Optional[int] = None) -> dict:
        """Step 2/2 of a GED upload — creates the DMS entry from a `signed_id` (control plane).

        To call AFTER having PUT the bytes locally to the `put_url` (see
        `pennylaneged_request_upload`). The `name` is the **final** name in the GED
        (standardized renaming = this field, no separate rename call).

        Returns the created `DmsFile`.

        Args:
            name: final name of the file in the GED.
            signed_id: `signed_id` returned by `pennylaneged_request_upload`.
            company_id: GED id of the file — ⚠️ **NOT** that of the `pennylane` connector (public
                API): two homonymous spaces, and getting it wrong returns a 401/403
                that IMITATES an expired session. It is read in the SPA URL,
                `app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`.
                ⚠️ MUST be the same company as at `pennylaneged_request_upload`.
            parent_id: id of the target folder (None = root).
        """
        cid = int(company_id)
        item: dict = {"name": name, "file": signed_id}
        if parent_id is not None:
            item["parent_id"] = int(parent_id)
        return await _call(_company_app(cid), f"/companies/{cid}/dms/items", "POST",
                           {"dms_items": [item]})

    @mcp.tool()
    async def pennylaneged_delete(company_id: int, item_id: int) -> dict:
        """Deletes an item (folder or file) from a company's GED.

        ⚠️ Deletion — only call after confirmation. A deleted folder takes its
        contents with it.

        Args:
            item_id: id of the DMS item to delete (see `pennylaneged_tree`).
            company_id: GED id of the file — ⚠️ **NOT** that of the `pennylane` connector (public
                API): two homonymous spaces, and getting it wrong returns a 401/403
                that IMITATES an expired session. It is read in the SPA URL,
                `app.pennylane.com/companies/<id>/…`, or via `pennylaneged_companies`.
        """
        cid, iid = int(company_id), int(item_id)
        # A successful DELETE answers 204 WITHOUT a body: returning the body (`{}`) tells
        # the agent nothing about the act it just committed, and that is
        # precisely what it was missing in #600. We confirm what we
        # deleted, with the code that attests to it.
        res = await _call_raw(_company_app(cid),
                              f"/companies/{cid}/dms/items/{iid}", "DELETE")
        return {"deleted": True, "item_id": iid, "company_id": cid,
                "status": res.get("status")}
