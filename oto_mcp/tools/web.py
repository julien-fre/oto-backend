"""web_read — read a public page that defends itself, by ESCALATING (#348).

Measured in a real campaign: on small-business sites, two out of three
can't be read by a bare fetch (timeouts, anti-bot 403) — and the contact
details sought there are the end client's priority. Three tiers, one verb,
and the answer ALWAYS SAYS which path it took:

  ① bare HTTP fetch     — free; enough for most sites;
  ② hosted scraper      — `serper` (~2 credits): basic anti-bot, but NO
                          guaranteed JS rendering — measured on 02/09/2026, a
                          client-rendered site comes back 200 + a near-empty
                          body; it is tier ③ that really executes the JS;
  ③ hosted browser      — DISPOSABLE Chrome session (Browserbase, no account
                          or vault), OPT-IN `browser=True`: never a silent
                          default that multiplies the bill.

An unavailable tier (no serper key, Browserbase not configured) is SKIPPED
AND REPORTED — silent fallback is ruled out, in both directions.

⚠️ SECURITY (tier ① only — ② and ③ run outside our network):
the fetch runs on the box, which lives in a VPC with PRIVATE services. The
SSRF guard: http(s) schemes only, DNS resolution then refusal of any non-public
IP (loopback, RFC1918, link-local, metadata…), redirects walked BY HAND and
re-checked at each hop (a public→private 302 bypasses nothing).
Accepted limit: a zero-TTL DNS that answers differently between the check and
the connection (pure rebinding) is not covered — internal targets require
paths/headers that a page read doesn't supply anyway.

Read bounds: the cap is counted on DECOMPRESSED bytes, DURING the read (never
accumulate-then-truncate — the lesson of the decompression bomb).
"""
from __future__ import annotations

import asyncio
import time
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests
from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import INVALID_REQUEST, ErrorData

from . import cesures

from .. import access, browserbase, egress, session_org, url_perimeter
from ..connectors import health as connector_health

#: Marker for tier ② skipped because the Serper account being served is dry.
A_SEC = "a_sec"

_TIMEOUT = (10, 30)              # bounds EACH socket — not the whole read
_DEADLINE_S = 45                 # GLOBAL budget of tier ① (see `_fetch_http`)
_MAX_FETCH_BYTES = 3_000_000     # max decompressed bytes read (tier ①)
_EMPTY_TEXT_CHARS = 200          # extracted text shorter than this = empty shell
# ⚠️ This threshold catches the EMPTY shell, not the UNRENDERED page: measured on
# 02/09/2026, a client-rendered home page returns 455 chars via tier ②
# — above the threshold, so served as "read", with nothing escalating.
# Raising it by guesswork would discard real short pages: the case is handled by
# STATING the fact (see the description of `serper_scrape`), not by guessing a
# number. oto-backend, signal #653.
_MAX_REDIRECTS = 5
_DEFAULT_MAX_CHARS = 12_000
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_REQUEST, message=msg))


def _now() -> float:
    """Clock indirection — the global budget can be tested without sleeping."""
    return time.monotonic()


