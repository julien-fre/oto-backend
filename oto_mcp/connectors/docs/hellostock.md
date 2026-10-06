## prerequisite — your HelloStock API token, from an administrator account

create a token at hellostock.fr → My account → Settings → API tokens (in the French UI: *Mon espace → Réglages → Jetons d'API*), then paste it into oto. It is only shown once; it starts with `hs_`.
- **one token per person**: it carries the rights of THEIR account, and what is done with it is done in their name (a request send is recorded under the name of the administrator whose token it is). No org token or shared oto key.
- **the account must be an administrator of the marketplace**: otherwise HelloStock answers 403, and recreating a token changes nothing — it is the account's role that is missing. The role is re-read on every call: a demoted account goes to 403 without the token changing.
- a revoked (or unknown) token answers 401: create a new one, then replace the old one on the card.
- the "test the connection" button reads a request: it tests both conditions at once.

## usage — the weekly review of the marketplace

- "this week's requests" → `hellostock_demande(since="2026-09-01")`; a whole record (positionings and sends included) → `op="get"`
- "the offers without keywords" → `hellostock_offre(certificat="sans-mots-cles")`, then `op="get"` on each: `certificat.verdictDetail` returns the values read on the certificate
- "who can answer this request?" → read the request, then `hellostock_membre(service=…, sector=…)` and `hellostock_offre(matiere=…, departement=…)`; the assistant proposes the matches, HelloStock computes none
- "who positioned themselves?" → `hellostock_positionnements(demande_id=…)`
- "send it to these three suppliers" → `hellostock_demande_send(demande_id=…, user_ids=[…])` first returns a **preview**; the send goes out with `dry_run=False`
- "write these keywords" → `hellostock_offre_update(offre_id=…, keywords=[…])`
- "set it to published" → `hellostock_demande_set_status` / `hellostock_offre_update(status=…)`

lists return `{items, nextCursor, total}`: `total` counts everything matching the filters, `cursor` resumes where the page stops. They are projected (the removed columns are named in `projection`); `full=True` returns everything.

## note — what each write triggers for third parties

- `hellostock_demande_send` **writes to real people**, from HelloStock's address: the request's specs (material, grade, format, dimensions, thickness, quantity, deadline, certificate), the accompanying note as-is, and two links to the request's page — never the buyer's identity, reference or comment. **Preview by default.** A member who already received the request is refused, unless `allow_resend=True`: HelloStock itself would resend without saying anything.
- the email's links open the request's public page, which displays **only published requests**: sending an unpublished request makes the recipients land on « Demande indisponible ». The preview flags it.
- `noop: true` in the response: HelloStock's mailer is not configured — **no email went out**, and yet the sends are recorded.
- `hellostock_demande_set_status` / `hellostock_offre_update(status=…)`: `published` is visible on the public marketplace **immediately**, any other status takes it off; all transitions are allowed. No email.
- `hellostock_offre_update(keywords=…)` **replaces** the list, and it is **public** (search). HelloStock normalizes (lowercase, spaces, duplicates) and refuses the entire list if a term names a steelmaker, a heat number or an order number — including when the certificate shows them. The response returns what is actually stored.

## note — verification status

written against the OpenAPI 3.1 contract of the admin API (`1.0.0`, served by `GET /api/admin/openapi.json`) and verified against it: every route and every parameter sent by the client exist in the contract, and the value lists (statuses, materials, certificate, sectors) are those it publishes.

**tested on a fake server that replays the contract**, not yet against hellostock.fr: pagination, refused filters, 401/403, the three writes. **Not exercised for real**: everything that depends on real data (size of the free fields, exact shape of a `verdictDetail`), and the sending email. The first try once a token is set: "test the connection", then `hellostock_demande(limit=1)`.

**out of reach here, on purpose**: deleting a member, modifying the site content, downloading a positioning's quote. `certificat.url` points to a site session page: it does not open with the token.
