"""GitHub tools — repositories and code, issues, pull requests, organizations, Actions.

Wraps `oto.tools.github.client.GitHubClient` (REST v3, Bearer). Seven tools, one
per family of the upstream API.

Four traps of this API are handled here rather than left to the agent, because
none of them shows up as an error:

- ⚠️ **A 404 on a private resource almost always means "the token is not
  allowed"**, not "does not exist": GitHub hides existence on purpose. The
  refusal message says so, otherwise one looks for a typo for an hour.
- ⚠️ **A pull request IS an issue** on GitHub's side: `github_issues op='search'`
  therefore drops PRs by default, for lack of an upstream filter. Without that, counting
  a repository's tickets gives a wrong number, often by a lot.
- ⚠️ **`per_page` caps at 100 and GitHub silently trims** anything above: the
  client refuses locally, naming the limit, rather than returning 100 rows
  where the agent believed 500.
- ⚠️ **Search stops at 1,000 results** whatever `total_count` says:
  `github_search` surfaces a truncation flag rather than letting
  "12,000 results" be read as a promise.

⚠️ **`github_actions op='dispatch'` triggers a real run** — thus
potentially a deployment. It is the only action of this connector that acts
outside GitHub, and it is **dry-run by default**, like `lightfield`'s email sending
and `origami`'s campaign launch.

Client calls are written in plain sight (`_client().list_issues(…)`): this is
what makes them checkable by the version-skew probe
(`test_tools_client_methods_exist`).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, egress
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    """Translate a GitHub refusal into an actionable message.

    The case that matters is **404**: on a private resource outside the token's
    reach, GitHub answers 404 rather than 403 so as not to disclose its existence.
    Returning "not found" as-is would send someone looking for a typo where
    a scope is missing.
    """
    status = e.status_code
    body = e.body if isinstance(e.body, dict) else {}
    msg = body.get("message") or ""
    if status == 401:
        return ("GitHub rejected the token (401) — it is invalid, expired or "
                "revoked. Set it again on this connector.")
    if status == 403:
        return (f"GitHub refused (403): {msg or 'unauthorized access'}. Either the "
                "token is missing a scope (classic token) or a permission / "
                "the repository in its list (fine-grained token), or it is a "
                "secondary usage limit — in that case, retry later.")
    if status == 404:
        return ("GitHub: not found (404). ⚠️ On a PRIVATE resource, GitHub "
                "answers 404 when the token is not allowed to see it, on purpose, "
                "so as not to disclose its existence. Before suspecting the name, "
                "check that the token really covers this repository / organization.")
    if status == 405:
        return (f"GitHub: operation not possible in the current state (405): {msg}. On a "
                "merge, this means the PR is not mergeable — "
                "conflicts, or failing branch checks.")
    if status == 409:
        return (f"GitHub: conflict (409): {msg}. The reference moved since the "
                "read — reread the fresh state and retry. On a file write, "
                "it is the blob's `sha` that is stale or missing.")
    if status == 422:
        return (f"GitHub refused the request (422): {msg or e.body}. It is a "
                "validation: missing field, out-of-range value, or — on "
                "search — beyond the 1,000 accessible results.")
    if status == 429:
        return ("GitHub: too many requests (429) — usage limit reached. "
                "Retry in a moment.")
    if status in (500, 502, 503, 504):
        return f"GitHub is temporarily unavailable (HTTP {status}) — retry later."
    return f"GitHub refused the request (HTTP {status}): {e.body}"


def _check_base_url(base_url) -> None:
    """Egress guard on the Enterprise Server API URL, when it is set.

    Empty = github.com, a constant of the lib: nothing to check. When filled in,
    it designates a self-hosted server — thus, potentially, a host on the
    platform's internal network (`oto_mcp/egress.py`)."""
    valeur = (base_url or "").strip()
    if valeur:
        egress.check_url(valeur, connector="github")


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """The "test connection" probe: `GET /user`, then the quota state.

    `/user` requires no particular scope: it separates "invalid token" (401)
    from "valid but restricted token" (403/404 elsewhere). Probing a repository
    would confuse the two — and worse, an out-of-reach repository would answer 404, which
    would show red on a perfectly healthy token.

    We CANNOT verify more: a classic token's scopes are only
    readable in a response header, and a fine-grained token does not expose
    its repository list. The probe therefore says "this token is alive and here is
    who it is" — and that is exactly what it promises.
    """
    from oto.tools.github.client import GitHubClient
    _check_base_url(fields.get("base_url"))
    client = GitHubClient(token=fields["token"],
                          base_url=(fields.get("base_url") or None))
    who = client.me()
    if not isinstance(who, dict) or not who.get("login"):
        raise ValueError(
            "GitHub answered without identifying the account — unexpected token, or "
            "API URL that does not point to a GitHub instance.")


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.github.client import GitHubClient

    connector_verify.register("github", _verify)

    def _client() -> GitHubClient:
        creds = access.resolve_credential_fields("github")
        _check_base_url(creds.get("base_url"))
        return GitHubClient(token=creds["token"],
                            base_url=(creds.get("base_url") or None))

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    def _need(value, nom: str, op: str):
        if value in (None, "", [], {}):
            raise _bad(f"op='{op}': `{nom}` required.")
        return value

    def _repo(owner: Optional[str], repo: Optional[str], op: str):
        _need(owner, "owner", op)
        _need(repo, "repo", op)
        return owner, repo

    def _bad_op(op: str, attendus: str):
        return _bad(f"`op` invalid: {op!r} (expected: {attendus}).")

    # --- repositories ---------------------------------------------------------

    @mcp.tool()
    def github_repos(
        op: Literal["mine", "org", "user", "get", "branches", "commits",
                    "commit", "compare", "tags", "contributors", "languages",
                    "topics", "releases", "release", "latest_release",
                    "create_release", "update_release"] = "mine",
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        org: Optional[str] = None,
        username: Optional[str] = None,
        ref: Optional[str] = None,
        base: Optional[str] = None,
        head: Optional[str] = None,
        path: Optional[str] = None,
        author: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        release_id: Optional[str] = None,
        sort: Optional[str] = None,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        per_page: int = 30,
    ) -> Any:
        """GitHub — repositories: profile, branches, commits, tags, releases.

        The entry point for getting oriented in a repository before reading its code
        (`github_files`) or its tickets (`github_issues`).

        ⚠️ A 404 on a private repository almost always signals a token without the
        right, not a wrong name.

        ⚠️ `op='commit'` returns the commit WITH its diff: GitHub caps at 300
        files and truncates beyond that without saying so in `files` — compare to
        `stats` to notice.

        ⚠️ `op='latest_release'` ignores drafts AND prereleases: it is
        not the last tag created.

        `op`: `mine` | `org` | `user` (repository lists) · `get` · `branches` ·
        `commits` · `commit` (with diff) · `compare` (base…head) · `tags` ·
        `contributors` · `languages` · `topics` · `releases` · `release` ·
        `latest_release` · `create_release` · `update_release`.

        Args:
            op: the operation, see above.
            owner: repository owner.
            repo: repository name.
            org: op='org' — the organization whose repositories are listed.
            username: op='user' — the account whose public repositories are listed.
            ref: op='commit' — branch, tag or SHA.
            base: op='compare' — the starting reference.
            head: op='compare' — the ending reference.
            path: op='commits' — only keep commits touching this path.
            author: op='commits' — filter by author.
            since: op='commits' — lower bound (ISO 8601).
            until: op='commits' — upper bound (ISO 8601).
            release_id: op='release'/'update_release' — the targeted release.
            sort: op='mine'/'org'/'user' — created | updated | pushed | full_name.
            fields: op='create_release'/'update_release' — the body (tag_name required).
            page: page number.
            per_page: rows per page (1-100, default 30).
        """
        c = _client()
        if op == "mine":
            return _run(lambda: c.list_my_repos(sort=sort, per_page=per_page,
                                                page=page))
        if op == "org":
            _need(org, "org", op)
            return _run(lambda: c.list_org_repos(org, sort=sort,
                                                 per_page=per_page, page=page))
        if op == "user":
            _need(username, "username", op)
            return _run(lambda: c.list_user_repos(username, sort=sort,
                                                  per_page=per_page, page=page))
        o, r = _repo(owner, repo, op)
        if op == "get":
            return _run(lambda: c.get_repo(o, r))
        if op == "branches":
            return _run(lambda: c.list_branches(o, r, per_page=per_page, page=page))
        if op == "commits":
            return _run(lambda: c.list_commits(o, r, sha=ref, path=path,
                                               author=author, since=since,
                                               until=until, per_page=per_page,
                                               page=page))
        if op == "commit":
            _need(ref, "ref", op)
            return _run(lambda: c.get_commit(o, r, ref))
        if op == "compare":
            _need(base, "base", op)
            _need(head, "head", op)
            return _run(lambda: c.compare_commits(o, r, base, head,
                                                  per_page=per_page, page=page))
        if op == "tags":
            return _run(lambda: c.list_tags(o, r, per_page=per_page, page=page))
        if op == "contributors":
            return _run(lambda: c.list_contributors(o, r, per_page=per_page,
                                                    page=page))
        if op == "languages":
            return _run(lambda: c.list_languages(o, r))
        if op == "topics":
            return _run(lambda: c.list_topics(o, r))
        if op == "releases":
            return _run(lambda: c.list_releases(o, r, per_page=per_page, page=page))
        if op == "release":
            _need(release_id, "release_id", op)
            return _run(lambda: c.get_release(o, r, release_id))
        if op == "latest_release":
            return _run(lambda: c.get_latest_release(o, r))
        if op == "create_release":
            _need(fields, "fields", op)
            return _run(lambda: c.create_release(o, r, fields))
        if op == "update_release":
            _need(release_id, "release_id", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_release(o, r, release_id, fields))
        raise _bad_op(op, "mine | org | user | get | branches | commits | commit | "
                          "compare | tags | contributors | languages | topics | "
                          "releases | release | latest_release | create_release | "
                          "update_release")

    # --- files ----------------------------------------------------------------

    @mcp.tool()
    def github_files(
        op: Literal["read", "list", "readme", "write", "delete"] = "read",
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        path: Optional[str] = None,
        ref: Optional[str] = None,
        content: Optional[str] = None,
        message: Optional[str] = None,
        sha: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Any:
        """GitHub — read and write repository files.

        `op='read'` returns the decoded TEXT; `op='list'` returns the contents of a
        directory; `op='readme'` finds the README whatever its name.

        ⚠️ **Beyond 1 MB, GitHub serves the metadata without the content**: the
        read then says so by name rather than returning an empty string that
        would read as an empty file.

        ⚠️ **Writing over an existing file REQUIRES its `sha`** (the
        blob's, returned by `op='list'` or by a read). Without it, GitHub answers
        409: it is its concurrency control, which guarantees we replace
        the version we read and not a change that arrived in the meantime.
        Omitting it is normal for a CREATION.

        ⚠️ Each write is a **real commit** on the targeted branch (`branch`,
        or the default branch) — visible in history, and attributed to the
        token holder.

        Args:
            op: read | list | readme | write | delete.
            owner: repository owner.
            repo: repository name.
            path: path of the file or directory.
            ref: branch, tag or SHA to read (default: default branch).
            content: op='write' — the text content to write.
            message: op='write'/'delete' — the commit message.
            sha: op='write' (update) / 'delete' — the existing blob's sha.
            branch: op='write'/'delete' — the target branch.
        """
        c = _client()
        o, r = _repo(owner, repo, op)
        if op == "read":
            _need(path, "path", op)
            return _run(lambda: {"path": path, "ref": ref,
                                 "text": c.read_text_file(o, r, path, ref)})
        if op == "list":
            _need(path, "path", op)
            return _run(lambda: c.get_content(o, r, path, ref))
        if op == "readme":
            return _run(lambda: c.get_readme(o, r, ref))
        if op == "write":
            _need(path, "path", op)
            _need(message, "message", op)
            if content is None:
                raise _bad("op='write': `content` required (the text to write).")
            return _run(lambda: c.create_or_update_file(
                o, r, path, message, content, sha=sha, branch=branch))
        if op == "delete":
            _need(path, "path", op)
            _need(message, "message", op)
            _need(sha, "sha", op)
            return _run(lambda: c.delete_file(o, r, path, message, sha,
                                              branch=branch))
        raise _bad_op(op, "read | list | readme | write | delete")

    # --- issues ---------------------------------------------------------------

    @mcp.tool()
    def github_issues(
        op: Literal["search", "get", "create", "update", "comments", "comment",
                    "update_comment", "delete_comment", "labels", "add_labels",
                    "set_labels", "remove_label", "create_label",
                    "assign", "unassign", "lock", "unlock",
                    "milestones", "create_milestone"] = "search",
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        number: Optional[int] = None,
        comment_id: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[list[str]] = None,
        label: Optional[str] = None,
        assignees: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        creator: Optional[str] = None,
        milestone: Optional[str] = None,
        since: Optional[str] = None,
        sort: Optional[str] = None,
        body: Optional[str] = None,
        lock_reason: Optional[str] = None,
        include_pull_requests: bool = False,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        per_page: int = 30,
    ) -> Any:
        """GitHub — a repository's issues, their comments and their labels.

        ⚠️ **At GitHub, a pull request IS an issue**: the API returns the two
        mixed together. `op='search'` therefore drops PRs by default — without which
        "how many open tickets?" gives a wrong number, often by a lot.
        `include_pull_requests=true` returns the API's raw response.
        This sorting happens after pagination: a page of 30 of which 12 are PRs
        returns 18, which is normal.

        Conversely, and this is useful: these same operations work on a PR
        by passing its number — thread comments, labels, assignments and
        milestones are shared by both.

        ⚠️ `op='update'` with `labels` or `assignees` **REPLACES** the list. To
        add without overwriting: `add_labels` / `assign`.

        ⚠️ `op='assign'` **silently ignores** an account without write access
        to the repository: the response comes back as a success without having assigned it. Compare the
        returned list with the requested one.

        ⚠️ Creating an issue or a comment **notifies** the repository's watchers and
        anyone mentioned. There is no issue draft at GitHub.

        Args:
            op: the operation, see above.
            owner: repository owner.
            repo: repository name.
            number: the issue's (or PR's) number.
            comment_id: the targeted comment (update_comment, delete_comment).
            state: op='search' — open | closed | all.
            labels: op='search' (filter) or add_labels / set_labels (write).
            label: op='remove_label' — the label to remove. op='create_label' — its name.
            assignees: op='assign'/'unassign' — the targeted accounts.
            assignee: op='search' — filter by assignee.
            creator: op='search' — filter by author.
            milestone: op='search' — filter by milestone.
            since: op='search'/'comments' — modified since (ISO 8601).
            sort: op='search' — created | updated | comments.
            body: op='comment'/'update_comment' — the comment text.
            lock_reason: op='lock' — off-topic | too heated | resolved | spam.
            include_pull_requests: op='search' — include PRs (default false).
            fields: op='create'/'update'/'create_label'/'create_milestone' — the body.
            page: page number.
            per_page: rows per page (1-100, default 30).
        """
        c = _client()
        o, r = _repo(owner, repo, op)
        if op == "search":
            return _run(lambda: c.list_issues(
                o, r, state=state, labels=labels, assignee=assignee,
                creator=creator, milestone=milestone, since=since, sort=sort,
                include_pull_requests=include_pull_requests,
                per_page=per_page, page=page))
        if op == "get":
            _need(number, "number", op)
            return _run(lambda: c.get_issue(o, r, number))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_issue(o, r, fields))
        if op == "update":
            _need(number, "number", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_issue(o, r, number, fields))
        if op == "comments":
            _need(number, "number", op)
            return _run(lambda: c.list_issue_comments(o, r, number, since=since,
                                                      per_page=per_page,
                                                      page=page))
        if op == "comment":
            _need(number, "number", op)
            _need(body, "body", op)
            return _run(lambda: c.create_issue_comment(o, r, number, body))
        if op == "update_comment":
            _need(comment_id, "comment_id", op)
            _need(body, "body", op)
            return _run(lambda: c.update_issue_comment(o, r, comment_id, body))
        if op == "delete_comment":
            _need(comment_id, "comment_id", op)
            return _run(lambda: c.delete_issue_comment(o, r, comment_id))
        if op == "labels":
            return _run(lambda: c.list_labels(o, r, per_page=per_page, page=page))
        if op == "add_labels":
            _need(number, "number", op)
            _need(labels, "labels", op)
            return _run(lambda: c.add_labels(o, r, number, labels))
        if op == "set_labels":
            _need(number, "number", op)
            if labels is None:
                raise _bad("op='set_labels': `labels` required — an empty list "
                           "removes all labels, which is an "
                           "intention, but it must be written out.")
            return _run(lambda: c.set_labels(o, r, number, labels))
        if op == "remove_label":
            _need(number, "number", op)
            _need(label, "label", op)
            return _run(lambda: c.remove_label(o, r, number, label))
        if op == "create_label":
            _need(fields, "fields", op)
            return _run(lambda: c.create_label(
                o, r, fields.get("name"), fields.get("color"),
                description=fields.get("description")))
        if op == "assign":
            _need(number, "number", op)
            _need(assignees, "assignees", op)
            return _run(lambda: c.add_assignees(o, r, number, assignees))
        if op == "unassign":
            _need(number, "number", op)
            _need(assignees, "assignees", op)
            return _run(lambda: c.remove_assignees(o, r, number, assignees))
        if op == "lock":
            _need(number, "number", op)
            return _run(lambda: c.lock_issue(o, r, number, lock_reason))
        if op == "unlock":
            _need(number, "number", op)
            return _run(lambda: c.unlock_issue(o, r, number))
        if op == "milestones":
            return _run(lambda: c.list_milestones(o, r, state=state,
                                                  per_page=per_page, page=page))
        if op == "create_milestone":
            _need(fields, "fields", op)
            return _run(lambda: c.create_milestone(o, r, fields.get("title"),
                                                   fields))
        raise _bad_op(op, "search | get | create | update | comments | comment | "
                          "update_comment | delete_comment | labels | add_labels | "
                          "set_labels | remove_label | create_label | assign | "
                          "unassign | lock | unlock | milestones | create_milestone")

    # --- pull requests --------------------------------------------------------

    @mcp.tool()
    def github_pulls(
        op: Literal["search", "get", "create", "update", "files", "commits",
                    "merged", "merge", "update_branch",
                    "reviews", "review", "submit_review",
                    "review_comments", "review_comment",
                    "reviewers", "request_review", "remove_reviewers"] = "search",
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        number: Optional[int] = None,
        review_id: Optional[str] = None,
        state: Optional[str] = None,
        base: Optional[str] = None,
        head: Optional[str] = None,
        sort: Optional[str] = None,
        event: Optional[str] = None,
        body: Optional[str] = None,
        merge_method: Optional[str] = None,
        commit_title: Optional[str] = None,
        sha: Optional[str] = None,
        reviewers: Optional[list[str]] = None,
        team_reviewers: Optional[list[str]] = None,
        fields: Optional[dict] = None,
        page: Optional[int] = None,
        per_page: int = 30,
    ) -> Any:
        """GitHub — pull requests: diff, reviews, reviewers, merging.

        A PR's THREAD comments, labels and assignments go through
        `github_issues` with the same number — this is intended on GitHub's side. What lives here
        is what is specific to a PR: the diff, reviews, line-by-line
        comments, and merging.

        ⚠️ **A merged PR is `closed`**: there is no `merged` state.
        To tell them apart, read `merged_at` (null = closed without merging), or
        `op='merged'`.

        ⚠️ **`mergeable` may be `null`** on `op='get'`: GitHub computes it
        in the background on the first call. `null` means "not known yet" —
        ask again, above all do not read it as "not mergeable".

        ⚠️ **`op='merge'` writes to the target branch and cannot be undone with
        one click.** The three methods differ: `merge` adds a merge
        commit, `squash` squashes the branch into a single commit, `rebase` rewrites
        the commits. Passing `sha` protects against the race: if the head moved
        since the read, GitHub refuses instead of merging something else.

        ⚠️ **A review without `event` stays PENDING** (`PENDING`): nothing is
        published, nobody is notified, and it is only visible to its author.
        This is useful for preparing, and a trap when you thought you were approving.
        `APPROVE` can unblock a protected merge: it is an act of
        governance, not a comment.

        ⚠️ `op='files'` is capped at 3,000 files and omits big `patch`es;
        `op='commits'` at 250. A massive PR is returned incomplete, without error.

        Args:
            op: the operation, see above.
            owner: repository owner.
            repo: repository name.
            number: the PR's number.
            review_id: op='submit_review' — the pending review to publish.
            state: op='search' — open | closed | all.
            base: op='search'/'create' — the target branch.
            head: op='search'/'create' — the source branch.
            sort: op='search' — created | updated | popularity | long-running.
            event: op='review'/'submit_review' — APPROVE | REQUEST_CHANGES | COMMENT.
            body: op='review'/'submit_review' — the review text.
            merge_method: op='merge' — merge | squash | rebase.
            commit_title: op='merge' — merge commit title.
            sha: op='merge' — the expected head (race protection).
            reviewers: op='request_review'/'remove_reviewers' — accounts.
            team_reviewers: op='request_review'/'remove_reviewers' — team slugs.
            fields: op='create'/'update'/'review_comment' — the body.
            page: page number.
            per_page: rows per page (1-100, default 30).
        """
        c = _client()
        o, r = _repo(owner, repo, op)
        if op == "search":
            return _run(lambda: c.list_pulls(o, r, state=state, head=head,
                                             base=base, sort=sort,
                                             per_page=per_page, page=page))
        if op == "get":
            _need(number, "number", op)
            return _run(lambda: c.get_pull(o, r, number))
        if op == "create":
            _need(fields, "fields", op)
            return _run(lambda: c.create_pull(o, r, fields))
        if op == "update":
            _need(number, "number", op)
            _need(fields, "fields", op)
            return _run(lambda: c.update_pull(o, r, number, fields))
        if op == "files":
            _need(number, "number", op)
            return _run(lambda: c.list_pull_files(o, r, number,
                                                  per_page=per_page, page=page))
        if op == "commits":
            _need(number, "number", op)
            return _run(lambda: c.list_pull_commits(o, r, number,
                                                    per_page=per_page, page=page))
        if op == "merged":
            _need(number, "number", op)
            return _run(lambda: {"number": number,
                                 "merged": c.check_pull_merged(o, r, number)})
        if op == "merge":
            _need(number, "number", op)
            return _run(lambda: c.merge_pull(o, r, number,
                                             commit_title=commit_title,
                                             commit_message=body, sha=sha,
                                             merge_method=merge_method))
        if op == "update_branch":
            _need(number, "number", op)
            return _run(lambda: c.update_pull_branch(o, r, number, sha))
        if op == "reviews":
            _need(number, "number", op)
            return _run(lambda: c.list_reviews(o, r, number, per_page=per_page,
                                               page=page))
        if op == "review":
            _need(number, "number", op)
            payload = dict(fields or {})
            if body is not None:
                payload["body"] = body
            if event is not None:
                payload["event"] = event
            return _run(lambda: c.create_review(o, r, number, payload))
        if op == "submit_review":
            _need(number, "number", op)
            _need(review_id, "review_id", op)
            _need(event, "event", op)
            return _run(lambda: c.submit_review(o, r, number, review_id, event,
                                                body))
        if op == "review_comments":
            _need(number, "number", op)
            return _run(lambda: c.list_review_comments(o, r, number,
                                                       per_page=per_page,
                                                       page=page))
        if op == "review_comment":
            _need(number, "number", op)
            _need(fields, "fields", op)
            return _run(lambda: c.create_review_comment(o, r, number, fields))
        if op == "reviewers":
            _need(number, "number", op)
            return _run(lambda: c.list_requested_reviewers(o, r, number))
        if op == "request_review":
            _need(number, "number", op)
            return _run(lambda: c.request_reviewers(o, r, number,
                                                    reviewers=reviewers,
                                                    team_reviewers=team_reviewers))
        if op == "remove_reviewers":
            _need(number, "number", op)
            return _run(lambda: c.remove_requested_reviewers(
                o, r, number, reviewers=reviewers,
                team_reviewers=team_reviewers))
        raise _bad_op(op, "search | get | create | update | files | commits | "
                          "merged | merge | update_branch | reviews | review | "
                          "submit_review | review_comments | review_comment | "
                          "reviewers | request_review | remove_reviewers")

    # --- organizations --------------------------------------------------------

    @mcp.tool()
    def github_orgs(
        op: Literal["me", "my_orgs", "rate_limit", "get", "members",
                    "is_member", "membership", "set_membership", "remove_member",
                    "teams", "team", "team_members", "team_repos",
                    "add_team_member", "remove_team_member",
                    "collaborators", "is_collaborator", "permission",
                    "add_collaborator", "remove_collaborator"] = "me",
        org: Optional[str] = None,
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        team_slug: Optional[str] = None,
        username: Optional[str] = None,
        role: Optional[str] = None,
        permission: Optional[str] = None,
        filter: Optional[str] = None,
        affiliation: Optional[str] = None,
        page: Optional[int] = None,
        per_page: int = 30,
    ) -> Any:
        """GitHub — who is in the organization, in a team, on a repository.

        ⚠️ **Three memberships look alike and are not the same thing**:
        member of the ORGANIZATION, member of a TEAM, collaborator on a REPOSITORY.
        Removing someone from one does not remove them from the others — this is the
        most frequent error here. `remove_member` leaves the organization (and all
        its teams); `remove_team_member` only touches the team;
        `remove_collaborator` only touches one repository, **and does not remove access
        inherited from a team**.

        ⚠️ **`members` only shows what the token is allowed to see**: without
        the organization scope, only PUBLIC members come out — a shorter
        list, with no error. It is therefore not a census.

        ⚠️ `set_membership` and `add_collaborator` **send an invitation**:
        access is only effective once accepted (`pending` until then).
        `role='admin'` on an organization gives owner rights.

        ⚠️ `permission` returns the EFFECTIVE level (team inheritance included), which
        the list of direct collaborators does not say.

        `op='me'` (the token's account) and `op='rate_limit'` (the quota state,
        without consuming them) are for diagnosing before blaming a name.

        Args:
            op: the operation, see above.
            org: the targeted organization.
            owner: repository owner (collaborator operations).
            repo: repository name (collaborator operations).
            team_slug: the team's slug (not its display name).
            username: the targeted account.
            role: admin | member (organization); member | maintainer (team).
            permission: pull | triage | push | maintain | admin (collaborator).
            filter: op='members' — 2fa_disabled | all.
            affiliation: op='collaborators' — outside | direct | all.
            page: page number.
            per_page: rows per page (1-100, default 30).
        """
        c = _client()
        if op == "me":
            return _run(lambda: c.me())
        if op == "my_orgs":
            return _run(lambda: c.list_my_orgs(per_page=per_page, page=page))
        if op == "rate_limit":
            return _run(lambda: c.rate_limit())
        if op in ("collaborators", "is_collaborator", "permission",
                  "add_collaborator", "remove_collaborator"):
            o, r = _repo(owner, repo, op)
            if op == "collaborators":
                return _run(lambda: c.list_collaborators(
                    o, r, affiliation=affiliation, permission=permission,
                    per_page=per_page, page=page))
            _need(username, "username", op)
            if op == "is_collaborator":
                return _run(lambda: {"username": username,
                                     "collaborator": c.check_collaborator(o, r, username)})
            if op == "permission":
                return _run(lambda: c.get_collaborator_permission(o, r, username))
            if op == "add_collaborator":
                return _run(lambda: c.add_collaborator(o, r, username, permission))
            return _run(lambda: c.remove_collaborator(o, r, username))
        _need(org, "org", op)
        if op == "get":
            return _run(lambda: c.get_org(org))
        if op == "members":
            return _run(lambda: c.list_org_members(org, filter=filter, role=role,
                                                   per_page=per_page, page=page))
        if op == "is_member":
            _need(username, "username", op)
            return _run(lambda: {"username": username,
                                 "member": c.check_org_membership(org, username)})
        if op == "membership":
            _need(username, "username", op)
            return _run(lambda: c.get_org_membership(org, username))
        if op == "set_membership":
            _need(username, "username", op)
            return _run(lambda: c.set_org_membership(org, username, role))
        if op == "remove_member":
            _need(username, "username", op)
            return _run(lambda: c.remove_org_member(org, username))
        if op == "teams":
            return _run(lambda: c.list_teams(org, per_page=per_page, page=page))
        if op == "team":
            _need(team_slug, "team_slug", op)
            return _run(lambda: c.get_team(org, team_slug))
        if op == "team_members":
            _need(team_slug, "team_slug", op)
            return _run(lambda: c.list_team_members(org, team_slug, role=role,
                                                    per_page=per_page, page=page))
        if op == "team_repos":
            _need(team_slug, "team_slug", op)
            return _run(lambda: c.list_team_repos(org, team_slug,
                                                  per_page=per_page, page=page))
        if op == "add_team_member":
            _need(team_slug, "team_slug", op)
            _need(username, "username", op)
            return _run(lambda: c.add_team_member(org, team_slug, username, role))
        if op == "remove_team_member":
            _need(team_slug, "team_slug", op)
            _need(username, "username", op)
            return _run(lambda: c.remove_team_member(org, team_slug, username))
        raise _bad_op(op, "me | my_orgs | rate_limit | get | members | is_member | "
                          "membership | set_membership | remove_member | teams | "
                          "team | team_members | team_repos | add_team_member | "
                          "remove_team_member | collaborators | is_collaborator | "
                          "permission | add_collaborator | remove_collaborator")

    # --- Actions --------------------------------------------------------------

    @mcp.tool()
    def github_actions(
        op: Literal["workflows", "workflow", "dispatch", "runs", "run",
                    "cancel", "rerun", "rerun_failed", "delete_run",
                    "jobs", "job", "logs",
                    "artifacts", "artifact", "download",
                    "delete_artifact"] = "runs",
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        workflow: Optional[str] = None,
        run_id: Optional[str] = None,
        job_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        ref: Optional[str] = None,
        branch: Optional[str] = None,
        event: Optional[str] = None,
        status: Optional[str] = None,
        actor: Optional[str] = None,
        inputs: Optional[dict] = None,
        dry_run: bool = True,
        page: Optional[int] = None,
        per_page: int = 30,
    ) -> Any:
        """GitHub Actions — workflows, runs, jobs, logs and artifacts.

        Read first: `runs` then `jobs` then `logs` is the normal path
        to understand why a pipeline failed.

        ⚠️ Read `status` AND `conclusion`: a `completed` run may have
        failed, and `conclusion` stays null while the work is not finished.

        ⚠️ **`op='dispatch'` triggers a real run** — thus
        potentially a build, a publication or a **deployment**. It is
        **dry-run by default**: `dry_run=false` to really trigger. The
        workflow must declare `workflow_dispatch`, otherwise GitHub answers 404 — here
        a 404 means "not triggerable", not "does not exist". The response
        does NOT return the created run: find it by listing the runs right
        afterwards (there is a short delay).

        ⚠️ `rerun` reruns the WHOLE run (billed minutes, possible
        redeployment); `rerun_failed` only replays the failed jobs — cheaper
        and less risky. `cancel` interrupts work in progress.

        ⚠️ `logs` and `download` return an **ephemeral signed URL** (~1 minute),
        to be downloaded **without an authentication header** — the storage refuses a
        doubly authenticated request. `None` signals expired logs or a
        stale artifact (90 days by default).

        Args:
            op: the operation, see above.
            owner: repository owner.
            repo: repository name.
            workflow: numeric id or file name (ci.yml) — the name is more stable.
            run_id: the targeted run.
            job_id: the targeted job (job, logs).
            artifact_id: the targeted artifact (artifact, download, delete_artifact).
            ref: op='dispatch' — the branch or tag to run on.
            branch: op='runs' — filter by branch.
            event: op='runs' — filter by triggering event.
            status: op='runs' — queued | in_progress | completed | success | failure…
            actor: op='runs' — filter by triggering actor.
            inputs: op='dispatch' — the workflow's inputs.
            dry_run: op='dispatch' — True (default) describes without triggering.
            page: page number.
            per_page: rows per page (1-100, default 30).
        """
        c = _client()
        o, r = _repo(owner, repo, op)
        if op == "workflows":
            return _run(lambda: c.list_workflows(o, r, per_page=per_page, page=page))
        if op == "workflow":
            _need(workflow, "workflow", op)
            return _run(lambda: c.get_workflow(o, r, workflow))
        if op == "dispatch":
            _need(workflow, "workflow", op)
            _need(ref, "ref", op)
            if dry_run:
                return {
                    "dry_run": True,
                    "would": "trigger a real run of this workflow",
                    "repo": f"{o}/{r}", "workflow": workflow, "ref": ref,
                    "inputs": inputs or {},
                    "avertissement": ("a run may build, publish "
                                      "or DEPLOY, and consumes billed "
                                      "minutes"),
                    "pour_declencher": "call again with dry_run=false",
                }
            return _run(lambda: c.dispatch_workflow(o, r, workflow, ref, inputs))
        if op == "runs":
            return _run(lambda: c.list_workflow_runs(
                o, r, workflow=workflow, actor=actor, branch=branch,
                event=event, status=status, per_page=per_page, page=page))
        if op == "run":
            _need(run_id, "run_id", op)
            return _run(lambda: c.get_workflow_run(o, r, run_id))
        if op == "cancel":
            _need(run_id, "run_id", op)
            return _run(lambda: c.cancel_workflow_run(o, r, run_id))
        if op == "rerun":
            _need(run_id, "run_id", op)
            return _run(lambda: c.rerun_workflow_run(o, r, run_id))
        if op == "rerun_failed":
            _need(run_id, "run_id", op)
            return _run(lambda: c.rerun_failed_jobs(o, r, run_id))
        if op == "delete_run":
            _need(run_id, "run_id", op)
            return _run(lambda: c.delete_workflow_run(o, r, run_id))
        if op == "jobs":
            _need(run_id, "run_id", op)
            return _run(lambda: c.list_run_jobs(o, r, run_id, per_page=per_page,
                                                page=page))
        if op == "job":
            _need(job_id, "job_id", op)
            return _run(lambda: c.get_job(o, r, job_id))
        if op == "logs":
            _need(job_id, "job_id", op)
            return _run(lambda: {"job_id": job_id,
                                 "url": c.get_job_logs_url(o, r, job_id),
                                 "note": ("signed URL valid ~1 minute, to "
                                          "be downloaded WITHOUT an "
                                          "authentication header; null = logs "
                                          "expired or absent")})
        if op == "artifacts":
            return _run(lambda: c.list_artifacts(o, r, run_id=run_id,
                                                 per_page=per_page, page=page))
        if op == "artifact":
            _need(artifact_id, "artifact_id", op)
            return _run(lambda: c.get_artifact(o, r, artifact_id))
        if op == "download":
            _need(artifact_id, "artifact_id", op)
            return _run(lambda: {"artifact_id": artifact_id,
                                 "url": c.get_artifact_download_url(o, r, artifact_id),
                                 "note": ("ephemeral signed URL, without an "
                                          "authentication header; null = artifact "
                                          "expired (90 days by default)")})
        if op == "delete_artifact":
            _need(artifact_id, "artifact_id", op)
            return _run(lambda: c.delete_artifact(o, r, artifact_id))
        raise _bad_op(op, "workflows | workflow | dispatch | runs | run | cancel | "
                          "rerun | rerun_failed | delete_run | jobs | job | logs | "
                          "artifacts | artifact | download | delete_artifact")

    # --- search ---------------------------------------------------------------

    @mcp.tool()
    def github_search(
        op: Literal["repos", "code", "issues", "users", "commits"] = "repos",
        q: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
        page: Optional[int] = None,
        per_page: int = 30,
    ) -> Any:
        """GitHub — search: repositories, code, issues/PRs, accounts, commits.

        `q` takes GitHub's qualifier syntax, passed as-is:
        `repo:`, `org:`, `language:`, `is:issue` / `is:pr`, `state:`, `in:file`…

        ⚠️ **Cap of 1,000 results, whatever `total_count` announces.** This
        counter is an estimate of the corpus, NOT the number of retrievable
        rows: beyond that, GitHub answers 422. The response therefore carries a computed
        `troncature`, which is also true when GitHub **gave up the
        search midway** (`incomplete_results`) — two causes
        invisible otherwise.

        ⚠️ **Code search has its own rules**, and they explain most of the
        "why doesn't it find it?" questions: only the default branch is
        indexed, files larger than 384 KB are not, and at least one real
        term is needed — `repo:x` alone is not enough. It does not return
        the file's content: read it afterwards with `github_files op='read'`.

        ⚠️ Its own, low usage limit (~30 requests/minute), an order of
        magnitude below the rest of the API.

        Args:
            op: repos | code | issues | users | commits.
            q: the query, GitHub syntax (required).
            sort: depends on op — stars/forks/updated (repos), indexed (code),
                comments/created/updated (issues). Absent = by relevance.
            order: asc | desc.
            page: page number.
            per_page: rows per page (1-100, default 30).
        """
        c = _client()
        _need(q, "q", op)
        fns = {"repos": c.search_repositories, "code": c.search_code,
               "issues": c.search_issues, "users": c.search_users,
               "commits": c.search_commits}
        fn = fns.get(op)
        if fn is None:
            raise _bad_op(op, "repos | code | issues | users | commits")
        payload = _run(lambda: fn(q, sort=sort, order=order,
                                  per_page=per_page, page=page))
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["troncature"] = {
                "tronque": c.search_is_truncated(payload),
                "pourquoi": ("GitHub only serves the first 1,000 results, "
                             "and sometimes gives up the search midway "
                             "(incomplete_results) — total_count is thus not "
                             "a number of retrievable rows"),
            }
        return payload
