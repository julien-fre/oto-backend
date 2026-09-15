# Qualify inbound Salesforce leads with official company data

**When to use it**: for new inbound leads, to resolve each one to its real legal entity and score it against your own thresholds before a rep spends ten minutes doing it by hand.

```
              Natural language input in Claude
              "Qualify today's new inbound leads against official company data."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull new leads                             │   salesforce_query
│  Since the last run, reading your own           │
│  schema so custom fields come along.            │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Resolve the legal entity                   ║   fr_search
║  Match on activity code, department and         ║
║  headcount — never name alone.                  ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ unresolved    no confident match — never a guess
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Pull the evidence                          │   fr_bilan
│  Identity, directors, filed accounts —          │   fr_events
│  plus any recent legal event.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Score against your own rules               │
│  Your team's existing thresholds — size,        │
│  revenue, sector, solvency signals.             │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Write it back                              │   salesforce_record
│  Upsert on SIREN, and leave the score's         │
│  reasoning as a note — not a bare number.       │
└─────────────────────────────────────────────────┘
```

## 1. Pull new leads
- <tool:salesforce_query> — leads created since the last run. Read your own lead schema first so custom fields come along in the same pass, rather than a second query per lead. Batch for the whole window, not one query per lead.

## 2. Resolve the legal entity
- <tool:fr_search> — match company name and location to a SIREN using a search that filters on activity code, department and headcount alongside the name, never name alone. Two companies sharing a trading name is common enough that a name-only match produces wrong matches often enough to matter, especially when one candidate is dormant. If nothing matches confidently, leave the lead unresolved — a wrong SIREN is worse than no SIREN.

## 3. Pull the evidence
- <tool:fr_bilan> — identity, current directors, and the latest filed accounts (headcount, revenue) for each resolved SIREN.
- <tool:fr_events> — a separate lookup, any legal event published since the company was last checked: ownership change, insolvency, officer change. Batch each by SIREN list where the tool supports it.

## 4. Score against your own rules
Apply your team's existing qualification thresholds — company size, revenue, sector, solvency signals from the legal-events check. Keep the score's inputs, not just the number, so you can explain it in the next step.

## 5. Write it back
- <tool:salesforce_record> — upsert the enriched fields onto the lead, keyed on SIREN, so a re-run updates the existing fields rather than creating a duplicate. Leave the score's reasoning as a note — a rep who can see *why* a lead scored 82 trusts the score, a bare number doesn't earn that. Only write fields you've actually resolved this run; never blank an existing field because this pass didn't find a value.

## Output
Report: how many leads processed, how many resolved to a SIREN, how many scored above your qualification threshold, and any lead that failed to resolve and why.