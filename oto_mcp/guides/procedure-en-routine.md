---
title: Running a procedure autonomously (Claude Code routine)
description: "Set up a Claude Code routine that runs an oto procedure with nobody in front of the screen: the prompt is a POINTER to the procedure (never a copy), the Oto connector carries the tools, and the trigger can be scheduled, API (instant) or GitHub. Covers the prompt template, passing context to a run (untrusted data, pass a reference), and the five causes of failure — secrets left on the workstation first."
---

# Running a procedure autonomously

A **Claude Code routine** is an agent hosted by Anthropic: a fixed prompt, MCP connectors, one or more triggers. Plugging the Oto connector into it and giving it a prompt that *loads a procedure* means running that procedure with no machine switched on and nobody in front of the screen.

The setup fits in one sentence: **the routine's prompt is a pointer, never a copy.** The know-how stays in the procedure — versioned, editable, shareable, read at every run. The prompt is three lines long and no longer moves.

Copying the procedure into the prompt is the most expensive mistake: two sources of truth that silently diverge, and a procedure fix that never reaches the agent.

## Setting up the routine

From Claude Code, `/schedule` creates the routine in conversation (scheduled or GitHub trigger); the API trigger token remains a web operation.

On `claude.ai/code/routines`, **New routine**:

1. **Name** — what the routine does, not the procedure it loads.
2. **Instructions** — the pointer (template below).
3. **Repository** — none, unless the procedure touches code. A routine without a repository starts on a bare context, which is exactly what we want: it only knows the procedure and the tools.
4. **Connectors** — **keep only those the procedure needs.** All the account's connectors are added by default, and during a run the agent can call any of their tools, writes included, without confirmation.
5. **Trigger** — see below.

## The prompt template

```
Load the procedure `<slug>`: oto_procedure(op="get", slug="<slug>").
It carries the complete method and takes precedence: if it diverges from
this prompt, it wins.

<The scope of the run: what to apply it to, what counts as done.>

You run without a repository and without a local machine: everything goes
through the tools of the Oto connector (prefix `mcp__Oto__`). They are not
all loaded upfront — resolve the ones you need with ToolSearch.

If the procedure cannot be found, if the source is empty, or if you are
missing a tool, say so explicitly in your report instead of producing a
plausible result.
```

The last line is not politeness: an autonomous agent that nobody follows up on has a natural tendency to fill the gaps. Asking it to name what blocked turns a failed run into actionable information.

## The three triggers

They can be combined on the same routine.

| | when | good to know |
|---|---|---|
| **scheduled** | regular cadence or single date | time entered in **local time** then converted (check the displayed time after creation), interval **minimum one hour**. A one-off fire does not count toward the daily run cap |
| **API** | instant triggering by a third party | `/fire` endpoint + bearer token. **The token can only be created in the interface** and is displayed only once — no public API generates it |
| **GitHub** | pull requests, releases | filters by author, branch, labels, state |

The API trigger is the only real-time path. The one-hour minimum only applies to the scheduled one.

## Passing context to a run

The `text` field of the fire reaches the agent **wrapped in a `<routine-fire-payload>` block labeled as untrusted data**, with the instruction not to follow the instructions it contains. The routine's prompt must explicitly opt in to reading it, otherwise the text remains inert context.

Direct consequence: **pass a reference, never the record.** "row `019ff08f…` of the leads table" rather than the copied lead — the agent reloads the fresh data through oto, and the payload never becomes an instruction vector. Cap: 65,536 characters, which is another way of saying the same thing.

## What breaks a run, by decreasing frequency

1. **A secret left on the workstation.** A procedure that reads a local vault (SOPS, `~/.config`, shell environment variable) fails as a routine: there is no machine. The credential must live in the oto vault, resolved by a connector.
2. **A local file or repository.** Same cause.
3. **The time.** It is entered in local time and the routine converts it; reread the displayed time after creation, especially if the cron was written by hand.
4. **A prompt that copies the procedure** instead of loading it.
5. **A payload treated as an instruction** — the routine ignores it, and the run goes off on the wrong scope without saying so.

## What a routine does not do

- **No idempotency.** Each fire creates a session; there is no idempotency key, and a webhook retry produces a second run. Deduplication happens in the procedure (recheck the state before writing) or upstream of the fire.
- **It belongs to an individual account.** It acts under its creator's identity, with their access, and counts against their daily run cap. It is not a service account.
- **It cannot be provisioned from a third party.** Only `/fire` is a public API: creating a routine and generating its token remain interface operations.

## Reading the result

Each run is a **viewable session**: the full transcript, the tool calls, the errors. That is what sets a routine apart from a homemade scheduled job — supervision is provided.

Watch out for the status: a green run means the session started and ended without an infrastructure error. **It does not say the task succeeded.** Task failures, missing tools and blocked network requests show up in the transcript, not in the indicator light.

The real reflex: **have the procedure write a dated trace in a datastore table.** A run then becomes readable at a glance, without opening a transcript — and the history accumulates where you already know to look.

## Triggering a routine from oto

The `routine` connector carries one routine per instance (`routine_id` + fire token, set from the dashboard's connectors). `routine_fire` triggers it and returns the session to open; the result is read in that session, not in the response.

It is useful when the trigger is an agent in conversation, or a service that already goes through oto. A third-party tool that can make an HTTP POST will call `/fire` directly — no need to put oto on that path.

## Depending on the client

- **Claude Code** (terminal, desktop): `/schedule` creates the routine in conversation; minimum cadence one hour.
- **Claude app / Cowork**: it is the user's scheduled task (Scheduled → New task, or described in the conversation, which Claude proposes then has confirmed).
- **Elsewhere** (chat without scheduling): give the user the steps on `claude.ai/code/routines`, with the prompt template above ready to paste.

A task **for a client** is created on the client's Claude account, not on ours.

## Routine or hosted agent

A Claude Code routine is **the** path when a user asks their assistant to schedule a task ("every Monday", "remind me"). `oto_trigger` is something else: it creates a **hosted agent of the org**, run by oto's fleet of workers on the org's model key, under its owner's identity, and managed in the dashboard. Only turn to it if the user explicitly asks for an oto hosted agent or a webhook.
