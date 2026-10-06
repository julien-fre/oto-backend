"""Gmail — oto-core surface (GmailClient) exposed per user, multi-account.

Each user connects one or more Google accounts on
`https://manage.oto.cx/` (Google section) through the unified OAuth flow (scope
`gmail.modify`). The `gmail_*` tools act on the default account, or on the
account targeted by the `account` parameter (the email address).

No platform key: access is strictly per-user via OAuth (like the
datastore and WhatsApp), so no `resolve_api_key` here.

**Consolidated surface (ADR 0047 §Amendment, applied to the gmail product)**: one tool
per business OBJECT, the verb as an `op` parameter — `gmail_message` (search/get/
attachment/drafts/archive/trash: everything that designates a message in the mailbox, by
query or by id). Two tools stay ALONE:
- `gmail_list_accounts`: no parameter (it enumerates the `account` values the others
  consume) — same case as `zoho_modules`, merging pure discovery
  homogenizes nothing;
- `gmail_compose`: its ~12 composition parameters (body/to/subject/reply_to/cc/
  bcc/html/from_name/markdown/attachments/mode/sign) overlap NONE of the parameters of
  the ops above — it is a disjoint variant, which would weigh in the schema
  exactly what it weighs today separate (criterion = parameter
  homogeneity, not counting).

⚠️ This module WRITES to the user's mailbox: `op="archive"`/`op="trash"`
(gmail_message) and `gmail_compose` (real send). The default of `gmail_message` is
`op="search"` — a READ: a call without `op` can neither write nor delete.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Literal, Optional, Union

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, file_content, file_source
from ..auth import google as google_oauth

# Ops of `gmail_message`, in reads → writes order. Single source: input
# validation AND the refusal message derive from it, so an added op
# cannot be accepted without being announced (nor the reverse).
_MESSAGE_READ_OPS = ("search", "get", "attachment", "drafts")
_MESSAGE_WRITE_OPS = ("archive", "trash")
_MESSAGE_OPS = _MESSAGE_READ_OPS + _MESSAGE_WRITE_OPS
_MESSAGE_OPS_ERROR = (
    "op must be 'search', 'get', 'attachment', 'drafts', 'archive' or 'trash'")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Required argument for THIS op — actionable error, never a fallback.

    An EMPTY value counts as absent: `message_ids=[]` on `op='trash'`
    would return a `{"trashed": []}` that passes for a success although nothing was
    requested, and `query=""` on `op='search'` would sweep the whole mailbox.
    """
    if value is None or (isinstance(value, (str, list)) and not value):
        raise _bad(f"op='{op}' requires {name}")
    return value


def _client_for_user(account: Optional[str] = None):
    """Instantiates an oto-core GmailClient with the user's credentials.

    `account` (email) targets a specific account; None = default account.
    Raises an actionable McpError if no Google account is connected.
    """
    sub = access.current_user_sub_or_raise()
    try:
        creds = google_oauth.credentials_for(sub, account=account, service="gmail")
    except RuntimeError as e:
        raise _bad(str(e))
    from oto.tools.google.gmail.lib.gmail_client import GmailClient
    return GmailClient(credentials=creds)


_GOOGLE_CLIENT_TIMEOUT_S = 20
# oto-backend#867 lot 2 — `_client_for_user` can trigger a token refresh
# (`google_oauth.credentials_for` → `_refresh_access_token`, synchronous HTTP
# 15s), inside an `async def` handler: off the loop + bounded, same method as the
# Unipile identity list (lot 1) and the FOD routes (lot 2). The Gmail API calls
# are already in `to_thread` — only the client construction (hence the refresh)
# still ran in the loop.
async def _client_for_user_async(account: Optional[str] = None):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_client_for_user, account),
                                      timeout=_GOOGLE_CLIENT_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise _bad(f"Google did not respond within {_GOOGLE_CLIENT_TIMEOUT_S}s "
                   "(token refresh) — retry.")


_ATTACHMENTS_TIMEOUT_S = 90


