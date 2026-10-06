"""Infosec — digital footprint of a domain (**passive** recon / OSINT).

Complements the "legal/financial identity" side (`fr_*`) with a company's
technical footprint when starting from a site/domain: whois (RDAP), DNS,
email posture (SPF/DMARC), subdomains (Certificate Transparency), TLS and
HTTP security headers.

**Passive only**: RDAP, DNS-over-HTTPS, public CT logs (crt.sh), TLS handshake,
one HTTP GET. NO port scanning or vulnerability probing — OSINT recon
of a prospect, nothing intrusive. No authorization from the target required since
we only consult public sources / the exposed service itself.

Open-data connector: no credential, no key. Exposed only if enabled
in the DB (activation gate, ADR 0010) — `register_all` gates on `connector_activation`.

**Consolidated surface (ADR 0047 §Amendment)**: the 6 original `infosec_*` tools
(whois / dns / email_security / subdomains / tls / headers) ALL took the same
single `domain` parameter — a single business object (the domain), 6 facets. They
are merged into `infosec_domain(op=…)`; the two non-shared parameters
(`limit` for subdomains, `port` for TLS) are optional and defaulted.
Each facet keeps its dedicated implementation (`_whois`, `_dns`, …): the dispatch
routes, it mixes nothing.

**Deliverability**: `blocklist` (free DNSBL blocklists, queried over DoH)
and `deliverability` (scored report + recommendations) serve the other reading of a
domain — YOUR OWN, to know whether its emails arrive. Lists that refuse
public resolvers (Spamhaus, SURBL, URIBL) are returned as `not_checked` with their
reason, never `clean`: a refusal answer (`127.255.255.x`…) is not a verdict.

**Bounds**: an op's DNS reads go through ONE probe (`_sonde`) — a reused
httpx client, a semaphore on parallel requests and a global deadline
(`_BUDGET_S`). A request that exceeds the deadline is an ERROR rendered in the
coverage, never an absence: the score only counts what was read. The SPF walk
stops beyond the RFC 7208 limit (10 lookups) — beyond that, the SPF is already in
`permerror`, and a booby-trapped SPF (fan-out includes) can no longer make DoH get hammered.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Literal, Optional

from urllib.parse import urlsplit

import httpx
from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INTERNAL_ERROR, INVALID_PARAMS

from .. import config


def _ua() -> str:
    """User-Agent of the probes: the contact address is that of THIS instance, never
    ours hard-coded (#968) — resolved at call time, `public_base_url` raises without it."""
    return f"oto-infosec/1.0 (+{config.public_base_url()})"


_DOH = "https://cloudflare-dns.com/dns-query"

# Bounds of a DNS op (`_sonde`): global deadline under the 45 s of a REST call,
# per-request timeout, and requests in flight at the same time (DKIM tests ~25 selectors,
# an IP list ~9 requests per IP — without a semaphore, a hundred go out at once).
_BUDGET_S = 25.0
_REQUETE_S = 8.0
_EN_VOL = 10


class BudgetEpuise(Exception):
    """The deadline of a DNS op is reached: the read did NOT happen (≠ absence)."""


class _Sonde:
    def __init__(self, client: "httpx.AsyncClient", budget_s: float):
        self.client = client
        self.sem = asyncio.Semaphore(_EN_VOL)
        self.fin = time.monotonic() + budget_s
        self.epuise = False

    def reste(self) -> float:
        return self.fin - time.monotonic()


_SONDE: ContextVar[Optional[_Sonde]] = ContextVar("infosec_sonde", default=None)


@asynccontextmanager
async def _sonde(budget_s: Optional[float] = None):
    """One probe per op: reused client, semaphore, deadline. The op's `_doh_raw`
    calls find it in the context."""
    async with httpx.AsyncClient(timeout=_REQUETE_S,
                                 headers={"accept": "application/dns-json"}) as c:
        sonde = _Sonde(c, _BUDGET_S if budget_s is None else budget_s)
        jeton = _SONDE.set(sonde)
        try:
            yield sonde
        finally:
            _SONDE.reset(jeton)

# Single source of the facets — the dispatch AND the error message derive from it, so
# that an op declared here without a branch cannot silently fall back onto something else.
_OPS = ("whois", "dns", "email_security", "subdomains", "tls", "headers",
        "blocklist", "deliverability")
_OPS_ERR = ("op must be 'whois', 'dns', 'email_security', 'subdomains', "
            "'tls', 'headers', 'blocklist' or 'deliverability'")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _norm_domain(value: str) -> str:
    """Reduces a URL/email/hostname to a bare domain (no scheme, port, path)."""
    v = (value or "").strip().lower()
    if "@" in v:
        v = v.split("@", 1)[1]
    if "://" not in v:
        v = "//" + v
    host = urlsplit(v).hostname or ""
    return host.strip(".")


async def _doh_raw(name: str, rtype: str) -> tuple[int, list[str]]:
    """(DNS status, answers) via Cloudflare DNS-over-HTTPS (JSON). Status 0 =
    NOERROR, 3 = NXDOMAIN, 2 = SERVFAIL — `blocklist` needs to tell them apart
    (absent from a list ≠ list unreachable). Only answers of the requested type
    are returned (a CNAME chain is not an answer)."""
    want = {"A": 1, "NS": 2, "CNAME": 5, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}.get(rtype)
    params = {"name": name, "type": rtype}
    sonde = _SONDE.get()
    if sonde is None:
        async with httpx.AsyncClient(timeout=15, headers={"accept": "application/dns-json"}) as c:
            r = await c.get(_DOH, params=params)
    else:
        async with sonde.sem:
            reste = sonde.reste()
            if reste <= 0:
                sonde.epuise = True
                raise BudgetEpuise(name)
            try:
                r = await asyncio.wait_for(sonde.client.get(_DOH, params=params),
                                           timeout=min(reste, _REQUETE_S))
            except asyncio.TimeoutError:
                if sonde.reste() <= 0:
                    sonde.epuise = True
                    raise BudgetEpuise(name)
                raise
    r.raise_for_status()
    data = r.json()
    out = []
    for ans in data.get("Answer", []) or []:
        if want is not None and ans.get("type") not in (want, None):
            continue
        d = (ans.get("data") or "").strip()
        if rtype == "TXT":
            # concatenate the chunks and strip the escaping quotes
            d = d.replace('" "', "").strip('"')
        out.append(d)
    return int(data.get("Status", 2)), out


async def _doh(name: str, rtype: str) -> list[str]:
    """Resolves a record type via Cloudflare DNS-over-HTTPS (JSON)."""
    return (await _doh_raw(name, rtype))[1]


def _vcard_field(vcard: list, field: str) -> Optional[str]:
    """Extracts a field from an RDAP jCard (vcardArray[1] = list of [name,_,_,value])."""
    try:
        for entry in vcard[1]:
            if entry[0] == field:
                val = entry[3]
                return val if isinstance(val, str) else " ".join(map(str, val))
    except (IndexError, TypeError):
        pass
    return None


# --- facets (one implementation per `op`, domain already normalized) ---------------

async def _whois(d: str) -> dict:
    """op="whois" — domain registration via RDAP."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"user-agent": _ua()}) as c:
        r = await c.get(f"https://rdap.org/domain/{d}")
        if r.status_code == 404:
            return {"domain": d, "found": False, "note": "not registered or TLD not covered by RDAP"}
        r.raise_for_status()
        j = r.json()
    events = {e.get("eventAction"): e.get("eventDate") for e in j.get("events", []) or []}
    registrar = registrant = None
    for ent in j.get("entities", []) or []:
        roles = ent.get("roles", []) or []
        name = _vcard_field(ent.get("vcardArray", []), "fn")
        if "registrar" in roles:
            registrar = name or ent.get("handle")
        if "registrant" in roles:
            registrant = name or ent.get("handle")
    return {
        "domain": j.get("ldhName", d),
        "found": True,
        "registrar": registrar,
        "registrant": registrant,
        "statuses": j.get("status", []),
        "created": events.get("registration"),
        "expires": events.get("expiration"),
        "last_changed": events.get("last changed"),
        "nameservers": [ns.get("ldhName") for ns in j.get("nameservers", []) or []],
    }


async def _dns(d: str) -> dict:
    """op="dns" — A/AAAA/MX/NS/TXT records + stack hints."""
    a, aaaa, mx, ns, txt = await asyncio.gather(
        _doh(d, "A"), _doh(d, "AAAA"), _doh(d, "MX"), _doh(d, "NS"), _doh(d, "TXT"),
        return_exceptions=True,
    )
    def ok(x): return x if isinstance(x, list) else []
    mx_list, txt_list = ok(mx), ok(txt)
    # A failed request is not an absence: it is named in `errors`.
    errors = {k: type(v).__name__ for k, v in
              (("A", a), ("AAAA", aaaa), ("MX", mx), ("NS", ns), ("TXT", txt))
              if isinstance(v, BaseException)}
    hints = []
    joined = " ".join(mx_list + txt_list).lower()
    for needle, label in (("google", "Google Workspace"), ("outlook", "Microsoft 365"),
                          ("protonmail", "Proton"), ("zoho", "Zoho"),
                          ("mailgun", "Mailgun"), ("sendgrid", "SendGrid"),
                          ("amazonses", "Amazon SES"), ("ovh", "OVH"),
                          ("atlassian", "Atlassian"), ("hubspot", "HubSpot")):
        if needle in joined:
            hints.append(label)
    return {
        "domain": d,
        "A": ok(a), "AAAA": ok(aaaa), "MX": mx_list, "NS": ok(ns), "TXT": txt_list,
        "stack_hints": sorted(set(hints)),
        "errors": errors,
    }


# Common DKIM selectors (mail and sending providers): DKIM is not
# enumerable, we test these — plus the one the caller gives.
_DKIM_SELECTORS = ("default", "google", "selector1", "selector2", "k1", "k2", "k3",
                   "dkim", "mail", "s1", "s2", "smtp", "mandrill", "mxvault",
                   "zoho", "protonmail", "protonmail2", "protonmail3", "sib",
                   "brevo", "mailjet", "resend", "pm", "everlytickey1", "turbo-smtp")

# SPF mechanisms that cost a DNS lookup — RFC 7208 §4.6.4 caps the total at 10.
_SPF_MAX_LOOKUPS = 10


def _spf_terms(record: str) -> list[str]:
    return [t for t in record.split()[1:] if t]


def _costs_lookup(term: str) -> bool:
    t = term.lstrip("+-~?").lower()
    return (t in ("a", "mx", "ptr")
            or t.startswith(("include:", "exists:", "redirect=", "a:", "a/",
                             "mx:", "mx/", "ptr:")))


class _Parcours:
    """Shared state of ONE SPF walk: domains read, lookups counted, stop.

    Breadth was not bounded (only depth): 10 includes per level
    over 4 levels gave 11,111 lookups. The counter is global to the walk, and
    the walk STOPS as soon as it exceeds the RFC 7208 limit — the SPF is then in
    `permerror`, the detail beyond that teaches nothing."""

    def __init__(self):
        self.seen: set = set()
        self.lookups = 0
        self.interrompu = False

    def depasse(self) -> bool:
        return self.lookups > _SPF_MAX_LOOKUPS


def _spf_vide(errors: Optional[list] = None) -> dict:
    return {"lookups": 0, "ipv4": [], "provider_ipv4": [], "duplicates": [],
            "errors": errors or [], "unread": False, "truncated": False}


async def _spf(domain: str) -> dict:
    """Full, bounded SPF walk of a domain; `unread` = the root record
    could NOT be read (≠ "no SPF")."""
    parcours = _Parcours()
    walk = await _spf_walk(domain, parcours)
    walk["lookups"] = parcours.lookups
    walk["truncated"] = parcours.interrompu
    return walk


async def _spf_walk(domain: str, parcours: "_Parcours", depth: int = 0,
                    path: tuple = ()) -> dict:
    """Counts an SPF's DNS lookups by following include/redirect (bounded), and
    collects the literal IPv4s it authorizes (for `blocklist`): `ipv4` = those
    of the domain's OWN record only, `provider_ipv4` = those of its includes
    (a provider's infrastructure, which says nothing about the domain)."""
    # `path` = the include chain leading here (a REAL loop); `seen` = everything
    # already walked (a duplicate: counted once, flagged by the caller).
    if depth > 10 or domain in path:
        return _spf_vide([f"loop or excessive depth on {domain}"])
    parcours.seen.add(domain)
    try:
        txt = await _doh(domain, "TXT")
    # noqa: SILENT — the failure is rendered in the result (errors, unread), not swallowed
    except Exception as e:
        out = _spf_vide([f"{domain}: {type(e).__name__}"])
        out["unread"] = depth == 0
        return out
    recs = [t for t in txt if t.lower().startswith("v=spf1")]
    if not recs:
        return _spf_vide([f"{domain}: no SPF"] if depth else [])
    ipv4, provider, duplicates, errors = [], [], [], []
    if len(recs) > 1:
        errors.append(f"{domain}: {len(recs)} SPF records (permerror)")
    for term in _spf_terms(recs[0]):
        low = term.lstrip("+-~?").lower()
        if low.startswith("ip4:"):
            ipv4.append(low[4:])
        if _costs_lookup(term):
            parcours.lookups += 1
            if parcours.depasse():
                # Beyond 10, the SPF is in permerror: we stop following it.
                parcours.interrompu = True
                continue
            target = None
            if low.startswith("include:"):
                target = low[8:]
            elif low.startswith("redirect="):
                target = low[9:]
            if target and target in path + (domain,):
                errors.append(f"include loop: {' → '.join(path + (domain, target))}")
                continue
            if target and target in parcours.seen:
                duplicates.append(target)   # the lookup is due, the content already read
                continue
            if target:
                sub = await _spf_walk(target, parcours, depth + 1, path + (domain,))
                # a `redirect=` replaces the record: its IPs remain the domain's
                if low.startswith("redirect="):
                    ipv4 += sub["ipv4"]
                else:
                    provider += sub["ipv4"]
                provider += sub["provider_ipv4"]
                duplicates += sub["duplicates"]
                errors += sub["errors"]
    return {"lookups": parcours.lookups, "ipv4": ipv4, "provider_ipv4": provider,
            "duplicates": duplicates, "errors": errors, "unread": False, "truncated": False}


def _tags(record: Optional[str]) -> dict:
    out = {}
    for part in (record or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


async def _email_security(d: str, dkim_selector: Optional[str] = None,
                          walk: Optional[dict] = None) -> dict:
    """op="email_security" — SPF/DMARC/DKIM/MTA-STS/TLS-RPT/BIMI posture + MX.

    `walk`: the SPF walk already done by the caller (`deliverability` shares it
    with `blocklist`); otherwise it is done here. `unread` names what could NOT be read
    (DNS error, deadline): the score never counts it as absent."""
    root_txt, dmarc_txt, mta, tlsrpt, bimi, mx = await asyncio.gather(
        _doh(d, "TXT"), _doh(f"_dmarc.{d}", "TXT"), _doh(f"_mta-sts.{d}", "TXT"),
        _doh(f"_smtp._tls.{d}", "TXT"), _doh(f"default._bimi.{d}", "TXT"), _doh(d, "MX"),
        return_exceptions=True,
    )
    def ok(x): return x if isinstance(x, list) else []
    dns_errors = {k: type(v).__name__ for k, v in
                  (("spf", root_txt), ("dmarc", dmarc_txt), ("mta_sts", mta),
                   ("tls_rpt", tlsrpt), ("bimi", bimi), ("mx", mx))
                  if isinstance(v, BaseException)}
    spf_records = [t for t in ok(root_txt) if t.lower().startswith("v=spf1")]
    spf = spf_records[0] if spf_records else None
    spf_all = None
    if spf:
        for term in _spf_terms(spf):
            if term.lower().lstrip("+-~?") == "all":
                spf_all = term if term[0] in "+-~?" else "+" + term
    if not spf:
        walk = _spf_vide()
    elif walk is None:
        walk = await _spf(d)
    dmarc = next((t for t in ok(dmarc_txt) if t.lower().startswith("v=dmarc1")), None)
    dt = _tags(dmarc)
    dmarc_policy = (dt.get("p", "").lower() or None) if dmarc else None
    # DKIM: selectors can't be enumerated, we test the common ones
    selectors = list(_DKIM_SELECTORS)
    if dkim_selector and dkim_selector.strip().lower() not in selectors:
        selectors.insert(0, dkim_selector.strip().lower())
    dkim_found = []
    results = await asyncio.gather(
        *[_doh(f"{s}._domainkey.{d}", "TXT") for s in selectors], return_exceptions=True
    )
    for sel, res in zip(selectors, results):
        if isinstance(res, list) and any("v=dkim1" in t.lower() or "p=" in t.lower() for t in res):
            dkim_found.append(sel)
    dkim_unread = [sel for sel, res in zip(selectors, results) if isinstance(res, BaseException)]
    if dkim_unread and not dkim_found:
        # No selector found BUT some could not be read: nothing to conclude.
        dns_errors["dkim"] = f"{len(dkim_unread)} selector(s) not read"
    mx_list = ok(mx)
    null_mx = any(m.strip().rstrip(".") in ("0", "0 .") or m.strip().endswith(" .") for m in mx_list)
    score = sum([bool(spf), dmarc_policy in ("quarantine", "reject"),
                 bool(mta if isinstance(mta, list) and mta else None), bool(dkim_found)])
    grade = ["faible", "faible", "moyenne", "bonne", "forte"][score]
    return {
        "domain": d,
        "spf": spf,
        "spf_record_count": len(spf_records),
        "spf_all": spf_all,
        "spf_dns_lookups": walk["lookups"],
        "spf_lookup_limit_exceeded": walk["lookups"] > _SPF_MAX_LOOKUPS,
        "spf_duplicate_includes": sorted(set(walk["duplicates"])),
        "spf_errors": walk["errors"],
        "dmarc": dmarc,
        "dmarc_policy": dmarc_policy,
        "dmarc_subdomain_policy": dt.get("sp", "").lower() or None,
        "dmarc_pct": int(dt["pct"]) if dt.get("pct", "").isdigit() else (100 if dmarc else None),
        "dmarc_rua": dt.get("rua") or None,
        "dmarc_alignment": {"dkim": dt.get("adkim", "r"), "spf": dt.get("aspf", "r")} if dmarc else None,
        "mta_sts": bool(ok(mta)),
        "tls_rpt": bool([t for t in ok(tlsrpt) if t.lower().startswith("v=tlsrptv1")]),
        "bimi": bool([t for t in ok(bimi) if t.lower().startswith("v=bimi1")]),
        "mx": mx_list,
        "null_mx": null_mx,
        "dkim_selectors_found": dkim_found,
        "dkim_selector_requested": dkim_selector or None,
        "posture": grade,
        "unread": sorted(dns_errors),
        "dns_errors": dns_errors,
        "spf_walk_truncated": bool(walk.get("truncated")),
        "note": ("DKIM tested on common selectors only (enumeration impossible) — "
                 "pass `dkim_selector` to test the domain's own"),
    }


async def _subdomains(d: str, limit: int) -> dict:
    """op="subdomains" — known names read from the Certificate Transparency logs."""
    rows = None
    last_err = "unknown"
    async with httpx.AsyncClient(timeout=40, headers={"user-agent": _ua()}) as c:
        for attempt in range(3):  # crt.sh often returns transient 5xx
            try:
                r = await c.get("https://crt.sh/", params={"q": f"%.{d}", "output": "json"})
                if r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                rows = r.json()
                break
            # noqa: SILENT — bounded retry; the last failure is rendered by the caller
            except Exception as e:
                last_err = type(e).__name__
                await asyncio.sleep(1.5 * (attempt + 1))
    if rows is None:
        return {"domain": d, "error": f"crt.sh unavailable ({last_err})", "subdomains": []}
    names: set[str] = set()
    for row in rows:
        for n in (row.get("name_value") or "").splitlines():
            n = n.strip().lower().lstrip("*.")
            if n.endswith(d) and n != d:
                names.add(n)
    ordered = sorted(names)
    return {"domain": d, "count": len(ordered), "subdomains": ordered[:limit]}


async def _tls(host: str, port: int) -> dict:
    """op="tls" — certificate presented by the host (issuer, validity, SANs, protocol)."""
    def probe() -> dict:
        ctx = ssl.create_default_context()
        try:
            with socket.create_connection((host, port), timeout=15) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    cert = ss.getpeercert() or {}
                    version, cipher = ss.version(), ss.cipher()
            validated, err = True, None
        except ssl.SSLCertVerificationError as e:
            # INSPECTION tool: we reconnect without verification ONLY
            # to read the protocol/cipher of a host with an invalid cert
            # (expired/self-signed/mismatch) — diagnostic, no data exchanged,
            # `validated=False` is surfaced as-is. Not a trust channel.
            cert, validated, err = {}, False, str(e)
            uctx = ssl._create_unverified_context()
            with socket.create_connection((host, port), timeout=15) as sock:
                with uctx.wrap_socket(sock, server_hostname=host) as ss:
                    version, cipher = ss.version(), ss.cipher()
        subject = {k: v for t in cert.get("subject", ()) for k, v in t}
        issuer = {k: v for t in cert.get("issuer", ()) for k, v in t}
        sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
        return {
            "host": host, "port": port, "validated": validated, "error": err,
            "protocol": version,
            "cipher": cipher[0] if cipher else None,
            "issuer": issuer.get("organizationName") or issuer.get("commonName"),
            "subject_cn": subject.get("commonName"),
            "valid_from": cert.get("notBefore"),
            "valid_until": cert.get("notAfter"),
            "subject_alt_names": sans,
        }

    try:
        return await asyncio.to_thread(probe)
    # noqa: SILENT — the failure is rendered in the result (error), not swallowed
    except Exception as e:
        return {"host": host, "port": port, "error": f"{type(e).__name__}: {e}"}


async def _headers(d: str) -> dict:
    """op="headers" — HTTP security headers + server fingerprint (a single GET)."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"user-agent": _ua()}) as c:
            r = await c.get(f"https://{d}")
    # noqa: SILENT — the failure is rendered in the result (error), not swallowed
    except Exception as e:
        return {"domain": d, "error": f"{type(e).__name__}: {e}"}
    h = {k.lower(): v for k, v in r.headers.items()}
    checks = {
        "hsts": "strict-transport-security" in h,
        "csp": "content-security-policy" in h,
        "x_frame_options": "x-frame-options" in h,
        "x_content_type_options": "x-content-type-options" in h,
        "referrer_policy": "referrer-policy" in h,
        "permissions_policy": "permissions-policy" in h,
    }
    present = sum(checks.values())
    return {
        "domain": d,
        "final_url": str(r.url),
        "status": r.status_code,
        "security_headers": checks,
        "security_headers_score": f"{present}/{len(checks)}",
        "server": h.get("server"),
        "x_powered_by": h.get("x-powered-by"),
    }


# --- deliverability ------------------------------------------------------------

# Blocklists queryable FOR FREE via a public resolver, AND whose
# terms allow automated commercial use (read on 04/10/2026):
# SpamCop (free, no usage restriction), PSBL ("anybody is free to use"),
# NordSpam (free, commercial or not — nordspam.com/usage), s5h (free, no
# limit — usenix.org.uk/content/rbl.html). `listed` = the codes that count as
# a listing; any other 127.x code is returned as `unexpected_answer`, never interpreted.
_IP_LISTS = (
    {"zone": "bl.spamcop.net", "name": "SpamCop", "listed": ("127.0.0.2",),
     "info": "https://www.spamcop.net/bl.shtml"},
    {"zone": "psbl.surriel.com", "name": "PSBL", "listed": ("127.0.0.2",),
     "info": "https://psbl.org/"},
    {"zone": "bl.nordspam.com", "name": "NordSpam", "listed": ("127.0.0.2",),
     "info": "https://www.nordspam.com/"},
    {"zone": "all.s5h.net", "name": "s5h", "listed": ("127.0.0.2",),
     "info": "https://www.usenix.org.uk/content/rbl.html"},
)
_DOMAIN_LISTS = (
    {"zone": "dbl.nordspam.com", "name": "NordSpam DBL", "listed": ("127.0.0.2",),
     "info": "https://www.nordspam.com/"},
)
# Major lists that REFUSE public resolvers (and whose commercial use is
# paid): named so that the absence of a verdict is visible, not queried.
_NOT_CHECKED = (
    {"name": "Spamhaus (ZEN / DBL)", "reason": "refuses public resolvers; commercial "
     "access by key (Data Query Service) not configured"},
    {"name": "SURBL", "reason": "refuses public resolvers; commercial feed required"},
    {"name": "URIBL", "reason": "refuses public resolvers; commercial feed required"},
    {"name": "Barracuda (BRBL)", "reason": "free but the resolver's IP must be "
     "registered with Barracuda; a shared public resolver is not"},
    {"name": "Mailspike", "reason": "public-resolver access is reserved for "
     "low-volume non-commercial sites"},
    {"name": "UCEPROTECT", "reason": "only allows manual lookups; an "
     "automated query gets the source blocked"},
)
_MAX_IPS = 10


async def _dnsbl(query: str, lst: dict) -> dict:
    """One DNSBL query: `listed` / `clean` / `error` — never `clean` on
    anything other than a clear NXDOMAIN."""
    row = {"list": lst["name"], "zone": lst["zone"]}
    try:
        status, answers = await _doh_raw(f"{query}.{lst['zone']}", "A")
    # noqa: SILENT — the failure is rendered in the result (status=error), not swallowed
    except Exception as e:
        return {**row, "status": "error", "detail": type(e).__name__}
    if status == 3:
        return {**row, "status": "clean"}
    if status != 0:
        return {**row, "status": "error", "detail": f"DNS status {status}"}
    if not answers:
        # NOERROR with no record: this is not the NXDOMAIN of an unlisted IP.
        return {**row, "status": "error", "detail": "empty response (NOERROR without A)"}
    if any(a in lst["listed"] for a in answers):
        return {**row, "status": "listed", "codes": answers, "delist_info": lst["info"]}
    return {**row, "status": "error", "detail": f"unexpected_answer {answers}"}


async def _ptr(ip: str) -> dict:
    """Reverse DNS of an IPv4 + forward confirmation (FCrDNS): a sender without a
    consistent PTR is penalized by most filters."""
    rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    try:
        names = [n.rstrip(".") for n in await _doh(rev, "PTR")]
        fwd = await _doh(names[0], "A") if names else []
    # noqa: SILENT — the failure is rendered in the result (error), not swallowed
    except Exception as e:
        return {"ptr": None, "fcrdns": False, "error": type(e).__name__}
    return {"ptr": names[0] if names else None, "fcrdns": bool(names) and ip in fwd}


async def _blocklist(d: Optional[str], ip: Optional[str],
                     walk: Optional[dict] = None) -> dict:
    """op="blocklist" — the domain on the domain lists, and the sending IPv4s
    (`ip` if given, else the literal `ip4:` entries of the SPF) on the IP lists. `walk`:
    the SPF walk already done by the caller, otherwise done here."""
    ips: list[tuple[str, str]] = []
    notes, ranges = [], []
    if ip:
        ips.append((ip, "argument"))
    elif d:
        if walk is None:
            walk = await _spf(d)
        if walk.get("unread"):
            notes.append("the SPF could not be read (DNS error): its sending IPs are "
                         "not checked — this is not the absence of SPF")
        for raw in walk["ipv4"]:
            try:
                net = ipaddress.ip_network(raw, strict=False)
            except ValueError:
                continue
            if net.num_addresses == 1:
                ips.append((str(net.network_address), "spf"))
            else:
                ranges.append(raw)
        if ranges:
            notes.append(f"{len(ranges)} IP range(s) of the SPF not checked — only "
                         f"single IPs are: {', '.join(ranges[:5])}"
                         + (" …" if len(ranges) > 5 else ""))
        if walk["provider_ipv4"]:
            notes.append(f"{len(walk['provider_ipv4'])} IP(s)/range(s) of the providers included "
                         "in the SPF not checked: they belong to their "
                         "infrastructure, not to the domain")
        if not ips and not walk.get("unread"):
            notes.append("no dedicated sending IP found in the SPF: sending probably goes "
                         "through shared infrastructure (Google, Microsoft, "
                         "an emailing tool) whose IPs say nothing about the domain — "
                         "pass `ip` to check a dedicated IP")
    seen, uniq = set(), []
    for addr, src in ips:
        if addr not in seen:
            seen.add(addr)
            uniq.append((addr, src))
    if len(uniq) > _MAX_IPS:
        notes.append(f"{len(uniq)} IPs found, only the first {_MAX_IPS} are checked")
        uniq = uniq[:_MAX_IPS]

    async def check_ip(addr: str, src: str) -> dict:
        q = ".".join(reversed(addr.split(".")))
        rows, ptr = await asyncio.gather(
            asyncio.gather(*[_dnsbl(q, lst) for lst in _IP_LISTS]), _ptr(addr))
        return {"ip": addr, "source": src, **ptr,
                "listed_on": [r["list"] for r in rows if r["status"] == "listed"],
                "results": list(rows)}

    ip_results = list(await asyncio.gather(*[check_ip(a, s) for a, s in uniq]))
    domain_results = (list(await asyncio.gather(*[_dnsbl(d, lst) for lst in _DOMAIN_LISTS]))
                      if d else [])
    every = domain_results + [r for x in ip_results for r in x["results"]]
    listed = sorted({r["list"] for r in every if r["status"] == "listed"})
    return {
        "domain": d,
        "ips": ip_results,
        "domain_results": domain_results,
        "listed": bool(listed),
        "listed_on": listed,
        "checked": sum(1 for r in every if r["status"] in ("clean", "listed")),
        "errors": sum(1 for r in every if r["status"] == "error"),
        "not_checked": list(_NOT_CHECKED),
        "notes": notes,
    }


def _recommend(sec: dict, bl: dict) -> tuple[int, list[dict]]:
    """Score out of 100 and ranked recommendations (high > medium > low).

    What could not be read (`sec["unread"]`) is NEITHER penalized NOR declared present:
    a DNS error on `_dmarc` is not "No DMARC"."""
    recs: list[dict] = []
    score = 100
    non_lu = set(sec.get("unread") or ())

    def add(priority: str, cost: int, issue: str, fix: str) -> None:
        nonlocal score
        score -= cost
        recs.append({"priority": priority, "issue": issue, "fix": fix})

    if "mx" in non_lu:
        pass
    elif sec.get("null_mx"):
        add("medium", 5, "Null MX (RFC 7505): the domain accepts no mail",
            "Replies and bounces to this domain are lost — publish real MX records if it sends mail.")
    elif not sec.get("mx"):
        add("medium", 5, "No MX record", "Publish MX records so replies and bounces can be received.")
    if "spf" in non_lu:
        pass
    elif not sec.get("spf"):
        add("high", 25, "No SPF record", "Publish a TXT `v=spf1 … ~all` listing every service that sends for this domain.")
    else:
        if sec.get("spf_record_count", 1) > 1:
            add("high", 20, "Several SPF records (permerror)", "Merge them into a single `v=spf1` record.")
        if sec.get("spf_lookup_limit_exceeded"):
            besoin = ("more than 10" if sec.get("spf_walk_truncated")
                      else str(sec.get("spf_dns_lookups")))
            add("high", 15, f"SPF needs {besoin} DNS lookups (max 10)",
                "Remove unused includes or flatten the record.")
        if sec.get("spf_duplicate_includes"):
            add("low", 2, "SPF repeats include(s): " + ", ".join(sec["spf_duplicate_includes"]),
                "Keep each include once — every repeat still costs a DNS lookup.")
        if sec.get("spf_all") in ("+all", "?all"):
            add("high", 15, f"SPF ends with `{sec.get('spf_all')}`: anyone can send as this domain",
                "End the record with `~all` (or `-all` once all senders are listed).")
        elif not sec.get("spf_all") and "redirect=" not in (sec.get("spf") or "").lower():
            add("low", 3, "SPF has no `all` qualifier", "End the record with `~all` or `-all`.")
    policy = sec.get("dmarc_policy")
    if "dmarc" in non_lu:
        pass
    elif not sec.get("dmarc"):
        add("high", 20, "No DMARC record",
            "Publish `_dmarc` TXT `v=DMARC1; p=none; rua=mailto:…`, then move to quarantine. "
            "Gmail and Yahoo require DMARC from bulk senders.")
    else:
        if policy == "none":
            add("medium", 8, "DMARC policy is `p=none` (monitoring only)",
                "Once reports are clean, move to `p=quarantine` then `p=reject`.")
        if not sec.get("dmarc_rua"):
            add("low", 3, "DMARC has no `rua` report address", "Add `rua=mailto:…` to receive aggregate reports.")
        pct = sec.get("dmarc_pct")
        if pct is not None and pct < 100:
            # `pct=0`: the policy applies to NO message — this is the biggest
            # gap, not an absence of value.
            add("medium" if pct == 0 else "low", 8 if pct == 0 else 2,
                f"DMARC applies to {pct}% of mail only", "Raise `pct` to 100.")
    if "dkim" in non_lu:
        pass
    elif not sec.get("dkim_selectors_found"):
        add("medium", 10, "No DKIM key found on common selectors",
            "Enable DKIM signing at the sending provider; pass `dkim_selector` to check a custom selector.")
    if not sec.get("mta_sts") and "mta_sts" not in non_lu:
        add("low", 0, "No MTA-STS", "Optional: publish MTA-STS to enforce TLS on inbound mail.")
    if not sec.get("tls_rpt") and "tls_rpt" not in non_lu:
        add("low", 0, "No TLS-RPT", "Optional: publish `_smtp._tls` TXT to receive TLS failure reports.")
    if not sec.get("bimi") and "bimi" not in non_lu:
        add("low", 0, "No BIMI", "Optional: BIMI shows the brand logo in Gmail/Yahoo once DMARC is enforced.")
    for r in bl.get("domain_results", []):
        if r["status"] == "listed":
            add("high", 25, f"Domain listed on {r['list']}", f"Find the cause, then request removal: {r['delist_info']}")
    for x in bl.get("ips", []):
        for r in x["results"]:
            if r["status"] == "listed":
                add("high", 15, f"IP {x['ip']} listed on {r['list']}",
                    f"Stop the source of spam, then request removal: {r['delist_info']}")
        if not x.get("ptr"):
            add("medium", 5, f"IP {x['ip']} has no reverse DNS (PTR)", "Ask the IP owner to set a PTR record.")
        elif not x.get("fcrdns"):
            add("low", 3, f"IP {x['ip']} PTR does not resolve back to it", "Align the PTR host's A record with the IP.")
    order = {"high": 0, "medium": 1, "low": 2}
    recs.sort(key=lambda r: order[r["priority"]])
    return max(score, 0), recs


async def _deliverability(d: str, ip: Optional[str], dkim_selector: Optional[str]) -> dict:
    """op="deliverability" — full report: authentication + blocklists,
    scored out of 100, with ranked recommendations."""
    # ONE SPF walk, shared: it used to be redone by `email_security` and `blocklist`.
    walk = await _spf(d)
    sec, bl = await asyncio.gather(_email_security(d, dkim_selector, walk=walk),
                                   _blocklist(d, ip, walk=walk))
    score, recs = _recommend(sec, bl)
    sonde = _SONDE.get()
    grade = "good" if score >= 85 else "fair" if score >= 65 else "poor"
    # Coverage travels WITH the score: a "good" on a single list queried
    # must not read as a domain verified everywhere.
    coverage = {
        "blocklists_checked": bl["checked"],
        "blocklist_errors": bl["errors"],
        "own_ips_checked": len(bl["ips"]),
        "not_checked": [x["name"] for x in bl["not_checked"]],
        # What was not READ is not scored: the score is partial, and says so.
        "unread_records": sec.get("unread", []),
        "spf_walk_truncated": sec.get("spf_walk_truncated", False),
        "budget_exhausted": bool(sonde and sonde.epuise),
        "partial": bool(sec.get("unread") or bl["errors"] or (sonde and sonde.epuise)),
    }
    return {
        "domain": d,
        "score": score,
        "grade": grade,
        "coverage": coverage,
        "recommendations": recs,
        "authentication": sec,
        "blocklists": bl,
        "limits": ("Blocklists that refuse public resolvers (Spamhaus, SURBL, URIBL) are not "
                   "checked; inbox placement and sender reputation at Gmail/Microsoft are not "
                   "visible from DNS."),
    }


async def _borne(coro):
    """Runs a DNS op under ONE probe (client, semaphore, deadline). Requests
    beyond the deadline become named errors in the result (partial result);
    `wait_for` is only the safety net for an op that would not return control."""
    async with _sonde():
        try:
            return await asyncio.wait_for(coro, timeout=_BUDGET_S + 5)
        except asyncio.TimeoutError:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=(
                f"check interrupted after {_BUDGET_S + 5:.0f} s — retry, or "
                "use a narrower op (email_security, blocklist)")))


def register(mcp: FastMCP) -> None:
    # `op` has NO default: no facet is "the" natural reading of a
    # domain, and a default would answer something other than what was asked.
    @mcp.tool()
    async def infosec_domain(
        op: Literal["whois", "dns", "email_security", "subdomains", "tls", "headers",
                    "blocklist", "deliverability"],
        domain: str = "",
        ip: Optional[str] = None,
        dkim_selector: Optional[str] = None,
        limit: int = 100,
        port: int = 443,
    ) -> dict:
        """Check a domain: email deliverability, blocklists, DNS, whois, TLS — `op` picks the check.

        Read-only and passive: DNS-over-HTTPS, RDAP, public CT logs, a TLS handshake,
        one HTTP GET. No port scan, no vulnerability probing, no key needed.

        `op` :
        - **"deliverability"** : full email-deliverability report for YOUR sending
          domain — SPF/DMARC/DKIM/MTA-STS/TLS-RPT/BIMI + blocklists, a `score` /100,
          a `grade` and `recommendations` sorted by priority. Start here to answer
          « do our emails land in spam? ». What could not be read (DNS error, time
          budget) is listed in `coverage` and NOT scored — `coverage.partial` says so. Pass `ip` for a dedicated sending IP and
          `dkim_selector` if the domain signs with a custom selector.
        - **"blocklist"** : is the domain or a sending IPv4 on a spam blocklist (DNSBL)?
          Domain lists + IP lists (SpamCop, PSBL, NordSpam, s5h — lists whose terms
          allow automated commercial lookups), with reverse DNS of each IP. IPs come from `ip`, else from the
          domain's SPF `ip4:` entries. Each list answers `listed` / `clean` / `error` —
          an `error` is NOT a clean result. ⚠️ Spamhaus, SURBL, URIBL, Barracuda,
          Mailspike and UCEPROTECT are returned under `not_checked` (public resolvers
          refused, or terms that forbid automated commercial use), never as clean. A domain
          sending through Google/Microsoft/an emailing tool has no IP of its own to
          check — the response says so in `notes`.
        - **"email_security"** : email authentication posture — SPF (record, `all`
          qualifier, DNS lookup count vs the 10 limit), DMARC (policy, sp, pct, rua,
          alignment), DKIM on common selectors (+ `dkim_selector`), MTA-STS, TLS-RPT,
          BIMI, MX. ⚠️ DKIM selectors cannot be enumerated: an empty
          `dkim_selectors_found` does not prove DKIM is absent.
        - **"whois"** : domain registration via RDAP — registrar, creation/expiration
          dates, statuses, nameservers, registrant org when public.
        - **"dns"** : A/AAAA/MX/NS/TXT records, with stack hints (mail provider, SaaS
          verification tokens).
        - **"subdomains"** : known subdomains from Certificate Transparency logs
          (crt.sh). `count` = all distinct found, `subdomains` = truncated to `limit`.
          crt.sh often fails transiently: the response then carries `error`.
        - **"tls"** : TLS certificate of the host (issuer, validity, SANs, protocol);
          a failed validation is reported (`validated=false` + `error`), not hidden.
        - **"headers"** : HTTP security headers (HSTS, CSP, …) + server fingerprint.

        Args:
            op: deliverability | blocklist | email_security | whois | dns | subdomains | tls | headers.
            domain: a domain, URL or email address (only the domain part is kept).
                Required except for op="blocklist" with `ip`.
            ip: op="blocklist"/"deliverability" — an IPv4 sending address to check.
            dkim_selector: op="email_security"/"deliverability" — DKIM selector to test
                in addition to the common ones.
            limit: op="subdomains" — max distinct subdomains returned (default 100).
            port: op="tls" — TLS port (default 443).
        """
        if op not in _OPS:
            raise _bad(_OPS_ERR)
        d = _norm_domain(domain)
        addr = None
        if ip:
            try:
                parsed = ipaddress.ip_address(ip.strip())
            except ValueError:
                raise _bad(f"invalid ip: {ip!r} (IPv4 expected)")
            if parsed.version != 4:
                raise _bad("only IPv4s can be checked on DNS blocklists")
            addr = str(parsed)
        if not d and not (op == "blocklist" and addr):
            return {"error": "invalid domain"}
        if op == "whois":
            return await _whois(d)
        if op == "dns":
            return await _borne(_dns(d))
        if op == "email_security":
            return await _borne(_email_security(d, dkim_selector))
        if op == "subdomains":
            return await _subdomains(d, limit)
        if op == "tls":
            return await _tls(d, port)
        if op == "headers":
            return await _headers(d)
        if op == "blocklist":
            return await _borne(_blocklist(d or None, addr))
        if op == "deliverability":
            return await _borne(_deliverability(d, addr, dkim_selector))
        raise _bad(_OPS_ERR)  # defense in depth: _OPS cannot drift from the dispatch
