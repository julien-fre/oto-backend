"""Kaspr — B2B contact enrichment from a LinkedIn URL (emails + phone numbers).

User-only provider: no platform quota, each user sets their key on
`/account`. Kaspr bills credits per enrichment.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# The LinkedIn slug normalization (URL → bare slug, otherwise Kaspr 500) lives in the
# oto-core client (`oto.tools.kaspr.client.linkedin_slug`), not here — canonical
# logic shared by all consumers. This wrapper only translates
# a Kaspr error into an actionable McpError.


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001 (config: probe contract, unused here)
    """"Test the connection" probe: does the key really authenticate?

    `verify_key()` (oto-core) does a sentinel POST with no side effect and no credit
    consumed (Kaspr has no `/me`) — 401 on an invalid key. Raises — the message
    bubbles up as-is to the UI.
    """
    from oto.tools.kaspr.client import KasprClient
    KasprClient(api_key=fields["key"]).verify_key()


def register(mcp: FastMCP) -> None:
    from oto.tools.kaspr.client import KasprClient

    connector_verify.register("kaspr", _verify)

    def _client() -> tuple[KasprClient, bool]:
        key, is_platform = access.resolve_api_key("kaspr")
        return KasprClient(api_key=key), is_platform

    @mcp.tool()
    def kaspr_enrich_linkedin(
        linkedin_id: str,
        name: Optional[str] = None,
        with_phone: bool = False,
        data_to_get: Optional[list[str]] = None,
    ) -> dict:
        """Enrich a LinkedIn profile with emails and (optionally) phone numbers.

        Cost: 1 credit per email, +1 per phone if `with_phone=True`.

        Args:
            linkedin_id: the person's LinkedIn handle. Either the bare slug
                ("alexis-laporte") OR the full profile URL
                ("https://www.linkedin.com/in/alexis-laporte/") — both work, the
                slug is extracted automatically. NOT a name or a search query.
            name: Optional fallback name if the slug alone is ambiguous.
            with_phone: Request mobile/work phones (extra credits cost).
            data_to_get: Kaspr field names, from its own enum — "workEmail",
                "directEmail", "phone". Kaspr answers 500 on a name it does not
                know. Omitted, the call asks for ["workEmail", "phone"] — NOT
                every field.
        """
        client, is_platform = _client()
        # with_phone=True → include "phone" in data_to_get (costs extra credits)
        effective_data = data_to_get
        if effective_data is None and with_phone:
            effective_data = ["workEmail", "phone"]
        try:
            # The oto-core client normalizes linkedin_id (URL → slug) before the call.
            result = client.enrich_linkedin(
                linkedin_id=linkedin_id,
                name=name,
                is_phone_required=with_phone,
                data_to_get=effective_data,
            )
        except ValueError as e:
            # LOCAL refusal by the oto-core client — a `dataToGet` name outside the
            # three Kaspr accepts. Its message already NAMES the accepted
            # values: we return it as-is rather than drowning it under a
            # profile-not-found hypothesis (same shape as `tools/cognism.py`).
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        except Exception as e:
            # ⚠️ A Kaspr 5xx DOES NOT PROVE an outage: Kaspr returns 500 on at
            # least two known input faults — a full URL instead of the
            # bare slug (seen on 15/06) and an unknown `dataToGet` (reproduced on
            # 01/09: `["emails","phones","company"]` → 500 `TypeError: Cannot
            # read properties of undefined (reading 'push')`). The message said
            # "it's not your input": a claim we cannot
            # make, and which closed off the only correct lead in the most
            # reachable case. It therefore NAMES both faults, and bounds the retry.
            #
            # The retry bounded to ONE deferred attempt is consistent with the
            # machine flag: this McpError(INVALID_PARAMS) is classified
            # `invalid_input` / `retryable: false` by `error_taxonomy`, where
            # `retryable` means "replayable AS IS". Replaying as is is
            # precisely not the first move here — it is fixing the input.
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            if status and status >= 500:
                msg = (f"Kaspr returned a server error ({status}) — which it ALSO "
                       "returns on a malformed request. Check `linkedin_id` (bare "
                       "slug or profile URL, never a name or a search) and "
                       "`data_to_get` (only workEmail, directEmail and phone "
                       "exist). If the input is correct: a single new "
                       "attempt, deferred.")
            elif status == 402:
                # 402 = insufficient credits on the Kaspr ACCOUNT. The generic message
                # below sent the agent to check the LinkedIn profile
                # — the only lead that can yield NOTHING here, and which
                # ends in a re-read of the slug then an identical new attempt.
                # Seen on 2026-09-02 (call 1345911): a 402 rendered
                # as "Check the LinkedIn profile (valid slug or URL)".
                # The oto-core client already drops `phone` and replays ONCE when
                # it was requested: a 402 that bubbles up to here is therefore a
                # refusal by the account, not a choice of fields to revisit.
                msg = ("Kaspr refused the call for lack of credits (402) — it is the "
                       "Kaspr account that is out of credits, not your input. The profile and "
                       "`data_to_get` have nothing to do with it: neither fixing them nor "
                       "retrying will change anything until the account is "
                       "topped up.")
            elif status == 429:
                # 429 = Kaspr rate-limits (otomata-tech/oto#144). The generic
                # branch returned "Check the LinkedIn profile" — twenty times in
                # thirty seconds on 28/08/2026 —, the only lead that can yield nothing
                # when all that is needed is to wait. Raised as `UpstreamHTTPError`
                # 429 and not as McpError: the taxonomy then classifies it
                # `rate_limited` / `retryable: true`, the only right verdict here (an
                # McpError would be `invalid_input` / `retryable: false`).
                from oto.tools.common.errors import UpstreamHTTPError
                raise UpstreamHTTPError(429, (
                    "Kaspr is rate limiting (429) — neither the profile nor `data_to_get` "
                    "has anything to do with it. Space out your calls: wait a few "
                    "seconds, then replay the exact same call."),
                    service="kaspr") from e
            elif status in (401, 403):
                # 401/403 = Kaspr refuses the KEY, not the profile. Seen on
                # 2026-10-06: a burst of 403 on an org key, the same profiles refused
                # as slug and as full URL — rendered "Check the LinkedIn profile",
                # the one lead that cannot help. A 401 is a dead key (marked by the
                # common path); a bare 403 is undocumented by Kaspr: API access or
                # plan of the key, so it is not declared a dead key.
                msg = (f"Kaspr refused access with this key ({status}) — not your "
                       "input: the profile and `data_to_get` have nothing to do with "
                       "it. The key's API access or plan must be checked in Kaspr; "
                       "retrying will not change anything until then.")
            else:
                msg = (f"Kaspr could not enrich `{linkedin_id}` ({e}). Check the "
                       f"LinkedIn profile (valid slug or URL).")
            raise McpError(ErrorData(code=INVALID_PARAMS, message=msg))
        if is_platform:
            access.record_platform_usage("kaspr")
        return result