def _meme_site(demande: str, servi: str) -> bool:
    """Do `demande` and `servi` designate the SAME site?

    A leading `www.` and a subdomain are not discrepancies (`acme.fr` →
    `www.acme.fr` → `shop.acme.fr`: same house); two distinct domains are one.
    Deliberately without a public-suffix list: the rule "one is a suffix of
    the other on a label boundary" doesn't have the `.co.uk` hole that a
    comparison of the last two labels would — `acme.co.uk` and
    `evil.co.uk` are suffixes of neither, so the discrepancy is REPORTED."""
    a = (demande or "").lower().removeprefix("www.")
    b = (servi or "").lower().removeprefix("www.")
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _cible_avec_repli_www(url: str) -> tuple[str, bool]:
    """`(url to read, fallback applied)` — `www.` in front of a BARE domain that doesn't resolve.

    Many sites only have a DNS record on `www.`: reading `acme.fr` therefore
    failed at the guard, in an immediate refusal, without the usual form ever
    being tried (oto#262, one of the gaps grouped in #188).

    ⚠️ **The fallback applies ONLY to a name that doesn't resolve.** A name that
    resolves to an internal address is a SECURITY refusal: it stays blunt, and
    no request is sent to its variant. The discriminator is the resolution
    itself (`egress.resolved_addresses`), not the refusal text, which is not
    a contract.

    ⚠️ **Nothing is bypassed**: the fallback URL goes through the full guard
    again when it is read, like any other target. Blocking DNS resolution, so
    called outside the loop."""
    morceaux = urlsplit(url)
    hote = morceaux.hostname or ""
    if not hote or hote.lower().startswith("www.") or hote.replace(".", "").isdigit() \
            or ":" in hote:
        return url, False
    port = morceaux.port or (443 if morceaux.scheme == "https" else 80)
    try:
        egress.resolved_addresses(hote, port)
        return url, False              # it resolves: the guard will decide normally
    except OSError:
        pass
    try:
        egress.resolved_addresses("www." + hote, port)
    except OSError:
        return url, False              # neither one: the original refusal will come out
    netloc = morceaux.netloc.replace(hote, "www." + hote, 1)
    return morceaux._replace(netloc=netloc).geturl(), True


# ── SSRF guard (tier ① only) ─────────────────────────────────────────────────
def check_url_public(url: str) -> None:
    """Raises (naming the reason) if `url` doesn't designate a PUBLIC target.

    This is the platform's egress guard (`oto_mcp/egress.py`), under the
    "URL chosen by the agent" policy: same decision on what is internal
    (fail-closed on the WHOLE set of resolved addresses — a host that resolves
    public AND private is refused, that being the typical bypass setup), and NO
    declared exception applies. Until 12/09/2026 (oto#180) this reader
    carried its own guard, written before the connectors' one: an open API
    reached from a run (`web_read` is the only path without an instance) went out
    through a separate rule — `not is_global` alone, without naming the refused range,
    and which let through a range the other refuses. One single seam."""
    try:
        egress.check_url(url, connector="web_read", field="url",
                         exceptions_declarees=False)
    except egress.EgressRefused as e:
        raise _bad(str(e)) from None


# ── text extraction (stdlib — no dependency needed to strip tags) ────────────
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "header", "footer", "ul", "ol", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data)


def extract_text(html_str: str) -> tuple:
    """(readable text, title) — repeated blank lines collapsed."""
    p = _TextExtractor()
    try:
        p.feed(html_str)
    # noqa: SILENT — optional text extraction: the raw HTML is still returned
    except Exception:  # noqa: BLE001 — monstrous HTML doesn't break the read
        pass
    # oto#208: the invisible soft hyphen cuts the words read by the agent.
    brut = cesures.retirer("".join(p.parts))
    lignes = [l.strip() for l in brut.splitlines()]
    texte = "\n".join(l for i, l in enumerate(lignes)
                      if l or (i > 0 and lignes[i - 1]))
    return texte.strip(), p.title.strip()


