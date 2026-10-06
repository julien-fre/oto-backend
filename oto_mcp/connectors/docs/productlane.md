## prerequisite — productlane API v2 key

create a key in Productlane (Settings → API), then paste it into oto.
- ⚠️ **a v1 key does not work here.** The v2 API is a separate API, with its own keys; v1 shuts down on **2026-11-20**. If the key is rejected with a 401 even though it "works elsewhere", this is almost always the cause
- byo-only: no shared oto key — these are your customers' conversations, each organization sets up its own
- the key carries **scopes** (`threads:read`, `threads:write`, `contacts:*`, `companies:*`, `projects:*`, `issues:*`, `changelogs:*`, `docs:*`, `tags:*`, `snippets:*`, `portal:read`): a **403** refusal means one is missing, not that the key is bad
- some families require a **plan**: reply snippets and changelog broadcast require Pro, changelog tags and the customer portal require Scale
- ⚠️ **Linear must be connected on the Productlane side** for anything touching the roadmap (projects, issues, links from a thread): it is not a standalone roadmap

## usage — the customer feedback inbox, and what to do with it

- "what are our customers asking for?" → `productlane_threads(op="search", status="open")`, or `productlane_roadmap(op="projects", sort="total_score")` to rank by the weight of attached feedback rather than by date
- "read a whole conversation" → `productlane_threads(op="get", thread_id="…", expand=["messages","comments"])`
- "reply to the customer" → `productlane_threads(op="send", thread_id="…", content="…")` — ⚠️ **this really goes out**, through the channel the thread came from
- "leave a note for the team" → `productlane_threads(op="comment", …)`: visible to teammates only, nothing goes out
- "link this feedback to the roadmap" → `productlane_threads(op="link", thread_id="…", issue_ids=[…])`: this is the gesture that raises a project's score
- "who asked for this?" → `productlane_contacts(op="search", …)` then `op="issues"` / `op="projects"` on the contact
- "publish a release note" → `productlane_changelogs(op="create", fields={…})`, then `op="update"` with `{"published": true}` to make it visible
- "notify subscribers" → `productlane_changelogs(op="broadcast", changelog_id="…", email=true, dry_run=false)`
- "what our online help says" → `productlane_docs(op="articles", title_contains="…")`
- check what the key is allowed to do, before running into it → `productlane_workspace(op="me")`: it returns the granted scopes and requires no right

## note — publishing is not broadcasting

⚠️ **`op="broadcast"` has NO effect on `published`** — the vendor spells it out. The two gestures are independent, and mixing them up is costly in both directions:
- broadcasting an **unpublished** changelog sends your subscribers a link to an invisible page
- publishing without broadcasting notifies no one

publish = `productlane_changelogs(op="update", fields={"published": true})`. broadcast = `op="broadcast"`, which is **dry-run by default**: you need `dry_run=false` for anything to go out, and there is no cancel or recall.

## note — the roadmap is a mirror of Linear

⚠️ **a write can succeed here while the Linear sync fails**: the vendor logs it on its side and **does not surface it in the response**. A success on `update_project` / `update_issue` therefore does not prove that Linear followed.
- **creation** starts from Linear (the issue is filed there first): without Linear connected, it fails outright
- `team_id`, `state_id`, `assignee_id`, `linear_status_id` are **Linear** identifiers — read them via `productlane_roadmap(op="workflows", team_id="…")` and `op="statuses"`, never hardcode them
- on an issue, `status` is not a fixed enum: these are the Linear team's workflow states, specific to each workspace
- `priority` follows Linear numbering: **`0` = no priority, `1` = urgent**, then 2, 3, 4 in decreasing urgency. It is not an ascending scale

## note — three other pitfalls

- ⚠️ **`productlane_contacts(op="block", block_type="DOMAIN")` cuts off an entire organization** in a single call, and the sender is not told. `"EMAIL"` targets only one address
- ⚠️ **`productlane_companies(op="merge")` is irreversible, and direction matters**: the `company_id` company survives, the one in `source_id` is deleted
- ⚠️ **`productlane_docs(op="accept")` can answer `superseded`** instead of `accepted`: the draft no longer applies cleanly, the article having moved underneath it. It is an HTTP success that **applied nothing** — read the returned status, not just the absence of an error
