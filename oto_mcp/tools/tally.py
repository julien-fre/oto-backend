"""Tally — online forms: forms, questions, blocks, responses,
analytics, workspaces, organization, webhooks.

Wraps `oto.tools.tally.client.TallyClient` (Bearer, `https://api.tally.so`).
keyed `api_key`, **byo-only**: a Tally key is tied to ONE user, inherits
their rights (no fine-grained scope exists) and stops working if they leave
the organization — so there cannot be a shared platform key.

38 operations, grouped into **SIX tools**, verb in `op`:
- `tally_form` — forms, questions, blocks (read + authoring)
- `tally_submission` — the responses
- `tally_analytics` — the five statistics views
- `tally_workspace` — workspaces and folders
- `tally_account` — the current user, members, invitations
- `tally_webhook` — event subscriptions and delivery log

**No param is silently dropped**: an argument an `op` does not use
is REJECTED rather than ignored (silae convention `_refuse_ignored`) —
`tally_submission(op="get", filter="completed")` would return ONE response while
suggesting that the filter filtered.

## What the tool layer adds on top of the transport

**1. The questions x responses join.** The API returns responses in
RELATIONAL form: `questions[]` once per page, and each
`submissions[].responses[]` points into it by `questionId`. Raw, an agent has to
do the join itself before reading a single field. `tally_submission`
does it: each response carries `answers: [{question_id, title, type, answer,
formatted}]`, and an `answers_by_title` **only if the titles are unique
on that form** (otherwise the key is absent and `title_collisions` says
why — we never return a map that silently overwrites an answer).

**2. `dry_run` on EVERY mutation** (oto convention: `email_send`, LinkedIn,
the Folk dispatchers). Validation is identical with or without it; only the final
mutating call is skipped. Where the API offers a read of the targeted object, the
preview is a REAL diff (`changes: {field: {from, to}}`), not an echo of what
would be sent — an echo does not protect against the real risk, which is overwriting an
existing value. Where no read exists, we SAY so
(`current_available: false`) instead of fabricating a diff.

**3. The merge of `PATCH /webhooks/{id}`.** Despite the verb, it is a full
REPLACEMENT: `formId`, `url`, `eventTypes` and `isEnabled` are all required. The
tool re-reads the webhook (`GET /webhooks`, there is no `GET /webhooks/{id}`)
and merges, so that an `op="update"` that only passes `is_enabled=False`
does not erase the URL.

## Destructive operations — exposed, and marked

At explicit request: coverage is complete, including the four
calls that destroy something. They are not hidden, they are annotated
and all accept `dry_run`:
- `tally_submission(op="delete")` — **no trash documented on Tally's side
  for responses**: it is a respondent's answer, lost.
- `tally_account(op="remove_user")` — removes someone from the organization AND kills
  every API key they had created, potentially the one making the call.
- `tally_workspace(op="delete")` / `op="delete_folder"` — take the contained
  forms with them (in the trash, restorable).
- `tally_form(op="delete")` — trash, restorable.

## Checked against the spec, NOT live-tested

This whole file is derived from the real OpenAPI 3.0.1 spec
(`developers.tally.so/api-reference/openapi.json`, read on 2026-08-31): body
shapes, `required`, enums, `limit` bounds. **No real call has been
made yet** — no `tly-` key was available. Nothing here claims
to have run. In particular, the default value of the `tally-version`
header (`2026-08-04`, the last entry of the public changelog) remains to be
confirmed against a real key.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

#: Periods accepted by the five analytics endpoints (`period` is REQUIRED).
_PERIODS = ("today", "yesterday", "24h", "7d", "30d", "3m", "6m", "12m", "all")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided: Any) -> None:
    """An argument provided that THIS op does not use is an error of intent,
    not a detail — otherwise `tally_form(op="get", limit=5)` would return ONE
    form while suggesting that `limit` bounded something."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} does not use `{name}` — {hint}")


def _need(op: str, **required: Any) -> None:
    """A missing required argument is reported before the network call."""
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise _bad(f"op={op!r} requires {', '.join('`' + m + '`' for m in missing)}.")


#: Details added to a 401's message depending on the call — the base message
#: NEVER assumes the key is at fault (see `_upstream_message`).
_401_HINTS = {
    "webhook_list": "no webhook has ever been created on this account "
                    "(the webhooks integration is born at the first `op=\"create\"`)",
    "blocks": "reading or writing blocks is not open on your plan",
    "workspace_write": "managing workspaces requires a Pro plan",
    "question_write": "renaming a question is not open on your plan",
}


