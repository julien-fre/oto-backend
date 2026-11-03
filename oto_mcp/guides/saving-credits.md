---
title: "Spending fewer credits: find where it goes, then cut it"
description: read before answering anything about cost, credits, a bill that is too high, or a runway that is too short — measure first, run a cost analysis of the tools used, offer cheaper options among the tools Tulina offers (cheaper tool for the same job, cheaper model, leaner agent behaviour), each applied one at a time with an approval
---

# Spending fewer credits

Read this **before** answering "why is it so expensive?", "how do I make my credits last?" or
"can this agent cost less?". The method is the same every time: **measure, analyse what each tool
costs for the job it does, offer cheaper options, change one, measure again.** A guess about where
the money goes is usually wrong, and a change nobody re-measures is a rumour.

Stay inside what Tulina offers. The savings are cheaper tools, cheaper models and cheaper behaviour
among the connectors and agents the person already has: do not steer them toward their own provider
keys. (If they ask about their own key, say it bills no credits but the cost moves to their own
invoice, and leave it there.)

## 1. Measure first

- `usage_report` (days 7, 30 or 90) — what was actually spent: credits by connector and by day,
  the runway at the last week's pace, and the **AI value** by agent (what the agents' runs are
  worth at API list prices; runs paid by a Claude subscription are counted as value *saved*,
  not credits spent). Every agent carries its `agent_id`.
- `credit_rates` — the published price grid and the list price of each model. Every saving you
  propose is arithmetic on these two: *volume × cost per result × the change*. Show the numbers.
- Without those tools (Slack, another client), ask the person to open **Settings › Usage** and read
  you the top three connectors and top three agents. Do not estimate without a figure.
- `unpriced_runs` > 0 means the totals are a floor: say so, don't round it away.

**Look at the top, not the average.** Two or three connectors and agents are almost always most of
the spend. A tip that touches the sixth is noise. If one connector is a third of the credits,
start there.

## 2. Cost analysis of the tools

For each of the top connectors, in order of spend:

1. **Name the job it does** for this workspace: find companies, find people at a company, get a work
   email, get a mobile number, search the web, read a page, find job signals, enrich a profile.
   `usage_report` tells you the connector and its credits; the agents' processes (`oto_procedure`,
   `oto_trigger`) tell you what it is used for.
2. **List the other tools Tulina offers for the same job.** `credit_rates` lists the priced tools by
   group; the guide `included-tools` is organised by use case (which connector for which job, and
   whether it runs on Tulina's credits); the `waterfall_*` tools chain several providers for one
   job.
3. **Put them on one unit: credits per useful result**, not per call. Read each rate's unit
   (per item, per result found, per provider credit, per run cost) and convert. A tool that bills
   only when it finds something (misses free) is cheaper than its list rate suggests when the hit
   rate is low; a tool that bills per call is dearer. Use the person's own recent volume
   (`usage_report`) to turn the difference into credits a month.
4. **Say what you give up.** Cheaper can mean fewer fields, lower coverage or a lower hit rate:
   name it. Never present a swap as free.
5. **Check on a small sample** before the swap goes into a process that runs every day.

Do not compare on list price alone and do not guess a rate you have not read: the rates change.

## 3. Cheaper ways to get the same result

Read the rates before suggesting any of these; a rate that is already tiny is not worth the change.

- **Cheapest source first, stop at the first answer.** The `waterfall_*` tools try providers one
  after another and stop at the first hit; the order is the call's, else the card's `steps`
  setting, else the default. Put the cheap, usually-right source first.
- **Ask only for what you use.** Enrichment that bills per kind found (work email, personal email,
  mobile) costs more for each kind requested: ask for work emails only when that is all the
  process reads.
- **Free read before paid read.** A web page is tried free first; reach for a scraper actor only
  when the page resists, since actors bill the real run cost.
- **Do not pay twice for what the workspace already has.** Check the table or the CRM for the
  person or company before calling a paid tool, and dedupe a list before enriching it.
- **Use a recipe for bulk reads — the biggest saving of this section.** `oto_recipe` moves the
  results of a read-only connector tool into a table on the server: the model never reads or retypes
  the rows, so a list of 500 companies costs the provider's own price and **no model turns at all**
  (an agent doing the same row by row pays a turn for every few rows). A recipe pages through the
  results itself, maps each field to a column, skips rows already there, and stops at a hard
  `limits.max_units` cap, so the spend is bounded before it starts. `for_each` fans out over a table
  (people at each company…). Reach for one whenever a process pulls many rows from a connector into a
  table, or an agent's loop is just "call, copy, call, copy". Sample and test first (`op=sample`,
  `op=test` are one cheap page), then publish and run; a run reports counts, not rows.
  The guide `save-tokens` holds the full decision table — recipes, Jev for qualifying rows,
  `data_aggregate` for counts, bulk edits, `*_push_rows` — read it before proposing a change to any
  process that loops over rows.
