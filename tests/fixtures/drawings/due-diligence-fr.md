# Run due diligence on a French company

**When to use it**: before a commitment (customer, supplier, acquisition target, partner), you want a quick, reliable read on a French company's health.

```
              Natural language input in Claude
              "Qualify this supplier before we sign."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Identity and status                        │   fr_get
│  One call: identity, directors, 7 ratios        │
│  from the latest filing, BODACC events.         │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Financial health                           │   fr_bilan
│  Full balance sheet, then 3 years of            │   fr_bilans
│  history to read the trend, not a photo.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Directors and governance                   │   fr_directors
│  Officers and their other mandates —            │
│  key-person risk, conflicts.                    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Alert signals                              │   fr_events
│  Insolvency proceedings, capital moves,         │
│  deregistration — a recent filing stops it.     │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Real size and verdict                      │   fr_siret
│  Establishments and headcount, then             │   fr_stock_etablissements
│  classify green, yellow or red.                 │
└─────────────────────────────────────────────────┘
```

## Identity & status
- `fr_get(siren)` — aggregated profile: identity, directors, 7 ratios from the latest filing, recent BODACC events. One call.

## Financial health
- `fr_bilan(siren, date_cloture)` — the full balance sheet (~13 CFO-grade ratios: margin, debt, cash, working capital).
- <tool:fr_bilans> — 3-year history to read the **trend**, not just a snapshot.

## Directors & governance
- <tool:fr_directors> — officers, other mandates (key-person risk, conflicts).

## Alert signals
- <tool:fr_events> (BODACC) — insolvency proceedings, capital moves, deregistrations. A recent insolvency filing = stop.

## Real size
- <tool:fr_siret> / <tool:fr_stock_etablissements> — establishments, headcount, headquarters.

## Verdict
- **Green**: healthy financials + positive trend + zero proceedings. **Yellow**: tight cash or an old filing. **Red**: insolvency proceedings, negative equity.
- Missing data is reported as missing — never a guessed number.