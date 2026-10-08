## prerequisite — your typeform personal access token

in Typeform: **Account → Personal tokens → Generate a new token**, then paste it into oto (format `tfp_…`). The token carries its scopes, and each call needs its own:
- to read: **forms:read**, **responses:read**, **workspaces:read**, **webhooks:read**
- to write: **forms:write** (create, edit, publish, delete forms), **responses:write** (delete responses), **webhooks:write** (create, delete webhooks)
- a read-only token keeps every read; a write without its scope is refused, naming the scope to add (generate a new token with it)
- byo-only: no shared oto key. The token acts as the account that created it and sees what that account sees
- **data center**: leave it empty (or `us`) unless the account stores its responses in the EU — then `eu` (`api.eu.typeform.com`) or `eu2` (`api.typeform.eu`, the newer EU data center, whose tokens are distinct). Ask the account owner if unsure
- the "test connection" button lists one form (`GET /forms?page_size=1`): it checks the token and its `forms:read` scope, nothing is written

## usage — read forms and their responses

- "which forms do we have?" → `typeform_forms(op="list")`, or `typeform_forms(op="list", search="NPS")`; by workspace → `typeform_workspaces()` then `typeform_forms(op="list", workspace_id="…")`
- "what does this form ask?" → `typeform_forms(op="get", form_id="…")`: each question with its id, ref, type, title and choices
- "the latest responses" → `typeform_responses(form_id="…")` — 25 newest, each as `{question title: value}`
- "responses since Monday" → `typeform_responses(form_id="…", since="2026-09-28T00:00:00")`
- "everything" → page with `before=<next_before>` until `next_before` is gone; `total_items` says how many there are
- "who started but did not finish?" → `typeform_responses(form_id="…", response_type=["partial"])`
- "the responses mentioning Lyon" → `typeform_responses(form_id="…", query="Lyon")`
- "how did people answer?" → `typeform_responses_summary(form_id="…")`: answer rate per question, choice counts, numeric stats, NPS, responses per day — computed by oto, no text answer read. `truncated: true` = the oldest responses were not counted (raise `max_pages` or narrow `since`)

## usage — create and change forms

- "create a feedback form" → `typeform_forms(op="create", definition={"title": "…", "fields": [{"title": "…", "type": "nps"}], "settings": {"is_public": false}})`. ⚠️ **without `settings.is_public: false` the form is public at once**: anyone with its link can answer
- "publish it" / "close it" → `typeform_forms(op="update", form_id="…", operations=[{"op": "replace", "path": "/settings/is_public", "value": true}])` (`false` to unpublish); `/title`, `/theme`, `/workspace` work the same way, fields untouched
- "change its questions" → `typeform_forms(op="get", form_id="…", full=True)`, edit the definition, then `typeform_forms(op="replace", form_id="…", definition=…)`. ⚠️ a field left out is deleted **with its answers**: keep each field's `id`
- "delete this form" → `typeform_forms(op="delete", form_id="…")` — the form and **all its responses**

## usage — delete responses, webhooks

- "delete these responses" → `typeform_delete_responses(form_id="…", response_ids=["…"])` (1 to 1000). The deletion is asynchronous; ids that match nothing are ignored by Typeform — the preview says which ones exist
- "send the responses to our endpoint" → `typeform_webhooks(form_id="…", op="upsert", tag="crm", url="https://…", enabled=true, event_types=["form_response"], secret="…")`. Every new response, with all its answers, is POSTed there
- "which integrations receive this form?" → `typeform_webhooks(form_id="…")`; pause one with `op="upsert"` and `enabled=false`, remove it with `op="delete"`

## note — two steps for what cannot be undone

`typeform_forms` op `replace` and `delete`, `typeform_delete_responses`, `typeform_webhooks` op `upsert` and `delete` first return a **preview** (`dry_run: true`) read from Typeform — the fields that would disappear, the responses at stake, the ids that exist, the webhook before and after — and write nothing. The same call with `confirm=True` writes. Show the preview to the person before confirming.

## note — what is misleading

- ⚠️ **a wrong data center does not fail, it returns nothing**: responses read outside the account's data center come back empty. `typeform_responses`, `typeform_responses_summary` and `typeform_delete_responses` read the form first and refuse when its responses live on another host, naming the setting to change. With `titles=False`, `typeform_responses` skips that check
- **`response_type` also changes what the dates filter**: `completed` (default) filters on submission time, `partial` on the last save, `started` on landing
- **the most recent responses (~30 minutes) may not be listed yet** — Typeform's own lag
- **answers carry no question text**: the tool joins them with the form's questions. Two questions with the same title are keyed `title [field id]`, never merged
- **file uploads answer with a URL** (`file_url`): it points to Typeform and needs the token to download — not served here
- **a webhook's signing secret is never shown back**, not even with `full=True`. `upsert` replaces the webhook whole: pass the `secret` again, or it may be dropped
- `full=True` returns Typeform's raw payload (metadata, tokens, field types) when the readable view is not enough

## note — scope

served: workspaces (read), forms (read, create, edit, publish, replace, delete), responses (read, statistics, delete), webhooks (read, create, replace, delete). Not served: themes, images, workspace management, translations, file downloads.
