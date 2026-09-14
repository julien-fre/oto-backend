# Turn Slack community members into an outreach list

**When to use it**: your list comes from membership in a community for an adjacent tool (a Slack group, a user community, a marketplace). Membership itself proves the person already buys this category — that's the qualification, not firmographics.

```
              Natural language input in Claude
              "Build a campaign from this Slack community's roster."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Extract the roster                         │
│  A member-only workspace has no API —           │
│  a real scroll repaints it, a jump does not.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Mine the intro channel first               │   slack_read_history
│  Where members state their own name and         │
│  company, then work the wider roster.           │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Resolve identity                           │   serper_search
│  Search each name for a corroborating           │
│  profile — prefer no match to a weak one.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Enrich, reject mismatches                  │   fullenrich_enrich_linkedin
│  Reject any email whose employer doesn't        │
│  match what the person said about themselves.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Segment and write                          │
│  Group by role, since founders, operators       │
│  and agencies don't share a problem.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Load the campaign                          │
│  Dedupe against what's already sending,         │
│  then stop — a person reviews and launches.     │
└─────────────────────────────────────────────────┘
```

## Rule zero: membership is the qualification
Don't filter by your own read of fit — a solo founder, an agency owner and an investor are all in scope; they get different copy, not a smaller list. The only two legitimate gates: can this person be identified and reached, and is there a conflict (competitor, existing customer, already in a live campaign).

## 1. Extract the roster, and expect it to disappoint
Member lists in chat tools are usually virtualized — only visible rows exist in the DOM, the rest render as you scroll. A script that sets scroll position programmatically often scrolls a list that never redraws, and silently re-reads the same rows forever, while clicks and screenshots keep working — which makes the failure easy to miss. A real scroll event (wheel, not a position jump) is what forces a repaint. This is the workaround for a workspace you can only reach as a plain member — see step 2 for the case where you have real API access instead.

## 2. Mine the self-introduction channel first
Most communities have an intro channel where members state their own name, company and role in prose — that's both the resolution key and the personalization source. If you already have Slack API access to this workspace (a bot or user token installed as a member, not just your own login), pull the channel's history directly with <tool:slack_read_history> instead of scrolling it in the browser — it's faster and it doesn't fight virtualization. Otherwise, work it fully in the browser before the wider roster: a bare handle is nearly unfindable, but "hi, I'm X from Y, I do Z" resolves in seconds. Then work the rest of the roster as a second, higher-volume pass with name-only search.

## 3. Resolve identity — reward corroboration, not confirmation
- <tool:serper_search> each name (plus company, when known). Accept a match only when something actually corroborates it — matching company, role, context. **Prefer no match to a weak one.**

## 4. Enrich, and reject anything that doesn't corroborate
- <tool:fullenrich_enrich_linkedin> — enrichment tools return a person's **current** employer, which isn't always what they introduced themselves as (a side project, a new venture). Reject any email whose domain doesn't match what the person actually said about themselves.

## 5. Segment, then write to each group's real problem
Founders, in-house operators, agencies/consultants and investors don't share a problem — a single angle won't land for all of them. Agencies in particular are often a partnership opportunity, not a direct buyer. Write from the person's own words wherever you have them, and check each opener with the stranger test: swap in a different person from the same segment — if the line still reads true, it isn't personalization yet.

**One hard rule on the copy**: never reference the community the list came from, and never assert what tool the person uses based on their membership in it. A fact about your own product is fine; an inference about their behavior, drawn only from where you found them, reads as surveillance the moment it's noticed.

## 6. Load the campaign, ready for a person to launch
Check what's already sending first — skip anyone already enrolled elsewhere targeting a similar audience. Load each segment into its own track with its own copy, spot-check a few for the personalization landing correctly, then stop: stage everything for review and never call a send or launch action yourself.