# ── tier ①: bare HTTP fetch, streamed, guarded ───────────────────────────────
def _fetch_http(url: str, deadline_s: float = _DEADLINE_S) -> dict:
    """{verdict, ok, status?, html?, final_url?} — redirects are walked
    BY HAND: each hop goes through the SSRF guard again.

    `deadline_s` = the GLOBAL budget, configurable because not all callers
    have the same patience: `web_read` escalates (45 s is justified), the
    obfuscation probe of `serper_scrape` comes on top of a scrape ALREADY paid for
    and is only entitled to a few seconds (#681).

    ⚠️ `_TIMEOUT` bounds each SOCKET, never the whole read: six redirect hops
    are worth six times that budget, and the streaming loop is bounded by
    nothing at all (a server that drips one byte at a time holds the
    connection indefinitely). Measured in the prod log of 17/08: 11 reads
    beyond 30 s, one at **57.5 s**. Hence a GLOBAL budget (`_DEADLINE_S`),
    checked before each hop AND during the read, which also trims the socket
    timeout to what remains. The verdict SAYS what it tried
    (how many hops, where it was) — a bare "timeout" tells the agent nothing
    when it must decide whether to retry (#491)."""
    t0 = _now()
    courante = url
    sauts = 0
    budget = max(1.0, float(deadline_s))

    def _delai(ou: str) -> dict:
        ecoule = _now() - t0
        return {"ok": False,
                "verdict": ("global timeout exceeded ({:.0f} s) {} — {} redirect(s) "
                            "followed, last target: {}"
                            .format(ecoule, ou, sauts, courante))}

    for _ in range(_MAX_REDIRECTS + 1):
        reste = budget - (_now() - t0)
        if reste <= 0:
            return _delai("before the next hop")
        check_url_public(courante)
        try:
            r = requests.get(courante, stream=True, allow_redirects=False,
                             timeout=(min(_TIMEOUT[0], reste), min(_TIMEOUT[1], reste)),
                             headers={"User-Agent": _UA})
        except requests.Timeout:
            return {"ok": False,
                    "verdict": "timeout after {} redirect(s), on {}".format(
                        sauts, courante)}
        except requests.RequestException as e:
            return {"ok": False, "verdict": f"network: {type(e).__name__}"}
        try:
            if r.is_redirect or r.is_permanent_redirect:
                cible = r.headers.get("Location")
                if not cible:
                    return {"ok": False, "verdict": "redirect without a target"}
                courante = urljoin(courante, cible)
                sauts += 1
                continue
            if r.status_code >= 400:
                return {"ok": False, "verdict": f"HTTP {r.status_code}",
                        "status": r.status_code}
            # STREAMED read, cap on DECOMPRESSED bytes, stop DURING.
            # The budget is rechecked on every chunk: this is where a slow
            # server held the read for 57 s (#491).
            morceaux, total = [], 0
            for chunk in r.iter_content(chunk_size=65536, decode_unicode=False):
                morceaux.append(chunk)
                total += len(chunk)
                if total >= _MAX_FETCH_BYTES:
                    break
                if _now() - t0 >= budget:
                    return _delai("while reading the body")
            brut = b"".join(morceaux)
            html_str = brut.decode(r.encoding or "utf-8", errors="replace")
            return {"ok": True, "status": r.status_code, "html": html_str,
                    "final_url": courante, "verdict": "read"}
        finally:
            r.close()
    return {"ok": False, "verdict": f"more than {_MAX_REDIRECTS} redirects"}