def _resolve_attachments(attachments):
    """Resolves `file_source` refs into TEMPORARY files (the GmailClient expects
    local PATHS for its attachments, but the server does not have the user's disk).
    `attachments` = list of `{"kind":"drive|gmail|url|project_file", …}` (see
    file_source.resolve). Returns `(paths, cleanup)` — the caller MUST call
    `cleanup()` in a finally. Raises FileSourceError on an unreadable ref (first
    cleans up the temp already written)."""
    if not attachments:
        return [], (lambda: None)
    tmpdir = tempfile.mkdtemp(prefix="oto-gmail-att-")

    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        paths = []
        for i, src in enumerate(attachments):
            rf = file_source.resolve(src)
            # defensive basename: never let a filename traverse the tmpdir.
            name = os.path.basename(rf.filename or "") or f"attachment-{i}"
            path = os.path.join(tmpdir, name)
            with open(path, "wb") as f:
                f.write(rf.data)
            paths.append(path)
        return paths, cleanup
    except Exception:
        cleanup()
        raise


def _signed_html(body: str, html: Optional[str], markdown: bool, signature: str) -> str:
    """The message's HTML body, with the account's signature appended after `--`.

    The Gmail API NEVER appends the signature: it is the web client that adds it at
    composition (oto#178, feedback 794/795). The signature is HTML, so the body
    must be too: `html` as is, otherwise the rendered markdown — the same rendering
    GmailClient would do itself —, otherwise the escaped plain text, line breaks kept
    (`markdown=False` means "no markdown", not "no HTML")."""
    if html is not None:
        corps = html
    elif markdown:
        from oto.tools.google.gmail.lib.gmail_client import _markdown_to_html_fragment
        corps = _markdown_to_html_fragment(body)
    else:
        import html as html_mod
        corps = ('<div dir="ltr">' + html_mod.escape(body).replace("\n", "<br>")
                 + "</div>")
    return f"{corps}<br>--<br>{signature}"


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def gmail_list_accounts() -> dict:
        """List the Gmail accounts this call can use: the user's own, then the
        mailboxes an admin shared with their team or the whole organization.

        Returns {accounts: [{email, is_default, shared}]}. `shared` is null for
        the user's own account, "group" or "org" for a shared mailbox. Use an
        `email` value as the `account` argument of the other gmail_* tools to
        act on a specific mailbox — shared ones included; omit `account` to use
        the default (`is_default`).
        """
        sub = access.current_user_sub_or_raise()
        accounts = google_oauth.reachable_accounts(sub, service="gmail")
        return {
            "accounts": [
                {"email": a.get("google_email"), "is_default": a.get("is_default", False),
                 "shared": a.get("shared")}
                for a in accounts
            ]
        }

    @mcp.tool()
    async def gmail_message(
        op: Literal["search", "get", "attachment", "drafts", "archive",
                    "trash"] = "search",
        query: Optional[str] = None,
        message_id: Optional[str] = None,
        message_ids: Optional[list[str]] = None,
        filename: Optional[str] = None,
        index: int = 0,
        max_results: int = 20,
        sheet: Optional[Union[int, str]] = None,
        max_rows: Optional[int] = None,
        account: Optional[str] = None,
    ) -> dict:
        """A message in the user's mailbox — search, read, fetch an attachment,
        list drafts, archive, trash.

        `op`:
        - **"search"** (default): search the user's Gmail with Gmail query syntax.
          `query` e.g. `from:foo@bar.com is:unread newer_than:7d`. Returns
          {messages: [{id, threadId, from, subject, date, snippet, labelIds}], count}.
        - **"get"**: fetch a full message (headers, body, attachment metadata) by
          `message_id`.
        - **"attachment"**: fetch the CONTENT of a Gmail attachment, by filename.
          Identify the attachment by its `filename` (from the `attachments` list of
          `op="get"`). The response depends on the file:
          - **small text** (JSON/CSV/Markdown/plain, ≤256 KB) → returned INLINE:
            `{encoding: "text", content: "<decoded text>"}` — read it directly.
          - **PDF** → its extracted TEXT returned INLINE: `{encoding: "text",
            format: "pdf-text", content, pages, truncated}` plus `raw_url` (+
            `raw_expires_in`), a short-lived signed URL to the original PDF
            (layout, images). A scanned or protected PDF has no text: it comes
            back as a URL, with `text_unavailable` saying why.
          - **binary or large** (image, archive, big file) → uploaded to temporary
            storage and returned as a short-lived signed URL: `{encoding: "url",
            url, expires_in}` (seconds). Fetch the URL to get the bytes.
          - **spreadsheet (.xlsx)** → returned INLINE as CSV, one section per
            sheet: `{encoding: "text", format: "csv", content, sheets,
            sheet_names, truncated}`. Each section starts with `# sheet=<index>
            name="…" rows_total=… rows_rendered=… truncated=…` then the CSV rows
            (computed values, not formulas; dates ISO 8601). All sheets by
            default, `max_rows` rows each (default 200, max 5000), size-capped:
            if `truncated`, ask one sheet with `sheet` and/or raise `max_rows`;
            a truncated render also carries `raw_url` (+ `raw_expires_in`, seconds):
            a short-lived signed URL to the FULL original file, e.g. to load it
            into a table or store it.
          Returns {filename, mimeType, size, encoding, content|url, expires_in?}.
        - **"drafts"**: list the user's Gmail drafts. Returns
          {drafts: [{id, message_id, to, subject, date, snippet}], count}.
        - **"archive"** — ⚠️ WRITES: removes the INBOX label from `message_ids`.
        - **"trash"** — ⚠️ WRITES: moves `message_ids` to the trash.

        Writing an email (send or save a draft, new message or reply) is a
        different tool: `gmail_compose`.

        Args:
            op: search (default) | get | attachment | drafts | archive | trash.
            query: op="search" — Gmail search query (e.g.
                `from:foo@bar.com is:unread newer_than:7d`).
            message_id: op="get"/"attachment" — Gmail message id (the one returned
                by op="search").
            message_ids: op="archive"/"trash" — Gmail message ids to act on.
            filename: op="attachment" — name of the attachment to fetch
                (e.g. "Contrat.pdf").
            index: op="attachment" — 0-based tiebreaker if several attachments
                share that name (e.g. inline images); default 0 = the first one.
            max_results: op="search"/"drafts" — max items to return (default 20).
            sheet: op="attachment" of an .xlsx — the sheet to read, by name or
                0-based index (see `sheet_names`). Omit for all sheets.
            max_rows: op="attachment" of an .xlsx — rows per sheet (default 200,
                max 5000).
            account: email of the Google account to use (default if omitted).
        """
        # Refusal BEFORE any credential resolution: an unknown op never reaches
        # the client — hence never, by a derived path, a write.
        if op not in _MESSAGE_OPS:
            raise _bad(_MESSAGE_OPS_ERROR)
        if (sheet is not None or max_rows is not None) and op != "attachment":
            raise _bad(f"`sheet`/`max_rows` only apply to op='attachment' of an "
                       f".xlsx spreadsheet (got op='{op}').")
        client = await _client_for_user_async(account)

        # ---- reads -----------------------------------------------------------
        if op == "search":
            messages = await asyncio.to_thread(
                client.search, _need(query, "query", op), max_results)
            return {"messages": messages, "count": len(messages)}

        if op == "get":
            return await asyncio.to_thread(
                client.get_message, _need(message_id, "message_id", op))

        if op == "attachment":
            mid = _need(message_id, "message_id", op)
            name = _need(filename, "filename", op)
            try:
                att = await asyncio.to_thread(client.get_attachment, mid, name, index)
            except Exception as e:
                raise _bad(str(e))
            data, att_filename, mime = att["data"], att["filename"], att["mimeType"]
            sub = access.current_user_sub_or_raise()
            try:
                return await asyncio.to_thread(
                    file_content.render_for_agent, data, att_filename, mime,
                    sub=sub, prefix="gmail-attachments", sheet=sheet, max_rows=max_rows)
            except (file_content.MediaUnavailable, file_content.SpreadsheetError) as e:
                raise _bad(str(e))

        if op == "drafts":
            drafts = await asyncio.to_thread(client.list_drafts, max_results)
            return {"drafts": drafts, "count": len(drafts)}

        # ---- writes ----------------------------------------------------------
        if op == "archive":
            results = await asyncio.to_thread(
                client.archive_messages, _need(message_ids, "message_ids", op))
            return {"archived": results}

        if op == "trash":
            # Gmail has no batch trash: it is one call per message,
            # hence a loop — and hence a PARTIAL write is possible. If the
            # 3rd fails, the first two ARE in the trash; letting the bare
            # exception bubble up would only tell the agent "failed", it would
            # conclude "nothing went through" and replay a write already
            # done. That is the defect described by signal #227 (an applied
            # action the caller learns nothing about), and the same fault as
            # #600: announcing a failure on a success. We name the three batches.
            ids = list(_need(message_ids, "message_ids", op))
            trashed: list = []
            for i, mid in enumerate(ids):
                try:
                    res = await asyncio.to_thread(client.trash_message, mid)
                except Exception as e:  # noqa: BLE001 — re-raised named, with the real state
                    restants = ids[i + 1:]
                    raise _bad(
                        "PARTIAL trash — the write stops at the first "
                        "failure, but what precedes did happen. "
                        f"ALREADY in the trash ({len(trashed)}): {trashed}. "
                        f"FAILED on `{mid}`: {type(e).__name__}: {e}. "
                        f"NOT ATTEMPTED ({len(restants)}): {restants}. "
                        "Only replay the not-attempted ones — retrying the first "
                        "ones is not necessary."
                    ) from e
                trashed.append(res.get("id", mid))
            return {"trashed": trashed}

        # Structurally unreachable (input guard above) — a safety net against
        # an implicit `return None` if an op were added to `_MESSAGE_OPS` without
        # its branch: better to refuse than to return "nothing" as a success.
        raise _bad(_MESSAGE_OPS_ERROR)

    @mcp.tool()
    async def gmail_compose(
        body: str,
        mode: Literal["send", "draft"] = "draft",
        to: Optional[str] = None,
        subject: Optional[str] = None,
        reply_to: Optional[str] = None,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        html: Optional[str] = None,
        from_name: Optional[str] = None,
        markdown: bool = True,
        account: Optional[str] = None,
        attachments: Optional[list[dict]] = None,
        sign: bool = True,
    ) -> dict:
        """Compose an email — **saved as a DRAFT by default**, or sent explicitly.

        ⚠️ **`mode="send"` is required to actually send.** Omitting `mode` writes a draft
        the user can review; it does NOT leave the mailbox. Say plainly which one you did
        (read `kind` in the answer) — never report "sent" for a draft, or the reverse.

        ⚠️ **A freshly created draft can be MISSING from an already-open Gmail tab**,
        for an indeterminate time. The write is fine and the API lists it; it is the
        client view that lags. Measured 2026-09-04: the API confirmed the draft, three
        reads found it, and the person staring at their Drafts folder saw one older
        draft and nothing else — a full diagnosis was spent before the cause was found.
        So `kind: "draft"` means SAVED, not VISIBLE: tell the person to reload the tab
        or search `in:drafts` rather than to look again.

        Returns `kind` — **"sent" (the mail LEFT) or "draft" (saved, not sent)** — plus
        the message ids. Always read `kind` before reporting what you did: it is the
        only field that states the act.

        The sending account's Gmail signature is appended by default, after `--`,
        like the Gmail web client does (the API never adds it on its own) — so do
        NOT write a sign-off block of your own in `body`. `signature` in the answer
        says what happened: "appended", "none_configured" (the account has no
        signature — nothing was added), or "disabled" (`sign=False`).

        Args:
            body: message body (rendered from markdown to HTML by default).
            mode: "draft" (default) saves for human review; "send" delivers it now.
            to: recipient(s), comma-separated. REQUIRED for a new message (omit when replying).
            subject: subject line (new message only; a reply keeps the thread's subject).
            reply_to: id of the message to reply to. When set, this is a threaded REPLY
                (subject/thread preserved) and `to`/`subject` are ignored.
            cc / bcc: optional carbon copy (bcc: new message only).
            html: explicit HTML body (bypasses markdown rendering).
            from_name: optional display name for the From header.
            markdown: render `body` from markdown when `html` is absent (default True).
            account: email of the Google account to use (default if omitted).
            sign: append the account's Gmail signature (default True); False sends
                the body alone.
            attachments: files to attach, as `source` refs oto resolves server-side
                (the agent has no local disk). Each item — `kind` selects the origin:
                - Drive: `{"kind":"drive","file_id":"<id>"}` (id from drive_list/metadata)
                - Gmail: `{"kind":"gmail","message_id":"<id>","filename":"<name>"}`
                - URL:   `{"kind":"url","url":"https://…"}` — e.g. a signed URL from
                  `oto_upload_url` (upload a local PDF first) or drive_download.
                - Project file: `{"kind":"project_file","project_id":<id>,"file_id":<id>}`
                  (ids from oto_project_files op=list)
        """
        if mode not in ("send", "draft"):
            raise _bad("mode must be 'send' or 'draft'.")
        client = await _client_for_user_async(account)
        signature_etat = "disabled"
        if sign:
            try:
                signature = await asyncio.to_thread(client.get_signature)
            except Exception as e:
                # No send WITHOUT the signature silently expected: named refusal,
                # with the gesture that works if the caller accepts doing without it.
                raise _bad(f"Could not read the Gmail signature ({e}) — nothing "
                           "was sent. Retry, or pass `sign=False` to "
                           "compose without a signature.")
            if signature:
                html = _signed_html(body, html, markdown, signature)
                signature_etat = "appended"
            else:
                signature_etat = "none_configured"
        try:
            # oto-backend#867 lot 2 — each attachment (drive/gmail/url) is
            # resolved by a synchronous HTTP call (file_source.resolve), in series:
            # off the loop + bounded, same method as the token refresh
            # above. 90s and not 20s: a send tolerates waiting for real
            # attachments (up to 25 MB each) — what must not happen
            # is freezing the whole process in the meantime.
            att = await asyncio.wait_for(
                asyncio.to_thread(_resolve_attachments, attachments),
                timeout=_ATTACHMENTS_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise _bad(f"Fetching the attachments took too long "
                      f"(> {_ATTACHMENTS_TIMEOUT_S}s) — retry.")
        except file_source.FileSourceError as e:
            raise _bad(str(e))
        att_paths, _cleanup = att

        def _acte(res: object) -> dict:
            """The return NAMES the act. Without this field, "sent" and "draft" can only
            be told apart by the NUMBER of keys returned (3 vs 2) — a difference you
            must already know about to read it. An agent that reports "draft created" after
            a real send did not lie: it had nothing to read that said so.
            Paid for on 14/08: three emails sent to a client."""
            out = dict(res) if isinstance(res, dict) else {"result": res}
            out["kind"] = "draft" if mode == "draft" else "sent"
            out["signature"] = signature_etat
            return out

        try:
            if reply_to:
                if mode == "draft":
                    return _acte(await asyncio.to_thread(
                        lambda: client.create_draft_reply(
                            message_id=reply_to, body=body, html=html, cc=cc, markdown=markdown,
                            attachments=att_paths,
                        )
                    ))
                return _acte(await asyncio.to_thread(
                    lambda: client.reply(
                        message_id=reply_to, body=body, html=html, cc=cc,
                        from_name=from_name, markdown=markdown, attachments=att_paths,
                    )
                ))
            if not to:
                raise _bad("`to` is required for a new message (or provide `reply_to` to reply).")
            if mode == "draft":
                return _acte(await asyncio.to_thread(
                    lambda: client.create_draft(
                        to=to, subject=subject or "", body=body, html=html, cc=cc, bcc=bcc,
                        markdown=markdown, attachments=att_paths,
                    )
                ))
            return _acte(await asyncio.to_thread(
                lambda: client.send(
                    to=to, subject=subject or "", body=body, html=html,
                    cc=cc, bcc=bcc, from_name=from_name, markdown=markdown, attachments=att_paths,
                )
            ))
        finally:
            _cleanup()
