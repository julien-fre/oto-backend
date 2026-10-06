## prerequisite — create the Salesforce application

Salesforce has no OAuth client shared between customers (unlike Google): each org must create its own. Allow about fifteen minutes, and an administrator profile.

Two models coexist. **Connected Apps are disabled on recent orgs** (Salesforce answers "contact Support") — in that case it is an **External Client App**, and the paths below account for that.

**1. Create the application.** Setup → quick search "**External Client Apps**" → **New External Client App**, then enable **OAuth**.

**2. Callback URL.** The one shown on this card, under "Authorize oto at Salesforce". Copy it exactly as is: a stray space or trailing slash is enough to make the consent fail.

**3. OAuth scopes.** Tick **`api`** (Manage user data via APIs) **and `refresh_token`** (Perform requests at any time).

⚠️ **`full` is not enough.** Full access does NOT include `refresh_token` / `offline_access`, which is a separate scope. Without it, the consent may succeed but Salesforce issues no durable token — failure with `invalid_scope`. This is the most common trap.

**4. Retrieve the credentials.** **Settings** tab → **OAuth Settings** → **Consumer Details**. Salesforce sends a verification code by email before revealing them. Note the **consumer key** and the **consumer secret**.

**5. Allow server calls.** **Policies** tab → **IP Relaxation** → "**Relax IP restrictions**".

⚠️ **The least intuitive trap.** You give your consent from your browser, but it is **our server** that then refreshes the token, from a different address. With restrictions applied, Salesforce refuses these calls with an `invalid_grant` whose wording is misleading ("expired token") even though the token is valid. If your policy forbids relaxing, allow the address `151.115.148.128` in your approved IP ranges instead.

## setup — connect oto

1. Paste the **consumer key**, the **consumer secret** and the **Login URL** on this card. The Login URL is your domain: `https://<your-domain>.my.salesforce.com` — not `login.salesforce.com` if you have a My Domain, and **without the `-setup`** of the console domain. For a sandbox: `https://<domain>.sandbox.my.salesforce.com`.

2. Saving is accepted **even if the connection is not complete yet**: this is normal, the token doesn't exist yet.

3. Click **Authorize oto at Salesforce** and choose who the connection is stored for — you, your team or the whole org. The last two require being an administrator, and read the saved application **at that level**: to connect on behalf of the org, the credentials must have been set at the org level.

It is this consent that produces the token; there is no field to fill in by hand.

## note — token rotation

Salesforce enforces **refresh token rotation** on external applications — a locked control, changeable only by their support. Each call consumes the token and receives a new one.

Nothing to do on your side, oto handles it. It is mentioned because it explains behavior that might seem abnormal: the stored token changes constantly, and reusing an old token makes Salesforce revoke the whole connection, requiring a new consent.

## usage — contacts, accounts, leads, opportunities

Generic CRUD per sObject (Contact, Account = "companies", Lead, Opportunity, custom objects) + raw SOQL/SOSL.
- "list the contacts of the Acme account"
- "create a contact Ada Lovelace at Acme Corp"
- "find open opportunities over €50k"
- "add a note to this account"

⚠️ **oto does not apply Salesforce's de-duplication, neither in bulk nor one at a time.** Whether your duplicate rules fire depends on YOUR Salesforce org's setup; oto doesn't enable them and doesn't verify that they fired. A bulk creation has already returned "success" for records that were exact duplicates of existing ones (same name, same account). A procedure that treats these rules as a safety net must check existence itself before creating. And even with active rules, Salesforce compares the records of a batch to what already exists, never with each other: a batch containing the same person twice goes through whole.
