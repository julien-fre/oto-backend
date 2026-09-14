# Write user stories from a Notion spec and file them in Linear

**When to use it**: the team has framed a problem and chosen which solution path to pursue, and engineering needs something it can build and test against. This turns the framing page into stories that each carry their "why" and a verifiable acceptance criterion, then files them under one project after a person has read the set. Stories written from a raw request, with no framing behind them, drift toward whatever the loudest customer asked for.

```
              Natural language input in Claude
              "Write the user stories for the report export problem we just framed."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Read the framing page                      │   notion_search
│  The problem sentence, who suffers and the      │   notion_get_page
│  solution path the team actually chose.         │   notion_get_blocks
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no framing      frame the problem first
                         ▼  a framing with one chosen path
┌─────────────────────────────────────────────────┐
│  2 · Check what is already filed                │   linear_team
│  The project that owns the area, and any        │   linear_project
│  story already filed from this page.            │   linear_issue
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Write and split the stories                │
│  Role, action and benefit, each story small     │
│  enough to show in a single demo.               │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Attach acceptance criteria                 │
│  Given, when, then: one observable result       │
│  per criterion, and one test for each.          │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Review before filing                       ║
║  A person approves the stories, project and     ║
║  labels before any issue exists.                ║
╚════════════════════════╤════════════════════════╝
                         ▼  approved set
┌─────────────────────────────────────────────────┐
│  6 · File them under one project                │   linear_project
│  Reuse or create the parent, then one sub-issue │   linear_label
│  per unfiled story, with its criteria.          │   linear_issue
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Link the stories back                      │   notion_get_blocks
│  Add every issue the page doesn't list yet,     │   notion_append_blocks
│  so its readers find all the work it produced.  │
└─────────────────────────────────────────────────┘

▪ terminal — no story is written until a framing with a chosen path exists
```

## 1. Read the framing page
- <tool:notion_search> — the problem's short name, `filter_type="page"`. If several pages match, take the one the request points to rather than the most recently edited: a revision section appended to an older page can make the wrong page look newest.
- <tool:notion_get_page> — the page's properties. If your framing pages carry a status (**[your approved status]**), read it here. A draft framing is not a basis for stories.
- <tool:notion_get_blocks> — `recursive=true`. Without it, anything nested inside a toggle or an indented list comes back as a parent block with its children missing, and solution paths are commonly written exactly like that.
- Pull out five things: the problem sentence, who suffers (these become the story roles), the **one chosen solution path**, what already exists (when the framing found the feature is already there but hard to find, write discoverability stories, not a rebuild), and the evidence quotes.
- **Stop when there is no framing, or no chosen path.** Stories for every candidate path are a menu, not a plan, and choosing between them is the team's call, not this process's. Say what is missing and point to the framing process.

## 2. Check what is already filed
- <tool:linear_team> — `op=list`, to resolve **[your owning team]** to its id. The project list below and every create in step 6 take the `team_id`, not the team's name.
- <tool:linear_project> — `op=list` with that `team_id`, to find a project that already covers this area. Stories spread across two projects for the same problem get planned twice or not at all. A project an earlier, interrupted run created carries the problem's short name, so this list finds it too.
- <tool:linear_issue> — **the framing page's id is the idempotency key.** Step 6 writes it into every description as a plain line of its own: `Framing parent: [page id]` on the parent issue, `Framing: [page id]` on each story. When a project was found, `op=list` with its `project_id`, walking `after` to the end, and read those lines in each description. The parent is the issue with the `Framing parent:` line (it also has no `parent` of its own); the stories are the issues with the `Framing:` line. Listing the project, rather than searching the text for the id, doesn't depend on full-text search matching a hyphenated id, or on an issue created a minute ago already being findable, which is exactly the situation of a rerun right after a partial failure.
- Keep the parent issue, when there is one, and the titles of the stories already filed. Step 5 matches them to the new set by title.
- <tool:linear_issue> — `op=search` as well, to catch stories somebody wrote by hand, outside the project or without the `Framing:` line. Search matches the query as one piece of text against titles and descriptions, so a three-word query only finds issues containing that exact phrase. Run one `op=search` per key term of the problem's short name (or per short phrase that would appear word for word), merge the hits by issue id, and drop the ones already found through the `Framing:` line. The rest are shown at review, not skipped automatically.

