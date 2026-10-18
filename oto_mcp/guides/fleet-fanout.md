---
title: "Too many rows for one conversation: set up a fleet"
description: when a queue exceeds what one conversation can loop through, set up an `oto_fleet` rather than running `data_claim_next` up to the turn ceiling
---

# Too many rows for one conversation

Read this **before** looping the pattern of the guide `work-queue` over a pool of several
dozen rows: enriching 200 companies, pushing a batch of accounts to a CRM,
any identical processing repeated row by row. `work-queue` remains correct — but a
conversation has a TURN ceiling (24 by default, 64 at most, never an automatic
resume), and a run that hits it stops `blocked`, not `done`. Looping 200 rows
in a single conversation means betting on that ceiling before you have even seen the queue.

## The principle: oto produces the work, not you

`oto_fleet` is **not** a second loop to write. `op=create` declares (procedure +
allowlist + target), `op=launch` ARMS — and that is all you have to do. No
scheduler to start: as soon as a worker polls the org's queue and has nothing
ready, oto **builds** the next job of the armed fleet for it, one by one, up to
`max_rows` or a stop. Each row becomes a **separate** job, with its own
turn budget — so the ceiling of a conversation no longer applies to the whole
queue, only to each row.

⚠️ **A pass runs on its org's model key.** `create` requires a `model` from the
catalog (`model_required` otherwise). If `launch` responds `model_key_required`, the org must
deposit its key for that provider: do not retry, tell the user, and
fall back to the pattern of the guide `work-queue` in the meantime — splitting the work into
several conversations if the volume exceeds what a single one can loop through.

## The procedure always takes precedence

Before `oto_fleet op=create`, look for an existing procedure (`oto_procedure op=list`)
that already covers this processing. Only write an ad hoc one if nothing matches — and in
that case, as for a normal run, write it as if it were going to be reread. `create` requires `label` + `procedure` +
`tools` + `model`; there is no free-text shortcut that would bypass the procedure.

## Never arm without an explicit agreement, in plain words

Arming a fleet really spends — on the model key deposited by the org, or on the
platform key if the org has deposited none — and writes into a real table, without
any human validating it elsewhere.
Before `op=launch`, say in one sentence what is going to happen: the number of targeted rows
(real count via `data_rows` on the `row_filter`, never an estimate), the procedure,
the declared tools — and wait for a yes. This is not a formality: it is the only
place where a human sees the spending before it goes out.

## Bound it, always

- `max_rows` = the REAL count of eligible rows, never left empty.
- `max_tokens_per_row` and `max_consecutive_failures`: set them every time — without the
  first, only the turn ceiling bounds a row that goes astray; without the second, a
  whole queue can spin idly on the same defect before anyone notices.
- `tools` = exactly the allowlist the procedure declares, never wider than what
  a one-row run would require.

## Follow up and report

`op=state` returns the aggregated progress (`pending`/`claimed`/`done`/`failed`,
`reservable_rows`) — query it to answer if you are asked how far along it is, in the
same conversation, without pointing to a screen. `op=stop` REQUESTS the stop (`stopping`):
the fleet finishes what is under way before becoming `stopped`, it does not stop dead.

See also the guide `work-queue` (the same pattern for a volume a conversation can
loop through itself) and the guide `bulk-load` (keeping large payloads out of context,
a question orthogonal to this one).
