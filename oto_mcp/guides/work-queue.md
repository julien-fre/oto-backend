---
title: "Work queue: draining a pool with N agents"
description: atomic claim, lease, release — the cycle that guarantees a fleet of sub-agents processes a table with no duplicate and no lost row
---

# Draining a table with several agents

Read this **before** any fan-out over a shared pool: enriching N leads, qualifying N
sites, processing N files with several sub-agents in parallel. The natural reflex —
read the table, split it into batches, hand them out — is **non-atomic**: two agents
that read at the same instant see the same free row and both process it.

The datastore has the primitive that settles this on the server side: **`data_claim_next`**.

⚠️ This pattern loops INSIDE your conversation, which has a turn ceiling. For a pool of
several dozen rows or more, first read the guide `fleet-fanout` — beyond a
certain volume, set up a fleet rather than risk a `blocked` stop halfway through the queue.

## The principle: a lease, not an exclusion list

`data_claim_next(datastore, worker, filter?, lease_s?)` takes **the next claimable
row and reserves it** in the same transaction (`FOR UPDATE SKIP LOCKED`, the PostgreSQL
work-queue pattern). Two concurrent workers **never** get the same
row — the second one simply skips to the next.

The claim sets a **lease**: `_claimed_by` (your worker label) and `_claimed_until`
(now + `lease_s`). A row under an active lease is invisible to other claims.

⚠️ **The claim does NOT modify the row's content** — no automatic switch to
"in progress". It is the lease, not the status, that protects against double processing. So
do not build anything that assumes the row has changed after the claim.

Response: `{datastore, ns_id, row}` with `row = null` when there is nothing left to take
(queue empty for this filter, or everything is already under lease).

## The full cycle

```
0. run = run_start(label="<my campaign>")      → run["run_id"]
1. row = data_claim_next(datastore="<table>", worker="<my-label>",
                         filter={"status": "nouveau"}, _run_id=run["run_id"])
2. if row == null  → done, the worker stops
3. process row (enrichment, connector calls, reasoning…)
4. data_write(datastore="<table>", id=row["_id"],
              row={"status": "traité", ...deliverables}, _run_id=run["run_id"])
5. data_release(datastore="<table>", id=row["_id"], worker="<my-label>",
                _run_id=run["run_id"])
6. loop back to 1                              → then run_finish(run["run_id"], "done")
```

⚠️ **`_run_id` is passed again on EVERY call, including the one that writes.** It is the
most frequent omission, and it costs the row: the token is almost always
passed to the claim (the instruction is fresh) then forgotten at the write — and the write is
then refused on the very row you are holding yourself. The server does not keep it from one
call to the next: each call arrives in its own session. ⚠️ **There is no other way: `data_write` does
not take a `worker`.** The run token is the only means of being recognized by
the lock at write time — carry it on EVERY call that writes, not only on the one
that claimed.

**Giving the row back is an operation, not a consequence.** Writing a verdict does not release
it: the lock does not know your business states, and it does not have to know them — "à
qualifier" or "traité" are YOUR words, not its. What gives a row back:

- **`data_release`** — the normal operation, at the end of each processed row;
- **the end of your processing** — if you framed your work with `run_start` /
  `run_finish`, all the rows you were holding are given back at closing, whatever
  the outcome. It is the safety net when you forget the release;
- **the lease expiring** — the last resort, the one that kicks in if your agent stops
  without closing anything. It can be long: do not count on it (see the lease duration
  below).

The `worker` is replayed at release as a guard — you do not release someone else's claim.

**As long as you hold a row, nobody else can write it.** The lease now
protects the data, not only the assignment: a write coming from elsewhere is
refused with a message saying who holds the row and until when.

**And "someone else" is you too as soon as you stop naming yourself.** You are
recognized by your **run**, and only by it — provided you pass `_run_id=` on
the call that writes, not only on the one that claimed. A `data_write` that does not
carry it is a stranger to the lock, even if it comes from the account holding the lease:
it is refused, and the row stays blocked until the lease expires.

⚠️ **Your `worker` does NOT get you recognized at write time, contrary to what this
guide said until 2026-09-05.** The server knows how to do it — but no surface
passes it your label, and `data_write` has no parameter to write it in. The advice
"carry `worker=` if you write outside a run" therefore pointed to an impossible operation, at the
very moment you are looking for what to do with a refusal. The `worker` is used for the claim and the release,
nowhere else.

