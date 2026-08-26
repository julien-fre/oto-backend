"""Waalaxy — LinkedIn prospecting automation: push prospects into a list and
a campaign.

Wraps `oto.tools.waalaxy.client.WaalaxyClient` (developers.waalaxy.com,
Bearer key `zpka_…` — app → Settings → CRM Sync → Generate API key,
Advanced/Business plans; the docs show `wa_live_…`, real keys are Zuplo
`zpka_…` ones). keyed `api_key`, byo-only: a key is bound to ONE
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
returns a lean receipt `{total, imported, enrolled, failed: [{index, url,
code, message}], items: [{index, url, importCode, addToCampaignCode,
prospect_id, publicIdentifier}]}` — the raw response embeds the FULL prospect
object per item (~2 KB: LinkedIn ids, picture URL, history…), which would
flood the agent on a 100-prospect batch.

**Live-tested 2026-08-26** with a real key: test probe, both lists, dry_run,
one real import (success — Waalaxy auto-enriched the profile from LinkedIn:
headline, region, company website, birthday) and its re-import
(`duplicated_prospect`, `message` null in practice). Not exercised: campaign
enrolment (the seat's only campaign is a real one) and the duplicate flags.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import requests
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
                "connecteur (Waalaxy : Settings → CRM Sync → API key, format zpka_… ; plan Advanced ou "
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
    res = WaalaxyClient(api_key=fields["key"]).test_connection()
    if res is not True:
        raise RuntimeError(f"Waalaxy /integrations/test n'a pas répondu true : {res!r} "
                           "(clé sans accès API ? plan Advanced/Business requis)")


def _receipt(raw: Any, prospects: List[Dict[str, Any]], campaign_id: Optional[str]) -> Dict[str, Any]:
    """Lean receipt over Waalaxy's per-item codes. `total` is always the number
    of prospects SENT; a response shorter/longer than the batch is reported,
    never silently truncated, and an unexpected body still yields the full
    shape (so `failed` is always readable)."""
    total = len(prospects)
    items = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {"total": total, "imported": 0, "items": [],
                "failed": [{"index": None, "url": None, "code": "unexpected_response",
                            "message": "Waalaxy a répondu 200 sans tableau `result`"}],
                "raw": raw, **({"enrolled": 0} if campaign_id else {})}
    imported = enrolled = 0
    failed: List[Dict[str, Any]] = []
    lean: List[Dict[str, Any]] = []
    for i, item in enumerate(items):
        item = item or {}
        code = item.get("importCode")
        camp = item.get("addToCampaignCode")
        url = prospects[i].get("url") if i < total else None
        prospect = item.get("prospect") or {}
        profile = prospect.get("profile") or {}
        row: Dict[str, Any] = {"index": i, "url": url, "importCode": code,
                               "prospect_id": prospect.get("_id"),
                               "publicIdentifier": profile.get("publicIdentifier")}
        if campaign_id:
            row["addToCampaignCode"] = camp
        lean.append(row)
        ok = code in _IMPORT_OK
        if ok:
            imported += 1
        else:
            failed.append({"index": i, "url": url, "code": code, "message": item.get("message")})
        if campaign_id and ok:
            if camp == "success":
                enrolled += 1
            else:
                failed.append({"index": i, "url": url, "code": camp or "not_enrolled",
                               "message": item.get("message"), "stage": "campaign"})
    out: Dict[str, Any] = {"total": total, "imported": imported, "failed": failed, "items": lean}
    if campaign_id:
        out["enrolled"] = enrolled
    if len(items) != total:
        out["warning"] = (f"Waalaxy a rendu {len(items)} résultats pour {total} prospects envoyés — "
                          "l'appariement par position est incertain, vérifie dans l'app")
        for i in range(len(items), total):
            failed.append({"index": i, "url": prospects[i].get("url"), "code": "no_result",
                           "message": "aucun résultat rendu par Waalaxy pour ce prospect"})
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
        except (requests.ConnectionError, requests.Timeout) as e:
            raise _bad(f"Waalaxy injoignable (réseau/timeout) — réessaie plus tard. {e}")

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
        message, stage?}], items: [{index, url, importCode,
        addToCampaignCode?, prospect_id, publicIdentifier}]}` — `prospect_id`
        is the Waalaxy prospect `_id` (null on failure). Waalaxy auto-enriches
        the profile from LinkedIn on import (headline, region, company…).
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
        # Same guards + same body as the real call: the preview cannot diverge.
        body = _run(lambda: WaalaxyClient.build_add_prospects_body(
            batch, prospect_list_id, origin=origin, **flags))
        if dry_run:
            return {"dry_run": True, "total": len(batch), "would_post": body}
        raw = _run(lambda: _client().add_prospects(batch, prospect_list_id, origin=origin, **flags))
        return _receipt(raw, batch, campaign_id)
