## usage — check a domain

**passive** read of a domain (nothing intrusive, public sources) — open data, no key.
a single tool, `infosec_domain(op=…, domain=…)` — the op picks the read:
- `deliverability` — email-deliverability report for YOUR OWN domain: score /100, ranked recommendations
  (spf/dmarc/dkim + blocklists). this is the entry point for "are our emails landing in spam?"
- `blocklist` — the domain and its sending ips (`ip`, else the spf's `ip4:` entries) on the free blocklists
  whose terms allow automated commercial use (spamcop, psbl, nordspam, s5h);
  spamhaus, surbl, uribl, barracuda, mailspike and uceprotect → returned as `not_checked`, never "clean"
- `email_security` — spf (lookup count vs the limit of 10), full dmarc, dkim (+ `dkim_selector`),
  mta-sts, tls-rpt, bimi, mx
- `whois` / `dns` — rdap registration and dns records (with mail/saas stack hints)
- `subdomains` — known subdomains via certificate transparency logs (crt.sh)
- `tls` / `headers` — tls certificate and http security headers

⚠️ what could not be read (dns error, 25 s deadline) is named in `coverage` and is not scored:
an error on `_dmarc` is not "no dmarc". the spf walk stops beyond 10 lookups (rfc 7208).

⚠️ a domain that sends via google, microsoft or an emailing tool has no ip of its own: the ip
lists say nothing about it, it is the domain lists that count (the response says so in `notes`).

## note — what this helps qualify, beyond security

the connector's name says "security", but the most common use here is
**commercial**: recognizing a target's tooling to qualify it before
approaching it. this is what the "mail/saas stack hints" line covers without saying so.

- the `dns` records (`MX`, `TXT`) name the mail provider and
  often the saas plugged into it — knowing that a prospect is with a given host, crm
  or signature tool tells you what your offer will have to coexist with, or what it would
  replace;
- `email_security` (spf/dmarc/dkim) reads as an **it-maturity signal**: a
  strict and complete posture doesn't describe the same organization as a domain with no
  dmarc;
- `subdomains` reveals products, environments and side brands that no
  home page shows.

⚠️ everything is **passive** and comes from public sources: no solicitation of the
target, nothing intrusive. this is what makes commercial use acceptable — we read what
the domain publishes about itself.
