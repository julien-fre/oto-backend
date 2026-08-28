"""Dropcontact — contact + company enrichment (email/phone/SIRENE), async bulk.

Wraps `oto.tools.dropcontact.client.DropcontactClient`. byo-only (no platform
key) — each user/org connects its own Dropcontact account.

Async submit/fetch (same idiom as FullEnrich, signal #252):
`dropcontact_enrich` acks a batch of up to 250 contacts immediately, the job
runs server-side on Dropcontact — THEN `dropcontact_result` collects it (a
single un-cached status check, poll again while `done` is false). No inbound
webhook wired on this side; polling is the agent's job.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status == 401:
        return "Dropcontact a rejeté la clé API (401) — vérifie la clé configurée sur ce connecteur."
    if status == 403:
        return "Dropcontact : quota du token dépassé (403) — recharge des crédits ou attends le renouvellement."
    if status == 429:
        return "Dropcontact : trop de requêtes (429, limite 60/s) — réessaie dans un instant."
    if status in (500, 503, 504, 524):
        return f"Dropcontact est momentanément indisponible (HTTP {status}) — réessaie dans un moment."
    return f"Dropcontact a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : POST 0-crédit (contact vide), authentifie
    la clé sans consommer de quota."""
    from oto.tools.dropcontact.client import DropcontactClient
    DropcontactClient(api_key=fields["key"]).check_credits()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.dropcontact.client import DropcontactClient

    connector_verify.register("dropcontact", _verify)

    def _client() -> DropcontactClient:
        key, _ = access.resolve_api_key("dropcontact")
        return DropcontactClient(api_key=key)

    @mcp.tool()
    def dropcontact_enrich(
        contacts: list[dict],
        siren: bool = False,
        language: Optional[str] = None,
    ) -> dict:
        """Submit an ASYNC enrichment batch (email, phone, company/SIRENE data) to
        Dropcontact.

        Returns immediately with a `request_id` — the batch runs server-side. THEN
        call `dropcontact_result(request_id)` to collect (first try after ~30s,
        then again every ~20-30s until `done` is true).

        Args:
            contacts: 1-250 contacts in ONE batch (≤15kB each). Each item should
                carry enough to identify a contact — `email`, OR `linkedin`, OR
                `first_name`+`last_name`+`company`, OR `full_name`+`company` — but
                an under-specified item does NOT fail the batch: Dropcontact still
                processes what it can and reports `errors`/`warnings` for that item
                in the result. Other recognized fields: `phone`, `company`,
                `website`, `num_siren`, `siret`, `linkedin`, `company_linkedin`,
                `country` (ISO code), `job`, `custom_fields` (freeform dict,
                echoed back unchanged in the result).
            siren: If true, also resolve French company data (SIREN/SIRET/TVA,
                registered address, legal representative) for each contact's
                company.
            language: `"en"` for English-language processing; omit for French
                (Dropcontact's default).

        Credits ("pay on success"): 1 credit per email found (refunded if none
        found); 1 credit per email you sent in for pure verification.
        """
        client = _client()
        try:
            result = client.submit(contacts, siren=siren, language=language)
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        return {
            "request_id": result.get("request_id"),
            "submitted": len(contacts),
            "credits_left": result.get("credits_left"),
            "items": result.get("data"),
            "next_step": ("Batch accepted. Call dropcontact_result(request_id) "
                          "in ~30s."),
        }

    @mcp.tool()
    def dropcontact_result(request_id: str, force_results: bool = False) -> dict:
        """Collect the result of a Dropcontact batch submitted with
        dropcontact_enrich.

        Single status check, returns immediately. If `done` is false, wait ~20-30s
        and call again. When done, `profiles` holds one entry per submitted
        contact: `email` (array of `{email, qualification}` — qualification is
        `<local_part>@<domain>`, e.g. `nominative@pro`, `generic@perso`,
        `invalid@invalid` — see Dropcontact's email-qualification docs), `phone`,
        `mobile_phone`, plus company fields (`siren`, `siret`, `vat`,
        `nb_employees`, `naf5_code`/`naf5_des`, `industry`, `company_turnover`,
        `job`/`job_level`/`job_function`, `custom_fields` echoed back).

        Args:
            request_id: the id returned by dropcontact_enrich.
            force_results: return partial results now (items Dropcontact hasn't
                finished yet are returned unchanged) instead of waiting for the
                whole batch to complete.
        """
        client = _client()
        try:
            res = client.fetch(request_id, force_results=force_results)
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        if not res["done"]:
            next_step = (
                "Still processing — call dropcontact_result again in ~20-30s."
                if res.get("pending")
                else "Not done, and the reason doesn't match Dropcontact's documented "
                     "'still processing' state — double-check request_id before retrying."
            )
            return {
                "done": False,
                "reason": res.get("reason", ""),
                "next_step": next_step,
            }
        return {
            "done": True,
            "credits_left": res.get("credits_left"),
            "profiles": res.get("data", []),
        }

    @mcp.tool()
    def dropcontact_credits() -> dict:
        """Check the remaining Dropcontact credit balance for the connected
        account, at zero cost (Dropcontact doesn't charge for this probe)."""
        client = _client()
        try:
            result = client.check_credits()
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        return {"credits_left": result.get("credits_left")}
