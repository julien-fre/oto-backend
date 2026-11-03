---
title: "Spending fewer credits: find where it goes, then cut it"
description: read before answering anything about cost, credits, a bill that is too high, or a runway that is too short — measure first, then the levers in the order they pay off (provider keys, cheaper providers and models, agent behaviour), each applied one at a time with an approval
---

# Spending fewer credits

Read this **before** answering "why is it so expensive?", "how do I make my credits last?" or
"can this agent cost less?". The method is the same every time: **measure, pick the few things
that matter, change one, measure again.** A guess about where the money goes is usually wrong,
and a change nobody re-measures is a rumour.

## 1. Measure first

- `usage_report` (days 7, 30 or 90) — what was actually spent: credits by connector and by day,
  the runway at the last week's pace, and the **AI value** by agent (what the agents' runs are
  worth at API list prices; runs paid by a Claude subscription are counted as value *saved*,
  not credits spent). Every agent carries its `agent_id`.
- `credit_rates` — the published price grid and the list price of each model. Every saving you
  propose is arithmetic on these two: *runs per day × cost per run × the change*. Show the numbers.
- Without those tools (Slack, another client), ask the person to open **Settings › Usage** and read
  you the top three connectors and top three agents. Do not estimate without a figure.
- `unpriced_runs` > 0 means the totals are a floor: say so, don't round it away.

**Look at the top, not the average.** Two or three connectors and agents are almost always most of
the spend. A tip that touches the sixth agent is noise. If one agent is a third of the value,
start there.

## 2. Who pays? The cheapest credit is the one nobody bills

- A call made with the workspace's **own key** for a connector bills no credits. When one connector
  dominates (search, enrichment, scraping) and the person holds, or can open, their own account
  with that provider, adding their key (`oto_connector`) moves that spend off the credits onto
  their own invoice. Say that trade plainly — it is cheaper for the credits, not free for them.
- Agent runs on the workspace's own model key, or on a **Claude subscription** (the `sub:*`
  models), bill no credits either. A subscription uses the person's plan and its usage limits: a
  run can wait for a reset, so it suits steady background work and not something urgent at a fixed hour.
  Only the owner of the subscription can put an agent on it.
- Never advise a key the person cannot legitimately use, and never suggest sharing credentials.

## 3. A cheaper way to get the same result

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
- **Fewer, bigger calls.** Bulk tools take many items in one call; a loop of single calls bills the
  same items but costs far more model turns (see the guides `bulk-load` and `work-queue`).

## 4. The model an agent runs on

An agent's model sets the cost of **every turn**. The list prices are in `credit_rates`.

- Match the model to the job. Routing, classifying, extracting a field, filling a table: a small
  model is usually enough. Judgment calls, long writing, anything that must not be wrong: keep the
  strong one.
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

## 6. Acting on it

You can change agents, but every change goes through an approval the person answers.

1. Say what you found with its figure ("agent #84 is 37 % of the value, 338 runs, mostly empty").
2. Propose **one** change with the saving it should bring, computed from the real numbers, and the
   risk in one line.
3. Apply it with `oto_trigger` (`enabled`, `cron`, `model`, `max_steps`, `max_tokens`, `tools`),
   using the `agent_id` from `usage_report`. Never change an agent you were not asked about.
4. Tell the person when to look again, and look again with `usage_report`. Report what really
   changed, including when it did not work.

Never invent a cause the figures do not show. If the spend is simply the work the person asked for,
say so: "this is what it costs to do what you set up" is a complete answer, and adding credits
(`show_credits`) is a fair next step.
