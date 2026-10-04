"""Infosec — empreinte numérique d'un domaine (recon **passif** / OSINT).

Complète le volet « identité légale/financière » (`fr_*`) par l'empreinte
technique d'une entreprise quand on part d'un site/domaine : whois (RDAP), DNS,
posture e-mail (SPF/DMARC), sous-domaines (Certificate Transparency), TLS et
en-têtes HTTP de sécurité.

**Passif uniquement** : RDAP, DNS-over-HTTPS, logs CT publics (crt.sh), handshake
TLS, un GET HTTP. PAS de scan de ports ni de probing de vulnérabilités — recon
OSINT d'un prospect, rien d'intrusif. Aucune autorisation de la cible requise car
on ne consulte que des sources publiques / le service exposé lui-même.

Connecteur open-data : pas de credential, pas de clé. Exposé seulement si activé
en DB (cran d'activation, ADR 0010) — `register_all` gate sur `connector_activation`.

**Surface consolidée (ADR 0047 §Amendement)** : les 6 tools `infosec_*` d'origine
(whois / dns / email_security / subdomains / tls / headers) prenaient TOUS le même
et unique paramètre `domain` — un seul objet métier (le domaine), 6 facettes. Ils
sont fusionnés en `infosec_domain(op=…)` ; les deux paramètres non partagés
(`limit` pour les sous-domaines, `port` pour TLS) sont optionnels et à défaut.
Chaque facette garde son implémentation dédiée (`_whois`, `_dns`, …) : le dispatch
route, il ne mélange rien.

**Délivrabilité** : `blocklist` (listes noires DNSBL gratuites, interrogées en DoH)
et `deliverability` (bilan noté + recommandations) servent l'autre lecture d'un
domaine — le SIEN, pour savoir si ses e-mails arrivent. Les listes qui refusent les
résolveurs publics (Spamhaus, SURBL, URIBL) sont rendues `not_checked` avec leur
raison, jamais `clean` : une réponse de refus (`127.255.255.x`…) n'est pas un verdict.

**Nom servi** : `oto_domain_check`, sous le namespace `oto_domain` déclaré par ce
connecteur (le gate d'activation reste celui d'`infosec`) ; un tenant à préfixe le
voit sous sa marque (`tool_alias`). `infosec_domain` reste appelable en alias
déprécié (`deprecations.TOOLS`).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from typing import Literal, Optional

from urllib.parse import urlsplit

import httpx
from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import config


def _ua() -> str:
    """User-Agent des sondes : l'adresse de contact est celle de CETTE instance, jamais
    la nôtre écrite en dur (#968) — résolue à l'appel, `public_base_url` lève sans elle."""
    return f"oto-infosec/1.0 (+{config.public_base_url()})"


_DOH = "https://cloudflare-dns.com/dns-query"

# Source unique des facettes — le dispatch ET le message d'erreur en dérivent, pour
# qu'une op déclarée ici sans branche ne puisse pas retomber en silence sur autre chose.
_OPS = ("whois", "dns", "email_security", "subdomains", "tls", "headers",
        "blocklist", "deliverability")
_OPS_ERR = ("op doit être 'whois', 'dns', 'email_security', 'subdomains', "
            "'tls', 'headers', 'blocklist' ou 'deliverability'")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _norm_domain(value: str) -> str:
    """Réduit une URL/e-mail/hostname à un domaine nu (sans schéma, port, chemin)."""
    v = (value or "").strip().lower()
    if "@" in v:
        v = v.split("@", 1)[1]
    if "://" not in v:
        v = "//" + v
    host = urlsplit(v).hostname or ""
    return host.strip(".")


async def _doh_raw(name: str, rtype: str) -> tuple[int, list[str]]:
    """(statut DNS, réponses) via DNS-over-HTTPS Cloudflare (JSON). Statut 0 =
    NOERROR, 3 = NXDOMAIN, 2 = SERVFAIL — `blocklist` a besoin de les distinguer
    (absent d'une liste ≠ liste injoignable). Seules les réponses du type demandé
    sont rendues (une chaîne CNAME n'est pas une réponse)."""
    want = {"A": 1, "NS": 2, "CNAME": 5, "PTR": 12, "MX": 15, "TXT": 16, "AAAA": 28}.get(rtype)
    async with httpx.AsyncClient(timeout=15, headers={"accept": "application/dns-json"}) as c:
        r = await c.get(_DOH, params={"name": name, "type": rtype})
        r.raise_for_status()
        data = r.json()
    out = []
    for ans in data.get("Answer", []) or []:
        if want is not None and ans.get("type") not in (want, None):
            continue
        d = (ans.get("data") or "").strip()
        if rtype == "TXT":
            # concatène les chunks et retire les guillemets d'échappement
            d = d.replace('" "', "").strip('"')
        out.append(d)
    return int(data.get("Status", 2)), out


async def _doh(name: str, rtype: str) -> list[str]:
    """Résout un type d'enregistrement via DNS-over-HTTPS Cloudflare (JSON)."""
    return (await _doh_raw(name, rtype))[1]


def _vcard_field(vcard: list, field: str) -> Optional[str]:
    """Extrait un champ d'un jCard RDAP (vcardArray[1] = liste de [name,_,_,value])."""
    try:
        for entry in vcard[1]:
            if entry[0] == field:
                val = entry[3]
                return val if isinstance(val, str) else " ".join(map(str, val))
    except (IndexError, TypeError):
        pass
    return None


# --- facettes (une implémentation par `op`, domaine déjà normalisé) ---------------

async def _whois(d: str) -> dict:
    """op="whois" — immatriculation du domaine via RDAP."""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"user-agent": _ua()}) as c:
        r = await c.get(f"https://rdap.org/domain/{d}")
        if r.status_code == 404:
            return {"domain": d, "found": False, "note": "non enregistré ou TLD non couvert par RDAP"}
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
    """op="dns" — enregistrements A/AAAA/MX/NS/TXT + indices de stack."""
    a, aaaa, mx, ns, txt = await asyncio.gather(
        _doh(d, "A"), _doh(d, "AAAA"), _doh(d, "MX"), _doh(d, "NS"), _doh(d, "TXT"),
        return_exceptions=True,
    )
    def ok(x): return x if isinstance(x, list) else []
    mx_list, txt_list = ok(mx), ok(txt)
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
    }


