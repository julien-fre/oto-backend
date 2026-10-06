"""Cloro — AI-search monitoring & Google SERP as JSON (cloro.dev).

Wraps `oto.tools.cloro.CloroClient`. Business surfaces:
- **AI engines** (ChatGPT, Gemini, Perplexity, Copilot, Grok, Google AI Mode):
  queries the engine and captures its answer + sources/citations → "AI SEO"
  brand monitoring (what the AI says about a brand/product), competitive intelligence.
- **Google SERP** as JSON (organic + AI Overview + People Also Ask) and **Google
  News**.

**Consolidated surface (ADR 0047 §Amendment)**: 8 tools → 2. The six engine tools
(`cloro_chatgpt`/`cloro_perplexity`/`cloro_gemini`/`cloro_copilot`/`cloro_grok`/
`cloro_ai_mode`) carried **exactly the same parameters** — the engine is not
a verb but a **variant** of the same operation → `cloro_ask(engine=…)`. And
`cloro_google_serp`/`cloro_google_news` query the same object (Google) with the
same `query`/`country` → `cloro_google(op=…)`, the three include flags
applying only to the SERP. The two tools stay separate: an AI engine takes a
conversational `prompt` and `markdown`/`searchQueries` flags, Google takes a
`query` and `aiOverview`/`organicResults`/`peopleAlsoAsk` flags — disjoint
params, a merge would weigh no less than two tools.

Key resolved per call via `access.resolve_api_key("cloro")`: user/org key, otherwise
platform key + daily quota for members. NB: AI engine calls can take
~30-45 s.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, url_perimeter

# AI engines → (Cloro API slug, human label). The slug is the value of the `engine`
# parameter of `cloro_ask` (⚠️ `aimode`, not `ai_mode`: it is the Cloro API's slug).
_AI_ENGINES = {
    "chatgpt": "ChatGPT (OpenAI)",
    "perplexity": "Perplexity",
    "gemini": "Google Gemini",
    "copilot": "Microsoft Copilot",
    "grok": "Grok (xAI)",
    "aimode": "Google AI Mode",
}


def register(mcp: FastMCP) -> None:
    from oto.tools.cloro.client import CloroClient

    def _client() -> tuple[CloroClient, bool]:
        key, is_platform = access.resolve_api_key("cloro")
        return CloroClient(api_key=key), is_platform

    def _run(method: str, **kwargs) -> dict:
        """Resolves the key, calls the client's method, counts platform usage."""
        client, is_platform = _client()
        result = getattr(client, method)(**kwargs)
        if is_platform:
            access.record_platform_usage("cloro")
        return result

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    # --- AI engines: one tool, the engine as a parameter --------------------

    @mcp.tool()
    def cloro_ask(
        engine: Literal["chatgpt", "perplexity", "gemini", "copilot", "grok",
                        "aimode"],
        prompt: str,
        country: Optional[str] = None,
        markdown: bool = True,
        search_queries: bool = False,
    ) -> dict:
        """Ask an AI search engine and capture its answer + sources/citations
        (AI-search brand monitoring / AI SEO — what the engine says about a brand,
        product or topic).

        `engine` — the engine is a VARIANT of the same call: all six take exactly
        the same parameters, only the answering model changes.
        - **"chatgpt"** : ChatGPT (OpenAI).
        - **"perplexity"** : Perplexity.
        - **"gemini"** : Google Gemini.
        - **"copilot"** : Microsoft Copilot.
        - **"grok"** : Grok (xAI).
        - **"aimode"** : Google AI Mode — ⚠️ the slug is `aimode` (not `ai_mode`).
          This is Google's conversational answer; for the SERP (organic results,
          AI Overview block, People Also Ask) use `cloro_google`.

        ⚠️ AI-engine calls are SLOW: ~30-45 s each (the engine is really queried).
        Under a project with `excluded_url_prefixes`, matching sources are dropped
        and counted.

        Args:
            engine: chatgpt | perplexity | gemini | copilot | grok | aimode.
            prompt: question/query to send to the engine (1-10000 chars).
            country: ISO country code (e.g. 'US', 'FR').
            markdown: return a markdown rendition of the answer.
            search_queries: also return the engine's internal fan-out queries
                (costs extra credits).
        """
        if engine not in _AI_ENGINES:
            valid = ", ".join(f"'{e}'" for e in _AI_ENGINES)
            raise _bad(f"engine must be one of {valid} (received {engine!r})")
        include: dict = {"markdown": markdown}
        if search_queries:
            include["searchQueries"] = True
        return url_perimeter.filter_results(
            _run("monitor", provider=engine, prompt=prompt, country=country,
                 include=include),
            url_perimeter.perimeter_of_call())

    # --- Google SERP / News: one tool, the verb in `op` ---------------------

    @mcp.tool()
    def cloro_google(
        query: str,
        op: Literal["serp", "news"] = "serp",
        country: Optional[str] = None,
        ai_overview: bool = True,
        organic: bool = True,
        people_also_ask: bool = False,
    ) -> dict:
        """Google as clean JSON via Cloro (AI SEO / SERP monitoring).

        `op` :
        - **"serp"** (default) : Google SERP as clean JSON via Cloro (AI SEO / SERP
          monitoring) — organic results, Google's AI Overview block and People Also
          Ask, selected by the three include flags below.
        - **"news"** : Google News as JSON via Cloro. Takes `query` + `country`
          only — the three include flags are SERP-only and do not apply here.

        Under a project with `excluded_url_prefixes`, matching results are dropped
        and counted.

        Args:
            query: search query.
            op: serp (default) | news.
            country: ISO country code (e.g. 'US', 'FR').
            ai_overview: op="serp" — include Google's AI Overview block.
            organic: op="serp" — include organic results.
            people_also_ask: op="serp" — include People Also Ask.
        """
        if op == "serp":
            include = {
                "aiOverview": ai_overview,
                "organicResults": organic,
                "peopleAlsoAsk": people_also_ask,
            }
            result = _run("google", query=query, country=country, include=include)
        elif op == "news":
            result = _run("google_news", query=query, country=country)
        else:
            raise _bad(f"op must be 'serp' or 'news' (received {op!r})")
        return url_perimeter.filter_results(result, url_perimeter.perimeter_of_call())