- **Fewer, bigger calls.** Bulk tools take many items in one call; a loop of single calls bills the
  same items but costs far more model turns (see the guides `bulk-load` and `work-queue`).

## 4. The model an agent runs on

An agent's model sets the cost of **every turn**. The list prices are in `credit_rates`.

- Match the model to the job. Routing, classifying, extracting a field, filling a table: a small
  model is usually enough. Judgment calls, long writing, anything that must not be wrong: keep the
  strong one.
- Where the person has a Claude plan and an agent suits it (steady background work, nothing urgent
  at a fixed hour), a `sub:*` model bills no credits: offer it as one option among the others, never
  as the answer. Only the plan's owner can put an agent on it.
- Never switch blind. Change one agent, let it run a day or a week, then compare **cost per run**
  in `usage_report` (agent value ÷ runs) and read a few outputs. If quality dropped, revert.
- Do not move an agent's model silently: it is a change to what the person's team relies on. Ask.

## 5. How an agent behaves

This is where most surprises are: a cheap run, many times.

- **How often it runs.** Cost = runs × cost per run. An agent that wakes every 15 minutes and
  mostly finds nothing is the classic leak. Move it to hourly, to a webhook that fires on the
  event, or to working hours only (`cron`, `oto_trigger`). Say how many runs a day that removes.
- **Stop early.** The process should open with the cheapest possible check — "is there anything
  to do?" — and end silently when there is not. A run that reads, finds nothing and writes a recap
  still pays for every turn.
- **Cap the run.** `max_steps`, `max_tokens` and `max_run_seconds` stop a loop that went wrong from
  running for ever. Set them a little above what a good run needs.
- **Narrow the tools.** The `tools` allowlist is sent on every turn: exactly the tools the process
  uses, no more. It also keeps the agent from wandering into a paid connector.
- **Replace a copying loop with a recipe.** If the process mostly moves rows from a connector into
  a table, the agent should start the recipe and read the receipt, not carry the rows itself.
- **Smaller context.** Read the columns and rows needed, not the table; pass a reference to a table
  rather than pasting it into the prompt; keep the process short and exact.
- **Fix what fails first.** Failed and blocked runs still spend. An agent that errors on every run
  burns credits for nothing: repair or switch it off before tuning anything else.

## 6. Offering the options, and acting on them

Present the result as **two or three cheaper options, ranked by credits saved**, each with the
figure, what is given up, and the effort — for example "swap the second email source: −X credits a
month, slightly lower coverage, one setting", "move agent #N to hourly: −Y credits, results arrive
later". Then ask which to apply. You can change agents, but every change goes through an approval the
person answers.

1. Say what you found with its figure ("TheirStack is 43 % of the credits, 1,400 company lookups").
2. Propose **one** change at a time with the saving it should bring, computed from the real numbers,
   and the risk in one line.
3. Apply it with `oto_trigger` (`enabled`, `cron`, `model`, `max_steps`, `max_tokens`, `tools`),
   using the `agent_id` from `usage_report`, or edit the process. Never change an agent you were not
   asked about.
4. Tell the person when to look again, and look again with `usage_report`. Report what really
   changed, including when it did not work.

Never invent a cause the figures do not show. If the spend is simply the work the person asked for,
say so: "this is what it costs to do what you set up" is a complete answer, and adding credits
(`show_credits`) is a fair next step.

## Related guides

`save-tokens` (let the server do the bulk work), `recipes` (writing and running one), `processes`
(how a process and its agent are set up), `included-tools` (which connector for which job, and what
runs on Tulina's credits).
