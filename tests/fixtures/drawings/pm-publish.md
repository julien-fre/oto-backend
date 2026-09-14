# Publish a product doc to Notion and announce it in Slack

**When to use it**: a triage report, a framing, a set of stories or a PRD has been reviewed and validated as a draft, and it now needs to become the shared version the team reads, with a short announcement pointing to it. Publishing is an explicit step: the draft stays separate, and nothing reaches the wiki or the channel before someone has seen exactly what will land there.

```
              Natural language input in Claude
              "Publish the validated PRD to the product wiki and announce it in the product channel."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Identify the deliverable and the target    │   notion_search
│  The validated version, the parent page or      │   notion_query_database
│  database, and what already lives there.        │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ can't copy     draft page longer than the read
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Build the page and the post                │   slack_list_channels
│  Page blocks and a three-line channel post,     │
│  drafted here and not yet written anywhere.     │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Preview and confirm                        ║
║  Parent, title, tags, channel and the exact     ║
║  post text, then ask: ok to publish?            ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ held back     not confirmed, nothing written
                         ▼  ok on this exact preview
┌─────────────────────────────────────────────────┐
│  4 · Create the page                            │   notion_create_page
│  Created with its first 100 blocks, the rest    │   notion_append_blocks
│  appended in order to that same page.           │
└────────────────────────┬────────────────────────┘
                         ▼  page complete
┌─────────────────────────────────────────────────┐
│  5 · Announce it                                │   slack_post_message
│  One short Slack post carrying the page link,   │   slack_join_channel
│  only once the page is whole.                   │
└─────────────────────────────────────────────────┘

▪ terminal — the run stops there and nothing is published
```

## 1. Identify the deliverable and the target
- **The deliverable** is the validated version, not the latest edit. If it lives in the conversation, take it from there. If it's a draft page, read it with <tool:notion_get_blocks> `recursive: true` so nested lists and toggles come with it. The tool reads 100 blocks per level and doesn't page: if the response has `has_more: true`, or a nested block comes back with exactly 100 children, the page is longer than it can copy. Stop there and take the deliverable from the conversation if it's there, or say the page is too long to copy; never publish a truncated one.
- <tool:notion_search> `filter_type: "page"` or `"database"` to find the parent **[your team wiki page or product docs database]**. Search only sees what's shared with the integration: if the parent doesn't come back, ask its owner to share it rather than picking a page with a similar name.
- If the parent is a database, read a few existing rows with <tool:notion_query_database> `page_size: 10`: the database schema call doesn't return columns, the rows do. The title column is the property whose `type` is `title`. The create call writes the title into a column named `Name`, so a database whose title column is called anything else refuses the page; an empty database gives no row to check. In both cases publish under a parent page instead.
- The same rows show the tag values in use. A near-miss spelling silently creates a new option, and options no row uses can't be seen this way, so the tags go into the preview for a person to confirm.
- <tool:notion_search> the exact title and check each result's parent. If the same deliverable is already published there, stop and ask: publish a new page and archive the old one, or leave things as they are.

## 2. Build the page and the post
- **The blocks.** Blocks read back from a draft page can't be sent again as they are: strip `id`, timestamps, `has_children` and the other read-only fields, re-nest children under their parent, and keep to two levels of nesting per request: set aside any children below the second level, step 4 appends them. Block types the API reports as `unsupported` can't be recreated; list them for the preview instead of dropping them silently. Images and files hosted by Notion come back with signed URLs that expire, so they can't be sent back either: list them as not carried over, or re-send one as `external` only when it has a stable public URL. Split any text run over 2,000 characters.
- **The post.** Three lines: what the document is with a placeholder for the page link, the one decision or ask it carries, and who should read it. <tool:slack_list_channels> `types: "public_channel,private_channel"` resolves the channel's name to the ID the post needs; a private channel only appears if the connected account is a member. The list comes back as a single page, so in a large workspace the channel may be missing: ask for its ID rather than picking a similar name. If the channel's `is_member` is false, a public channel needs <tool:slack_join_channel> before the post (it goes in the preview), and a private one needs someone to `/invite` the app. To mention an owner, get their ID with <tool:slack_find_user_by_email> and write `<@ID>`: a typed "@name" posts as plain text and notifies nobody.
- Nothing is written during this step. Both artifacts exist only in the conversation.

## 3. Preview and confirm
Show, in one message: the parent (with its path), the page title, the properties and tags, the number of blocks and anything that couldn't be carried over, the channel (and whether the app joins it first), the exact post text, and the other writes the run will make: the older page to archive, if any, and the draft's status change. Then ask **"ok to publish?"**
- The answer can be partial: "publish, don't announce" is a valid ok.
- Any edit to the title, the tags or the post text means showing the preview again. An ok covers the preview it answered, not a later version of it.
- No answer means nothing happens.

## 4. Create the page
- <tool:notion_create_page> with `parent_type` and `parent_id` from step 1, the title, the properties, and the first 100 blocks in `content`: Notion accepts at most 100 blocks per request.
- **Keep the page id the moment the create call returns.** <tool:notion_append_blocks> the remaining blocks in batches of 100, in order, to that page id. If a batch fails, resume from that batch on the same page. Re-running the whole step creates a second, half-finished copy.
- Children set aside in step 2 go in follow-up <tool:notion_append_blocks> calls whose `page_id` is the id of their created parent block (the tool accepts a block id). Responses only list the top level they added, so read that id with <tool:notion_get_blocks> on the block one level up.
- **Check completeness from the write responses, not a read-back.** The create call accepts its `content` whole or refuses it, and each append returns the blocks it added in `results`: add those up and compare with the top-level count built in step 2. Reading the page back stops at 100 blocks, so on a long page it always looks short and invites re-appending blocks that are already there. A page missing its second half is not ready to be announced.
- If the preview agreed to replace an older page, archive it now with <tool:notion_update_page> `archived: true`. Archiving can be undone; deleting is never part of this process. If the draft sits in a database with a status property, mark it published and link the new page with <tool:notion_update_page> `properties`, as the preview showed.

## 5. Announce it
- If the preview included joining the channel, <tool:slack_join_channel> with its ID first; without it the post fails `not_in_channel`.
- <tool:slack_post_message> to the channel ID from step 2, with the page URL returned by the create call in place of the placeholder. Post once, in the channel or as a reply in an existing discussion with `thread_ts`.
- A Slack message can be deleted but not edited, which is why the exact text went through the preview. If the response carries `split_into`, the text was long enough to be split into threaded parts, not truncated: don't post it again.

## Output
The page link, the channel and the message link, the number of blocks published, any blocks that couldn't be carried over, and anything the confirmation held back (for instance, published but not announced).