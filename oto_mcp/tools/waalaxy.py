"""Waalaxy — LinkedIn prospecting automation: push prospects into a list and
a campaign.

Wraps `oto.tools.waalaxy.client.WaalaxyClient` (developers.waalaxy.com,
Bearer `wa_live_…` key — app → Settings → CRM Sync → Generate API key,
Advanced/Business plans). keyed `api_key`, byo-only: a key is bound to ONE
Waalaxy seat (its LinkedIn account), a platform key makes no sense.

The public API is **import-only** — 4 endpoints, 3 tools (one per business
object, silae/ADR 0047):
- `waalaxy_prospect_list` — op=list : the lists prospects can be imported into.
- `waalaxy_campaign` — op=list : the running/paused campaigns (archived ones
  are invisible to the API).
- `waalaxy_prospect` — op=add : import one (`prospect`) or many (`prospects`)
  into a list, optionally enrolling them in a campaign. dry_run previews the
  exact payload without calling Waalaxy.

There is NO read/search/delete of prospects, no campaign start/stop, no inbox,
no stats via the API — those live in the app only.

⚠️ `waalaxy_prospect(op="add")` gets HTTP 200 from Waalaxy even when every
item failed: the tool reads the per-item `importCode`/`addToCampaignCode` and
returns a receipt `{total, imported, enrolled, failed: [{index, url, code,
message}]}` PLUS the raw `result` so nothing is lost.

Not live-tested (no key at build time, 2026-08-26): built from the official
API reference + its embedded OpenAPI schema.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify

MAX_PROSPECTS_PER_CALL = 100  # one HTTP call whatever the size; cap keeps payload + receipt sane

_IMPORT_OK = {"success", "prospect_successfully_moved_to_another_list"}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Waalaxy a rejeté la clé (HTTP {status}) — vérifie la clé API posée sur ce "
                "connecteur (Waalaxy : Settings → CRM Sync → API key ; plan Advanced ou "
                "Business requis).")
    if status == 404:
        return f"Waalaxy : ressource introuvable (HTTP 404) — {e.body}"
    if status in (400, 422):
        return f"Waalaxy : paramètres refusés (HTTP {status}) — {e.body}"
    if status == 429:
        return "Waalaxy : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"Waalaxy est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Waalaxy a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """« Tester la connexion » : GET /integrations/test, prévu exactement pour ça."""
    from oto.tools.waalaxy.client import WaalaxyClient
    WaalaxyClient(api_key=fields["key"]).test_connection()


def _receipt(raw: Any, prospects: List[Dict[str, Any]], campaign_id: Optional[str]) -> Dict[str, Any]:
    items = (raw or {}).get("result") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {"total": len(prospects), "raw": raw}
    imported = enrolled = 0
    failed: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        item = item or {}
        code = item.get("importCode")
        camp = item.get("addToCampaignCode")
        url = prospects[i].get("url") if i < len(prospects) else None
        if code in _IMPORT_OK:
            imported += 1
        else:
            failed.append({"index": i, "url": url, "code": code, "message": item.get("message")})
        if campaign_id:
            if camp == "success":
                enrolled += 1
            elif camp is not None and code in _IMPORT_OK:
                failed.append({"index": i, "url": url, "code": camp,
                               "message": item.get("message"), "stage": "campaign"})
    out: Dict[str, Any] = {"total": len(items), "imported": imported, "failed": failed, "result": items}
    if campaign_id:
        out["enrolled"] = enrolled
    return out


def register(mcp: FastMCP) -> None:
    from oto.tools.waalaxy.client import WaalaxyClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("waalaxy", _verify)

    def _client() -> WaalaxyClient:
        key, _ = access.resolve_api_key("waalaxy")
        return WaalaxyClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    @mcp.tool()
    def waalaxy_prospect_list(op: Literal["list"] = "list") -> object:
        """The Waalaxy prospect lists of the connected seat — start here: the
        `_id` returned is the `prospect_list_id` that `waalaxy_prospect`
        needs.

        Returns `[{_id, name, totalProspects, user, iconColor, iconLabel}]`
        (no pagination: every list comes back).
        """
        if op != "list":
            raise _bad("op inconnu — seul op='list' existe (l'API Waalaxy ne crée pas de liste)")
        return _run(lambda: _client().list_prospect_lists())

    @mcp.tool()
    def waalaxy_campaign(op: Literal["list"] = "list") -> object:
        """The Waalaxy campaigns a prospect can be enrolled in — their `_id` is
        the `campaign_id` for `waalaxy_prospect(op="add")`.

        Returns `{total, campaigns: [{_id, name}]}`. ⚠️ Only campaigns in state
        running or paused are listed — archived/finished ones are invisible to
        the API, and enrolling into one fails with
        `cant_add_prospect_campaign_is_archived`.
        """
        if op != "list":
            raise _bad("op inconnu — seul op='list' existe (l'API Waalaxy ne crée ni ne "
                       "démarre de campagne)")
        return _run(lambda: _client().list_campaigns())

    @mcp.tool()
    def waalaxy_prospect(
        op: Literal["add"] = "add",
        prospect_list_id: Optional[str] = None,
        prospect: Optional[Dict[str, Any]] = None,
        prospects: Optional[List[Dict[str, Any]]] = None,
        campaign_id: Optional[str] = None,
        can_create_duplicates: Optional[bool] = None,
        move_duplicates_to_other_list: Optional[bool] = None,
        should_overwrite_custom_profile_data: Optional[bool] = None,
        add_existing_prospect_in_campaign: Optional[bool] = None,
        origin: str = "oto",
        dry_run: bool = False,
    ) -> object:
        """Import prospects into a Waalaxy list (`prospect_list_id`, from
        `waalaxy_prospect_list`) and optionally enrol them in a campaign
        (`campaign_id`, from `waalaxy_campaign`). The ONLY write the Waalaxy
        API offers — no read, update or delete of prospects exists.

        Exactly one of `prospect` (one → direct receipt) or `prospects`
        (many, max 100 per call → same receipt over the batch; ONE HTTP call
        either way). Each prospect:
        - `url` (required): the LinkedIn profile URL —
          https://www.linkedin.com/in/<handle>.
        - `customProfile` (optional): firstName, lastName, occupation, email,
          region, company{name, linkedinUrl, website},
          phoneNumbers[{type, number}], birthday{day, month}.
        - `customVariables` (optional): [{label, value}] usable as {{label}}
          in campaign messages — value ≤ 1000 chars.

        Duplicate handling (all default false on Waalaxy's side):
        `can_create_duplicates` (import even if the prospect exists in another
        list — needs the account's import_duplicates permission),
        `move_duplicates_to_other_list` (move the existing prospect into this
        list instead of skipping), `should_overwrite_custom_profile_data`
        (else customProfile only fills blanks),
        `add_existing_prospect_in_campaign` (also enrol prospects that were
        already in the CRM). `origin` is a label Waalaxy shows as
        `API-<origin>` on each imported prospect.

        Receipt: `{total, imported, enrolled?, failed: [{index, url, code,
        message, stage?}], result}` — `result` is Waalaxy's raw per-item
        array (`importCode`, `addToCampaignCode`, `prospect{_id, profile}`).
        ⚠️ Waalaxy answers 200 even when every item failed; read `failed`.
        Codes: importCode ∈ success | duplicated_prospect |
        prospect_successfully_moved_to_another_list |
        failed_to_change_prospect_list | max_limit_crm |
        custom_variable_exceeds_1000_chars | profile_deleted | server_error |
        unknown_error ; addToCampaignCode ∈ success | already_in_campaign |
        cant_add_prospect_campaign_not_exist |
        cant_add_prospect_campaign_is_archived |
        prospect_does_not_match_preconditions | unknown_error.

        `dry_run=true` validates everything and returns the exact payload
        that would be POSTed, without calling Waalaxy.
        """
        if op != "add":
            raise _bad("op inconnu — seul op='add' existe (l'API Waalaxy est import-only)")
        if (prospect is None) == (prospects is None):
            raise _bad("passe exactement un de `prospect` (un seul) ou `prospects` (liste)")
        batch = [prospect] if prospect is not None else list(prospects or [])
        if not batch:
            raise _bad("`prospects` est vide")
        if len(batch) > MAX_PROSPECTS_PER_CALL:
            raise _bad(f"max {MAX_PROSPECTS_PER_CALL} prospects par appel (reçu {len(batch)}) — "
                       "découpe en plusieurs appels")
        if not prospect_list_id:
            raise _bad("`prospect_list_id` requis — récupère-le via waalaxy_prospect_list")
        for i, p in enumerate(batch):
            if not isinstance(p, dict) or not p.get("url"):
                raise _bad(f"prospects[{i}] : `url` (profil LinkedIn) requis")
            if "linkedin.com/" not in str(p["url"]):
                raise _bad(f"prospects[{i}] : `url` doit être une URL de profil LinkedIn "
                           f"(reçu {p['url']!r})")
        flags = dict(
            campaign_id=campaign_id,
            can_create_duplicates=can_create_duplicates,
            move_duplicates_to_other_list=move_duplicates_to_other_list,
            should_overwrite_custom_profile_data=should_overwrite_custom_profile_data,
            add_existing_prospect_in_campaign=add_existing_prospect_in_campaign,
        )
        if dry_run:
            body: Dict[str, Any] = {"prospects": batch, "prospectListId": prospect_list_id,
                                    "origin": {"name": origin}}
            if campaign_id:
                body["campaignId"] = campaign_id
            for key, val in (("canCreateDuplicates", can_create_duplicates),
                             ("moveDuplicatesToOtherList", move_duplicates_to_other_list),
                             ("shouldOverwriteCustomProfileData", should_overwrite_custom_profile_data),
                             ("addExistingProspectInCampaign", add_existing_prospect_in_campaign)):
                if val is not None:
                    body[key] = bool(val)
            return {"dry_run": True, "total": len(batch), "would_post": body}
        raw = _run(lambda: _client().add_prospects(batch, prospect_list_id, origin=origin, **flags))
        return _receipt(raw, batch, campaign_id)