## The three parameters that matter

**`worker`** — a label you choose (e.g. `"enrich-13"`, `"qualif-nord-2"`), stable
for a given sub-agent and **replayed verbatim** on `data_release`. It acts as a guard and
makes the queue readable for supervision ("who holds what"). Give a distinct label per
sub-agent: that is what lets you see which one died.

**`filter`** — exact equality `{column: value}`, which defines what **counts as
claimable**. Typically `{"status": "nouveau"}`. Without a filter, any row whose lease
is free is a candidate — including those already processed. **Always set a filter**
as soon as the table carries a status.

⚠️ **And this filter must be on a column that your processing WRITES.** It is the
condition that makes the queue EMPTY, and nothing enforces it. The order: rows
never served first, then the least recently served — a released row goes
behind all the fresh ones, but it comes back once those have been served, as long as
it matches your filter. Filter on column A, write in column B, and
the queue never empties: your already processed rows come back to you, round after round.

Measured on 2026-09-07 on a table of 3,766 rows, when the order was still "the
oldest first": three workers were filtering on a column the processing did not
touch. **834 fresh rows were never reached**, and one worker claimed
the same one SEVEN times. Every call succeeded, no error was raised — it is a
livelock, not a failure, and you cannot see it from the inside.

⚠️ **How to see it anyway**: the row carries `_claims`. Above 1, it says
"I have already been served to you and I was not written" — that is the signature. Stop and
reread your filter: claiming again will not change anything. These rows eventually leave
the queue — at the declared ceiling (`lifecycle.max_claims`, in the abandon state), otherwise at
the platform ceiling (3 claims without a write, status unchanged, reason in
`_abandon`) — **lost for the pass, without being at fault in any way**. The reason ends with what
the LAST attempt ran into ("last attempt: calls in error:
serper_search", "write refused: …", "no write attempted"…), and
`_abandon_run` names its run: that is where you read why, before putting the row back
in the queue. If the table declares a claim scope
(`lifecycle.claimable` in its schema), your `filter` is added to it with AND: it narrows it,
it never widens it — and a `row: null` response names that scope to you.

**`lease_s`** — lease duration, 900 s (15 min) by default. It is the
recovery mechanism: a worker that dies along the way does not block its row
forever, it becomes claimable again when the lease expires. Set it to the real duration
of one processing, with some margin: too short, a slow row gets stolen and
processed twice; too long, an abandoned row sleeps.

## ⚠️ The trap: writing a "final" state releases nothing

The lock is **the same for all tables**: there is nothing to declare in the schema
for it to work, and nothing to configure per table. An agent takes a row, gives it back.

Your status field, on the other hand, remains **entirely yours**: `"à qualifier"`, `"traité"`,
`"écarté"` are values of your business, in an ordinary column. The lock does not
read them — that is what guarantees it works the same for everyone.

> **What changed (August 2026).** Writing a state declared "final" used to release
> the row automatically. This behavior is removed: to do so the platform had to
> know your states and guess which ones are endings, which failed silently as soon
> as the declaration did not exactly match the usage. If you relied on that
> release, add a `data_release` after your write — or frame your work with
> `run_start` / `run_finish`.

**Symptom to recognize**: the queue "empties" while the rows are not
processed, then fills up again later. It is the lease expiring, not a bug — and
it is the sign that a `data_release` is missing somewhere.

## Fan-out: what it replaces

With the claim, a sub-agent has **no need to know what the others are doing**. So you
no longer have to:

- inject into each agent an **exclusion list** of rows already taken (it is
  stale the second you write it);
- split into **batches of 3-4** fixed in advance (a slow agent blocks its batch, a dead
  agent loses it);
- **deduplicate by rereading the table** before each processing (costly and non-atomic
  — two simultaneous rereads give the same verdict).

The pattern: launch N identical sub-agents, each with its own `worker`, each looping
`claim → process → write → give the row back` until `row == null`. Parallelism is
tuned by the number of agents, not by a split. For large volumes, combine with
the guide `bulk-load` (keep payloads out of the main context).

## Miscellaneous

- `datastore` accepts `slot:<name>` — the table bound by the active project.
- The lease only appears on a row (`_claimed_by` / `_claimed_until`) **if it is
  set**: an ordinary read of a free row does not have these fields.
- The serving order is the creation order (oldest first).
- The dashboard shows the rows under lease ("en cours · worker") and allows a
  forced release by a human, without a worker guard.
