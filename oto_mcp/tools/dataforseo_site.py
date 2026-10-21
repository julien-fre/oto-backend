"""DataForSEO — a site's technical health and the technologies it runs on.

Third module of the `dataforseo` connector (`providers/dataforseo.py::modules`);
credential, refusals, view and billing are shared with `tools/dataforseo.py`.

- `dataforseo_site_audit`: one page audited now, a Lighthouse run, a page's
  content, and the site crawl — the connector's only queued flow: `crawl_start`
  returns a `task_id` (billed per page at start), `crawl_status` until
  `finished`, then `crawl_results` per report (free reads).
- `dataforseo_technologies`: a site's stack, and the sites that use a technology
  (prospect lists by CMS, e-commerce platform, analytics…).
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from .lecture import LECTURE

from . import dataforseo as d

_CRAWL_PAGES_DEFAULT = 100
_CRAWL_PAGES_MAX = 2000
_FAILED_AUDITS_MAX = 25
_REPORTS = ("pages", "non_indexable", "duplicate_titles", "duplicate_descriptions",
            "broken_links", "links", "redirect_chains")


def _lighthouse_view(task: dict) -> dict:
    """A Lighthouse report is ~1 MB of JSON: keep the category scores (0-100) and
    the audits that did not pass, worst first."""
    r = (task.get("result") or [{}])[0] or {}
    scores = {k: round(v["score"] * 100) for k, v in (r.get("categories") or {}).items()
              if isinstance(v, dict) and v.get("score") is not None}
    failed = sorted(
        ({"id": k, "title": a.get("title"), "score": a.get("score"),
          "value": a.get("displayValue")}
         for k, a in (r.get("audits") or {}).items()
         if isinstance(a, dict) and isinstance(a.get("score"), (int, float))
         and a["score"] < 0.9),
        key=lambda a: a["score"])
    return {"cost_usd": task.get("cost"), "url": r.get("finalUrl") or r.get("requestedUrl"),
            "fetched_at": r.get("fetchTime"), "scores": scores,
            "failed_audits": failed[:_FAILED_AUDITS_MAX],
            "failed_audits_count": len(failed)}


def register(mcp: FastMCP) -> None:
    from oto.tools.dataforseo import DataForSeoClient

    def _client() -> tuple[DataForSeoClient, bool]:
        login, password, is_platform = d.resolve()
        return DataForSeoClient(login=login, password=password), is_platform

    @mcp.tool()
    def dataforseo_site_audit(
        op: str,
        url: Optional[str] = None,
        target: Optional[str] = None,
        task_id: Optional[str] = None,
        max_pages: Optional[int] = None,
        report: Optional[str] = None,
        mobile: Optional[bool] = None,
        categories: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        filters: Optional[list] = None,
        order_by: Optional[list[str]] = None,
        options: Optional[dict] = None,
        full: bool = False,
    ) -> dict:
        """Technical SEO audit: one page now, Lighthouse, or a whole-site crawl.

        One page (`url`, absolute):
        - `page` — meta tags, headings, content, links, load timing, on-page score
          and the failed checks. ≈$0.0002.
        - `lighthouse` — Lighthouse scores (performance, accessibility, best
          practices, SEO) and the audits that failed; `mobile=True` for mobile,
          `categories` to run only some. ≈$0.005.
        - `content` — the page's content, structured (title, headings, text).

        Whole site (`target` = domain without `https://`):
        - `crawl_start` — crawl up to `max_pages` (default 100, max 2000), billed
          per page at start (≈$0.00015/page). Returns `task_id`.
        - `crawl_status` — `task_id`: progress (`in_progress` | `finished`) and
          the site-wide findings (broken pages, duplicate content, https, sitemap…). Free.
        - `crawl_results` — `task_id` + `report`: `pages`, `non_indexable`,
          `duplicate_titles`, `duplicate_descriptions`, `broken_links`, `links`,
          `redirect_chains` (default 20 rows). Free.

        Returns — `{cost_usd, …}`, trimmed (`full=True` for the raw result).

        Args:
            op: see above.
            url / target / task_id: per op.
            max_pages: `crawl_start` only.
            report: `crawl_results` only.
            mobile / categories: `lighthouse` only.
            limit, offset, filters, order_by: `crawl_results` (`order_by` on `pages`).
            options: other documented fields (e.g. `{"enable_javascript": true}`
                on `page` and `crawl_start`).
            full: as in `dataforseo_keywords`.
        """
        page_ops = ("page", "lighthouse", "content")
        if op in page_ops:
            d.refuse(op, target=target, task_id=task_id, max_pages=max_pages, report=report,
                     limit=limit, offset=offset, filters=filters, order_by=order_by)
            d.need(url, "url", op)
            if op != "lighthouse":
                d.refuse(op, mobile=mobile, categories=categories)
        elif op in ("crawl_start", "crawl_status", "crawl_results"):
            d.refuse(op, url=url, mobile=mobile, categories=categories)
            if op != "crawl_start":
                d.refuse(op, target=target, max_pages=max_pages, options=options)
            if op != "crawl_results":
                d.refuse(op, report=report, limit=limit, offset=offset, filters=filters,
                         order_by=order_by)
        else:
            raise d.bad("op must be one of: page, lighthouse, content, crawl_start, "
                        "crawl_status, crawl_results.")
        if op == "crawl_results" and report not in _REPORTS:
            raise d.bad(f"op='crawl_results' requires `report`: one of {', '.join(_REPORTS)}.")
        c, is_platform = _client()
        with d.upstream(is_platform):
            if op == "page":
                task = c.onpage_instant(url, options=options)
            elif op == "content":
                task = c.onpage_content(url, options=options)
            elif op == "lighthouse":
                task = c.lighthouse(url, for_mobile=mobile, categories=categories,
                                    options=options)
                d.bill(task)
                return {"cost_usd": task.get("cost"), "result": task.get("result")} if full \
                    else _lighthouse_view(task)
            elif op == "crawl_start":
                pages = d.limit_of(max_pages, _CRAWL_PAGES_DEFAULT, _CRAWL_PAGES_MAX,
                                   name="max_pages")
                task = c.onpage_crawl_start(d.need(target, "target", op), pages,
                                            options=options)
                d.bill(task)
                return {"cost_usd": task.get("cost"), "task_id": task.get("id"),
                        "max_pages": pages,
                        "next": "dataforseo_site_audit(op='crawl_status', task_id=…) "
                                "until crawl_progress is 'finished'"}
            elif op == "crawl_status":
                task = c.onpage_summary(d.need(task_id, "task_id", op))
            else:
                tid = d.need(task_id, "task_id", op)
                rows = dict(limit=d.limit_of(limit), offset=offset)
                if report != "pages":
                    d.refuse(op, order_by=order_by)
                if report == "pages":
                    task = c.onpage_pages(tid, filters=filters, order_by=order_by, **rows)
                elif report == "non_indexable":
                    task = c.onpage_non_indexable(tid, filters=filters, **rows)
                elif report in ("duplicate_titles", "duplicate_descriptions"):
                    d.refuse(op, filters=filters)
                    task = c.onpage_duplicate_tags(
                        tid, type="duplicate_title" if report == "duplicate_titles"
                        else "duplicate_description", **rows)
                elif report == "broken_links":
                    d.refuse(op, filters=filters)
                    task = c.onpage_links(tid, filters=[["is_broken", "=", True]], **rows)
                elif report == "links":
                    task = c.onpage_links(tid, filters=filters, **rows)
                else:
                    d.refuse(op, filters=filters)
                    task = c.onpage_redirect_chains(tid, **rows)
        d.bill(task)
        return d.view(task, full)

    @mcp.tool(annotations=LECTURE)
    def dataforseo_technologies(
        op: str,
        target: Optional[str] = None,
        technologies: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        filters: Optional[list] = None,
        order_by: Optional[list[str]] = None,
        options: Optional[dict] = None,
        full: bool = False,
    ) -> dict:
        """What a website is built with, and which websites use a technology. ≈$0.01-0.02 per call.

        Detected on the sites themselves (CMS, e-commerce, analytics, payment,
        marketing tools…), not inferred from job posts.

        Ops:
        - `domain` — `target` (domain): its technologies by group and category,
          its country and language, and the emails / phones / social links found
          on it.
        - `domains_using` — `technologies` (e.g. ["Shopify"]) and/or `keywords`
          (in the sites' title and meta): matching domains, filterable, e.g.
          `filters=[["country_iso_code", "=", "FR"], "and", ["domain_rank", ">", 200]]`,
          `order_by=["domain_rank,desc"]` (default 20, max 1000).
        - `summary` — `technologies` / `keywords`: how many sites, by country,
          language and the technologies used alongside.

        Returns — `{cost_usd, …}`, trimmed (`full=True` for the raw result).

        Args:
            op: see above.
            target / technologies / keywords: per op.
            limit, offset, order_by: `domains_using` only. filters: `domains_using`, `summary`.
            options, full: as in `dataforseo_keywords`.
        """
        if op != "domains_using":
            d.refuse(op, limit=limit, offset=offset, order_by=order_by)
        if op == "domain":
            d.refuse(op, technologies=technologies, keywords=keywords, filters=filters,
                     options=options)
        elif op in ("domains_using", "summary"):
            d.refuse(op, target=target)
            if not (d.present(technologies) or d.present(keywords)):
                raise d.bad(f"op='{op}' requires `technologies` or `keywords`.")
        else:
            raise d.bad("op must be one of: domain, domains_using, summary.")
        c, is_platform = _client()
        with d.upstream(is_platform):
            if op == "domain":
                task = c.domain_technologies(d.need(target, "target", op))
            elif op == "domains_using":
                task = c.domains_by_technology(technologies=technologies, keywords=keywords,
                                               limit=d.limit_of(limit), offset=offset,
                                               filters=filters, order_by=order_by,
                                               options=options)
            else:
                task = c.technologies_summary(technologies=technologies, keywords=keywords,
                                              filters=filters, options=options)
        d.bill(task)
        return d.view(task, full)
