## prerequisite — your signwell api key

create a key in SignWell: **Settings → API → Create API key**, then paste it into oto.
- byo-only: no shared oto key, and this is not a catalog choice — a SignWell key **acts on behalf of the account that created it**. That name is what appears on the invitations and in the audit trail of the signed document: set the key of the account in whose name the documents should go out.
- sent in the `X-Api-Key` header. The API's test mode (`test_mode`) is free and has no legal value.

## usage — send a document to sign, track it, retrieve the pdf

five tools, verb in `op`:
- "send this NDA to be signed" → `signwell_document(op="create", files=[{name, file_url}], recipients=[{id:"1", name, email}], text_tags=True)` creates a **draft**; review it, then `signwell_document(op="send", document_id=…)`
- "a file from the project" → `oto_project_files(op="list")` returns a signed `download_url`: that is the `file_url` to pass
- "where does the signature stand?" → `signwell_document(op="get", document_id=…)` — status, and for each recipient their `signing_link`, whether they received an email (`emailed`) and where they are
- "remind them" → `signwell_document(op="remind", document_id=…)`
- "give me the signed PDF" → `signwell_document(op="completed_pdf", document_id=…, audit_page=True)`
- "a link I will send myself, without a SignWell email" → `embedded_signing=True` at creation: each recipient gets a `signing_link` that opens in a browser
- "from my template" → `signwell_template(op="create_document", template_id=…, recipients=[{id, placeholder_name, name, email}])`
- "one send per row of a file" → `signwell_bulk_send(op="csv_template")`, fill it in, `op="validate_csv"`, then `op="create"` (a preview until `dry_run=False` is passed)
- "notify my system when it is signed" → `signwell_webhook(op="create", callback_url="https://…")`

placing the fields in the file (text tags): `oto_guide op=read slug="signwell-envoi"`.

## note — what the tools do differently from the api

- **creating does not mean sending.** At SignWell, `POST /documents` SENDS by default. Here `op="create"` (and `signwell_template(op="create_document")`) creates a draft unless `draft=False` is explicit; sending is `op="send"`.
- **a single link per recipient**: SignWell returns it under `signing_url` or `embedded_signing_url` depending on the mode; the view always returns it under `signing_link`, with `emailed` saying whether SignWell writes to that person. `full=True` returns the raw payload.
- **the signed PDF is returned as a link**, never as bytes. This link is a bearer: whoever holds it downloads the document.
- **no document list**: the API has none. Keep the `id` returned at creation — it cannot be found afterwards.
- every write accepts `dry_run=True`: identical validation, nothing is written or sent; a recipient change returns a real diff, an access code is never rewritten in clear.

## note — observed live (2026-09-16), to know before sending

- **test mode NEVER writes to the recipients**: every invitation of a `test_mode` document goes to the **account holder**, subject prefixed `[TEST]`, with the intended recipient's name in the body. An "I received nothing" test is read in the holder's mailbox.
- **`Sending` is not a send**: the status goes `Created → Sending → Sent`. A document stayed in `Sending` for several minutes without the invitation going out, while an identical document created just after reached `Sent`. Only `Sent` (or later) attests the send.
- **fields coming from text tags arrive deferred**: the creation response may carry zero fields, a `get` a few seconds later lists them all.
- **an embedded signing link opens in a plain browser**, not only in an iframe — and SignWell does not check the signer's address there: whoever has the link can sign. For a verified identity, let SignWell send the invitation by email.
- with `embedded_signing=True` and `send_email: false`, the holder still received an "unable to send this document" notice for an invalid recipient address: do not count on the total absence of SignWell email. Cause not established.

## note — verification status

client and tools derived from the OpenAPI definitions of the SignWell reference pages (read on 2026-09-16), **and exercised live on 2026-09-16** with a real key (Business account), through the tools themselves:
- **exercised through the tools, matches the code**: `signwell_account(op="me")`; `signwell_document` — creating a test-mode draft from a base64 `.docx` with text tags (15 fields detected, deferred), `get`, `dry_run` previews of `create`, `send` and `update_recipients` (real diff), `delete`; `signwell_template` — `create` with text tags and two roles, `get` (status `Available`), `delete`; `signwell_bulk_send(op="list")` and `op="csv_template"` (the returned header is derived from the template's roles and field labels); `signwell_webhook(op="list")`; the 401 (invalid key) and 404 refusals.
- **exercised on the API directly, through the same endpoints** (before the tools were written): a real document send, a test-mode send, embedded signing with `send_email: false` — hence the observations of the previous note.
- **the signed PDF of a document NOT yet completed returns 404** ("Couldn't find the document requested"), not a state refusal: the tool says so rather than letting the caller think the identifier is lost.
- **shape of a refusal**: `{message, meta: {error, message, messages[]}}` — the useful reason is in `meta.messages`.
- **not exercised, spec only** (would have sent real emails, or requires a completed document): a real `send` through the tool, `remind`, real `update_recipients` and `update_authentication`, the signed PDF and the NOM-151 certificate of a completed document, `create_document` from a template, `validate_csv` and bulk send creation, webhook creation and deletion, the API applications.