# Sélecteurs DKIM courants (fournisseurs de messagerie et d'envoi) : DKIM n'est pas
# énumérable, on teste ceux-là — plus celui que l'appelant donne.
_DKIM_SELECTORS = ("default", "google", "selector1", "selector2", "k1", "k2", "k3",
                   "dkim", "mail", "s1", "s2", "smtp", "mandrill", "mxvault",
                   "zoho", "protonmail", "protonmail2", "protonmail3", "sib",
                   "brevo", "mailjet", "resend", "pm", "everlytickey1", "turbo-smtp")

# Mécanismes SPF qui coûtent une requête DNS — la RFC 7208 §4.6.4 en borne le total à 10.
_SPF_MAX_LOOKUPS = 10


def _spf_terms(record: str) -> list[str]:
    return [t for t in record.split()[1:] if t]


def _costs_lookup(term: str) -> bool:
    t = term.lstrip("+-~?").lower()
    return (t in ("a", "mx", "ptr")
            or t.startswith(("include:", "exists:", "redirect=", "a:", "a/",
                             "mx:", "mx/", "ptr:")))


async def _spf_walk(domain: str, seen: set, depth: int = 0, path: tuple = ()) -> dict:
    """Compte les requêtes DNS d'un SPF en suivant include/redirect (borné), et
    relève les IPv4 littérales qu'il autorise (pour `blocklist`) : `ipv4` = celles
    du SEUL enregistrement du domaine, `provider_ipv4` = celles de ses include
    (l'infrastructure d'un fournisseur, qui ne dit rien du domaine)."""
    # `path` = la chaîne d'include qui mène ici (une VRAIE boucle) ; `seen` = tout ce
    # qui a déjà été parcouru (un doublon : compté une fois, signalé par l'appelant).
    if depth > 10 or domain in path:
        return {"lookups": 0, "ipv4": [], "provider_ipv4": [], "duplicates": [],
                "errors": [f"boucle ou profondeur excessive sur {domain}"]}
    seen.add(domain)
    try:
        txt = await _doh(domain, "TXT")
    # noqa: SILENT — l'échec est rendu dans le résultat (errors), pas avalé
    except Exception as e:
        return {"lookups": 0, "ipv4": [], "provider_ipv4": [], "duplicates": [],
                "errors": [f"{domain}: {type(e).__name__}"]}
    recs = [t for t in txt if t.lower().startswith("v=spf1")]
    if not recs:
        return {"lookups": 0, "ipv4": [], "provider_ipv4": [], "duplicates": [],
                "errors": [f"{domain}: aucun SPF"] if depth else []}
    lookups, ipv4, provider, duplicates, errors = 0, [], [], [], []
    if len(recs) > 1:
        errors.append(f"{domain}: {len(recs)} enregistrements SPF (permerror)")
    for term in _spf_terms(recs[0]):
        low = term.lstrip("+-~?").lower()
        if low.startswith("ip4:"):
            ipv4.append(low[4:])
        if _costs_lookup(term):
            lookups += 1
            target = None
            if low.startswith("include:"):
                target = low[8:]
            elif low.startswith("redirect="):
                target = low[9:]
            if target and target in path + (domain,):
                errors.append(f"boucle d'include : {' → '.join(path + (domain, target))}")
                continue
            if target and target in seen:
                duplicates.append(target)   # la requête est due, le contenu déjà lu
                continue
            if target:
                sub = await _spf_walk(target, seen, depth + 1, path + (domain,))
                lookups += sub["lookups"]
                # un `redirect=` remplace l'enregistrement : ses IP restent celles du domaine
                if low.startswith("redirect="):
                    ipv4 += sub["ipv4"]
                else:
                    provider += sub["ipv4"]
                provider += sub["provider_ipv4"]
                duplicates += sub["duplicates"]
                errors += sub["errors"]
    return {"lookups": lookups, "ipv4": ipv4, "provider_ipv4": provider,
            "duplicates": duplicates, "errors": errors}


