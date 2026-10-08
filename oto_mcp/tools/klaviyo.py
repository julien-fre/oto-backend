"""Klaviyo — email & SMS marketing, the customer-relationship side: account,
profiles, lists, segments and marketing consent.

Wraps `oto.tools.klaviyo.KlaviyoClient` (private key, `Klaviyo-API-Key`,
revision pinned by the library). keyed `api_key`, **byo-only**: a private key
belongs to one Klaviyo account and carries the scopes chosen at its creation.

25 library functions over TEN tools, verb in `op`: `klaviyo_account`,
`klaviyo_profiles`, `klaviyo_lists`, `klaviyo_segments` and `klaviyo_consent`
here; `klaviyo_campaigns`, `klaviyo_flows`, `klaviyo_metrics`, `klaviyo_events`
and `klaviyo_reports` in `klaviyo_marketing.py`. The shared base (key, refusal,
views, pagination) lives in `klaviyo_socle.py`.

## What the tool layer adds to the transport

1. **Nothing sends a campaign.** The library has no such function; the tools
   neither create nor edit a campaign, flow or template.
2. **Sensitive writes are a PREVIEW until `confirm=True`** (claap / origami
   pattern): adding profiles to a list and recording an event start the flows
   they trigger, subscribing changes consent (a double opt-in list emails a
   confirmation), unsubscribing without a list — or a profile outside the
   given list — is GLOBAL. The preview reads what it can (the list's name and
   opt-in process, which profiles are outside the list) and sends nothing.
   Plain writes (upsert a profile, leave a list) go through directly.
3. **Arguments, not JSON:API.** The tools build the `{data: {type, attributes,
   relationships}}` bodies from named arguments; an attribute Klaviyo does not
   take is refused here, never dropped.
4. **Tightened views** (`full=True` returns the raw payload) and an honest
   pagination block (`klaviyo_socle`).
5. **No argument silently ignored**: an argument an `op` does not use is
   refused.

Client calls are written in plain sight (`_client().list_profiles(…)`) for the
version probe (`test_tools_client_methods_exist`). Checked against the library's
contract (`connectors/klaviyo/connector.yaml`); **not tested live** — no Klaviyo
key available at writing time.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP

from ..connectors import verify as connector_verify
from .klaviyo_socle import (
    PROFILE_ATTRIBUTES, PROFILE_IDENTIFIERS, PROFILE_ROW_OMITTED, _bad, _client,
    _verify, hors_op, listing, need, pages, preview, profile_record, profile_refs,
    profiles_for, run, slim_group, slim_profile,
)
from .lecture import LECTURE

_LIST_OPS = ("filter", "sort", "page_size", "page_cursor", "all_pages", "max_pages", "full")


def _account_view(res: dict) -> dict:
    item = (res.get("data") or [{}])[0] if isinstance(res.get("data"), list) else {}
    a = item.get("attributes") or {}
    contact = a.get("contact_information") or {}
    out = {"id": item.get("id"), "organization_name": contact.get("organization_name"),
           "default_sender_name": contact.get("default_sender_name"),
           "default_sender_email": contact.get("default_sender_email"),
           "website_url": contact.get("website_url"), "industry": a.get("industry"),
           "timezone": a.get("timezone"), "preferred_currency": a.get("preferred_currency"),
           "locale": a.get("locale"), "omitted": ["street_address", "public_api_key"]}
    return {k: v for k, v in out.items() if v is not None}


def _membership(client: Any, list_id: str, profiles: List[dict]) -> List[dict]:
    """The profiles of `profiles` that are NOT members of `list_id` — those an
    unsubscribe with this list would unsubscribe GLOBALLY. One filtered read per
    identifier kind (`any(email,[…])`), never a walk of the whole list."""
    found: set = set()
    for ident in ("email", "phone_number"):
        values = sorted({p[ident] for p in profiles if p.get(ident)})
        if not values:
            continue
        flt = f"any({ident},{json.dumps(values, separators=(',', ':'))})"
        res = run(lambda: client.list_profiles_in_list(
            list_id, filter=flt, page_size=100, all_pages=True, max_pages=2),
            "lists:read, profiles:read")
        for row in res.get("data") or []:
            v = (row.get("attributes") or {}).get(ident)
            if v:
                found.add((ident, v.lower()))
    return [p for p in profiles
            if not any((k, str(p.get(k) or "").lower()) in found
                       for k in ("email", "phone_number"))]


def register(mcp: FastMCP) -> None:
    connector_verify.register("klaviyo", _verify)

    @mcp.tool(annotations=LECTURE)
    def klaviyo_account(full: bool = False) -> dict:
        """The Klaviyo account the private key reaches (read only): `{id,
        organization_name, default_sender_name, default_sender_email, website_url,
        industry, timezone, preferred_currency, locale}`. Checks that the key works
        and which account it acts on. `full=True` returns the raw payload.

        Args:
            full: raw payload.
        """
        res = run(lambda: _client().get_account(), "accounts:read")
        return res if full else _account_view(res)

    @mcp.tool()
    def klaviyo_profiles(
        op: Literal["list", "get", "upsert"] = "list",
        profile_id: Optional[str] = None,
        filter: Optional[str] = None,
        sort: Optional[str] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        additional_fields: Optional[List[Literal["subscriptions", "predictive_analytics"]]] = None,
        include: Optional[List[Literal["lists", "segments"]]] = None,
        attributes: Optional[Dict[str, Any]] = None,
        patch_properties: Optional[Dict[str, Any]] = None,
        full: bool = False,
    ) -> dict:
        """Klaviyo profiles (people): find, read, create or update. Never touches
        marketing consent (`klaviyo_consent` does).

        `op`:
        - `list` (default) — `{profiles: [{id, email, phone_number, external_id,
          first_name, last_name, organization, created, updated, last_event_date}],
          count, next_cursor, omitted}`. To find one person, filter
          `equals(email,"jane@example.com")` rather than paging.
        - `get` — one profile (`profile_id`) with all its attributes (location,
          custom `properties`); `include` adds the `lists` / `segments` it is in.
        - `upsert` — create a profile, or update the one matching `profile_id`,
          else the email / phone_number / external_id in `attributes`. A key left
          out stays unchanged, a key set to null is cleared, `properties` merges.
          Returns `{profile}`.

        The profile id is 26 letters and digits (`01H…`) — not an email.

        Args:
            op: list | get | upsert.
            profile_id: get / upsert — Klaviyo profile id.
            filter: list — clauses joined by commas (AND): `equals(email,"x")`,
                `any(email,["a","b"])`, `greater-than(updated,2026-09-01T00:00:00Z)`;
                fields id, email, phone_number, external_id, created, updated.
            sort: list — created | email | id | updated, `-` prefix descends.
            page_size: list — 1-100, default 20.
            page_cursor: list — `next_cursor` of the previous page.
            all_pages: list — follow the pages (up to `max_pages`); `complete`
                says whether the walk reached the end.
            max_pages: list with all_pages — 1-20, default 10.
            additional_fields: list / get — subscriptions (consent per channel),
                predictive_analytics (predicted CLV, churn).
            include: get — lists, segments.
            attributes: upsert — email, phone_number (E.164), external_id,
                first_name, last_name, organization, title, locale, image,
                location {address1, city, region, zip, country, timezone…},
                properties {custom}.
            patch_properties: upsert — {append?: {prop: value}, unappend?: {…},
                unset?: name or [names]} on custom properties.
            full: list / get / upsert — raw JSON:API payload.
        """
        if op == "list":
            hors_op(op, (*_LIST_OPS, "additional_fields"), profile_id=profile_id,
                    include=include, attributes=attributes,
                    patch_properties=patch_properties)
            walk = pages(all_pages, max_pages)
            res = run(lambda: _client().list_profiles(
                filter=filter, sort=sort, page_size=page_size, page_cursor=page_cursor,
                additional_fields=additional_fields, **walk), "profiles:read")
            return res if full else listing(res, "profiles", slim_profile,
                                            PROFILE_ROW_OMITTED, all_pages=all_pages)
        if op == "get":
            need(op, profile_id=profile_id)
            hors_op(op, ("profile_id", "additional_fields", "include", "full"),
                    filter=filter, sort=sort, page_size=page_size,
                    page_cursor=page_cursor, all_pages=all_pages, max_pages=max_pages,
                    attributes=attributes, patch_properties=patch_properties)
            res = run(lambda: _client().get_profile(
                profile_id, additional_fields=additional_fields, include=include),
                "profiles:read" + (", lists:read / segments:read" if include else ""))
            return res if full else profile_record(res.get("data") or {},
                                                   res.get("included") or [])
        if op == "upsert":
            hors_op(op, ("profile_id", "attributes", "patch_properties", "full"),
                    filter=filter, sort=sort, page_size=page_size,
                    page_cursor=page_cursor, all_pages=all_pages, max_pages=max_pages,
                    additional_fields=additional_fields, include=include)
            attrs = dict(attributes or {})
            unknown = sorted(set(attrs) - set(PROFILE_ATTRIBUTES))
            if unknown:
                hint = (" — consent is not a profile attribute: use klaviyo_consent."
                        if "subscriptions" in unknown else "")
                raise _bad(f"`attributes`: Klaviyo does not take {unknown}; allowed: "
                           f"{', '.join(PROFILE_ATTRIBUTES)}{hint}")
            if not profile_id and not any(attrs.get(k) for k in PROFILE_IDENTIFIERS):
                raise _bad("op='upsert' needs `profile_id`, or an email, phone_number "
                           "or external_id in `attributes` to match the profile.")
            data: Dict[str, Any] = {"type": "profile", "attributes": attrs}
            if profile_id:
                data["id"] = profile_id
            if patch_properties:
                extra = sorted(set(patch_properties) - {"append", "unappend", "unset"})
                if extra:
                    raise _bad(f"`patch_properties` takes append, unappend, unset — "
                               f"not {extra}.")
                data["meta"] = {"patch_properties": patch_properties}
            res = run(lambda: _client().create_or_update_profile(data), "profiles:write")
            return res if full else {"profile": profile_record(res.get("data") or {})}
        raise _bad(f"invalid `op`: {op!r} (expected: list | get | upsert).")

    @mcp.tool()
    def klaviyo_lists(
        op: Literal["list", "get", "members", "add", "remove"] = "list",
        list_id: Optional[str] = None,
        filter: Optional[str] = None,
        sort: Optional[str] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        profile_count: bool = False,
        profile_ids: Optional[List[str]] = None,
        confirm: bool = False,
        full: bool = False,
    ) -> dict:
        """Klaviyo lists and their members: read, add or remove profiles.

        `op`:
        - `list` (default) — `{lists: [{id, name, created, updated,
          opt_in_process}], count, next_cursor}`, at most 10 per page. The list
          id is 6 characters (`Y6nRLr`).
        - `get` — one list (`list_id`); `profile_count=True` adds its size.
        - `members` — the profiles of a list (`list_id`), same rows as
          `klaviyo_profiles`.
        - `add` — ⚠️ SENSITIVE: add `profile_ids` (≤ 1000) to the list. Flows
          triggered by this list ("Added to List") START for them and may send
          messages. It gives no marketing consent (`klaviyo_consent` does).
          Without `confirm=True` it only returns a preview — nothing is sent.
        - `remove` — remove `profile_ids` (≤ 1000) from the list, at once; their
          consent is unchanged. Returns `{removed, list_id}`.

        Args:
            op: list | get | members | add | remove.
            list_id: get / members / add / remove — Klaviyo list id.
            filter: list — `equals(name,"Newsletter")`, fields name, id, created,
                updated; members — `greater-than(joined_group_at,2026-09-01T00:00:00Z)`,
                fields email, phone_number, joined_group_at.
            sort: list — created | id | name | updated; members — joined_group_at;
                `-` prefix descends.
            page_size: list — 1-10; members — 1-100, default 20.
            page_cursor: list / members — `next_cursor` of the previous page.
            all_pages: list / members — follow the pages up to `max_pages`.
            max_pages: with all_pages — 1-20, default 10.
            profile_count: get — add the member count (Klaviyo allows 15 per minute).
            profile_ids: add / remove — Klaviyo profile ids (not emails).
            confirm: add — True to really add (after reading the preview).
            full: list / get / members — raw JSON:API payload.
        """
        if op in ("list", "members"):
            permis = _LIST_OPS + (("list_id",) if op == "members" else ())
            hors_op(op, permis, list_id=list_id, profile_count=profile_count,
                    profile_ids=profile_ids, confirm=confirm)
            walk = pages(all_pages, max_pages)
            if op == "list":
                res = run(lambda: _client().list_lists(
                    filter=filter, sort=sort, page_size=page_size,
                    page_cursor=page_cursor, **walk), "lists:read")
                return res if full else listing(res, "lists", slim_group,
                                                all_pages=all_pages)
            need(op, list_id=list_id)
            res = run(lambda: _client().list_profiles_in_list(
                list_id, filter=filter, sort=sort, page_size=page_size,
                page_cursor=page_cursor, **walk), "lists:read, profiles:read")
            return res if full else listing(res, "profiles", slim_profile,
                                            PROFILE_ROW_OMITTED, all_pages=all_pages)
        if op == "get":
            need(op, list_id=list_id)
            hors_op(op, ("list_id", "profile_count", "full"), filter=filter, sort=sort,
                    page_size=page_size, page_cursor=page_cursor, all_pages=all_pages,
                    max_pages=max_pages, profile_ids=profile_ids, confirm=confirm)
            res = run(lambda: _client().get_list(
                list_id, additional_fields=["profile_count"] if profile_count else None),
                "lists:read")
            return res if full else slim_group(res.get("data") or {})
        if op in ("add", "remove"):
            need(op, list_id=list_id, profile_ids=profile_ids)
            hors_op(op, ("list_id", "profile_ids") + (("confirm",) if op == "add" else ()),
                    filter=filter, sort=sort, page_size=page_size,
                    page_cursor=page_cursor, all_pages=all_pages, max_pages=max_pages,
                    profile_count=profile_count, confirm=confirm, full=full)
            refs = profile_refs(profile_ids)
            if op == "remove":
                run(lambda: _client().remove_profiles_from_list(list_id, refs),
                    "lists:write, profiles:write")
                return {"removed": len(refs), "list_id": list_id,
                        "note": "membership only — consent is unchanged."}
            if not confirm:
                lst = slim_group(run(lambda: _client().get_list(list_id),
                                     "lists:read").get("data") or {})
                return preview(
                    "add_profiles_to_list",
                    "Flows triggered by this list (trigger 'Added to List') start for "
                    "these profiles and may send them messages. No consent is given. "
                    "To see which live flows listen to lists: klaviyo_flows(filter="
                    "\"equals(status,'live'),equals(trigger_type,'Added to List')\").",
                    list=lst, profiles=len(refs))
            run(lambda: _client().add_profiles_to_list(list_id, refs),
                "lists:write, profiles:write")
            return {"added": len(refs), "list_id": list_id}
        raise _bad(f"invalid `op`: {op!r} (expected: list | get | members | add | remove).")

    @mcp.tool(annotations=LECTURE)
    def klaviyo_segments(
        op: Literal["list", "get", "members"] = "list",
        segment_id: Optional[str] = None,
        filter: Optional[str] = None,
        sort: Optional[str] = None,
        page_size: Optional[int] = None,
        page_cursor: Optional[str] = None,
        all_pages: bool = False,
        max_pages: Optional[int] = None,
        profile_count: bool = False,
        full: bool = False,
    ) -> dict:
        """Klaviyo segments (read only) — computed from their definition: members
        cannot be added or removed.

        `op`:
        - `list` (default) — `{segments: [{id, name, created, updated, is_active,
          is_processing, is_starred}], count, next_cursor}`, at most 10 per page.
        - `get` — one segment (`segment_id`) with its `definition`;
          `profile_count=True` adds its size.
        - `members` — the profiles of a segment, same rows as `klaviyo_profiles`.

        Args:
            op: list | get | members.
            segment_id: get / members — Klaviyo segment id.
            filter: list — `equals(is_active,true)`, fields name, id, created,
                updated, is_active, is_starred; members — `equals(email,"x")`,
                fields profile_id, email, phone_number, joined_group_at.
            sort: list — created | id | name | updated; members — joined_group_at;
                `-` prefix descends.
            page_size: list — 1-10; members — 1-100, default 20.
            page_cursor: list / members — `next_cursor` of the previous page.
            all_pages: list / members — follow the pages up to `max_pages`.
            max_pages: with all_pages — 1-20, default 10.
            profile_count: get — add the member count (Klaviyo allows 15 per minute).
            full: raw JSON:API payload.
        """
        if op in ("list", "members"):
            permis = _LIST_OPS + (("segment_id",) if op == "members" else ())
            hors_op(op, permis, segment_id=segment_id, profile_count=profile_count)
            walk = pages(all_pages, max_pages)
            if op == "list":
                res = run(lambda: _client().list_segments(
                    filter=filter, sort=sort, page_size=page_size,
                    page_cursor=page_cursor, **walk), "segments:read")
                return res if full else listing(res, "segments", slim_group,
                                                all_pages=all_pages)
            need(op, segment_id=segment_id)
            res = run(lambda: _client().list_profiles_in_segment(
                segment_id, filter=filter, sort=sort, page_size=page_size,
                page_cursor=page_cursor, **walk), "segments:read, profiles:read")
            return res if full else listing(res, "profiles", slim_profile,
                                            PROFILE_ROW_OMITTED, all_pages=all_pages)
        if op == "get":
            need(op, segment_id=segment_id)
            hors_op(op, ("segment_id", "profile_count", "full"), filter=filter,
                    sort=sort, page_size=page_size, page_cursor=page_cursor,
                    all_pages=all_pages, max_pages=max_pages)
            res = run(lambda: _client().get_segment(
                segment_id,
                additional_fields=["profile_count"] if profile_count else None),
                "segments:read")
            if full:
                return res
            item = res.get("data") or {}
            out = slim_group(item)
            definition = (item.get("attributes") or {}).get("definition")
            if definition is not None:
                out["definition"] = definition
            return out
        raise _bad(f"invalid `op`: {op!r} (expected: list | get | members).")

    @mcp.tool()
    def klaviyo_consent(
        op: Literal["subscribe", "unsubscribe"],
        profiles: List[Dict[str, str]],
        channels: Optional[List[Literal["email", "sms"]]] = None,
        list_id: Optional[str] = None,
        custom_source: Optional[str] = None,
        historical_import: bool = False,
        consented_at: Optional[str] = None,
        confirm: bool = False,
    ) -> dict:
        """⚠️ SENSITIVE — give or withdraw email / SMS MARKETING CONSENT. Without
        `confirm=True` it only returns a preview: nothing is sent.

        `op`:
        - `subscribe` — consent for ≤ 1000 profiles, matched (or created) by email
          or phone_number, and added to `list_id` if given. If the list uses double
          opt-in, Klaviyo EMAILS each a confirmation and subscribes it only once
          confirmed; otherwise the list's flows start. Clears unsubscribe, spam
          and manual suppressions. Only for people who did consent.
          `historical_import=True` records consent gathered earlier
          (`consented_at` required): no confirmation message, no flow.
        - `unsubscribe` — withdraw consent from ≤ 100 profiles. With `list_id`,
          members of that list leave it and are unsubscribed; a profile NOT in
          it, or every profile when no list is given, is unsubscribed GLOBALLY
          from all marketing. The preview names those. To only leave a list,
          use `klaviyo_lists(op="remove")`.

        The job is asynchronous: the confirmed call returns `{accepted: true}`
        (HTTP 202), not the result.

        Args:
            op: subscribe | unsubscribe.
            profiles: [{email?, phone_number? (E.164)}] — each channel asked needs
                its identifier on each profile.
            channels: email (default) and/or sms.
            list_id: the Klaviyo list concerned (see the effects above).
            custom_source: subscribe — where consent was gathered, kept on record.
            historical_import: subscribe — record past consent (needs consented_at).
            consented_at: subscribe with historical_import — ISO 8601, in the past.
            confirm: True to really do it (after reading the preview).
        """
        chans = list(dict.fromkeys(channels or ["email"]))
        if op == "subscribe":
            if historical_import and not consented_at:
                raise _bad("`historical_import=True` needs `consented_at` (when the "
                           "consent was given).")
            if consented_at and not historical_import:
                raise _bad("`consented_at` only applies with `historical_import=True`.")
            items = profiles_for(profiles, chans, consent="SUBSCRIBED", limit=1000,
                                 consented_at=consented_at)
            attrs: Dict[str, Any] = {"profiles": {"data": items}}
            if custom_source:
                attrs["custom_source"] = custom_source
            if historical_import:
                attrs["historical_import"] = True
            data: Dict[str, Any] = {"type": "profile-subscription-bulk-create-job",
                                    "attributes": attrs}
        elif op == "unsubscribe":
            hors_op(op, (), custom_source=custom_source,
                    historical_import=historical_import, consented_at=consented_at)
            items = profiles_for(profiles, chans, consent="UNSUBSCRIBED", limit=100)
            data = {"type": "profile-subscription-bulk-delete-job",
                    "attributes": {"profiles": {"data": items}}}
        else:
            raise _bad(f"invalid `op`: {op!r} (expected: subscribe | unsubscribe).")
        if list_id:
            data["relationships"] = {"list": {"data": {"type": "list", "id": list_id}}}

        if not confirm:
            client = _client()
            lst = (slim_group(run(lambda: client.get_list(list_id), "lists:read")
                              .get("data") or {}) if list_id else None)
            if op == "subscribe":
                if historical_import:
                    effect = ("Past consent is recorded: no confirmation message, no "
                              "flow starts.")
                elif lst and lst.get("opt_in_process") == "double_opt_in":
                    effect = ("Double opt-in list: Klaviyo EMAILS each profile a "
                              "confirmation and subscribes it only once confirmed.")
                else:
                    effect = ("Consent is given at once" + (" and the list's flows "
                              "start for these profiles." if lst else
                              " (no list given)."))
                return preview("subscribe_profiles", effect, channels=chans,
                               list=lst, profiles=len(items))
            outside = _membership(client, list_id, profiles) if list_id else profiles
            effect = ("Members of the list leave it and are unsubscribed; the "
                      "profiles in `unsubscribed_globally` are NOT in it and lose "
                      "all marketing consent on these channels." if list_id else
                      "No list given: every profile is unsubscribed GLOBALLY from "
                      "all marketing on these channels.")
            return preview("unsubscribe_profiles", effect, channels=chans, list=lst,
                           profiles=len(items), unsubscribed_globally=outside)

        if op == "subscribe":
            run(lambda: _client().subscribe_profiles(data),
                "subscriptions:write, profiles:write, lists:write")
        else:
            run(lambda: _client().unsubscribe_profiles(data),
                "subscriptions:write, profiles:write, lists:write")
        return {"accepted": True, "action": f"{op}_profiles", "profiles": len(items),
                "channels": chans, "list_id": list_id,
                "note": "asynchronous job (HTTP 202): Klaviyo applies it shortly; "
                        "read the profile with additional_fields=['subscriptions'] "
                        "to check."}
