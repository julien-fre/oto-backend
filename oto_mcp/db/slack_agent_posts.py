"""Slack messages a hosted agent posted — table `slack_agent_posts`
(DDL: `schema/runs.py::SLACK_AGENT_POSTS`).

What turns a reply in Slack into a request to the agent that wrote the message
(`slack_events.py`): Slack's event names only the channel and the thread's parent
`ts`. Every message a hosted run posts is noted; only the agent's own — as the app,
outside a thread — is one a reply goes to (`lire`), the others only tell the route an
event is ours (`est_note`). Written by `slack_post_message` (`tools/slack.py`), read by
the event route, purged by `maintenance slack-agent-posts`.

Not flattened into `db.*`: `noter`/`lire`/`purger` are too common for the flat
surface — callers write `db.slack_agent_posts.<fn>`.

⚠️ Synchronous (psycopg): an async caller goes through `run_in_threadpool`.
"""
from __future__ import annotations

from typing import Optional, Sequence

from ._conn import _connect

#: How long a reply in the thread still reaches the agent — and what the purge keeps.
RETENTION_JOURS = 30

#: The message kept, in characters — what the reply's run is shown of it.
TEXTE_MAX = 8_000


def noter(org_id: int, trigger_id: int, job_id: Optional[int], channel: str,
          tss: Sequence[str], texte: str, auteur: str,
          thread_ts: Optional[str] = None) -> None:
    """The agent `trigger_id` posted `texte` at `(channel, tss[0])` — and, a long text
    being SPLIT, its later parts at `tss[1:]`, threaded under the first. `auteur`:
    `app` or `me`; `thread_ts`: the thread it was posted in, None outside one. A second
    write of the same message (a retried call) keeps the first."""
    if not tss:
        return
    premier = tss[0]
    lignes = [(channel, premier, int(org_id), int(trigger_id), job_id, auteur, thread_ts,
               (texte or "")[:TEXTE_MAX])]
    lignes += [(channel, ts, int(org_id), int(trigger_id), job_id, auteur,
                thread_ts or premier, "") for ts in tss[1:]]
    with _connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO slack_agent_posts (channel, ts, org_id, trigger_id, job_id, "
            "author, thread_ts, text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (channel, ts) DO NOTHING", lignes)


def lire(channel: str, ts: str) -> Optional[dict]:
    """The agent's OWN message at `(channel, ts)` — posted as the app, outside a thread,
    younger than the retention — `{org_id, trigger_id, job_id, text, created_at}`, or
    None. What a reply in its thread goes to."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT org_id, trigger_id, job_id, text, created_at FROM slack_agent_posts "
            "WHERE channel = %s AND ts = %s AND author = 'app' AND thread_ts IS NULL "
            "AND created_at > NOW() - make_interval(days => %s)",
            (channel, ts, RETENTION_JOURS)).fetchone()
    return dict(row) if row else None


def est_note(channel: str, ts: str) -> bool:
    """Was the message at `(channel, ts)` posted by one of our agents, whatever its
    author? An event for it is OURS, never a request — posted with a user token, it
    carries no `bot_id`."""
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM slack_agent_posts WHERE channel = %s AND ts = %s",
                            (channel, ts)).fetchone() is not None


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