def _tags(record: Optional[str]) -> dict:
    out = {}
    for part in (record or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


async def _email_security(d: str, dkim_selector: Optional[str] = None) -> dict:
    """op="email_security" — posture SPF/DMARC/DKIM/MTA-STS/TLS-RPT/BIMI + MX."""
    root_txt, dmarc_txt, mta, tlsrpt, bimi, mx = await asyncio.gather(
        _doh(d, "TXT"), _doh(f"_dmarc.{d}", "TXT"), _doh(f"_mta-sts.{d}", "TXT"),
        _doh(f"_smtp._tls.{d}", "TXT"), _doh(f"default._bimi.{d}", "TXT"), _doh(d, "MX"),
        return_exceptions=True,
    )
    def ok(x): return x if isinstance(x, list) else []
    spf_records = [t for t in ok(root_txt) if t.lower().startswith("v=spf1")]
    spf = spf_records[0] if spf_records else None
    spf_all = None
    if spf:
        for term in _spf_terms(spf):
            if term.lower().lstrip("+-~?") == "all":
                spf_all = term if term[0] in "+-~?" else "+" + term
    walk = (await _spf_walk(d, set()) if spf
            else {"lookups": 0, "ipv4": [], "provider_ipv4": [], "duplicates": [], "errors": []})
    dmarc = next((t for t in ok(dmarc_txt) if t.lower().startswith("v=dmarc1")), None)
    dt = _tags(dmarc)
    dmarc_policy = (dt.get("p", "").lower() or None) if dmarc else None
    # DKIM : on ne peut pas énumérer les sélecteurs, on teste les courants
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
        "note": ("DKIM testé sur sélecteurs courants seulement (énumération impossible) — "
                 "passer `dkim_selector` pour tester celui du domaine"),
    }


