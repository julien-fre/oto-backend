# Local test gate — TheirStack + Origami connectors

Run this in full before anyone pushes `feat/theirstack-origami-connectors`. Nothing here needs a running server:
the tools are mounted on a bare FastMCP exactly as the cloud runner will call them.

## 0. Environment (once)

```bash
cd ~/Desktop/oto-backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # backend + pinned oto-core from GitHub
pip install -e ~/Desktop/oto-core  # then OVERRIDE with the local oto-core branch that holds the new clients
python -c "import oto.tools.theirstack.client, oto.tools.origami.client; print('local clients resolve')"
```

Baseline before the new code (measured 17 Aug 2026): `pytest tests/test_cognism.py tests/test_tools_client_methods_exist.py` → 61 passed.

## 1. Unit layer (mocked clients) — must be green

```bash
pytest -q tests/test_theirstack.py tests/test_origami.py
pytest -q tests/test_tools_client_methods_exist.py     # version-skew guard: every tool→client method exists
pytest -q                                              # whole suite; nothing that was green may go red
```

Assertions the new tests must carry (reject the PR if any is missing):
- registry entry present, `keyed=True`, `auth_modes == {"byo_user","byo_org"}`, `secret_kind == "api_key"`, `in_default_bundle is False`
- every registered `theirstack_*` / `origami_*` tool has a NON-EMPTY `.description` (the f-string-docstring trap)
- Origami: every mutating tool honours `dry_run` (validation runs, final call skipped, response echoes `dry_run: true`)
- Origami: `origami_campaign_launch` defaults to `dry_run=True`
- Origami: `origami_campaign_delete` after `confirm=True` re-GETs and reports whether the campaign is really gone
- Origami: `origami_campaign_create` exposes `block_prior_contacts` / `block_active_duplicates` and passes them as `settings`

## 2. MCP layer with REAL keys, read-only — must succeed

Keys come from a local outreach `.env` file (`THEIRSTACK_API_KEY`, `ORIGAMI_API_KEY`). Never print them.
Patch `oto_mcp.access.resolve_api_key` to return the env key, mount with `register_all`, call the tool `fn`s.

TheirStack:
- `theirstack_companies_search(company_names=["PUIG & FILS","ARTEXTYL"], company_country_code_or=["FR"])` → 200, envelope present; a hit for at least one is expected but an empty list is NOT a failure (coverage ~8%)
- `theirstack_jobs_search(company_names=["SPIRIDOM"], posted_at_max_age_days=365)` → 200; expect the "Responsable Administratif et Financier" posting seen on 17 Aug (may have aged out — then just assert shape)
- projection: default result carries only the sourcing fields; `full=True` returns the raw record

Origami (READ ONLY — no create/upsert/launch/delete against production):
- `origami_workspaces(op="list")` → contains workspace `5071dde1-db17-410c-908e-6f45137c0854` ("Grossistes FR")
- `origami_tables(op="list")` → contains tables `20b15cd8-…` (hyper standing) and `a86c8074-…` (site standing)
- `origami_tables(op="columns", table_id="a86c8074-8af3-442c-b138-0379baf1226c")` → includes slug `opener-fr`, kind `input`
- `origami_rows(op="list", table_id=<site>, max_pages=2)` → follows `nextCursor` (table has 52 rows; page 1 is 50)
- `origami_campaigns(op="stats", campaign_id="a7d0addd-391d-438b-a127-ded7b0841e40")` → `found == 52`, `noMessagingCount == 0`
- `origami_sequences(workspace_id="5071dde1-…")` → ≥ 369 sequences across ≥ 4 distinct campaignIds
- `origami_campaign_launch(campaign_id=<site>)` with the DEFAULT args → must be a dry run: response `dryRun: true`, `wouldLaunch: true`, and campaign status unchanged afterwards (re-GET)

## 3. MCP layer, WRITE path, against a throwaway table only

Create a scratch workspace `smoke-<date>` and do the whole loop there, then leave it (no table delete endpoint exists):
- `origami_upload_csv` a 2-row CSV with an `opener_fr` column → `table_id` + `table_slug` at the TOP level of the response (the created table lives at `result.results[0].table.id`; a harness missed it at that depth, so the tool surfaces it; a `kind: "error"` entry comes back as `error`, never as a silent success)
- `origami_rows(op="upsert", dry_run=True)` → preview only, table unchanged (re-list)
- `origami_rows(op="upsert")` with a wrong slug (`opener_fr`) → refused BEFORE the API call, naming the unknown slug and the valid input slugs (`clés refusées — slugs inconnus ['opener_fr'] ; slugs d'entrée valides : [...]`); never a swallowed 400
- `origami_rows(op="upsert")` with `opener-fr` → 201; re-list shows the value
- `origami_campaign_create(dry_run=True)` → preview, no agent run started (re-list campaigns on the table: still 0)
- `origami_campaign_delete(confirm=True)` on a nonexistent id → 404 translated (`ressource introuvable`), never a claimed success
- Do NOT create a real campaign in the smoke test; the agentic create costs a run and cannot be deleted cleanly by API.

