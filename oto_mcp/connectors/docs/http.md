## prerequisite — an API key, and where the API lives

the Custom API connector connects a tool that has **no card of its own** in the catalogue (a payroll, an ERP, an internal service) as long as it offers an API. Gather, from the tool's API documentation:
- the **base URL** of its API (e.g. `https://api.acme.test/v1`) — the paths of its documentation are relative to it
- how it authenticates: a bearer token, a key in a header (and the header's name) or in a query parameter, a basic login, OAuth2 client credentials, or nothing
- the key itself, created in the tool's settings — **paste it only in this form**: never in a chat, never in an email. If a key was pasted in a conversation, revoke it at the provider and create a new one
- optionally, the path of its OpenAPI description in JSON (e.g. `/openapi.json`), to import its operations

## setup — connect it, or have it connected

- fill in the card yourself, or ask the agent: `http(op="setup_link", label="Acme Payroll", base_url=…, auth_mode="bearer")` returns a link to this form, pre-filled with everything **except** the key, to send to whoever holds the key
- several APIs, or several accounts of the same one: each is a named account of the card, chosen on every call with `_account`
- an internal address (loopback, private network) is refused until the platform operator declares it as a named exception

## usage — configure it once, every member uses it

- the org admin configures the tool **once**: the card (base URL, auth, key), then its **definition** on this page or via the agent with `http_act(op="set_definition")` — a display name, a `description` of what the tool holds (read by every member's agent), a logo domain, constant non-secret `headers` (e.g. an API version), saved `variables` (e.g. your organization id, filled into any action argument of the same name) and its **actions**
- an action: `name` (snake_case), `description` (what the agent reads to choose it), `method` (GET, POST, PUT, PATCH, DELETE), `path` relative to the base URL with `{param}` placeholders, `input` (a JSON schema of the arguments), and optionally `in` to send an argument to `path`, `query` or `body` — by default a placeholder goes in the path, the rest in the query for GET/DELETE and in the body otherwise
- import actions instead of typing them: `http_act(op="import", source="doc_path"|"url"|"inline")` converts an OpenAPI/Swagger JSON document into candidates without saving them
- every member's agent then finds it by name (`oto_connector(name="Acme Payroll")`), lists the org's custom connectors with `http(op="list")`, and calls an action with `http_act(op="call", operation="list_collaborators", args={"page": 1})` — `_account` is only needed when two custom connectors share an action name; `dry_run=true` shows the request without sending it
- for anything no action describes: `http_act(op="request", method="PATCH", path="/…", body={…})`, or `http_get`/`http_post`; `http_doc()` reads the configured documentation route
- two tools: `http` only reads (the custom connectors, an action's input, the setup link); `http_act` calls the API, imports and saves — the one your MCP client asks you to confirm
- reuse a definition in another org: export it from this page (never a secret), connect the tool there with its own key, then import the definition

## note — what the connector does and does not do

- oto calls the API directly with the key you stored: what a POST, PUT, PATCH or DELETE does is decided by the target API and the rights of the key — give it the narrowest rights the tool allows
- redirects are never followed (the key would leave with them); header and cookie parameters, form bodies and YAML documents are not imported — they are listed as skipped, with the reason