def _upstream_message(e: Any, context: Optional[str] = None) -> str:
    status = e.status_code
    if status == 401:
        # ⚠️ Tally returns 401 for a PLAN or feature GATE as much as
        # for an invalid key — verified live on 2026-08-31 on a FREE
        # account: `GET /webhooks` (as long as no webhook exists), blocks,
        # `POST /workspaces` and `PATCH .../questions/{id}` all return 401 with
        # a perfectly valid key. Asserting "key rejected" therefore sends
        # the user to regenerate a healthy key, and leaves them searching where there
        # is nothing. We name both causes, and give the probe that settles it.
        hint = _401_HINTS.get(context)
        return ("Tally answered 401. At Tally, a 401 does NOT mean \"invalid "
                "key\": it is also its way of refusing a feature that "
                "your plan does not open"
                + (f" — here, {hint}" if hint else "")
                + ". Settle it with `tally_account(op=\"me\")`: if it answers, the key is "
                  "good and it is the plan (or the account state) that blocks; if it fails "
                  "too, set the key again (Tally: Settings → API keys).")
    if status == 403:
        return (f"Tally rejected the call (HTTP {status}) — check the key set on this "
                "connector (Tally: Settings → API keys). Reminder: a Tally key is tied "
                "to ONE user and stops working if they leave the organization.")
def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """"Test the connection" probe: the current user. No parameter,
    free, and it fails exactly where the key is at fault."""
    from oto.tools.tally.client import TallyClient
    TallyClient(api_key=fields["key"]).get_me()


# ---------------------------------------------------------------------------
# Shaping of responses (the questions x responses join)
# ---------------------------------------------------------------------------

