# Publish a Monday leadership report in Notion

**When to use it**: the weekly pre-read is a document somebody writes on Sunday night or not at all. Both halves already exist, the numbers in the sheet the team maintains and the customer feedback in last week's written-up notes, and deciding what is worth mentioning is mostly the same comparison against a trend every week. This reads both, keeps only the numbers that broke their trend, joins them to recurring feedback themes with counts, and posts one short brief to the channel before the meeting. A brief is not a dashboard: its job is to say what changed, so most of the numbers never appear in it.

```
              Scheduled routine, weekly before the leadership meeting
              "Put together this week's Monday brief and post it before the meeting."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  0 · Find last week's brief                     │   slack_read_history
│  Its header line marks the week it covered and  │
│  where this week's feedback window starts.      │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ already posted    this week's header is there
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Gather this week's agreed numbers          │   sheets_spreadsheet
│  The sheet the team maintains, read as the      │   raw values, labels formatted
│  source for the last complete week.             │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ sheet not filled  the week's figures are all empty
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Keep metrics that broke their trend        ║   against the trailing weeks
║  Out of their trailing range, or moving one     ║
║  way three weeks running; the rest is steady.   ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Extract feedback themes with counts        │   notion_query_database
│  Notes written since the last brief, counted    │   notion_get_blocks
│  in distinct customers rather than mentions.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Write a one-screen brief                   │
│  What moved, what is steady, themes, what did   │
│  not happen, and no recommendations.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Post it before the meeting                 │   slack_post_message
│  In the channel as a message, never as a file,  │
│  and once: a Slack post cannot be edited.       │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and nothing is posted to the channel
```

Every call in this process is a read except the one Slack post. It never edits the sheet, never writes to the documentation tool, and never recommends anything: the meeting is where decisions get made.

## 0. Find last week's brief
- <tool:slack_read_history> on **[your leadership channel]**, `oldest` = the timestamp of **[eight days ago]**, `limit=100`. Every brief opens with the same fixed first line, `*Monday brief · week of <Monday's date>*`, and that line is the key the run recognizes its own posts by.
- **This week's header is already there**: stop and post nothing. A re-run, a retried schedule or a person who ran it by hand earlier must never produce a second brief; a Slack message can be deleted but not edited, so a duplicate has to be cleaned up by hand.
- **Last week's header is there**: its timestamp is the start of this week's feedback window in step 3, so nothing written between two briefs is missed or read twice. With no previous brief in the window, start the feedback window **[seven days]** back and say so in the report.
- History returns top-level messages only. A brief long enough to have been split into threaded parts shows only its first part here, which still carries the header.

## 1. Gather this week's agreed numbers
- <tool:sheets_spreadsheet> `op="metadata"` on **[your weekly metrics spreadsheet]** first, to find the tab by its title, with its row and column counts. Never assume the first tab: a tab inserted in front of it silently moves every read.
- <tool:sheets_spreadsheet> `op="read"` on that tab with `formatted=false` for the figures: a formatted cell comes back as display text ("12.4%", "1,204"), and a thousands separator or a decimal comma parsed as a number gives a different number. Read the week-label row or column a second time with `formatted=true`, because raw dates come back as serial numbers.
- **Read the sheet as the source.** Whatever definition the team agreed on (which accounts count, which trials are excluded, when a churn is recognized) is in that sheet. Recomputing a metric from the underlying systems produces a defensible number that disagrees with the one everyone else looks at, and a brief that has to be reconciled against the dashboard before it can be discussed is not opened twice.
- **The week covered is the last complete week**: the latest week whose period ended before today. A column for the week in progress, partly filled, is not this week's figures. State the week's start and end dates at the top of the brief.
- **A blank cell is missing, never zero**, and a cell holding a formula error is missing too. Missing figures are listed by name in the brief and the report, never filled in from another system.
- **Every figure for the week is empty**: stop, post nothing, and report that the sheet had not been filled. A brief built on last week's numbers with this week's date on it is worse than no brief.

