"""Typeform — the webhooks of a form: `typeform_webhooks`.

Third module of the connector (`typeform` holds the shared base and the
responses, `typeform_formulaires` the forms). A webhook POSTs every new
response of its form, with all its answers, to an https endpoint; it is named
by its `tag`, unique within the form.

**What the tool layer adds to the transport**:
1. **The signing secret never comes back.** Typeform signs each payload with
   HMAC SHA256 by the `secret` set on write; no view returned here carries it,
   raw payload included (`_sans_secret`, on top of the client that already
   strips it — the guarantee is held where the payload is served). The argument
   itself is declared secret to the call journal (`journal_secrets`).
2. **`upsert` and `delete` take two steps.** Pointing a form's responses at an
   endpoint is a data transfer, deleting a webhook cuts an integration: without
   `confirm=True` both return a preview read from Typeform — the current
   webhook, and for `upsert` what would change (`would_create` when the tag is
   new).
3. **`event_types` is a list of the events to send** (`["form_response"]`),
   turned into the `{event: bool}` object Typeform takes — an event left out is
   sent as false, never left to an upstream default.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP

from .typeform import _bad, _client, _preview, _refuse_ignored, _run

#: The events a webhook can send, in Typeform's names.
EVENTS = ("form_response", "form_response_partial")


def _sans_secret(webhook: Any) -> Any:
    if isinstance(webhook, dict):
        return {k: v for k, v in webhook.items() if k != "secret"}
    return webhook


def _slim(webhook: Any) -> Any:
    """A webhook as served: what identifies and describes it, never its secret."""
    webhook = _sans_secret(webhook)
    if not isinstance(webhook, dict):
        return webhook
    keys = ("tag", "url", "enabled", "event_types", "verify_ssl", "id",
            "created_at", "updated_at")
    return {k: webhook[k] for k in keys if webhook.get(k) is not None}


def _current(client: Any, form_id: str, tag: str) -> Optional[dict]:
    """The webhook named `tag`, or None when the tag is free (404)."""
    from oto.tools.common import UpstreamHTTPError

    try:
        return client.get_webhook(form_id, tag)
    except UpstreamHTTPError as e:
        if e.status_code != 404:
            raise
    return None


def _event_flags(event_types: Optional[List[str]]) -> Optional[Dict[str, bool]]:
    if event_types is None:
        return None
    if not event_types:
        raise _bad("`event_types` names at least one event — to stop sending, "
                   "pass `enabled=false`.")
    return {event: event in event_types for event in EVENTS}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def typeform_webhooks(
        form_id: str,
        op: Literal["list", "get", "upsert", "delete"] = "list",
        tag: Optional[str] = None,
        url: Optional[str] = None,
        enabled: Optional[bool] = None,
        event_types: Optional[List[Literal["form_response", "form_response_partial"]]] = None,
        verify_ssl: Optional[bool] = None,
        secret: Optional[str] = None,
        confirm: bool = False,
        full: bool = False,
    ) -> dict:
        """Webhooks of a Typeform form: list, read, create or replace, delete.

        A webhook POSTs every new response of the form, with all its answers,
        to its `url`. Its signing secret is never returned.

        `op`:
        - `list` — `{form_id, count, webhooks: [{tag, url, enabled, event_types,
          verify_ssl, id, created_at, updated_at}]}`.
        - `get` — one webhook by `tag`.
        - `upsert` — ⚠️ creates the webhook `tag`, or REPLACES it whole
          (`url` and `enabled` required; a `secret` not passed again may be
          dropped). Once enabled, responses go to `url`.
        - `delete` — ⚠️ the endpoint stops receiving responses. To pause
          instead, `upsert` with `enabled=false`.

        `upsert` and `delete` take two steps: without `confirm=True` they only
        return a preview (`dry_run`: the current webhook, what would change).
        Scopes webhooks:read, webhooks:write.

        Args:
            form_id: the form id (`typeform_forms`).
            op: list | get | upsert | delete.
            tag: get / upsert / delete — the webhook's name within its form.
            url: upsert — https endpoint receiving the responses.
            enabled: upsert — true to send at once, false to pause.
            event_types: upsert — events to send: form_response (submitted),
                form_response_partial (partial); the others are turned off.
            verify_ssl: upsert — true to have Typeform check the certificate.
            secret: upsert — key signing the payloads (HMAC SHA256); never
                returned.
            confirm: upsert / delete — True to write, after the preview.
            full: list / get — raw payload (still without the secret).
        """
        writing = dict(url=url, enabled=enabled, event_types=event_types,
                       verify_ssl=verify_ssl, secret=secret)
        if op not in ("upsert", "delete"):
            _refuse_ignored(op, "it only applies to op='upsert' or op='delete'",
                            confirm=confirm or None)
        if op != "upsert":
            _refuse_ignored(op, "it only applies to op='upsert'", **writing)
        if op in ("upsert", "delete"):
            _refuse_ignored(op, "it only applies to op='list' or op='get'", full=full or None)

        if op == "list":
            _refuse_ignored(op, "use op='get' to read one webhook", tag=tag)
            res = _run(lambda: _client().list_webhooks(form_id), "webhooks:read")
            items = list((res or {}).get("items") or [])
            if full:
                return {**(res or {}), "items": [_sans_secret(w) for w in items]}
            return {"form_id": form_id, "count": len(items),
                    "webhooks": [_slim(w) for w in items]}
        if not tag:
            raise _bad(f"op={op!r}: `tag` is required.")
        if op == "get":
            hook = _run(lambda: _client().get_webhook(form_id, tag), "webhooks:read")
            return _sans_secret(hook) if full else _slim(hook)
        if op == "upsert":
            if not url or enabled is None:
                raise _bad("op='upsert' requires `url` and `enabled`.")
            if not url.startswith("https://"):
                raise _bad("`url` must be an https:// address.")
            flags = _event_flags(event_types)
            client = _client()
            if not confirm:
                current = _run(lambda: _current(client, form_id, tag), "webhooks:read")
                after = {"url": url, "enabled": enabled, "event_types": flags,
                         "verify_ssl": verify_ssl}
                what: Dict[str, Any] = {
                    "form_id": form_id, "tag": tag,
                    "secret": "set" if secret else "not sent"}
                if current is None:
                    what.update({k: v for k, v in after.items() if v is not None})
                    would = "would_create"
                else:
                    what["changes"] = {k: {"from": current.get(k), "to": v}
                                       for k, v in after.items()
                                       if v is not None and current.get(k) != v}
                    would = "would_replace"
                return _preview(would, what,
                                f"Every new response of form {form_id}, with all its "
                                f"answers, is then POSTed to {url}"
                                + ("." if enabled else " once enabled."))
            hook = _run(lambda: client.upsert_webhook(
                form_id, tag, url=url, enabled=enabled, event_types=flags,
                secret=secret, verify_ssl=verify_ssl), "webhooks:write")
            return {"upserted": True, "form_id": form_id, "webhook": _slim(hook)}
        if op == "delete":
            client = _client()
            if not confirm:
                hook = _run(lambda: client.get_webhook(form_id, tag), "webhooks:read")
                return _preview("would_delete", {"form_id": form_id, **_slim(hook)},
                                "Its endpoint stops receiving responses.")
            _run(lambda: client.delete_webhook(form_id, tag), "webhooks:write")
            return {"deleted": True, "form_id": form_id, "tag": tag}
        raise _bad(f"invalid `op`: {op!r} (expected: list | get | upsert | delete).")