def _index_questions(questions: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    return {q.get("id"): q for q in (questions or []) if isinstance(q, dict) and q.get("id")}


def _shape_submission(sub: Dict[str, Any], by_id: Dict[str, Dict[str, Any]],
                      unique_titles: bool) -> Dict[str, Any]:
    """Makes a response READABLE: each `responses[]` joins its question.

    What the API gives (`{questionId, answer, formattedAnswer}`) is only
    interpretable with the `questions` table delivered alongside. We resolve it here
    once and for all rather than letting each agent redo it.
    """
    answers = []
    for r in sub.get("responses") or []:
        if not isinstance(r, dict):
            continue
        q = by_id.get(r.get("questionId")) or {}
        answers.append({
            "question_id": r.get("questionId"),
            "title": q.get("title"),
            "type": q.get("type"),
            "answer": r.get("answer"),
            # `formattedAnswer` only exists on recent API versions
            # (cf. the `tally-version` header pinned client-side): if absent, we
            # do not fabricate it.
            "formatted": r.get("formattedAnswer"),
        })
    shaped = {
        "id": sub.get("id"),
        "form_id": sub.get("formId"),
        "respondent_id": sub.get("respondentId"),
        "is_completed": sub.get("isCompleted"),
        "submitted_at": sub.get("submittedAt"),
        # Tally renders each response as a PDF and a web preview — useful to
        # archive the document without rebuilding it yourself.
        "preview_url": sub.get("previewUrl"),
        "pdf_url": sub.get("pdfUrl"),
        "answers": answers,
    }
    if unique_titles:
        shaped["answers_by_title"] = {
            a["title"]: (a["formatted"] if a["formatted"] is not None else a["answer"])
            for a in answers if a["title"]
        }
    return shaped


def _shape_submissions_page(payload: Any) -> Any:
    """Adds the joined view WITHOUT removing the original payload.

    We never replace what the API returned: `questions` and the responses
    stay as they are under `raw_*`, so that a case not anticipated here remains
    workable by the caller.

    ⚠️ TWO envelopes, not one. `GET /forms/{f}/submissions` returns
    `{questions, submissions: [...]}`; `GET /forms/{f}/submissions/{s}` returns
    `{questions, submission: {...}}` — SINGULAR. Reading only the plural
    made reading ONE response silently empty: the payload did arrive,
    and the tool announced zero responses.
    """
    if not isinstance(payload, dict):
        return payload
    questions = payload.get("questions")
    single = payload.get("submission")
    if single is not None and payload.get("submissions") is None:
        payload = {**payload, "submissions": [single] if isinstance(single, dict) else []}
    by_id = _index_questions(questions)
    titles = [q.get("title") for q in by_id.values() if q.get("title")]
    collisions = sorted({t for t in titles if titles.count(t) > 1})
    unique = not collisions
    subs = [_shape_submission(s, by_id, unique)
            for s in (payload.get("submissions") or []) if isinstance(s, dict)]
    out = {
        "page": payload.get("page"),
        "limit": payload.get("limit"),
        "has_more": payload.get("hasMore"),
        "counts": payload.get("totalNumberOfSubmissionsPerFilter"),
        "questions": [{"id": q.get("id"), "type": q.get("type"), "title": q.get("title")}
                      for q in by_id.values()],
        "submissions": subs,
    }
    if collisions:
        # Two questions with the same title: a map by title would overwrite
        # one. We omit it and say which ones.
        out["title_collisions"] = collisions
        out["note"] = ("`answers_by_title` is omitted: several questions share the same "
                       "title on this form. Use `answers[].question_id`.")
    if payload.get("submissions") and not subs:
        out["submissions"] = payload.get("submissions")
    out["raw_questions"] = questions
    if single is not None:
        out["raw_submission"] = single
    return out


def _diff(current: Optional[Dict[str, Any]], patch: Dict[str, Any],
          field_map: Dict[str, str]) -> Dict[str, Any]:
    """Preview of a modification: a REAL diff when the current object is
    readable, an explicit admission when it is not."""
    if current is None:
        return {"dry_run": True, "current_available": False, "would_send": patch}
    changes = {}
    for api_field, value in patch.items():
        if value is None:
            continue
        before = current.get(field_map.get(api_field, api_field))
        if before != value:
            changes[api_field] = {"from": before, "to": value}
    return {"dry_run": True, "current_available": True, "changes": changes,
            "unchanged": not changes}


def register(mcp: FastMCP) -> None:
    from oto.tools.tally.client import TallyClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("tally", _verify)

    def _client() -> TallyClient:
        key, _ = access.resolve_api_key("tally")
        return TallyClient(api_key=key)

    def _run(fn, context: Optional[str] = None):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e, context))

    # ================================================================
    # Forms, questions, blocks
    # ================================================================

    @mcp.tool()
    def tally_form(
        op: Literal["list", "get", "create", "update", "delete",
                    "questions", "update_question", "blocks", "update_blocks"] = "list",
        form_id: Optional[str] = None,
        question_id: Optional[str] = None,
        name: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[Literal["BLANK", "DRAFT", "PUBLISHED"]] = None,
        blocks: Optional[List[dict]] = None,
        settings: Optional[dict] = None,
        workspace_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        template_id: Optional[str] = None,
        workspace_ids: Optional[List[str]] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> object:
        """Tally forms — list, read, author, and delete them.

        Returns —
            The Tally payload for reads; the created/updated object for
            writes; a `{dry_run: true, ...}` preview when `dry_run` is set.

        Args:
            op: which action.
                · "list" — the forms this key can see. `page` (1-based),
                  `limit` (1-500, default 50), `workspace_ids` to filter
                  (resolve ids with `tally_workspace(op="list")`).
                · "get" — one form with all its blocks and settings (`form_id`).
                · "create" — a new form. `blocks` and `status` are required by
                  the API; pass `blocks=[]` for an empty one. Optional
                  `workspace_id`, `folder_id`, `template_id`, `settings`.
                · "update" — change `name`, `status`, `blocks` or `settings`
                  on `form_id`.
                · "delete" — move `form_id` to the trash (restorable).
                · "questions" — the answerable questions of `form_id`. Their
                  `id` is what a submission's `question_id` points at.
                · "update_question" — rename `question_id` on `form_id` via
                  `title` (the only editable field).
                · "blocks" — the authoring view of `form_id`: every block,
                  including layout ones that are never answered.
                · "update_blocks" — replace the block list of `form_id`.
            blocks: ordered list of block objects
                (`{uuid, type, groupUuid, groupType, payload}`). 39 block
                types exist — see https://developers.tally.so/blocks-reference
                for each payload shape. ⚠️ On "update" and "update_blocks"
                this REPLACES the whole array: a block you leave out is
                deleted. Read the current set with op="blocks" first.
            settings: form settings — `language`, `isClosed`, `closeDate`,
                `closeTime`, `submissionsLimit`, `redirectOnCompletion`,
                `hasProgressBar`, `hasPartialSubmissions`, `password`,
                `submissionsDataRetentionDuration`/`Unit`, and the self /
                respondent email-notification block.
            status: "BLANK", "DRAFT" or "PUBLISHED". "DELETED" was removed by
                Tally on 2026-08-04 and now returns 400 — it left forms
                unrecoverable; use op="delete" instead.
            dry_run: validate and preview without writing. On "update" and
                "delete" the preview is a real diff against the current form.
        """
        c = _client()

        if op == "list":
            _refuse_ignored(op, "it lists the forms",
                            form_id=form_id, question_id=question_id, name=name,
                            title=title, status=status, blocks=blocks, settings=settings,
                            workspace_id=workspace_id, folder_id=folder_id,
                            template_id=template_id)
            return _run(lambda: c.list_forms(page=page, limit=limit,
                                             workspaceIds=workspace_ids))

        if op == "get":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "it reads ONE form", page=page, limit=limit,
                            workspace_ids=workspace_ids, blocks=blocks, settings=settings)
            return _run(lambda: c.get_form(form_id))

        if op == "questions":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "it lists the form's questions",
                            page=page, limit=limit, blocks=blocks, settings=settings)
            return _run(lambda: c.list_questions(form_id))

        if op == "blocks":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "it reads the form's blocks",
                            page=page, limit=limit, blocks=blocks, settings=settings)
            return _run(lambda: c.get_blocks(form_id), "blocks")

        if op == "create":
            _need(op, blocks=blocks, status=status)
            _refuse_ignored(op, "it creates a form",
                            form_id=form_id, question_id=question_id, title=title,
                            page=page, limit=limit, workspace_ids=workspace_ids)
            body = {"workspaceId": workspace_id, "folderId": folder_id,
                    "templateId": template_id, "settings": settings, "name": name}
            if dry_run:
                return {"dry_run": True, "would_create": {
                    "status": status, "blocks": len(blocks), **{k: v for k, v in body.items()
                                                                if v is not None}}}
            return _run(lambda: c.create_form(
                blocks=blocks, status=status,
                **{k: v for k, v in body.items() if v is not None}))

        if op == "update":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "it edits ONE form",
                            question_id=question_id, title=title, page=page, limit=limit,
                            workspace_ids=workspace_ids, template_id=template_id)
            patch = {"name": name, "status": status, "blocks": blocks, "settings": settings}
            if all(v is None for v in patch.values()):
                raise _bad("op='update' requires at least one of `name`, `status`, "
                           "`blocks`, `settings`.")
            if dry_run:
                current = _run(lambda: c.get_form(form_id))
                return _diff(current if isinstance(current, dict) else None,
                             {k: v for k, v in patch.items() if v is not None}, {})
            return _run(lambda: c.update_form(
                form_id, **{k: v for k, v in patch.items() if v is not None}))

        if op == "delete":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "it deletes ONE form", blocks=blocks,
                            settings=settings, page=page, limit=limit)
            if dry_run:
                current = _run(lambda: c.get_form(form_id))
                return {"dry_run": True, "would_delete": "form", "form_id": form_id,
                        "current": current,
                        "recoverable": "yes — Tally moves the form to the trash"}
            return _run(lambda: c.delete_form(form_id))

        if op == "update_question":
            _need(op, form_id=form_id, question_id=question_id, title=title)
            _refuse_ignored(op, "it renames ONE question", blocks=blocks, settings=settings,
                            name=name, status=status, page=page, limit=limit)
            if dry_run:
                qs = _run(lambda: c.list_questions(form_id))
                items = qs if isinstance(qs, list) else (qs or {}).get("questions") or []
                cur = next((q for q in items if isinstance(q, dict)
                            and q.get("id") == question_id), None)
                return _diff(cur, {"title": title}, {})
            return _run(lambda: c.update_question(form_id, question_id, title=title),
                        "question_write")

        if op == "update_blocks":
            _need(op, form_id=form_id, blocks=blocks)
            _refuse_ignored(op, "it replaces the blocks", question_id=question_id,
                            title=title, name=name, status=status, page=page, limit=limit)
            if dry_run:
                current = _run(lambda: c.get_blocks(form_id), "blocks")
                n_before = len(current) if isinstance(current, list) else None
                return {"dry_run": True, "current_available": n_before is not None,
                        "blocks_before": n_before, "blocks_after": len(blocks),
                        "warning": "PATCH blocks REPLACES the entire list — "
                                   "any block absent from `blocks` is deleted."}
            return _run(lambda: c.update_blocks(
                form_id, blocks, **({"settings": settings} if settings else {})), "blocks")

        raise _bad(f"unknown op: {op!r}")

    # ================================================================
    # Responses
    # ================================================================

    @mcp.tool()
    def tally_submission(
        op: Literal["list", "get", "delete"] = "list",
        form_id: Optional[str] = None,
        submission_id: Optional[str] = None,
        filter: Optional[Literal["all", "completed", "partial"]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        after_id: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        raw: bool = False,
        dry_run: bool = False,
    ) -> object:
        """Form submissions — the answers people sent.

        The API returns these RELATIONALLY: a `questions` table once per page,
        and each response pointing into it by `questionId`. This tool joins
        them, so every submission carries `answers: [{question_id, title,
        type, answer, formatted}]` plus `answers_by_title` when the form's
        question titles are unique. Pass `raw=True` for Tally's untouched
        payload.

        Returns —
            "list" — `{page, limit, has_more, counts, questions, submissions}`
            where each submission carries `answers`, `preview_url` and
            `pdf_url`. File-upload questions answer with the uploaded file's
            URL, so `answers` is also how you reach attachments.

        Args:
            op: which action.
                · "list" — submissions of `form_id`.
                · "get" — one submission (`form_id` + `submission_id`).
                · "delete" — ⚠️ DESTROYS a respondent's answer. Tally documents
                  a trash for forms and workspaces but NOT for submissions:
                  treat this as permanent. Supports `dry_run`.
            filter: "all", "completed" or "partial". "partial" is how you see
                who started and abandoned — a follow-up signal, not noise.
            after_id: return submissions that came AFTER this submission id.
                This is the right cursor for incremental ingestion (poll the
                last id you stored) and is cheaper than date windows.
            start_date: ISO 8601 — submitted on or after.
            end_date: ISO 8601 — submitted on or before.
            page: 1-based page number.
            limit: 1-500, default 50. `limit=1` is the cheap way to read the
                per-filter counts without pulling any rows.
            raw: return Tally's payload as-is, skipping the join.
            dry_run: on "delete", preview the submission instead of deleting.
        """
        c = _client()

        if op == "list":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "it lists the responses", submission_id=submission_id)
            payload = _run(lambda: c.list_submissions(
                form_id, page=page, limit=limit, filter=filter,
                startDate=start_date, endDate=end_date, afterId=after_id))
            return payload if raw else _shape_submissions_page(payload)

        if op == "get":
            _need(op, form_id=form_id, submission_id=submission_id)
            _refuse_ignored(op, "it reads ONE response", filter=filter, start_date=start_date,
                            end_date=end_date, after_id=after_id, page=page, limit=limit)
            payload = _run(lambda: c.get_submission(form_id, submission_id))
            return payload if raw else _shape_submissions_page(payload)

        if op == "delete":
            _need(op, form_id=form_id, submission_id=submission_id)
            _refuse_ignored(op, "it deletes ONE response", filter=filter,
                            start_date=start_date, end_date=end_date, after_id=after_id,
                            page=page, limit=limit)
            if dry_run:
                current = _run(lambda: c.get_submission(form_id, submission_id))
                return {"dry_run": True, "would_delete": "submission",
                        "submission_id": submission_id,
                        "current": current if raw else _shape_submissions_page(current),
                        "recoverable": "no — no trash documented for responses"}
            return _run(lambda: c.delete_submission(form_id, submission_id))

        raise _bad(f"unknown op: {op!r}")

    # ================================================================
    # Analytics
    # ================================================================

    @mcp.tool()
    def tally_analytics(
        op: Literal["metrics", "visits", "submissions", "dimensions", "drop_off"] = "metrics",
        form_id: Optional[str] = None,
        period: Optional[str] = None,
    ) -> object:
        """Form analytics — five views, one required time window.

        Args:
            op: which view.
                · "metrics" — aggregate: visits, submissions, completion rate.
                · "visits" — visit counts over time.
                · "submissions" — completed and partial counts over time.
                · "dimensions" — visitors by source, browser, OS, device,
                  location.
                · "drop_off" — where respondents abandon, question by
                  question. The one that tells you WHY a form under-converts.
            form_id: the form.
            period: REQUIRED — "today", "yesterday", "24h", "7d", "30d",
                "3m", "6m", "12m" or "all".
        """
        _need(op, form_id=form_id, period=period)
        if period not in _PERIODS:
            raise _bad(f"`period` must be one of {', '.join(_PERIODS)} — got {period!r}.")
        c = _client()
        # EXPLICIT calls and not a dict dispatch: the version-skew
        # guard (`tests/test_tools_client_methods_exist.py`) reads the
        # `c.method()` calls statically — a dict of bound methods falls out of
        # its scope SILENTLY, and these five would stop being verified
        # against the pinned oto-core tag (the hole experienced in `tools/apollo.py`).
        if op == "metrics":
            return _run(lambda: c.analytics_metrics(form_id, period))
        if op == "visits":
            return _run(lambda: c.analytics_visits(form_id, period))
        if op == "submissions":
            return _run(lambda: c.analytics_submissions(form_id, period))
        if op == "dimensions":
            return _run(lambda: c.analytics_dimensions(form_id, period))
        if op == "drop_off":
            return _run(lambda: c.analytics_drop_off(form_id, period))
        raise _bad(f"unknown op: {op!r}")

    # ================================================================
    # Workspaces and folders
    # ================================================================

    @mcp.tool()
    def tally_workspace(
        op: Literal["list", "get", "create", "update", "delete",
                    "folders", "create_folder", "update_folder", "delete_folder"] = "list",
        workspace_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        name: Optional[str] = None,
        parent_id: Optional[str] = None,
        page: Optional[int] = None,
        dry_run: bool = False,
    ) -> object:
        """Workspaces and folders — where forms live.

        Start here when you need a `workspace_id`: `tally_form(op="list")`
        filters on workspace ids, and this is what resolves a name to one.

        Args:
            op: which action.
                · "list" — workspaces with members, pending invites, folders.
                · "get" — one workspace (`workspace_id`).
                · "create" — a workspace named `name`. Pro plan.
                · "update" — rename `workspace_id` to `name`.
                · "delete" — ⚠️ trashes the workspace AND every form in it
                  (restorable).
                · "folders" — folders of `workspace_id`. Pro plan.
                · "create_folder" — `name`, optional `parent_id` to nest.
                · "update_folder" — rename `folder_id` to `name`.
                · "delete_folder" — ⚠️ deletes the folder and its ENTIRE
                  subtree, moving contained forms to the trash.
            dry_run: validate and preview without writing. Real diffs where
                Tally lets the current object be read.
        """
        c = _client()

        def _folders(ws: str):
            got = _run(lambda: c.list_folders(ws))
            return got if isinstance(got, list) else (got or {}).get("folders") or []

        if op == "list":
            _refuse_ignored(op, "it lists the workspaces",
                            workspace_id=workspace_id, folder_id=folder_id,
                            name=name, parent_id=parent_id)
            return _run(lambda: c.list_workspaces(page=page))

        if op == "get":
            _need(op, workspace_id=workspace_id)
            _refuse_ignored(op, "it reads ONE workspace", folder_id=folder_id, name=name,
                            parent_id=parent_id, page=page)
            return _run(lambda: c.get_workspace(workspace_id))

        if op == "folders":
            _need(op, workspace_id=workspace_id)
            _refuse_ignored(op, "it lists the folders", folder_id=folder_id, name=name,
                            parent_id=parent_id, page=page)
            return _run(lambda: c.list_folders(workspace_id))

        if op == "create":
            _need(op, name=name)
            _refuse_ignored(op, "it creates a workspace", workspace_id=workspace_id,
                            folder_id=folder_id, parent_id=parent_id, page=page)
            if dry_run:
                return {"dry_run": True, "would_create": "workspace", "name": name}
            return _run(lambda: c.create_workspace(name), "workspace_write")

        if op == "update":
            _need(op, workspace_id=workspace_id, name=name)
            _refuse_ignored(op, "it renames ONE workspace", folder_id=folder_id,
                            parent_id=parent_id, page=page)
            if dry_run:
                current = _run(lambda: c.get_workspace(workspace_id))
                return _diff(current if isinstance(current, dict) else None,
                             {"name": name}, {})
            return _run(lambda: c.update_workspace(workspace_id, name))

        if op == "delete":
            _need(op, workspace_id=workspace_id)
            _refuse_ignored(op, "it deletes ONE workspace", folder_id=folder_id, name=name,
                            parent_id=parent_id, page=page)
            if dry_run:
                current = _run(lambda: c.get_workspace(workspace_id))
                return {"dry_run": True, "would_delete": "workspace",
                        "workspace_id": workspace_id, "current": current,
                        "warning": "takes ALL the workspace's forms with it",
                        "recoverable": "yes — workspace and forms go to the trash"}
            return _run(lambda: c.delete_workspace(workspace_id))

        if op == "create_folder":
            _need(op, workspace_id=workspace_id, name=name)
            _refuse_ignored(op, "it creates a folder", folder_id=folder_id, page=page)
            if dry_run:
                return {"dry_run": True, "would_create": "folder", "name": name,
                        "workspace_id": workspace_id, "parent_id": parent_id}
            return _run(lambda: c.create_folder(
                workspace_id, name, **({"parentId": parent_id} if parent_id else {})))

        if op == "update_folder":
            _need(op, workspace_id=workspace_id, folder_id=folder_id, name=name)
            _refuse_ignored(op, "it renames ONE folder", parent_id=parent_id, page=page)
            if dry_run:
                cur = next((f for f in _folders(workspace_id)
                            if isinstance(f, dict) and f.get("id") == folder_id), None)
                return _diff(cur, {"name": name}, {})
            return _run(lambda: c.update_folder(workspace_id, folder_id, name))

        if op == "delete_folder":
            _need(op, workspace_id=workspace_id, folder_id=folder_id)
            _refuse_ignored(op, "it deletes ONE folder", name=name, parent_id=parent_id,
                            page=page)
            if dry_run:
                folders = _folders(workspace_id)
                cur = next((f for f in folders
                            if isinstance(f, dict) and f.get("id") == folder_id), None)
                children = [f.get("id") for f in folders
                            if isinstance(f, dict) and f.get("parentId") == folder_id]
                return {"dry_run": True, "would_delete": "folder", "folder_id": folder_id,
                        "current_available": cur is not None, "current": cur,
                        "direct_children": children,
                        "warning": "deletes the folder AND its whole subtree; "
                                   "the contained forms go to the trash"}
            return _run(lambda: c.delete_folder(workspace_id, folder_id))

        raise _bad(f"unknown op: {op!r}")

    # ================================================================
    # Account, members, invitations
    # ================================================================

    @mcp.tool()
    def tally_account(
        op: Literal["me", "users", "remove_user",
                    "invites", "invite", "cancel_invite"] = "me",
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
        invite_id: Optional[str] = None,
        emails: Optional[str] = None,
        workspace_ids: Optional[List[str]] = None,
        timezone: Optional[str] = None,
        dry_run: bool = False,
    ) -> object:
        """The current user, the organization's members, and its invitations.

        `op="me"` is where `organization_id` comes from — every other op here
        needs it, and nothing else hands it to you except a form's own
        `organizationId`.

        Args:
            op: which action.
                · "me" — the authenticated user. Optional `timezone` (IANA).
                · "users" — everyone in `organization_id`.
                · "remove_user" — ⚠️ removes `user_id` from the organization.
                  Only the org creator can remove someone else; anyone may
                  remove themselves. This also KILLS every API key that user
                  created — possibly the one making this call. `dry_run`
                  shows who would go.
                · "invites" — pending invitations.
                · "invite" — invite `emails` into `workspace_ids`. ⚠️ `emails`
                  is a STRING, not a list — that asymmetry is Tally's, not a
                  typo here.
                · "cancel_invite" — cancel `invite_id`. Only its creator can.
            dry_run: preview instead of writing.
        """
        c = _client()

        def _members(org: str):
            got = _run(lambda: c.list_organization_users(org))
            return got if isinstance(got, list) else (got or {}).get("users") or []

        if op == "me":
            _refuse_ignored(op, "it reads the current user",
                            organization_id=organization_id, user_id=user_id,
                            invite_id=invite_id, emails=emails, workspace_ids=workspace_ids)
            return _run(lambda: c.get_me(**({"timezone": timezone} if timezone else {})))

        if op == "users":
            _need(op, organization_id=organization_id)
            _refuse_ignored(op, "it lists the members", user_id=user_id, invite_id=invite_id,
                            emails=emails, workspace_ids=workspace_ids, timezone=timezone)
            return _run(lambda: c.list_organization_users(organization_id))

        if op == "invites":
            _need(op, organization_id=organization_id)
            _refuse_ignored(op, "it lists the invitations", user_id=user_id,
                            invite_id=invite_id, emails=emails,
                            workspace_ids=workspace_ids, timezone=timezone)
            return _run(lambda: c.list_invites(organization_id))

        if op == "remove_user":
            _need(op, organization_id=organization_id, user_id=user_id)
            _refuse_ignored(op, "it removes ONE member", invite_id=invite_id, emails=emails,
                            workspace_ids=workspace_ids, timezone=timezone)
            if dry_run:
                cur = next((u for u in _members(organization_id)
                            if isinstance(u, dict) and u.get("id") == user_id), None)
                return {"dry_run": True, "would_remove": "organization member",
                        "user_id": user_id, "current_available": cur is not None,
                        "current": cur,
                        "warning": "also revokes ALL the API keys created by this "
                                   "user, possibly including this call's own"}
            return _run(lambda: c.remove_organization_user(organization_id, user_id))

        if op == "invite":
            _need(op, organization_id=organization_id, emails=emails,
                  workspace_ids=workspace_ids)
            _refuse_ignored(op, "it creates invitations", user_id=user_id,
                            invite_id=invite_id, timezone=timezone)
            if dry_run:
                return {"dry_run": True, "would_invite": emails,
                        "workspace_ids": workspace_ids}
            return _run(lambda: c.create_invites(organization_id, workspace_ids, emails))

        if op == "cancel_invite":
            _need(op, organization_id=organization_id, invite_id=invite_id)
            _refuse_ignored(op, "it cancels ONE invitation", user_id=user_id, emails=emails,
                            workspace_ids=workspace_ids, timezone=timezone)
            if dry_run:
                invites = _run(lambda: c.list_invites(organization_id))
                items = invites if isinstance(invites, list) else (invites or {}).get("invites") or []
                cur = next((i for i in items
                            if isinstance(i, dict) and i.get("id") == invite_id), None)
                return {"dry_run": True, "would_cancel": "invite", "invite_id": invite_id,
                        "current_available": cur is not None, "current": cur}
            return _run(lambda: c.cancel_invite(organization_id, invite_id))

        raise _bad(f"unknown op: {op!r}")

    # ================================================================
    # Webhooks
    # ================================================================

    @mcp.tool()
    def tally_webhook(
        op: Literal["list", "create", "update", "delete", "events", "retry"] = "list",
        webhook_id: Optional[str] = None,
        event_id: Optional[str] = None,
        form_id: Optional[str] = None,
        url: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        signing_secret: Optional[str] = None,
        http_headers: Optional[List[dict]] = None,
        external_subscriber: Optional[str] = None,
        is_enabled: Optional[bool] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> object:
        """Webhooks — push a form's responses somewhere as they arrive.

        Worth preferring over polling: Tally states webhook deliveries do NOT
        consume the 100 req/min quota. ⚠️ oto is not itself a webhook
        receiver — this registers a URL YOU control (an n8n or Make endpoint,
        your own service). To pull responses into oto, poll
        `tally_submission(op="list", after_id=...)` on a schedule instead.

        Args:
            op: which action.
                · "list" — every webhook across accessible forms. `page`,
                  `limit` (1-100, default 25). Also the ONLY way to read one
                  back: Tally has no `GET /webhooks/{id}`.
                · "create" — needs `form_id`, `url`, `event_types`.
                · "update" — change a webhook. ⚠️ Tally's PATCH is a full
                  REPLACE (`formId`, `url`, `eventTypes`, `isEnabled` all
                  required), so this tool reads the current webhook and merges
                  your changes into it. Pass only what you want changed.
                · "delete" — stop deliveries. If it is the form's last
                  webhook, Tally also marks the integration deleted.
                · "events" — the delivery log for `webhook_id`: status,
                  response code, retry state. `page`.
                · "retry" — re-deliver `event_id`. This fires a REAL request
                  at the endpoint; if the receiver is not idempotent, a retry
                  is a second delivery, not a correction.
            event_types: currently `["FORM_RESPONSE"]` is the only value the
                API defines.
            signing_secret: ⚠️ YOU supply it (Tally does not mint one). Without
                it, deliveries are unsigned and the receiver cannot tell a
                genuine payload from a forged one. It is never echoed back by
                this tool.
            http_headers: `[{"name": ..., "value": ...}]` — extra headers sent
                with each delivery, e.g. an auth header for the receiver.
            external_subscriber: your own identifier for whoever owns this
                subscription.
            dry_run: preview instead of writing; on "update" a real diff
                against the current webhook.
        """
        c = _client()

        def _find(wid: str) -> Optional[Dict[str, Any]]:
            """There is no GET /webhooks/{id}: we paginate the list."""
            seen_page = 1
            while seen_page <= 20:  # hard bound: 20 x 100 = 2000 webhooks
                got = _run(lambda p=seen_page: c.list_webhooks(page=p, limit=100),
                           "webhook_list")
                items = got if isinstance(got, list) else (got or {}).get("webhooks") or []
                for w in items:
                    if isinstance(w, dict) and w.get("id") == wid:
                        return w
                if not items or (isinstance(got, dict) and not got.get("hasMore")):
                    return None
                seen_page += 1
            return None

        if op == "list":
            _refuse_ignored(op, "it lists the webhooks", webhook_id=webhook_id,
                            event_id=event_id, url=url, event_types=event_types,
                            signing_secret=signing_secret, http_headers=http_headers,
                            external_subscriber=external_subscriber, is_enabled=is_enabled)
            return _run(lambda: c.list_webhooks(page=page, limit=limit), "webhook_list")

        if op == "events":
            _need(op, webhook_id=webhook_id)
            _refuse_ignored(op, "it reads the delivery log", event_id=event_id,
                            url=url, event_types=event_types, signing_secret=signing_secret,
                            http_headers=http_headers, is_enabled=is_enabled, limit=limit)
            return _run(lambda: c.list_webhook_events(webhook_id, page=page),
                        "webhook_list")

        if op == "create":
            _need(op, form_id=form_id, url=url, event_types=event_types)
            _refuse_ignored(op, "it creates a webhook", webhook_id=webhook_id,
                            event_id=event_id, is_enabled=is_enabled, page=page, limit=limit)
            extra = {"signingSecret": signing_secret, "httpHeaders": http_headers,
                     "externalSubscriber": external_subscriber}
            if dry_run:
                return {"dry_run": True, "would_create": "webhook", "form_id": form_id,
                        "url": url, "event_types": event_types,
                        "signed": signing_secret is not None,
                        "header_names": [h.get("name") for h in (http_headers or [])]}
            return _run(lambda: c.create_webhook(
                form_id, url, event_types,
                **{k: v for k, v in extra.items() if v is not None}))

        if op == "update":
            _need(op, webhook_id=webhook_id)
            _refuse_ignored(op, "it edits ONE webhook", event_id=event_id,
                            external_subscriber=external_subscriber, page=page, limit=limit)
            current = _find(webhook_id)
            if current is None:
                raise _bad(
                    f"webhook {webhook_id!r} not found in the list. `PATCH /webhooks/"
                    "{id}` is a full REPLACEMENT (formId, url, eventTypes, isEnabled "
                    "all required): without the current state, a partial edit "
                    "would erase the fields not provided. Check the id with op='list'.")
            merged = {
                "form_id": form_id if form_id is not None else current.get("formId"),
                "url": url if url is not None else current.get("url"),
                "event_types": (event_types if event_types is not None
                                else current.get("eventTypes")),
                "is_enabled": (is_enabled if is_enabled is not None
                               else current.get("isEnabled")),
            }
            missing = [k for k, v in merged.items() if v is None]
            if missing:
                raise _bad(f"cannot rebuild the webhook: {', '.join(missing)} "
                           "missing from the current state — pass them explicitly.")
            if dry_run:
                asked = {"formId": form_id, "url": url, "eventTypes": event_types,
                         "isEnabled": is_enabled}
                out = _diff(current, {k: v for k, v in asked.items() if v is not None}, {})
                out["merged_payload_fields"] = sorted(merged)
                out["note"] = ("PATCH is a full replacement; fields not provided "
                               "are taken from the current state, not left empty.")
                return out
            extra = {"signingSecret": signing_secret, "httpHeaders": http_headers}
            return _run(lambda: c.update_webhook(
                webhook_id, merged["form_id"], merged["url"], merged["event_types"],
                merged["is_enabled"],
                **{k: v for k, v in extra.items() if v is not None}))

        if op == "delete":
            _need(op, webhook_id=webhook_id)
            _refuse_ignored(op, "it deletes ONE webhook", event_id=event_id, url=url,
                            event_types=event_types, signing_secret=signing_secret,
                            http_headers=http_headers, is_enabled=is_enabled,
                            page=page, limit=limit)
            if dry_run:
                current = _find(webhook_id)
                return {"dry_run": True, "would_delete": "webhook",
                        "webhook_id": webhook_id, "current_available": current is not None,
                        "current": current,
                        "note": "if this is the form's last webhook, Tally also marks "
                                "the webhooks integration as deleted"}
            return _run(lambda: c.delete_webhook(webhook_id))

        if op == "retry":
            _need(op, webhook_id=webhook_id, event_id=event_id)
            _refuse_ignored(op, "it replays ONE event", url=url, event_types=event_types,
                            signing_secret=signing_secret, http_headers=http_headers,
                            is_enabled=is_enabled, page=page, limit=limit)
            if dry_run:
                return {"dry_run": True, "would_retry": event_id, "webhook_id": webhook_id,
                        "warning": "a retry is a REAL HTTP delivery; if the receiver "
                                   "is not idempotent, it is a second send, not a "
                                   "correction"}
            return _run(lambda: c.retry_webhook_event(webhook_id, event_id))

        raise _bad(f"unknown op: {op!r}")
