"""Instagram (statistiques) — the five tools, as thin wrappers.

The client lives in oto-core (`oto.tools.instagram_meta`); token resolution
and renewal live in `instagram_meta_session.py`. Here, nothing but: a
schema, a call, and Meta's JSON returned **as is**.

Returning the raw data is a choice, not laziness: these figures go to an agent that
knows how to read an object, and any in-house recomposition — renaming a metric,
deriving a ratio, rounding — would become a contract we would have to honor while
Meta is already evolving its own. Metric names are the API's.

The tool names and their schemas follow those of the MCP server that preceded this
connector, up to the prefix: they are names an agent and a user
already know.

⚠️ **Read-only, without exception**: nothing is published, modified or deleted.
The consent requested would not allow it anyway — the permissions
`instagram_business_basic` and `instagram_business_manage_insights` carry
no write access.
"""
from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from . import instagram_meta_session as session
from .instagram_meta_session import _client, appeler


def register(mcp: FastMCP) -> None:
    # ⚠️ NO import of the core here. The connector stays MOUNTED even when oto-core
    # is too old to carry it, or when the application's credentials are not
    # set: it is then visible, selectable, and every call
    # refuses by naming what is missing. A connector that vanishes from the catalog
    # goes unnoticed and unexplained — the user pays the
    # difference.
    session.avertir_au_demarrage()

    @mcp.tool()
    async def instagram_meta_get_profile() -> dict:
        """Profile of the connected Instagram professional account: username, name,
        biography, followers/follows/media counts, profile picture URL, website.

        Use it first to confirm WHICH account is connected.
        """
        ig = await _client()
        return await appeler("the account profile", ig.get_profile)

    @mcp.tool()
    async def instagram_meta_get_recent_media(
        limit: Annotated[int, Field(ge=1, le=50,
                                    description="Number of posts to fetch")] = 10,
    ) -> list[dict]:
        """List the N most recent posts with their basic metrics.

        Returns: id, caption, timestamp, media_type (IMAGE/VIDEO/CAROUSEL_ALBUM),
        media_product_type (FEED/REELS/STORY), like_count, comments_count, permalink.

        Useful to browse history, spot recent posts, and get a media_id to pass to
        instagram_meta_get_media_insights.
        """
        ig = await _client()
        return await appeler("recent posts", ig.get_recent_media, limit)

    @mcp.tool()
    async def instagram_meta_get_media_insights(
        media_id: Annotated[str, Field(
            description="Post id (from instagram_meta_get_recent_media)")],
    ) -> dict:
        """Detailed insights for one post.

        The metrics requested depend on the media type:
        - feed post / carousel: reach, views, likes, comments, saved, shares,
          total_interactions, follows, profile_visits
        - reel: same minus follows/profile_visits, plus ig_reels_avg_watch_time
          and ig_reels_video_view_total_time
        - story: reach, views, replies, shares, total_interactions, follows,
          profile_visits
        """
        ig = await _client()
        return await appeler("this post's insights",
                             ig.get_media_insights, media_id)

    @mcp.tool()
    async def instagram_meta_get_account_insights(
        days: Annotated[int, Field(ge=1, le=30,
                                   description="Window in days, max 30")] = 30,
    ) -> dict:
        """Account-level stats over the last N days (max 30, the API's since/until
        window): reach, views, accounts_engaged, total_interactions, likes,
        comments, saves, shares and profile_links_taps (taps on the profile links —
        replaces the retired profile_views and website_clicks).
        """
        ig = await _client()
        return await appeler("the account statistics",
                             ig.get_account_insights, days)

    @mcp.tool()
    async def instagram_meta_get_best_hours(
        sample_size: Annotated[int, Field(ge=5, le=50,
                                          description="Number of posts to analyse")] = 30,
    ) -> dict:
        """Heuristic: best hours and weekdays to publish, from the average
        engagement (likes + comments) of the N most recent posts.

        Returns by_hour and by_weekday sorted by average engagement, plus
        sample_size. Hours are those of the timestamps returned by the API (UTC).
        This is a local computation over posts, not a metric Instagram publishes —
        read it with sample_size in mind.
        """
        ig = await _client()
        media = await appeler("recent posts", ig.get_recent_media,
                              sample_size)
        return session._coeur().compute_best_hours(media)
