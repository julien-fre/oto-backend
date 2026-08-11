"""Cloro — veille AI-search & SERP Google en JSON (cloro.dev).

Wrappe `oto.tools.cloro.CloroClient`. Surfaces métier :
- **moteurs IA** (ChatGPT, Gemini, Perplexity, Copilot, Grok, Google AI Mode) :
  interroge le moteur et capture sa réponse + sources/citations → veille de marque
  « AI SEO » (ce que l'IA dit d'une marque/produit), intelligence concurrentielle.
- **Google SERP** en JSON (organique + AI Overview + People Also Ask) et **Google
  News**.

**Surface consolidée (ADR 0047 §Amendement)** : 8 tools → 2. Les six tools moteurs
(`cloro_chatgpt`/`cloro_perplexity`/`cloro_gemini`/`cloro_copilot`/`cloro_grok`/
`cloro_ai_mode`) portaient **exactement les mêmes paramètres** — le moteur n'est pas
un verbe mais une **variante** de la même opération → `cloro_ask(engine=…)`. Et
`cloro_google_serp`/`cloro_google_news` interrogent le même objet (Google) avec les
mêmes `query`/`country` → `cloro_google(op=…)`, les trois flags d'inclusion ne
concernant que la SERP. Les deux tools restent séparés : un moteur IA prend un
`prompt` conversationnel et des flags `markdown`/`searchQueries`, Google prend une
`query` et des flags `aiOverview`/`organicResults`/`peopleAlsoAsk` — params
disjoints, une fusion ne pèserait pas moins que deux tools.

Clé résolue par appel via `access.resolve_api_key("cloro")` : user/org key sinon
clé plateforme + quota daily pour les members. NB : les appels moteurs IA peuvent
prendre ~30-45 s.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access

# Moteurs IA → (slug API Cloro, libellé humain). Le slug est la valeur du paramètre
# `engine` de `cloro_ask` (⚠️ `aimode`, pas `ai_mode` : c'est le slug de l'API Cloro).
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
        """Résout la clé, appelle la méthode du client, compte l'usage plateforme."""
        client, is_platform = _client()
        result = getattr(client, method)(**kwargs)
        if is_platform:
            access.record_platform_usage("cloro")
        return result

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    # --- moteurs IA : un tool, le moteur en paramètre -----------------------

    @mcp.tool()
    def cloro_ask(
        engine: str,
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
            raise _bad(f"engine doit être l'un de {valid} (reçu {engine!r})")
        include: dict = {"markdown": markdown}
        if search_queries:
            include["searchQueries"] = True
        return _run("monitor", provider=engine, prompt=prompt,
                    country=country, include=include)

    # --- Google SERP / News : un tool, le verbe en `op` ---------------------

    @mcp.tool()
    def cloro_google(
        query: str,
        op: str = "serp",
        country: Optional[str] = None,
        ai_overview: bool = True,
        organic: bool = True,
        people_also_ask: bool = False,
    ) -> dict:
        """Google as clean JSON via Cloro (AI SEO / SERP monitoring).

        `op` :
        - **"serp"** (défaut) : Google SERP as clean JSON via Cloro (AI SEO / SERP
          monitoring) — organic results, Google's AI Overview block and People Also
          Ask, selected by the three include flags below.
        - **"news"** : Google News as JSON via Cloro. Takes `query` + `country`
          only — the three include flags are SERP-only and do not apply here.

        Args:
            query: search query.
            op: serp (défaut) | news.
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
            return _run("google", query=query, country=country, include=include)
        if op == "news":
            return _run("google_news", query=query, country=country)
        raise _bad(f"op doit être 'serp' ou 'news' (reçu {op!r})")
