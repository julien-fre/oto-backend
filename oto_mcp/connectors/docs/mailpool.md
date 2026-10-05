## prerequisite — mailpool api key

a Mailpool workspace member creates a key in Mailpool → Settings → **API Keys**, then pastes it into oto. The key is shown once.
- byo-only: no shared oto key — these are your own sending domains and mailboxes
- the "test connection" button reads the subscription slots: no domain or mailbox data is touched

## usage — is my sending setup healthy, and fix it

- "audit my domains" → `mailpool_domains(op="audit")`: SPF, DKIM, DMARC, MX and tracking domain per domain, worst first
- "show the DNS of this domain" → `mailpool_domains(op="list")` to find its id, then `mailpool_domains(op="dns", domain_id=…)`
- "switch SPF to -all and send DMARC reports to dmarc@…" → `mailpool_domain_fix(domain_id=…, spf_hard_fail=True, dmarc_report_email="dmarc@…")` returns the diff; re-call with `dry_run=False` to apply
- "add the lemlist tracking domain" → `mailpool_domain_fix(domain_id=…, tracking_host="track", tracking_target="custom.lemlist.com", dry_run=False)`, then set `track.<domain>` as custom tracking domain in lemlist; if lemlist then asks for a verification TXT, add it with `set_records=[{"type": "TXT", "key": null, "value": "…"}]`
- "are my inboxes landing well?" → `mailpool_spam_checks(op="run", mailbox_id=…)`: score and per-check status in a few seconds, free
- "which mailboxes do we have?" → `mailpool_mailboxes(op="list")`, optionally `domain_id=…`
- "is this domain name free?" → `mailpool_domains(op="availability", domain="…")` or `op="suggestions"`
- "do we have room for more inboxes?" → `mailpool_account(op="slots")`

## note — what is misleading

- ⚠️ **a DNS write replaces the whole record set** in Mailpool: `mailpool_domain_fix` always sends the full set, reads it back and restores the previous one if anything went missing. Never hand-build a partial list
- **DMARC is published from Mailpool's DMARC setting**: changing the report address also writes it as `ruf` (forensic reports) — Mailpool does not allow removing it
- **the audit reads Mailpool's stored configuration**, not public DNS: right after a fix, public DNS follows within ~5 minutes
- **DMARC reports sent to another domain** (e.g. your main domain) are only delivered if that domain publishes `<sending-domain>._report._dmarc.<main-domain>` (or a `*._report._dmarc` wildcard) as TXT `v=DMARC1` — a change in the main domain's DNS, outside Mailpool. The audit flags it
- a **tracking CNAME alone does nothing**: the sending tool must also be told to use it
- `-all` only once **every** service sending as the domain is in SPF (Google Workspace, Microsoft 365 or Mailpool's relay — a sending tool connected through them needs nothing more)
- some lookups are **enabled per Mailpool workspace** (Google Workspace / Microsoft 365 checks): refused otherwise
- a spam check is refused (404) for some mailbox types
- the `dmarc_policy` and new `redirect_url` changes of `mailpool_domain_fix` are **not live-verified yet**: run `op="audit"` after applying them
- lemwarm or another warmup paused on a **Microsoft 365** domain because "external forwarding is not allowed" is a tenant setting (Microsoft Defender → anti-spam outbound policy → automatic forwarding), not DNS

## note — credentials and scope

- **mailbox credentials are never returned** (mailbox, IMAP/SMTP, admin passwords): they stay in the Mailpool dashboard. Handing inboxes to a sending tool is done from Mailpool's own export
- **nothing billed is served**: registering, transferring or renewing a domain, creating a mailbox, buying slots, enrolling in warmup and inbox-placement tests are done in Mailpool
