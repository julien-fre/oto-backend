## prerequisite — a shared OpenRouter API key: the tenant's, or the instance's platform key

jev is a **TypeSafe** model served by **OpenRouter**: the key is an [OpenRouter API key](https://openrouter.ai/settings/keys) (starts with `sk-or-`), not a TypeSafe key — there is none. It is a **shared** key: either **the tenant's**, set once for all its organizations, or a key set at the **platform level of the instance** and granted to the organizations it serves.
- use a dedicated key with its own spend cap: the same key elsewhere opens OpenRouter's whole model catalog
- a key set on an organization, team or person is **not** used and shadows the shared one: the tool refuses it and names who must remove it
- no key, no call — there is no free tier
- the state goes to a **third party** (OpenRouter, then TypeSafe), as a subprocessor of whoever set the key (the tenant, or the instance for a platform key)

## usage — triage a batch with one rubric

jev returns a **typed answer with its probability**, never text: yes/no (`noul`), a choice among options (`choice`), a position on an ordered scale (`score`). It pays off on **batches**: a single case the agent can judge itself does not need jev.
- "qualify these 200 profiles before I enrich them" → `jev_items`: one rubric, N states, nothing written; a batch that runs out of time (~40 s) returns what was answered and, in `retry`, the states to send again
- "qualify every row of this table" → `jev_rows`: the server reads, judges and writes back (answer, `<col>_p` confidence, model); only rows with an empty `model_column`, so a re-run resumes; `dry_run` on known cases first
- a whole table (hundreds of rows or more) → `jev_rows(background=true)` after the dry run: one call returns a `job_id`, the server judges every matching row itself, slice by slice; follow it with `jev_job(op='status')` every minute or so, stop it with `jev_job(op='cancel')`. A dropped connection doesn't stop it; a revoked key or a spent quota does, at the next slice (what was judged stays written and billed). One active job per table and `model_column`
- `jev_rows` creates missing output columns (choice → enum of its criteria, score/noul → number, plus `<col>_p` and `model_column`); an existing column is never changed, and a type mismatch is refused
- "does this row match my target?", tuning a rubric before a batch → `jev_ask` with the row's state and a `noul`
- a state fits in **16 KB** of JSON: only the fields the judgement needs, never the whole record or document
- put **the whole rubric in one call**: questions are answered in parallel, the state is billed once, each extra question costs ~48 tokens
- questions in one call cannot see each other: a dependent question needs a second call

## note — reading a probability, and where to set the threshold

A probability near 0.5 means "as likely as not", never "medium".
- validate fit against known outcomes before trusting it; combine Jev's answers with structured fields in plain code
- keep **two** thresholds and send the middle band to a human or a text model: cheaper than forcing a label
- `confidence` describes how concentrated the distribution is, not how safe the next step is
- a `choice` ALWAYS answers, even when the question does not apply: read the filtering `noul` first, or add a "none" option and check its probability
- the response names the exact `model`: store it next to the answer, it makes a threshold reproducible

## note — cost

Only **input** is billed, output is free, and every response carries `usage.cost`, the real cost in dollars — that is what gets billed, not a call count.
- a short state (a table row, a profile) costs a few dozen micro-dollars
- a state is capped at 16 KB of JSON (upstream context goes to 32,000 tokens)
