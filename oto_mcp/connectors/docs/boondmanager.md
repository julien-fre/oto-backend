## prerequisite — boondmanager client token + client key + user token

a Boond admin copies the **client token** and the **client key** from the administrator interface (dashboard, developer / API area), then the user the calls will act as copies their **user token** (settings → security). All three are pasted into oto. Reference: [Boond API authentication](https://doc.boondmanager.com/api-externe/).
- ⚠️ **REST API access must be enabled** in the Boond account (admin dashboard or settings → security), otherwise every call is refused
- calls act with **the rights of the user** behind the user token: what they cannot see in Boond, the connector cannot see
- byo-only: it is your company's CRM, each organization sets up its own tokens
- ⚠️ Boond counts API calls **per month** (500 per manager on the Core plan, more on higher plans): every tool call consumes one

## usage — an IT-services firm's CRM

- "find this contact" → `boondmanager_search(entity="contacts", keywords="dupont")`, or by email: `keywords_type="emails"`
- "the contacts of this company" → `boondmanager_search(entity="contacts", keywords="CSOC<id>")`
- "the history with this contact" → `boondmanager_search(entity="actions", keywords="CCON<id>", sort="startDate", order="desc")`
- "open opportunities" → `boondmanager_dictionary(path="setting.state.opportunity")` for the state id, then `boondmanager_search(entity="opportunities", filters={"opportunityStates": [id]})`
- "the full record" → `boondmanager_get(entity="contacts", record_id=…)`
- "add this contact" → search first (Boond does not deduplicate), find or create the company, then `boondmanager_create(entity="contacts", attributes={…}, relationships={"company": {"type": "company", "id": …}})` — **dry-run by default**, `dry_run=false` to create
- "log this exchange" → `boondmanager_create(entity="actions", attributes={"typeOf": <id>, "text": "…"}, relationships={"dependsOn": {"type": "contact", "id": …}})`, the type id coming from `boondmanager_dictionary(path="setting.action.contact")`; action dates in the format `2026-10-02T09:30:00+0200`
- the connector **never modifies or deletes** anything in Boond

## note — what misleads

- ⚠️ **no contact without a company** in Boond: the company first
- states, types, origins and action types are **account-specific ids**: always read them from the dictionary, never guess them
- an id prefix the entity does not know would make Boond answer 0 rows without an error: the connector refuses it and says which ones are accepted
- `max_results` above 500 (100 for actions) would make Boond fall back to 30 without saying so: the connector refuses it
- invalid tokens, or an API access that is not enabled, come back from Boond as a **422** ("unable to load agency key"), not a 401: the connector translates it into an access refusal
- any attribute or relationship Boond does not know is refused **before** the call, with the accepted list: the connector's rules are those of the creation schemas in the Boond reference
- an action attaches to a contact, an opportunity, a project… but **not to a company** (`dependsOn` does not accept `company`)
