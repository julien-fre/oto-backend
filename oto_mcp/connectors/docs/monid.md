## prerequisite — your Monid API key, and the wallet it debits

create a key in the [Monid](https://monid.ai) dashboard → API keys (it starts with `monid_`), then paste it into oto.
- **each call is debited from the Monid wallet** of the workspace the key is tied to: what you launch is paid there, not at oto
- a key tied to no workspace answers 403; a revoked or badly copied key, 401 — create a new one and replace the old one on the card
- the platform key opens only on explicit grant: without a grant, it is your key or your org's
- ⚠️ under the platform key, the Monid workspace is **shared** by the orgs that have a grant: the list of runs is refused there (it would show theirs); a run is re-read and stopped by its identifier
- ⚠️ under the platform key, the **balance** is refused too: it is that of the platform's shared wallet, not yours. A launch short of funds says so (402); to see a balance, set your own Monid key
- the "test the connection" button calls the key's identity (free): it launches nothing and does not read the balance

## usage — find the endpoint, read its price, launch it

Monid puts roughly 2,000 endpoints from about 70 providers behind a single key: web search, scraping, contact enrichment, social networks…
- "find the work email of this LinkedIn profile" → `monid_endpoint(q="work email from a linkedin profile")` to find the endpoint, then `monid_endpoint(op="inspect", provider=…, endpoint=…)` for its input schema and price, finally `monid_run(provider=…, endpoint=…, query_params={…})`
- long run (the launch returns a run not yet finished) → `monid_runs(op="get", run_id=…, wait_seconds=30)` until `done: true`
- "stop that" → `monid_runs(op="stop", run_id=…)` (stops the spending; the stop is asynchronous)
- "what have I launched?" → `monid_runs()` (newest to oldest) — **with your own Monid key** (or your org's): under the platform key, the list is refused
- "how much is left?" → `monid_wallet()`: `balance` is the spendable amount (it can be negative), `held` what is reserved for runs in flight — **with your own Monid key** (or your org's): under the platform key, the balance is refused

## note — the input, the price and what is billed

- a run's input has **three parts** — `body`, `query_params`, `path_params` — where `inspect`'s schema places them, never flattened; `provider` and `endpoint` pass as `discover` returned them
- the price that counts is the one from `inspect`. Its types are open (per call, per result with a flat fee, per unit, tiered, per matrix…), and volume parameters (`maxItems`, `limit`) often apply **per query**: three terms × 10 results is 30 billed results
- ⚠️ for a tiered or matrix price, `amount` is only a base, sometimes 0 on a paid endpoint: the real rate is in `default` / `tiers` / `variants`, and a tier read on the output is only known after the run, so the displayed price is a floor
- ⚠️ **run status ≠ provider status**: `COMPLETED` means "the provider answered", whatever it answered. `COMPLETED` + 404 = "nothing found", not billed; `provider_ok` settles it for you — and stays empty when Monid gives no provider status (then read `next_step`)
- `BLOCKED` = a Monid workspace cap (budget, number of runs) refused the run before execution: nothing is billed, and relaunching blocks again until the cap is changed at Monid. `FAILED` (failure on Monid's side), `TIMED_OUT` and `STOPPED` are not billed either
- ⚠️ **never relaunch a run whose outcome is unknown**: Monid has no idempotency key, relaunching can pay twice. That is the case when the launch does not answer in time — a synchronous provider holding the connection longer than ~35 s returns this refusal, and the run probably exists. Finding it in `monid_runs()` requires your own Monid key; under the platform key, an administrator looks at the platform's workspace
- the wait on a launch is bounded (40 s at most, ~55 s in per-socket timeouts — as long as the connection is established on the first try: each unreachable address adds up to 10 s, and DNS resolution is not bounded): beyond that, the tool returns the run in progress and the next step rather than making the client hang up
- `cost_usd` is what Monid declares billed, read from the response and never recomputed; it stays empty until the run is settled

## note — verification status

written against the OpenAPI contract `0.1.0` published by Monid, and tested on a fake transport that replays its responses (runs returned under an HTTP code copying the provider's, error envelope, 202 then re-read, stop 409, wallet). **Not yet exercised against a real account**: neither the exact shape of `whoami`, nor the cursor of the list of runs, nor the real format of the key.

**out of reach here, on purpose**: the workspace's budgets and run caps, resources, API key management, wallet top-up and history, the public registry. Monid's `hints` are not requested (no client header is sent) and are not read.
