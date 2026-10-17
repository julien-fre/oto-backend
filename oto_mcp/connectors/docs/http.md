## prerequisite — an API key, and where the API lives

the Custom API connector connects a tool that has **no card of its own** in the catalogue (a payroll, an ERP, an internal service) as long as it offers an API. Gather, from the tool's API documentation:
- the **base URL** of its API (e.g. `https://api.acme.test/v1`) — the paths of its documentation are relative to it
- how it authenticates: a bearer token, a key in a header (and the header's name) or in a query parameter, a basic login, OAuth2 client credentials, or nothing
- the key itself, created in the tool's settings — **paste it only in this form**: never in a chat, never in an email. If a key was pasted in a conversation, revoke it at the provider and create a new one
- optionally, the path of its OpenAPI description in JSON (e.g. `/openapi.json`), to import its operations

## setup — connect it, or have it connected

- fill in the card yourself, or ask the agent: `http_setup_link(label="Acme Payroll", base_url=…, auth_mode="bearer")` returns a link to this form, pre-filled with everything **except** the key, to send to whoever holds the key
- several APIs, or several accounts of the same one: each is a named account of the card, chosen on every call with `_account`
- an internal address (loopback, private network) is refused until the platform operator declares it as a named exception

## usage — call the API

- by path: `http_get(path="/organizations")`, `http_post(path="/search", body={…})`; `http_doc()` reads the configured documentation route
- by name, once operations are described: `http_operations()` lists them (name, description, method, path, input), `http_call(operation="list_collaborators", args={"organizationId": "…", "page": 1})` calls one — arguments are checked against its input, path parameters are encoded segment by segment, and `dry_run=true` shows the request without sending it
- **describe the operations** on this page (org admin), or via the agent: `http_operations(op="import", source="doc_path"|"url"|"inline")` converts an OpenAPI/Swagger JSON document into candidates without saving them; `http_set_operations(operations=[…])` saves the list (it replaces the previous one, 100 operations max)
- an operation: `name` (snake_case), `description` (what the agent reads to choose it), `method` (GET, POST, PUT, PATCH, DELETE), `path` relative to the base URL with `{param}` placeholders, `input` (a JSON schema of the arguments), and optionally `in` to send an argument to `path`, `query` or `body` — by default a placeholder goes in the path, the rest in the query for GET/DELETE and in the body otherwise

## note — what the connector does and does not do

- oto calls the API directly with the key you stored: what a POST, PUT, PATCH or DELETE does is decided by the target API and the rights of the key — give it the narrowest rights the tool allows
- redirects are never followed (the key would leave with them); header and cookie parameters, form bodies and YAML documents are not imported — they are listed as skipped, with the reason
