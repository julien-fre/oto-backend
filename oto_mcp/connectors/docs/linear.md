## prerequisite — linear api key

create a personal API key in Linear (Settings → Security & access → Personal API keys — see the [developer docs](https://linear.app/developers)), then paste it into oto.
- personal key or org key: the Linear API key acts on behalf of its holder. Set it in your settings for yourself only, or at the org level if it must serve everyone. No shared oto key.
- limits: 5,000 requests/hour and 3,000,000 complexity points/hour per key

## usage — issues, projects, cycles, teams, labels, comments, webhooks

linear manages the team's ticket/project tracking, in 8 tools:
- "list my in-progress issues on the Product team" → `linear_issue(op="list", team_id="...", state_id="...")`
- "search for issues about billing" → `linear_issue(op="search", query="billing")`
- "create a bug ticket for X" → `linear_issue(op="create", title="...", team_id="...", description="...")`
- "assign this issue to Jane and set it to in progress" → `linear_issue(op="update", issue_id="...", assignee_id="...", state_id="...")`
- "what statuses are possible on the Product team?" → `linear_team(op="states", team_id="...")` (do this before an `update` that changes the status — you need the `state_id`)
- "comment on this issue" → `linear_comment(op="create", issue_id="...", body="...")`
- "the Product team's projects" → `linear_project(op="list", team_id="...")`
- "create a project, then move it to a given status" → `linear_project(op="create", name="...", team_ids=["..."])` then `op="update", status_id="..."` (`status_id` — not a free-text status, read it from an existing project via `op="get"|"list"`)
- "delete this test project/label" → `linear_project(op="delete", project_id="...")` / `linear_label(op="delete", label_id="...")`
- "the current sprint" → `linear_cycle(op="list", team_id="...")`
- "what labels exist?" → `linear_label(op="list", team_id="...")`
- "who am I on Linear?" → `linear_user(op="viewer")`
- "notify this endpoint on every new issue" → `linear_webhook(op="create", url="...", team_id="...", resource_types=["Issue"])` (`resource_types` is required by Linear, not just `url`)

## note — tested live, 6 bugs fixed

Built from the Linear developer documentation (GraphQL, no OpenAPI spec — Linear exposes a GraphQL schema, not a REST contract), then **tested live on 2026-08-21** against a real workspace (GraphQL introspection + full create/get/update/delete/archive cycle on issues, comments, projects, labels, webhooks, all cleaned up afterwards). 6 real bugs found and fixed, none detectable through introspection alone:
- `Project` has no `state` field for reading (it is `status`, an object) nor for writing (`ProjectCreateInput`/`ProjectUpdateInput` only have `statusId`) — hence `status_id` rather than a free-text status on `linear_project`.
- A GraphQL operation must NEVER declare a variable it does not reference — an omitted optional filter made `list_issues`/`list_projects`/`list_cycles`/`list_labels`/`search` fail as soon as ONE filter among several was absent.
- `Query.webhooks` accepts no `filter`/`teamId` argument — filtering by team goes through `team(id:){webhooks{...}}`.
- `issueSearch` exists in the schema but is dead in practice ("This endpoint deprecated.") — `linear_issue(op="search")` now goes through `issues(filter:{searchableContent:{contains:...}})`.
- The team filter of `linear_project(op="list")` needs the `some:` wrapper (`accessibleTeams` is a collection, not a simple filter) — confirmed against a real project.
- Resolving an issue by its readable identifier (`"ENG-123"`) works, confirmed live — identical to the UUID.

## note — no `Bearer` prefix

Unlike most key-based APIs of this connector (Fireflies, Grain, Granola…), Linear expects the raw key in the `Authorization` header, without a `Bearer` prefix — a specificity documented by Linear itself, not an empirical discovery.
