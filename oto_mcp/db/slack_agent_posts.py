"""Slack messages a hosted agent posted AS THE APP — table `slack_agent_posts`
(DDL: `schema/runs.py::SLACK_AGENT_POSTS`).

What turns a reply in Slack into a request to the agent that wrote the message
(`slack_events.py`): Slack's event names only the channel and the thread's parent
`ts`. Written by `slack_post_message` (`tools/slack.py`), read by the event route,
purged by `maintenance slack-agent-posts`.

Not flattened into `db.*`: `noter`/`lire`/`purger` are too common for the flat
surface — callers write `db.slack_agent_posts.<fn>`.

⚠️ Synchronous (psycopg): an async caller goes through `run_in_threadpool`.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

#: How long a reply in the thread still reaches the agent — and what the purge keeps.
RETENTION_JOURS = 30

#: The message kept, in characters — what the reply's run is shown of it.
TEXTE_MAX = 8_000


def noter(org_id: int, trigger_id: int, job_id: Optional[int], channel: str, ts: str,
          texte: str) -> None:
    """The agent `trigger_id` posted `texte` at `(channel, ts)`. A second write of the
    same message (a retried call) keeps the first."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO slack_agent_posts (channel, ts, org_id, trigger_id, job_id, text) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (channel, ts) DO NOTHING",
            (channel, ts, int(org_id), int(trigger_id), job_id,
             (texte or "")[:TEXTE_MAX]))


def lire(channel: str, ts: str) -> Optional[dict]:
    """The agent's message at `(channel, ts)`, if it is younger than the retention —
    `{org_id, trigger_id, job_id, text, created_at}` — or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT org_id, trigger_id, job_id, text, created_at FROM slack_agent_posts "
            "WHERE channel = %s AND ts = %s "
            "AND created_at > NOW() - make_interval(days => %s)",
            (channel, ts, RETENTION_JOURS)).fetchone()
    return dict(row) if row else None


def purger() -> int:
    """Removes the messages older than the retention. Returns how many."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM slack_agent_posts "
            "WHERE created_at <= NOW() - make_interval(days => %s)", (RETENTION_JOURS,))
        return cur.rowcount or 0


def compter_purgeables() -> int:
    """What `purger` would remove — for the dry-run maintenance."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM slack_agent_posts "
            "WHERE created_at <= NOW() - make_interval(days => %s)",
            (RETENTION_JOURS,)).fetchone()
    return int(row["n"])