async def _subdomains(d: str, limit: int) -> dict:
    """op="subdomains" — noms connus lus dans les logs Certificate Transparency."""
    rows = None
    last_err = "inconnu"
    async with httpx.AsyncClient(timeout=40, headers={"user-agent": _ua()}) as c:
        for attempt in range(3):  # crt.sh renvoie souvent des 5xx transitoires
            try:
                r = await c.get("https://crt.sh/", params={"q": f"%.{d}", "output": "json"})
                if r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                rows = r.json()
                break
            # noqa: SILENT — retry borné ; le dernier échec est rendu par l'appelant
            except Exception as e:
                last_err = type(e).__name__
                await asyncio.sleep(1.5 * (attempt + 1))
    if rows is None:
        return {"domain": d, "error": f"crt.sh indisponible ({last_err})", "subdomains": []}
    names: set[str] = set()
    for row in rows:
        for n in (row.get("name_value") or "").splitlines():
            n = n.strip().lower().lstrip("*.")
            if n.endswith(d) and n != d:
                names.add(n)
    ordered = sorted(names)
    return {"domain": d, "count": len(ordered), "subdomains": ordered[:limit]}


async def _tls(host: str, port: int) -> dict:
    """op="tls" — certificat présenté par l'hôte (émetteur, validité, SANs, protocole)."""
    def probe() -> dict:
        ctx = ssl.create_default_context()
        try:
            with socket.create_connection((host, port), timeout=15) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as ss:
                    cert = ss.getpeercert() or {}
                    version, cipher = ss.version(), ss.cipher()
            validated, err = True, None
        except ssl.SSLCertVerificationError as e:
            # Outil d'INSPECTION : on reconnecte sans vérification UNIQUEMENT
            # pour lire le protocole/cipher d'un hôte au certif invalide
            # (expiré/auto-signé/mismatch) — diagnostic, aucune donnée échangée,
            # `validated=False` est remonté tel quel. Pas un canal de confiance.
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
    # noqa: SILENT — l'échec est rendu dans le résultat (error), pas avalé
    except Exception as e:
        return {"host": host, "port": port, "error": f"{type(e).__name__}: {e}"}


async def _headers(d: str) -> dict:
    """op="headers" — en-têtes de sécurité HTTP + empreinte serveur (un seul GET)."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"user-agent": _ua()}) as c:
            r = await c.get(f"https://{d}")
    # noqa: SILENT — l'échec est rendu dans le résultat (error), pas avalé
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


# --- délivrabilité ------------------------------------------------------------

# Listes noires interrogeables GRATUITEMENT via un résolveur public (vérifié sur leur
# entrée de test officielle, `127.0.0.2` / domaine de test, en DoH Cloudflare ET
# Google). `listed` = les codes qui valent inscription ; tout autre code 127.x est
# rendu `unexpected_answer`, jamais interprété.
_IP_LISTS = (
    {"zone": "bl.spamcop.net", "name": "SpamCop", "listed": ("127.0.0.2",),
     "info": "https://www.spamcop.net/bl.shtml"},
    {"zone": "b.barracudacentral.org", "name": "Barracuda", "listed": ("127.0.0.2",),
     "info": "https://www.barracudacentral.org/lookups"},
    {"zone": "psbl.surriel.com", "name": "PSBL", "listed": ("127.0.0.2",),
     "info": "https://psbl.org/"},
    {"zone": "bl.mailspike.net", "name": "Mailspike", "listed": ("127.0.0.2",),
     "info": "https://mailspike.org/"},
    {"zone": "dnsbl-1.uceprotect.net", "name": "UCEPROTECT L1", "listed": ("127.0.0.2",),
     "info": "https://www.uceprotect.net/"},
    {"zone": "bl.nordspam.com", "name": "NordSpam", "listed": ("127.0.0.2",),
     "info": "https://www.nordspam.com/"},
    {"zone": "all.s5h.net", "name": "s5h", "listed": ("127.0.0.2",),
     "info": "https://www.usenix.org.uk/content/rbl.html"},
)
_DOMAIN_LISTS = (
    {"zone": "dbl.nordspam.com", "name": "NordSpam DBL", "listed": ("127.0.0.2",),
     "info": "https://www.nordspam.com/"},
)
# Listes majeures qui REFUSENT les résolveurs publics (et dont l'usage commercial est
# payant) : nommées pour que l'absence de verdict soit visible, pas interrogées.
_NOT_CHECKED = (
    {"name": "Spamhaus (ZEN / DBL)", "reason": "refuse les résolveurs publics ; accès "
     "commercial par clé (Data Query Service) non configuré"},
    {"name": "SURBL", "reason": "refuse les résolveurs publics ; flux commercial requis"},
    {"name": "URIBL", "reason": "refuse les résolveurs publics ; flux commercial requis"},
)
_MAX_IPS = 10


async def _dnsbl(query: str, lst: dict) -> dict:
    """Une interrogation DNSBL : `listed` / `clean` / `error` — jamais `clean` sur
    autre chose qu'un NXDOMAIN franc."""
    row = {"list": lst["name"], "zone": lst["zone"]}
    try:
        status, answers = await _doh_raw(f"{query}.{lst['zone']}", "A")
    # noqa: SILENT — l'échec est rendu dans le résultat (status=error), pas avalé
    except Exception as e:
        return {**row, "status": "error", "detail": type(e).__name__}
    if status == 3:
        return {**row, "status": "clean"}
    if status != 0:
        return {**row, "status": "error", "detail": f"DNS status {status}"}
    if not answers:
        return {**row, "status": "clean"}
    if any(a in lst["listed"] for a in answers):
        return {**row, "status": "listed", "codes": answers, "delist_info": lst["info"]}
    return {**row, "status": "error", "detail": f"unexpected_answer {answers}"}