## 2. Keep metrics that broke their trend
Compare each metric with its own recent history, never with the previous week alone. Weekly business data moves several percent either way for no reason, and a brief that calls out every swing teaches its readers that none of them matter.
- **The trailing window** is the **[four to eight]** complete weeks before the week covered. A metric with fewer than **[four]** earlier values is too new to judge: it goes on the steady line marked as such, with no trend claim.
- **A metric earns a line in the brief** when one of two rules trips:
  - **Out of range**: this week's value is above the highest or below the lowest value of the trailing window.
  - **A drift**: it has moved in the same direction week on week for **[three]** weeks or more, this week included. A flat week breaks the streak. A slow drift is the thing most worth catching, and the one a week-on-week view never shows.
- For each metric that trips, keep the figure, its change against the trailing average (not against last week), and which rule tripped it. State the direction, not a verdict: whether a rise is good depends on the metric, and saying so is the meeting's job.
- **Everything else is one line**: the names of the steady metrics, with no figures.

## 3. Extract feedback themes with counts
- <tool:notion_query_database> on **[your customer feedback database]** with `filter_obj={"timestamp": "created_time", "created_time": {"on_or_after": "<window start>"}}`. Filter on when a note was **created**, not last edited: an old research page someone tidied this week is not this week's feedback. `page_size` tops out at 100 and the call takes no cursor, so a result of exactly 100 rows means the window was cut: split it into two date ranges and query each.
- <tool:notion_search> with `sort="last_edited_time"` and `filter_type="page"` for **[research summaries or call write-ups kept outside that database]**, keeping only the pages whose creation time falls inside the window.
- <tool:notion_get_blocks> with `recursive=true` on each page kept. The substance of a feedback note usually sits in nested bullets and toggles, which a non-recursive read returns as empty parents.
- **Themes, not quotes.** Group what came up repeatedly into short themes and **count distinct customers, not mentions**: the same customer raising the same thing in three notes counts once. One customer saying something is an anecdote; the same thing from several is the reason to put it in front of the leadership team. A theme raised by a single customer stays out of the brief and is counted in the report.
- **Join the two halves.** Where a theme touches the same product area or customer segment as a metric that tripped in step 2, say so in one sentence, as a pairing to discuss rather than a cause. That sentence is the most useful one in the brief, and nobody writes it by hand because the numbers and the notes live in different tools.

## 4. Write a one-screen brief
In this order, under the fixed header line:
- **What moved**: each metric from step 2, with its figure, its change against the trailing average and the rule it tripped.
- **Steady**: one line, names only.
- **Customer feedback**: each theme with its count of distinct customers, and its pairing with a metric where there is one.
- **Did not happen**: figures the sheet has a row for but no value this week, and any recurring write-up that did not appear in the window.

Hold it to what fits on a phone screen, **[about fifteen lines]**. It competes with a Monday morning, and length is the reason briefs stop being read. No recommendations and no adjectives that grade a number ("worrying", "strong"): a brief that arrives with conclusions attached shapes the discussion before anyone has looked at the figures.

## 5. Post it before the meeting
- <tool:slack_post_message> to **[your leadership channel]**, with the brief as the message text: not a file, not a link to a document. Anything that needs a click to open is opened by about half the people who need it.
- Read the response. `ts` alone means one message went out. `split_into` means the text passed Slack's length limit and went out as a first message with the rest threaded under it: nothing is lost, so **do not post it again**, but report it, because the brief broke the one-screen rule in step 4.
- If the call errors, read the channel history once more before retrying: a post that timed out on the way back may still have landed.

## Output
The week covered, with its dates; the metrics that broke their trend and the rule each tripped; the steady metrics; the themes extracted with their counts, including the single-customer themes left out; the pairings made; every figure the sheet was missing; the feedback window used and whether it came from last week's brief or the default; and the link to the posted brief, or the reason nothing was posted.