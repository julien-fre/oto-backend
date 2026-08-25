"""Lemlist — campagnes, séquences, leads, stats, lead lifecycle et enrichissement.

Volontairement borné : `lemlist_create_lead`/`lemlist_launch_lead`/
`lemlist_add_lead_variables` exposent la création + le lancement d'un lead et
la pose de ses variables — mais pas créer/pauser une campagne ni supprimer un
lead. Un mauvais call LLM peut là aussi déclencher un envoi involontairement,
donc ce périmètre reste la porte d'entrée écrite minimale ; le reste passe par
l'UI Lemlist.

L'enrichissement (`lemlist_enrich`, `lemlist_enrich_lead`) n'envoie rien — mais
il DÉPENSE des crédits lemlist à chaque action. D'où la même borne, prise
autrement : aucune action par défaut, un appel sans action demandée échoue ici
(INVALID_PARAMS) plutôt que d'aller chercher le 400 documenté de lemlist.

Surface async, comme FullEnrich (signal #252) : le POST rend un `enrichment_id`
en ~1s et le travail continue côté lemlist. Le polling appartient à l'agent —
`lemlist_enrich_result` relève un statut et rend la main. Jamais de boucle
d'attente in-process : tout client MCP raccroche vers 60s, et le résultat
serait perdu ALORS QUE les crédits, eux, sont consommés.

Clé résolue par appel via `access.resolve_api_key("lemlist")`. Pas de
quota plateforme par défaut — chaque user voit SES propres campagnes,
donc user key obligatoire.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access

#: Vocabulaire d'actions du bulk v2 de lemlist. Volontairement écrit à la main :
#: ce n'est PAS un snake_case des flags v1 — la vérification d'email s'appelle
#: `verify` et non `verify_email`. Miroir de `LemlistClient.ENRICH_BULK_ACTIONS`,
#: gardé aligné par un test de version-skew.
BULK_ACTIONS = {
    "find_email": "find_email",
    "verify_email": "verify",
    "linkedin_enrichment": "linkedin_enrichment",
    "find_phone": "find_phone",
}


def register(mcp: FastMCP) -> None:
    from oto.tools.lemlist import LemlistClient

    def _client() -> tuple[LemlistClient, bool]:
        key, is_platform = access.resolve_api_key("lemlist")
        return LemlistClient(api_key=key), is_platform

    def _record_if_platform(is_platform: bool) -> None:
        if is_platform:
            access.record_platform_usage("lemlist")

    @mcp.tool()
    def lemlist_status() -> dict:
        """Workspace status (account, credits, plan)."""
        client, is_platform = _client()
        result = client.status()
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_list_campaigns() -> dict:
        """List all campaigns in the workspace.

        Returns a list of `{id, name, status, senders, emoji}`. Use `id` for
        the other lemlist tools.
        """
        client, is_platform = _client()
        campaigns = client.list_campaigns()
        _record_if_platform(is_platform)
        return {"campaigns": [asdict(c) for c in campaigns]}

    @mcp.tool()
    def lemlist_get_campaign(campaign_id: str) -> dict:
        """Fetch full campaign details by ID."""
        client, is_platform = _client()
        result = client.get_campaign(campaign_id)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_get_campaign_stats(campaign_id: str) -> dict:
        """Get campaign performance stats (sent, opened, replied, bounced…)."""
        client, is_platform = _client()
        result = client.get_campaign_stats(campaign_id)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_get_activities(
        campaign_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Get recent activity events (opens, clicks, replies…).

        Args:
            campaign_id: Restrict to a campaign (optional).
            limit: Max events (default 100).
            offset: Pagination offset.
        """
        client, is_platform = _client()
        events = client.get_activities(
            campaign_id=campaign_id, limit=limit, offset=offset,
        )
        _record_if_platform(is_platform)
        return {"activities": events}

    @mcp.tool()
    def lemlist_get_leads(campaign_id: str) -> dict:
        """List all leads for a campaign with their state (sent, replied…)."""
        client, is_platform = _client()
        leads = client.get_all_leads(campaign_id)
        _record_if_platform(is_platform)
        return {"leads": leads}

    @mcp.tool()
    def lemlist_create_lead(
        campaign_id: str,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        company_name: Optional[str] = None,
        job_title: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        phone: Optional[str] = None,
        company_domain: Optional[str] = None,
        icebreaker: Optional[str] = None,
        timezone: Optional[str] = None,
        contact_owner: Optional[str] = None,
        custom_variables: Optional[dict] = None,
        deduplicate: bool = False,
        linkedin_enrichment: bool = False,
        find_email: bool = False,
        verify_email: bool = False,
        find_phone: bool = False,
    ) -> dict:
        """Create a lead in a campaign.

        All lead fields are optional (lemlist accepts phone/LinkedIn-only
        leads), but you'll usually pass at least `email` or `linkedin_url`.
        `custom_variables` merges extra key-value pairs into the lead, used for
        campaign personalization (e.g. `{{variableName}}` in a template).

        Enrichment flags (all default False, each may cost lemlist credits):
        `deduplicate` skips the insert if the email already exists in another
        campaign, `linkedin_enrichment` runs LinkedIn enrichment,
        `find_email`/`verify_email` find or verify the email, `find_phone`
        finds a phone number.

        Returns the created lead, including `_id` — pass it to
        `lemlist_launch_lead`/`lemlist_add_lead_variables`. If the campaign has
        review-before-send enabled, the lead is created paused and won't send
        until `lemlist_launch_lead` is called.
        """
        lead = {
            k: v for k, v in {
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "companyName": company_name,
                "jobTitle": job_title,
                "linkedinUrl": linkedin_url,
                "phone": phone,
                "companyDomain": company_domain,
                "icebreaker": icebreaker,
                "timezone": timezone,
                "contactOwner": contact_owner,
            }.items() if v is not None
        }
        if custom_variables:
            lead.update(custom_variables)
        client, is_platform = _client()
        result = client.create_lead(
            campaign_id, lead,
            deduplicate=deduplicate, linkedin_enrichment=linkedin_enrichment,
            find_email=find_email, verify_email=verify_email, find_phone=find_phone,
        )
        _record_if_platform(is_platform)
        return result

    def _require_action(**flags: bool) -> None:
        if not any(flags.values()):
            raise McpError(ErrorData(
                code=INVALID_PARAMS,
                message=("No enrichment requested — set at least one of "
                         "find_email, verify_email, linkedin_enrichment, find_phone."),
            ))

    @mcp.tool()
    def lemlist_enrich(
        email: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        company_name: Optional[str] = None,
        company_domain: Optional[str] = None,
        find_email: bool = False,
        verify_email: bool = False,
        linkedin_enrichment: bool = False,
        find_phone: bool = False,
    ) -> dict:
        """Submit an ASYNC enrichment on a person — no campaign, no lead needed.

        Returns immediately with an `enrichment_id`; the work runs server-side.
        THEN call `lemlist_enrich_result(enrichment_id)` to collect it (first
        poll after ~10-20s, then every ~15-30s until `done`).

        Args:
            email, linkedin_url, first_name, last_name, company_name,
                company_domain: the identity to enrich. All optional, but
                lemlist only resolves what it can match — pass a LinkedIn URL,
                or a first/last name together with a company domain.

        
        Enrichment actions — at least one is required, and each spends lemlist
        credits: `find_email` finds a verified email, `verify_email` verifies the
        email you passed (debounce), `linkedin_enrichment` runs the LinkedIn
        enrichment, `find_phone` finds a phone number. Ask only for what you need.
        """
        _require_action(
            find_email=find_email, verify_email=verify_email,
            linkedin_enrichment=linkedin_enrichment, find_phone=find_phone,
        )
        client, is_platform = _client()
        result = client.enrich(
            email=email, linkedin_url=linkedin_url,
            first_name=first_name, last_name=last_name,
            company_name=company_name, company_domain=company_domain,
            find_email=find_email, verify_email=verify_email,
            linkedin_enrichment=linkedin_enrichment, find_phone=find_phone,
        )
        _record_if_platform(is_platform)
        return {
            "enrichment_id": result.get("id"),
            "next_step": ("Enrichment accepted. Call "
                          "lemlist_enrich_result(enrichment_id) in ~10-20s."),
        }

    @mcp.tool()
    def lemlist_enrich_lead(
        lead_id: str,
        find_email: bool = False,
        verify_email: bool = False,
        linkedin_enrichment: bool = False,
        find_phone: bool = False,
    ) -> dict:
        """Enrich a lead that is ALREADY in a campaign, in place.

        Same actions as `lemlist_enrich`, but the identity comes from the
        existing lead and lemlist writes the result back onto it. Async too:
        returns an `enrichment_id` for `lemlist_enrich_result`.

        Args:
            lead_id: the lead's `_id`, as returned by `lemlist_create_lead`.

        
        Enrichment actions — at least one is required, and each spends lemlist
        credits: `find_email` finds a verified email, `verify_email` verifies the
        email you passed (debounce), `linkedin_enrichment` runs the LinkedIn
        enrichment, `find_phone` finds a phone number. Ask only for what you need.
        """
        _require_action(
            find_email=find_email, verify_email=verify_email,
            linkedin_enrichment=linkedin_enrichment, find_phone=find_phone,
        )
        client, is_platform = _client()
        result = client.enrich_lead(
            lead_id,
            find_email=find_email, verify_email=verify_email,
            linkedin_enrichment=linkedin_enrichment, find_phone=find_phone,
        )
        _record_if_platform(is_platform)
        return {
            "enrichment_id": result.get("id"),
            "next_step": ("Enrichment accepted. Call "
                          "lemlist_enrich_result(enrichment_id) in ~10-20s."),
        }

    @mcp.tool()
    def lemlist_enrich_result(enrichment_id: str | list[str]) -> dict:
        """Collect the result of an enrichment submitted with `lemlist_enrich`,
        `lemlist_enrich_lead` or `lemlist_enrich_bulk`.

        Single status check per id, returns immediately — no waiting. If a
        result is not `done`, wait ~15-30s and call again.

        Args:
            enrichment_id: one id, or a list of ids (a bulk submit yields one id
                per person, so pass them all here in one go).

        Returns `results`: one entry per id, `{enrichment_id, status, done,
        input, data}`. `status` is `done`, `in-progress` or `not-found`. `data`
        holds what was found, e.g. `{"email": {"email": "john@lempire.co",
        "notFound": false}}`. `all_done` says whether every id has settled.
        """
        ids = [enrichment_id] if isinstance(enrichment_id, str) else list(enrichment_id)
        client, _ = _client()
        results = []
        for eid in ids:
            res = client.get_enrichment(eid)
            status = res.get("enrichmentStatus", "unknown")
            results.append({
                "enrichment_id": res.get("enrichmentId", eid),
                "status": status,
                # `not-found` est terminal aussi : re-poller ne le fera pas
                # apparaître, c'est un id inconnu de lemlist.
                "done": status in ("done", "not-found"),
                "input": res.get("input", {}),
                "data": res.get("data", {}),
            })
        pending = [r["enrichment_id"] for r in results if not r["done"]]
        out = {"results": results, "all_done": not pending}
        if pending:
            out["next_step"] = (
                f"{len(pending)} still running — call lemlist_enrich_result "
                "again in ~15-30s with the pending ids."
            )
        return out

    @mcp.tool()
    def lemlist_enrich_bulk(people: list[dict]) -> dict:
        """Submit several enrichments in one call.

        Args:
            people: one entry per person. Identity keys (all optional, same
                matching rules as `lemlist_enrich`): `email`, `linkedin_url`,
                `first_name`, `last_name`, `company_name`, `company_domain`.
                Plus `actions`: a list among `find_email`, `verify_email`,
                `linkedin_enrichment`, `find_phone` — required, and each action
                spends lemlist credits per person.

        Returns `submitted`: one entry per person, in order, each carrying
        either `enrichment_id` or `error` (e.g. `MISSING_INPUTS`). Unlike a
        FullEnrich job, a bulk submit yields one id PER PERSON — pass the whole
        list of ids to `lemlist_enrich_result`.
        """
        if not people:
            raise McpError(ErrorData(
                code=INVALID_PARAMS, message="`people` is empty — nothing to enrich.",
            ))
        client, is_platform = _client()

        items = []
        for i, person in enumerate(people):
            actions = person.get("actions") or []
            if isinstance(actions, str):
                actions = [actions]
            unknown = [a for a in actions if a not in BULK_ACTIONS]
            if unknown:
                raise McpError(ErrorData(
                    code=INVALID_PARAMS,
                    message=(f"people[{i}]: unknown action(s) {unknown} — allowed: "
                             f"{sorted(BULK_ACTIONS)}."),
                ))
            if not actions:
                raise McpError(ErrorData(
                    code=INVALID_PARAMS,
                    message=(f"people[{i}]: no `actions` — set at least one of "
                             f"{sorted(BULK_ACTIONS)}."),
                ))
            item = {
                "input": {
                    k: v for k, v in {
                        "email": person.get("email"),
                        "linkedinUrl": person.get("linkedin_url"),
                        "firstName": person.get("first_name"),
                        "lastName": person.get("last_name"),
                        "companyName": person.get("company_name"),
                        "companyDomain": person.get("company_domain"),
                    }.items() if v is not None
                },
                # Vocabulaire v2 : `verify`, pas `verify_email` — la table de
                # correspondance vit dans le client, ce n'est pas un snake_case
                # mécanique des flags v1.
                "enrichmentRequests": [BULK_ACTIONS[a] for a in actions],
                "metadata": {"index": str(i)},
            }
            items.append(item)

        raw = client.bulk_enrich(items)
        _record_if_platform(is_platform)
        submitted = []
        for i, entry in enumerate(raw if isinstance(raw, list) else []):
            row = {"index": i}
            if entry.get("id"):
                row["enrichment_id"] = entry["id"]
            if entry.get("error"):
                row["error"] = entry["error"]
            submitted.append(row)
        ids = [r["enrichment_id"] for r in submitted if r.get("enrichment_id")]
        return {
            "submitted": submitted,
            "enrichment_ids": ids,
            "next_step": ("Call lemlist_enrich_result(enrichment_ids) in ~10-20s."
                          if ids else "Nothing accepted — check the per-entry errors."),
        }

    @mcp.tool()
    def lemlist_launch_lead(lead_id: str) -> dict:
        """Launch a lead that's paused for manual review.

        Only relevant for a campaign with review-before-send enabled — such a
        campaign leaves a newly created lead paused until launched. Returns
        `{"ok": true}` on success; raises with a lemlist error code if it can't
        launch (already launched, paused, no sender available, invalid AI
        variable, campaign step errors…).
        """
        client, is_platform = _client()
        result = client.launch_lead(lead_id)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_add_lead_variables(lead_id: str, variables: dict) -> dict:
        """Set custom variables on a lead — merged into its personalization
        data, e.g. for `{{variableName}}` placeholders in campaign templates."""
        client, is_platform = _client()
        result = client.add_lead_variables(lead_id, variables)
        _record_if_platform(is_platform)
        return result
