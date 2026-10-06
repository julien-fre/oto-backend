## prerequisite — cloro api key

create an API key in [Cloro](https://cloro.dev), then paste it into oto.
- members consume a platform quota if no personal/org key is set

## usage — ai-search monitoring + google serp as json

queries the AI engines (ChatGPT, Gemini, Perplexity, Copilot, Grok, Google AI Mode) and captures their answers + sources — "AI SEO" brand monitoring — plus Google SERP/News as clean JSON. (AI engine calls take ~30-45 s.)
- "what does ChatGPT say about brand X?" (answer + citations)
- "compare what Gemini and Perplexity say about this product"
- "Google SERP for `best CRM` with the AI Overview"
- "Google News on this company"

## note — project perimeter (#605, 2026-08-29)

under a project with `excluded_url_prefixes`, `cloro_google` (serp, news) and `cloro_ask` (sources/citations) drop the matching results and say so (`excluded_by_perimeter`). an AI engine's answer is prose: not filtered. details: `docs/projects.md`.