def register(mcp: FastMCP) -> None:

    def _serper_scrape(url: str) -> "Optional[dict] | str":
        """Tier ② — None if the serper key can't be resolved (tier skipped), `A_SEC`
        if the account of the served key is dry (tier skipped, key marked)."""
        from .serper import a_sec, client_for, credits_consumed, MSG_A_SEC

        try:
            key, is_platform = access.resolve_api_key("serper")
        # noqa: SILENT — declared debt: vault error read as "no serper key" (#424, verdict C)
        except Exception:  # noqa: BLE001 — no key = tier unavailable, not an outage
            return None
        # The client SHARED with the `serper_*` tools: a single limiter per key (oto#115).
        try:
            res = client_for(key).scrape_page(url, include_markdown=True)
        except RuntimeError as e:
            if a_sec(e) is None:
                raise
            # The tier is SKIPPED (the others can still read the page), so
            # `web_read` doesn't raise and the envelope marks nothing: here we mark the
            # served key, using the same helper. And we remove it from the trace, otherwise a
            # `web_read` that succeeds through another tier would immediately erase the mark.
            trace = session_org.current_call_trace()
            ligne = (trace or {}).pop("credential_row", None)
            if ligne is not None:
                connector_health.marquer_quota_epuise(ligne, MSG_A_SEC)
            return A_SEC
        if is_platform:
            # `web_read` is the backend's SECOND serper mouth, and it debited 1 where
            # a scrape costs 2 (the description below has always said so): the
            # internal quota therefore under-counted by half everything that went
            # through tier ②. Same rule as the `serper_*` tools, imported and
            # not copied — a duplicated cost rule is a rule that diverges.
            access.record_platform_usage("serper", credits_consumed("scrape_page", res))
        return res

    @mcp.tool()
    async def web_read(url: str, browser: bool = False, as_html: bool = False,
                       max_chars: int = _DEFAULT_MAX_CHARS) -> dict:
        """Read a PUBLIC web page, escalating until it yields — and always
        saying which path was used.

        Escalation: ① plain HTTP fetch (free) → on 403/timeout/empty shell
        ② hosted scraper (serper, ~2 credits — basic anti-bot, but NO guaranteed
        JS rendering: a client-rendered page comes back 200 with a near-empty
        body, and ② will report it as read) →
        ③ ONLY IF `browser=true`: a disposable hosted Chrome session
        (real fingerprint, patience — costs a browser session). Without
        `browser=true` the answer stops at ② and tells you what to do.

        Returns `{content, title, final_url, hote, chemin, tentatives, cout,
        truncated}` — `chemin` = which path actually produced the content
        (`http` | `serper` | `browser`), `tentatives` = every path tried and
        why it moved on, `cout` = what the read cost (serper credits, browser
        session). A skipped path (no serper key, Browserbase not configured)
        is REPORTED, never silent.

        ⚠️ `final_url` is the OBSERVED landing URL, or `null` when the path
        used cannot report one (the hosted scraper follows redirects silently).
        `hote` = `{demande, servi, conforme}` says whether the page actually
        served belongs to the domain you asked for: `conforme` is `true`
        (same site), `false` (a redirect took you elsewhere — the content is
        that OTHER site's) or `null` (unknowable on this path). Anything but
        `true` also sets `avertissement`. Never assume the body came from the
        host you requested — read `hote`.

        Args:
            url: absolute public URL (http/https). Internal/private addresses
                are refused by design, and so is a page under the project's
                `excluded_url_prefixes` (requested or landed on by redirect).
            browser: opt-in for the costly last resort (real Chrome session).
            as_html: True = raw HTML/DOM instead of readable text (①/③ only —
                ② returns markdown).
            max_chars: cap on returned content (truncation is flagged).
        """
        # Project perimeter (#605): resolved ONCE (DB read → outside the loop),
        # applied to the requested URL here and to the URL OBSERVED after redirect
        # further down — an `acme.fr/equipe/x` that lands on a profile is a profile.
        # And FIRST (#632): before any other rule of this tool.
        per = await asyncio.to_thread(url_perimeter.perimeter_of_call)
        url_perimeter.refuse_if_excluded(url, per)

        cap = max(1, int(max_chars))
        tentatives: list = []
        cout = {"serper_credits": 0, "browser_session": False}

        demande = urlsplit(url).hostname or ""

        def _sortie(chemin: str, content: str, title: str = "",
                    final_url: Optional[str] = None) -> dict:
            """Assembles the response — and never ASSERTS the final URL.

            Signal #491: this field copied the REQUESTED URL when the tier
            observed none (`final_url or url`). But serper follows
            redirects silently and returns NO final URL: the tool
            therefore swore the page came from the requested host, without knowing
            anything about it — and the caller's only defence (comparing `final_url` to
            the requested host) was structurally blind on this tier.

            Now: `final_url` is OBSERVED or `None`, `hote` carries the
            verdict (`conforme` is `None` when unknown), and anything
            that is not a clear `True` is stated in `avertissement`. It is the
            tool that announces the discrepancy, not the caller who has to think of it."""
            url_perimeter.refuse_if_excluded(final_url, per)
            servi = urlsplit(final_url).hostname if final_url else None
            conforme = _meme_site(demande, servi) if servi else None
            out = {"chemin": chemin, "content": content[:cap],
                   "truncated": len(content) > cap, "title": title,
                   "final_url": final_url,
                   "hote": {"demande": demande, "servi": servi,
                            "conforme": conforme},
                   "tentatives": tentatives, "cout": cout}
            if conforme is None:
                out["avertissement"] = (
                    "Cannot confirm which site answered: the tier "
                    "`{}` doesn't return the final URL and follows redirects "
                    "without saying so. The content may come from a domain other "
                    "than `{}` — cross-check before drawing a fact from it.".format(
                        chemin, demande))
            elif conforme is False:
                out["avertissement"] = (
                    "You asked for `{}`; the page served comes from `{}` "
                    "(redirect followed). The content above is that of "
                    "`{}` — check that it is the intended site before "
                    "drawing a fact from it.".format(demande, servi, servi))
            return out

        # ── ① the bare fetch ─────────────────────────────────────────────────
        # `requests` is SYNCHRONOUS and this handler is `async def` (it `await`s
        # tier ③): run as-is, it freezes the loop — hence ALL
        # users — for the duration of the read. Measured in the 17/08 log:
        # 11 reads > 30 s, one at 57.5 s (docs/event-loop-perf.md, mode no. 1).
        # The AST safeguard can't see it: the handler does `await`
        # something, further down. Hence `to_thread` here (#491).
        url, repli_www = await asyncio.to_thread(_cible_avec_repli_www, url)
        if repli_www:
            url_perimeter.refuse_if_excluded(url, per)
            tentatives.append({"cran": "dns", "verdict": (
                f"`{demande}` doesn't resolve — fallback to `{urlsplit(url).hostname}`")})
        res = await asyncio.to_thread(_fetch_http, url)
        if res.get("ok"):
            texte, title = extract_text(res["html"])
            contenu = res["html"] if as_html else texte
            if len(texte) >= _EMPTY_TEXT_CHARS:
                tentatives.append({"cran": "http", "verdict": "read"})
                return _sortie("http", contenu, title, res.get("final_url") or None)
            tentatives.append({"cran": "http",
                               "verdict": f"empty shell ({len(texte)} useful chars)"})
        else:
            tentatives.append({"cran": "http", "verdict": res["verdict"]})

        # ── ② the hosted scraper ─────────────────────────────────────────────
        # Same reason as tier ①: `SerperClient` is synchronous (and rate-limits
        # itself with a `time.sleep`, per key: shared instance, oto#115), it has
        # no business in the loop.
        scrape = await asyncio.to_thread(_serper_scrape, url)
        if scrape is None:
            tentatives.append({"cran": "serper",
                               "verdict": "skipped — no serper key resolvable"})
        elif scrape == A_SEC:
            tentatives.append({"cran": "serper",
                               "verdict": "skipped — serper account dry (credits exhausted: "
                                          "top it up or set another key)"})
        else:
            # The count comes from the RESPONSE: Serper bills 2 credits on an
            # ordinary page and up to 10 on a hard page. The hard-coded 1
            # under-declared the spend to the caller who reads `cout` to decide
            # whether to escalate. Falls back to 1 if upstream doesn't say. SAME rule as
            # the one that debits the quota just above (`_serper_scrape`) and as
            # that of the `serper_*` tools: what we announce and what we debit can't
            # be two different readings of the same response.
            from .serper import credits_consumed
            cout["serper_credits"] = credits_consumed("scrape_page", scrape)
            md = scrape.get("markdown") or scrape.get("text") or ""
            meta = scrape.get("metadata") or {}
            if len(md.strip()) >= _EMPTY_TEXT_CHARS:
                tentatives.append({"cran": "serper", "verdict": "read"})
                return _sortie("serper", md, str(meta.get("title") or ""))
            tentatives.append({"cran": "serper",
                               "verdict": f"empty shell ({len(md.strip())} chars)"})

        # ── ③ the disposable browser — strict OPT-IN ─────────────────────────
        if not browser:
            raise _bad(
                "Page unreadable by fetch and scraper "
                f"({'; '.join(t['cran'] + ': ' + t['verdict'] for t in tentatives)}). "
                "Last resort: retry with browser=true — a hosted Chrome "
                "session (real cost, a few seconds of browser).")
        if not browserbase.is_configured():
            raise _bad("browser=true requested but Browserbase is not configured "
                       "on the platform side — report it (feedback signal=gap).")
        try:
            page = await browserbase.fetch_page_ephemeral(url, as_html=as_html)
        except browserbase.BrowserbaseError as e:
            tentatives.append({"cran": "browser", "verdict": str(e)[:200]})
            raise _bad("The browser session failed too — the page is "
                       f"unreadable by all three tiers. Attempts: {tentatives}")
        cout["browser_session"] = True
        contenu = page.get("content") or ""
        if not as_html:
            # innerText is already "text" — no second extraction.
            pass
        tentatives.append({"cran": "browser", "verdict": "read"})
        return _sortie("browser", contenu, page.get("title") or "",
                       page.get("final_url") or None)
