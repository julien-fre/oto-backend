## prerequisite — your promptwatch api key

promptwatch exposes one api key per project (or per organization). go to **settings → api keys** on the [promptwatch dashboard](https://promptwatch.com), and create a key.
- paste it into oto on your account (`/account`), **promptwatch** connector
- byo only: your key or your org's shared one, no platform key (no otomata↔promptwatch commercial agreement)
- if your key is **org-level** (covers several projects), also fill in the **project id** — use `promptwatch_project` to list the accessible projects and retrieve its id. a **project-level** key is already scoped, leave this field empty.

## usage — your brand's ai visibility

tracks how your brand/product appears in the answers of chatgpt, claude, gemini… over a set of prompts organized into monitors, with visibility/sentiment/citations analytics and content generation to fill the gaps.
- "create a monitor to track "crm alternatives"" → `promptwatch_monitor` (op `create`), then `promptwatch_prompt` (op `create` or `bulk_create`) to attach prompts to it
- "how is our visibility evolving this month" → `promptwatch_visibility` (op `time_series`), and `op="competitor_heatmap"` to compare against competitors
- "what do ai answers say about us, positive or negative" → `promptwatch_response` (op `sentiment_distribution` or `sentiment_time_series`)
- "which sites are cited most often" → `promptwatch_citation` (op `top_pages`, `domains_over_time`, `llm_sources`…)
- "which prompts aren't covered yet, and suggest content" → `promptwatch_content` (op `gap_prompts`, `gap_recommendations`, then `create` in CREATE or OPTIMIZE mode)
- "publish this content on our cms" → `promptwatch_publishing` (op `push_draft` then `publish_live`, after listing the cms connections with `op="list_connections"`)
- "where do the content slots planned by the agent stand" → `promptwatch_content_agent` (op `list_slots`, `accept_slot`, `publish_slot_now`)
- "are competitor ads showing up in ai answers?" → `promptwatch_ads`, and for e-commerce (product position, top merchants) → `promptwatch_shopping`
- "organize my prompts by tag or by topic" → `promptwatch_taxonomy` (tags/topics), `promptwatch_persona` (audience angle) and `promptwatch_brand` (tracked competitors)
- "tracking of specific pages (ours or a competitor's) cited by ai" → `promptwatch_page_tracker`, distinct from the site crawl (`promptwatch_sitemap`, seo health included)
