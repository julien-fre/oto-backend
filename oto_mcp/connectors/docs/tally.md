## prerequisite — your tally api key

create a key on [tally.so/settings/api-keys](https://tally.so/settings/api-keys) (Settings → API keys → Create API key), then paste it into oto. It is only shown once.
- byo-only: no shared oto key, and this is not a catalogue choice — a Tally key is tied to **one user**, inherits THEIR rights (Tally offers no fine-grained scope today) and **stops working if they leave the organization**. A "service" key does not exist: set one from an account that will stay.
- format `tly-…`, sent as `Authorization: Bearer`

## usage — read responses, drive forms

six tools, verb in `op`:
- "which responses have arrived since last time?" → `tally_submission(op="list", form_id="…", after_id="<last id seen>")`
- "give me this response" → `tally_submission(op="get", form_id="…", submission_id="…")`
- "which forms do I have?" → `tally_form(op="list")`, then `op="get"` / `op="questions"` / `op="blocks"`
- "create / edit a form" → `tally_form(op="create"|"update"|"update_blocks", blocks=[…])` (39 block types, see [blocks-reference](https://developers.tally.so/blocks-reference))
- "where do people drop off this form?" → `tally_analytics(op="drop_off", form_id="…", period="30d")`
- "my workspaces, my folders" → `tally_workspace(op="list"|"folders"|…)`
- "who is in the organization, invite someone" → `tally_account(op="me")` (it returns `organizationId`) then `op="users"|"invite"|"invites"`
- "push each response to my system" → `tally_webhook(op="create", form_id="…", url="https://…", event_types=["FORM_RESPONSE"], signing_secret="…")`

## note — the responses join, and five traps

- **responses are not self-describing**: the API returns `questions` once per page and each response points into it by `questionId`. `tally_submission` does the join — each response carries `answers: [{question_id, title, type, answer, formatted}]`, plus `answers_by_title` **only if the titles are unique** on that form (otherwise the key is absent and `title_collisions` says which ones step on each other). `raw=True` returns the Tally payload intact.
- **a `FILE_UPLOAD` question answers with a LIST of files** — `[{id, name, url, mimeType, size}]`, verified live. The three metadata fields `name`/`mimeType`/`size` arrive IN the response: enough to check that a file is present and in the right format **without downloading anything** (which makes a completeness check possible even before a DPA allows reading the content). Each response also carries `pdf_url` and `preview_url` — Tally renders the full response as a PDF, no need to rebuild it.
- ⚠️ **these three URLs carry a signed token** (`accessToken` = a JWT, plus `signature`, in the query string): `preview_url`, `pdf_url` and the `url` of each file. The token IS the access right — these are not public links. They travel through the agent's context and through anything that logs a tool result: treat them as a bearer of rights, not as an inert reference.
- ⚠️ **`formattedAnswer` did not come back live** (INPUT_TEXT, INPUT_EMAIL, FILE_UPLOAD), although the spec documents it. The plausible reading is that it only appears where `answer` is not already readable (a choice question, whose `answer` is an option id) — **unverified**. `answers_by_title` therefore falls back to `answer`, and `formatted` can be `null`.
- **`tally-version` is pinned client-side.** Tally versions by DATE (Stripe-style) and a key is frozen at the version of the day it was created, with no way to change it. Without an explicit header, two clients of the same org would get different response shapes depending on the age of their key — which is exactly why a 2025 key does not return `formattedAnswer`. The client therefore always sends `tally-version` (default `2026-08-04`).
- **`PATCH /webhooks/{id}` is a FULL REPLACE** despite the verb (`formId`, `url`, `eventTypes`, `isEnabled` all required). `tally_webhook(op="update")` re-reads the webhook and merges — do not bypass it by hitting the API by hand.
- **`blocks` replaces the entire list** on `op="update"` and `op="update_blocks"`: a block absent from the array is deleted. Read the state with `op="blocks"` first.
- **oto is not a webhook receiver.** `tally_webhook` registers a URL that YOU control (n8n, Make, your service); to bring responses into oto, schedule a `tally_submission(op="list", after_id=…)`. Webhook deliveries do not consume the 100 req/min quota — polling does.

## note — what destroys, and the safety net

four operations destroy something; all accept `dry_run=True`, which validates identically and returns a real diff instead of writing:
- `tally_submission(op="delete")` — **irreversible**: Tally documents a trash for forms and workspaces, **not for responses**
- `tally_account(op="remove_user")` — removes someone from the organization **and revokes every API key they created**, possibly including the one of the current call
- `tally_workspace(op="delete")` and `op="delete_folder"` — take the contained forms with them (trash, restorable)
- `tally_form(op="delete")` — trash, restorable

## note — a Tally 401 does NOT mean "invalid key"

this is the costliest trap of this API, recorded live:
- `GET /webhooks` returns **401 as long as no webhook has ever been created** on the account. After a first creation it returns 200 — and keeps doing so even after all of them are deleted. The 401 says "the webhooks integration does not exist yet", not "your key is bad".
- `tally_form(op="blocks")`, `tally_form(op="update_question")` and `tally_workspace(op="create")` return 401 on a **FREE** plan: it is a PLAN gate, not authentication.

the connector therefore translates **no** 401 into "key rejected" — even without known context, the message names both causes and points to `tally_account(op="me")`, which settles it: if it answers, the key is good and it is the plan that blocks.

## note — verification status

derived from the real OpenAPI 3.0.1 spec (`developers.tally.so/api-reference/openapi.json`, read on 2026-08-31), **and tested live on 2026-08-31** with a real `tly-` key on a FREE account.

**exercised for real, conforms to the code**: `op="me"`; the full cycle of a form (create → read → rename → publish → trash); questions; responses (list + `filter=partial`); the five analytics views; the full cycle of a webhook (create → list → event log → merged PATCH → delete) — including the check that `op="update"` with only `is_enabled=False` **does not wipe the URL**; the refusal of an irrelevant argument; and the fact that `dry_run` writes nothing and never echoes the `signing_secret`. The account was returned to its initial state (0 forms, 0 webhooks).

**envelopes recorded** (the spec did not settle them): `/forms` and `/workspaces` return `{items, page, limit, total, hasMore}`; `/webhooks` returns `{webhooks, …}` and **not** `items`; `/forms/{id}/questions` returns `{questions, hasResponses}`; `/organizations/{id}/users` and `.../invites` return a **bare array**; webhook `PATCH`/`DELETE` return an **empty body**.

**NOT exercised**, for lack of plan or material, and therefore derived from the spec alone: reading/writing blocks (plan 401), writing workspaces and anything touching folders (Pro), `update_question` (a freshly created form returns no question, even published), reading and deleting ONE response (no response on the account), invitations (would send real emails), removing a member (would remove the owner from their own org and revoke the call's key), replaying an event (no event delivered).

**a question takes its title from the `LABEL` block that precedes it**: a form whose inputs have no `LABEL` returns `{"questions": [], "hasResponses": false}` even published — it is not a bug, there is simply nothing to name. Found live after believing an endpoint was broken.

⚠️ **`metrics` and `drop_off` do not give the same `completionRate`** over the same period (100 vs 50 on one visit and one submission, measured): they do not count the same thing. Do not mix them in one dashboard without saying which one is cited.

**block traps** met at creation: a `TITLE` or `LABEL` block must not share its `groupUuid` with an input block, and `TitlePayload` carries `html`, **not** `title` (only `FormTitlePayload` has both).