## 4. Behaviour the tools must NOT have

- No tool prints or returns the API key — check tool output, the 401 error text (`og_live_…` prefix only) and both `_verify` probes (return `None`).
- No `f"""` docstrings; every registered `origami_*` / `theirstack_*` tool has a non-empty description.
- Origami tools never call `/launch` without dry_run unless `dry_run=False` was passed explicitly (check the schema default AND the client call: `launch_campaign("c1", dry_run=True)` with default args).
- `origami_campaign_delete` never claims success on the step-1 `200`: with a mocked 200 + campaign still readable → `really_deleted: false` and a note.

## 5. Sign-off

Record in the PR: unit counts, the read-only MCP results (with the 52 / 369 numbers), the throwaway-table loop result, and the
two maintainer questions — write-capable mount acceptable? which oto-core tag to pin?

### Results — 17 Aug 2026 (local, Mac, real keys, before any push)

- **Layer 1** — `tests/test_origami.py` + `tests/test_theirstack.py` + `tests/test_tools_client_methods_exist.py`: **104 passed** (incl. `test_sequences_follows_next_cursor_and_lists_distinct_campaigns` and `test_upload_csv_surfaces_table_id_from_flat_shape_and_error_kind`, added by this gate). Whole backend suite: **4869 passed / 1 failed / 258 errors** — the failing/erroring test ids are **byte-identical to the base commit `bb44a71`** (4808 passed there): all are the testcontainers fixture failing on `docker info` (no Docker on the Mac) and `DATABASE_URL not set`; none import origami/theirstack. oto-core `feat/theirstack-origami-clients`: 492 passed.
- **Layer 2** (real keys, read-only, through `FastMCP.call_tool`) — workspaces list contains `5071dde1…`; tables list contains `20b15cd8…` and `a86c8074…`; `columns` on the site table shows slug `opener-fr` kind `input`; `rows(list, max_pages=2)` follows `nextCursor` (52 rows, page 1 = 50); `campaigns(stats)` on `a7d0addd…` → `found 52`, `noMessagingCount 0`; **`origami_sequences` was single-page** (50 seqs / 1 campaign) — fixed to follow `nextCursor` (`max_pages=10`, `campaign_ids`), verified live: **369 sequences / 8 pages / 4 campaigns**; `origami_campaign_launch(campaign_id)` with default args → `dry_run: true`, status still `active` afterwards. TheirStack: `companies_search` 200/empty then 402 once credits ran out, `jobs_search` 402 — both translated to `crédits épuisés (402)`.
- **Layer 3** (scratch workspace `5fd41634-d137-4efd-ad4f-2365479eb3b7`, tables `5609ece4…`, `97ecb9e9…`) — upload → top-level `table_id`/`table_slug` ✓; upsert dry-run leaves `opener-fr = "Ligne un."` ✓; wrong slug refused client-side with the valid-slug list ✓; real upsert reads back `MODIFIÉ` ✓; `campaign_create(dry_run=True)` → 0 campaigns on the table, settings echoed `{blockPriorContacts: true, blockActiveDuplicates: true}` ✓; delete of a nonexistent id → 404 translated ✓.
- **Layer 4** — no f-string docstrings; 14 tools, all described; key absent from outputs, 401 text and `_verify` returns; `launch_campaign` called with `dry_run=True` on default args and `dry_run=False` only when passed; delete with 200-but-still-readable → `really_deleted: false`; step-1 (no confirm) → `delete_campaign(confirm=False)`.
- **Smoke scripts** — `scripts.origami_smoke_test` exit 0 (lists the 5 live tables + pilot); `scripts.theirstack_smoke_test` fails cleanly on 402 (account out of credits — expected until refilled).
- **Open for the maintainer** — (1) the Origami mount is write-capable (`upsert`, `campaign_create`, `launch`, `delete`) with `dry_run` on every mutating tool and `launch` defaulting to dry-run: acceptable as `in_default_bundle=False`, byo-only? (2) which oto-core tag to pin in `pyproject.toml` once `feat/theirstack-origami-clients` is tagged (currently `v1.82.0` + TODO).