async def _ptr(ip: str) -> dict:
    """Reverse DNS d'une IPv4 + confirmation directe (FCrDNS) : un expéditeur sans
    PTR cohérent est pénalisé par la plupart des filtres."""
    rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    try:
        names = [n.rstrip(".") for n in await _doh(rev, "PTR")]
        fwd = await _doh(names[0], "A") if names else []
    # noqa: SILENT — l'échec est rendu dans le résultat (error), pas avalé
    except Exception as e:
        return {"ptr": None, "fcrdns": False, "error": type(e).__name__}
    return {"ptr": names[0] if names else None, "fcrdns": bool(names) and ip in fwd}


async def _blocklist(d: Optional[str], ip: Optional[str]) -> dict:
    """op="blocklist" — le domaine sur les listes de domaines, et les IPv4 d'envoi
    (`ip` donnée, sinon les `ip4:` littérales du SPF) sur les listes d'IP."""
    ips: list[tuple[str, str]] = []
    notes, ranges = [], []
    if ip:
        ips.append((ip, "argument"))
    elif d:
        walk = await _spf_walk(d, set())
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
            notes.append(f"{len(ranges)} plage(s) d'IP du SPF non vérifiée(s) — seules les "
                         f"IP seules le sont : {', '.join(ranges[:5])}"
                         + (" …" if len(ranges) > 5 else ""))
        if walk["provider_ipv4"]:
            notes.append(f"{len(walk['provider_ipv4'])} IP/plage(s) des fournisseurs inclus "
                         "dans le SPF non vérifiées : elles appartiennent à leur "
                         "infrastructure, pas au domaine")
        if not ips:
            notes.append("aucune IP d'envoi propre trouvée dans le SPF : l'envoi passe "
                         "probablement par une infrastructure partagée (Google, Microsoft, "
                         "outil d'emailing) dont les IP ne disent rien du domaine — "
                         "passer `ip` pour vérifier une IP dédiée")
    seen, uniq = set(), []
    for addr, src in ips:
        if addr not in seen:
            seen.add(addr)
            uniq.append((addr, src))
    if len(uniq) > _MAX_IPS:
        notes.append(f"{len(uniq)} IP trouvées, seules les {_MAX_IPS} premières sont vérifiées")
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
    """Note sur 100 et recommandations classées (high > medium > low)."""
    recs: list[dict] = []
    score = 100

    def add(priority: str, cost: int, issue: str, fix: str) -> None:
        nonlocal score
        score -= cost
        recs.append({"priority": priority, "issue": issue, "fix": fix})

    if not sec.get("mx"):
        add("medium", 5, "No MX record", "Publish MX records so replies and bounces can be received.")
    if not sec.get("spf"):
        add("high", 25, "No SPF record", "Publish a TXT `v=spf1 … ~all` listing every service that sends for this domain.")
    else:
        if sec.get("spf_record_count", 1) > 1:
            add("high", 20, "Several SPF records (permerror)", "Merge them into a single `v=spf1` record.")
        if sec.get("spf_lookup_limit_exceeded"):
            add("high", 15, f"SPF needs {sec.get('spf_dns_lookups')} DNS lookups (max 10)",
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
    if not sec.get("dmarc"):
        add("high", 20, "No DMARC record",
            "Publish `_dmarc` TXT `v=DMARC1; p=none; rua=mailto:…`, then move to quarantine. "
            "Gmail and Yahoo require DMARC from bulk senders.")
    else:
        if policy == "none":
            add("medium", 8, "DMARC policy is `p=none` (monitoring only)",
                "Once reports are clean, move to `p=quarantine` then `p=reject`.")
        if not sec.get("dmarc_rua"):
            add("low", 3, "DMARC has no `rua` report address", "Add `rua=mailto:…` to receive aggregate reports.")
        if (sec.get("dmarc_pct") or 100) < 100:
            add("low", 2, f"DMARC applies to {sec.get('dmarc_pct')}% of mail only", "Raise `pct` to 100.")
    if not sec.get("dkim_selectors_found"):
        add("medium", 10, "No DKIM key found on common selectors",
            "Enable DKIM signing at the sending provider; pass `dkim_selector` to check a custom selector.")
    if not sec.get("mta_sts"):
        add("low", 0, "No MTA-STS", "Optional: publish MTA-STS to enforce TLS on inbound mail.")
    if not sec.get("tls_rpt"):
        add("low", 0, "No TLS-RPT", "Optional: publish `_smtp._tls` TXT to receive TLS failure reports.")
    if not sec.get("bimi"):
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
    """op="deliverability" — bilan complet : authentification + listes noires,
    noté sur 100, avec recommandations classées."""
    sec, bl = await asyncio.gather(_email_security(d, dkim_selector), _blocklist(d, ip))
    score, recs = _recommend(sec, bl)
    grade = "good" if score >= 85 else "fair" if score >= 65 else "poor"
    # La couverture voyage AVEC la note : un « good » sur une seule liste interrogée
    # ne doit pas se lire comme un domaine vérifié partout.
    coverage = {
        "blocklists_checked": bl["checked"],
        "blocklist_errors": bl["errors"],
        "own_ips_checked": len(bl["ips"]),
        "not_checked": [x["name"] for x in bl["not_checked"]],
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


def register(mcp: FastMCP) -> None:
    # `op` est SANS défaut : aucune facette n'est « la » lecture naturelle d'un
    # domaine, et un défaut ferait répondre autre chose que ce qui est demandé.
    @mcp.tool()
    async def oto_domain_check(
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
          « do our emails land in spam? ». Pass `ip` for a dedicated sending IP and
          `dkim_selector` if the domain signs with a custom selector.
        - **"blocklist"** : is the domain or a sending IPv4 on a spam blocklist (DNSBL)?
          Domain lists + IP lists (SpamCop, Barracuda, PSBL, Mailspike, UCEPROTECT,
          NordSpam, s5h), with reverse DNS of each IP. IPs come from `ip`, else from the
          domain's SPF `ip4:` entries. Each list answers `listed` / `clean` / `error` —
          an `error` is NOT a clean result. ⚠️ Spamhaus, SURBL and URIBL refuse public
          resolvers: they are returned under `not_checked`, never as clean. A domain
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
                raise _bad(f"ip invalide : {ip!r} (IPv4 attendue)")
            if parsed.version != 4:
                raise _bad("seules les IPv4 sont vérifiables sur les listes noires DNS")
            addr = str(parsed)
        if not d and not (op == "blocklist" and addr):
            return {"error": "domaine invalide"}
        if op == "whois":
            return await _whois(d)
        if op == "dns":
            return await _dns(d)
        if op == "email_security":
            return await _email_security(d, dkim_selector)
        if op == "subdomains":
            return await _subdomains(d, limit)
        if op == "tls":
            return await _tls(d, port)
        if op == "headers":
            return await _headers(d)
        if op == "blocklist":
            return await _blocklist(d or None, addr)
        if op == "deliverability":
            return await _deliverability(d, addr, dkim_selector)
        raise _bad(_OPS_ERR)  # défense en profondeur : _OPS ne peut pas dériver du dispatch