## 3. Write and split the stories
- **Story**: *As a [role], I want [action] so that [benefit].* The role comes from the framing's "who suffers", a real role rather than "a user". The benefit traces back to the problem sentence. A story whose benefit you can't connect to the problem doesn't belong in this set.
- **INVEST**, checked story by story: Independent (it can ship without another story in the set), Negotiable (it states the need, not the implementation), Valuable (a person in that role would notice it), Estimable, Small, Testable.
- **Split anything that can't be shown in one demo.** Split along the workflow's steps, along business-rule or data variations, or happy path first and edge cases after. Never split by technical layer: "the backend part" is not something a person in that role can see. More than five or six acceptance criteria on one story usually means it is two stories.
- Each story that comes out of a split carries its own benefit. "Part 2" is not a why.

## 4. Attach acceptance criteria
- *Given [context], when [action], then [observable result].* One criterion is one test.
- The "then" must be something a tester can observe. "Works correctly" is not observable, and "loads fast" needs **[your threshold]** written in.
- Give every story at least one unhappy path: the empty state, the missing permission, the input that is too large.
- Next to each story, keep the quote or account from the framing that it serves, so the reason survives in the tracker once the framing page stops being read.

## 5. Review before filing
Show the whole set before anything is created: each story, its criteria, a rough size, the project it will go into (the existing one from step 2 or a new one), its labels, and whether the parent issue will be reused or created. For each story, show whether it was matched by title to an issue step 2 found already filed, and to which one, so a wrong pairing is caught here instead of leaving a story unfiled or filing it twice. Hand-written issues from the short-name search sit next to the story they seem to cover, for the reviewer to decide. The reviewer edits, merges or drops stories here. Nothing reaches the tracker before an explicit OK. Issues created in bulk are tedious to undo, and a backlog full of unreviewed stories is noise the team learns to ignore.

## 6. File them under one project
- <tool:linear_project> — reuse the project from step 2. If there is none, `op=create` with `team_ids` and the problem's short name. A `status_id` has to be the id of one of the workspace's configured project statuses, read from an existing project's `status.id` with `op=get` or `op=list`. Linear has no free-text state, so a guessed value is refused.
- <tool:linear_label> — `op=list` without `team_id`, walking `after`. A list scoped to the team leaves out workspace labels, which have no team, so a story label defined for the whole workspace would look missing and get recreated at team level. Match by name among the labels whose `team` is empty or is this team, and reuse the one your team already puts on stories. Create one only if none matches: every run that invents its own label adds to label sprawl.
- <tool:linear_issue> — **reuse the parent issue step 2 found; create it only when none exists**, with `op=create` in the project, titled with the problem's short name, its description holding the problem sentence, the framing page's link, and `Framing parent: [page id]` on a line of its own. Then one `op=create` per story not already filed, with `parent_id` set to that parent (`parent_id` is only accepted on create, so set it now), plus `project_id`, `team_id` and `label_ids`.
  - **Title**: the action in a few words, not "As a…". The full story sentence goes in the description.
  - **Description**: the story, the acceptance criteria as a checklist, the evidence quote, the framing page's link, and `Framing: [page id]` on a line of its own (the key step 2 reads).
  - **Leave priority, estimate, assignee and cycle unset.** Those are planning decisions, not filing ones.
- Create the parent first, then the stories one at a time, and keep each returned identifier. If a create fails partway, the rerun's step 2 lists the project, finds the parent and the stories that landed, and step 6 files only the rest under that same parent, instead of opening a second parent and splitting one set across two, or duplicating the stories that went through. Step 7 then links every story the page is still missing, including the ones the first run filed.

## 7. Link the stories back
- <tool:notion_get_blocks> — `recursive=true` on the framing page again, just before writing: the review in step 5 can take a while, and someone may have edited the page since step 1. Find the "User stories" heading if there is one, and collect every issue identifier already listed on the page.
- **What the page already lists is the dedup key, not what this run created.** The set to link is every issue in the project carrying this page's `Framing parent:` or `Framing:` line: the ones step 2 found plus the ones step 6 just created. Link each one whose identifier is not on the page yet, matching on the identifier rather than the title, which people edit in the tracker. Keying on "created this run" loses links in two ways: a rerun after a failed create never links the stories the first run filed, and a rerun after a failed append creates nothing, so it links nothing.
- <tool:notion_append_blocks> — one call, one item per missing issue (identifier, title, link). This tool always appends at the end of the page. When the page has no "User stories" heading yet, the call starts with that heading. When the heading already exists, other content may sit below it by now, so the call starts with a short line, "More stories, added **[date]**", and the new items follow it. Nothing already listed is written twice. When nothing is missing, skip the call.

## Output
The project and the parent issue, each marked reused or created; every story filed, with its identifier and number of criteria; the stories skipped because step 2 found them already filed; the stories changed or dropped at review; and, for the framing page, its link plus which issue links were added this run and which were already there. When the run stopped at step 1: whether the framing or the chosen path was missing.