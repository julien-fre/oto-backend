# Flag legal changes at companies in your CRM

**When to use it**: on a recurring schedule, to catch the accounts already in your CRM that changed — new ownership, a name change, an insolvency filing — before a rep finds out the hard way.

```
              Scheduled routine, on a recurring cadence
              "Check every CRM account for legal events since the last run."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull the accounts you already hold         │   salesforce_query
│  Every account carrying a SIREN, in             │
│  batches — never one record at a time.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Check what's been published                │   fr_events
│  Legal announcements per SIREN since the        │
│  last run, or the last 90 days on a first one.  │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Keep only what changes a decision          ║
║  Ownership, name, address, insolvency,          ║
║  officer changes — drop routine filings.        ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ nothing qualifying    no event worth a flag
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write it back                              │   salesforce_record
│  Update the account's flag, and leave a         │
│  note naming the event and its date.            │
└─────────────────────────────────────────────────┘
```

## 1. Pull the accounts you already hold
- <tool:salesforce_query> — every Account carrying a SIREN (or your own French-company identifier field), in batches. If your CRM tracks a last-checked date, pull only what's due; otherwise pull the full set and let the registry's own since-date filter keep the volume down.

## 2. Check what's been published
- <tool:fr_events> — legal announcements (BODACC) published since the last run, or the last 90 days on a first run. Batch by SIREN list where the tool supports it; fall back to one call per company only if it doesn't.

## 3. Keep only what changes a decision
Filter to event types that actually change how a rep would run the account: ownership change, name change, address change, insolvency/collective proceedings, officer changes. Drop routine filings (annual accounts, minor administrative notices) — publishing everything trains the team to ignore the flag. If an account has more than one qualifying event, keep all of them; don't collapse to "most recent only."

## 4. Write it back
- <tool:salesforce_record> — update the account's status/flag field and leave a note naming the event type and its publication date, not just "something changed." Only touch accounts that actually had a qualifying event this run. If this runs on a schedule, record the run's cutoff date somewhere so the next run knows where to pick up.

## Output
Report: how many accounts checked, how many had a qualifying event (broken down by event type), and any SIREN that failed to resolve.