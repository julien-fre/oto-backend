"""Lemlist — campagnes, séquences, plannings, leads, stats et enrichissement.

Le connecteur savait LIRE une campagne et y poser des leads ; il ne savait pas
en conduire une. `lemlist_campaign`, `lemlist_campaign_start`,
`lemlist_sequence` et `lemlist_schedule` ferment ce trou : créer et régler une
campagne, écrire sa séquence pas à pas, tenir ses fenêtres d'envoi, la valider,
la démarrer, la mettre en pause, la dupliquer, la mesurer.

La borne n'a pas disparu, elle s'est DÉPLACÉE là où elle mord vraiment : sur ce
qui met des messages sur le fil, pas sur l'écriture en général. Deux gestes le
font — `lemlist_campaign_start` (lemlist déroule la séquence pour tous les leads
lancés) et `lemlist_launch_lead` (un lead sort de la revue). Les deux sont
masqués par défaut (`DEFAULT_HIDDEN_TOOLS`, self-activables). Tout le reste —
créer, régler, dupliquer, pauser, planifier — travaille sur un BROUILLON et
n'envoie rien.

C'est ce grain-là qui a dicté le découpage : `DEFAULT_HIDDEN_TOOLS` a le grain du
TOOL, pas de l'`op`. `start` en `lemlist_campaign(op="start")` serait rentré dans
un tool visible et aurait dégondé la borne en silence — d'où un tool nu pour lui
seul.

Corollaire, et c'est le point le moins évident du module : `autoReview` /
`autoReviewConditions` sont REFUSÉS sur `create` et `update`. Ce réglage lance
tout lead dès son ajout, donc il transformerait `lemlist_create_lead` — visible,
et visible PARCE QU'il n'envoie rien — en chemin d'envoi, sans qu'aucun tool
masqué soit appelé. Il se règle dans l'UI lemlist.

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
import datetime as _dt
from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..output_projection import project

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


#: Les DEUX réglages de campagne qui dissolvent la revue manuelle : avec eux, un
#: lead créé part TOUT DE SUITE. Le modèle de sûreté du connecteur repose sur
#: l'inverse — `lemlist_create_lead` est visible parce qu'il n'envoie rien, et
#: seul `lemlist_launch_lead` (masqué par défaut) déclenche l'envoi. Les laisser
#: passer ici transformerait un tool visible en chemin d'envoi, sans que rien ne
#: le signale. Refusés au bord, pas filtrés en silence.
AUTO_REVIEW_KEYS = ("autoReview", "autoReviewConditions")

#: Plancher de la fenêtre de stats. Les deux dates sont OBLIGATOIRES côté lemlist
#: et un agent qui veut « les stats de la campagne » n'en a aucune en tête : ce
#: plancher précède lemlist lui-même, donc il vaut « depuis toujours ».
STATS_EPOCH = "2015-01-01T00:00:00.000Z"

#: Détail rendu sur `full=True` seulement — un `steps` par étape de séquence et
#: un `perChannel` par canal, là où la question courante tient dans les compteurs
#: de tête.
STATS_DETAIL = ("steps", "perChannel")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _default_window(start_date: Optional[str], end_date: Optional[str]) -> tuple[str, str]:
    """Complète la fenêtre de stats — bornes ISO 8601, les deux exigées upstream."""
    # Aliasé `_dt` : `timezone` est un ARGUMENT de `lemlist_campaign`/
    # `lemlist_schedule` (la zone IANA de lemlist), l'importer nu le masquerait.
    now = _dt.datetime.now(_dt.timezone.utc)
    return start_date or STATS_EPOCH, end_date or (
        now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z")


def _project_stats(result: dict, *, full: bool) -> dict:
    """Coupe le détail par étape/par canal, et NOMME ce qui a été écarté."""
    if full or not isinstance(result, dict):
        return result
    dropped = {k: len(result[k]) for k in STATS_DETAIL
               if isinstance(result.get(k), (list, dict))}
    out = project(result, drop=STATS_DETAIL)
    if dropped:
        out["projection"] = {
            "dropped": dropped,
            "hint": "Détail par étape / par canal écarté — `full=True` le rend.",
        }
    return out


def _refuse_auto_review(settings: dict) -> None:
    """Refuse `autoReview*` — cf. AUTO_REVIEW_KEYS."""
    present = [k for k in AUTO_REVIEW_KEYS if k in settings]
    if present:
        raise _bad(
            f"{', '.join(present)} n'est pas réglable ici. Ce réglage fait partir "
            "tout lead ajouté SANS revue : il transformerait `lemlist_create_lead` "
            "en envoi. Il se règle dans l'UI lemlist (paramètres de la campagne), "
            "délibérément hors de portée d'un appel d'agent."
        )


def _found_digest(data: dict) -> dict:
    """Ce qui a VRAIMENT été trouvé, par axe — `data` porte toujours la clé de
    l'axe demandé, même vide, donc sa seule présence ne dit rien.

    Formes relevées en live (au-delà du schéma publié) : `email` porte `email`
    et un `status` de vérification (`deliverable`/`undeliverable`), `phone`
    porte `phone`, `linkedin` porte un profil complet — ou `{}` quand le profil
    n'a pas pu être résolu. `notFound` n'est PAS fiable : on l'a vu à `false`
    sur une charge sans numéro.
    """
    found = {}
    email = (data.get("email") or {}).get("email")
    if email:
        found["email"] = email
    status = (data.get("email") or {}).get("status")
    if status:
        found["email_status"] = status
    phone = (data.get("phone") or {}).get("phone")
    if phone:
        found["phone"] = phone
    linkedin = data.get("linkedin") or {}
    if linkedin:
        found["linkedin"] = {
            k: v for k, v in linkedin.items()
            if k in ("firstName", "lastName", "tagline", "locationName", "linkedinUrl")
        } or True
    return found


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
    def lemlist_list_campaigns(
        status: Optional[str] = None,
        created_by: Optional[str] = None,
        newest_first: bool = False,
        max_campaigns: int = 500,
    ) -> dict:
        """List the campaigns of the workspace.

        Args:
            status: Keep only `running`, `draft`, `archived`, `ended`, `paused`
                or `errors`. A campaign can hold several at once (paused WITH
                errors), so this filters, it does not partition.
            created_by: Keep only campaigns created by a user id (`usr_…`).
            newest_first: Sort on creation date, most recent first.
            max_campaigns: Ceiling on the walk (lemlist pages 100 at a time).

        Returns `{campaigns: [{id, name, status, senders, emoji, labels,
        timezone, created_at, created_by, has_error, errors}], count,
        truncated}`. `truncated: true` means the ceiling was hit and the list is
        INCOMPLETE — do not conclude a campaign is absent from it.
        """
        client, is_platform = _client()
        filters = {}
        if status is not None:
            filters["status"] = status
        if created_by is not None:
            filters["created_by"] = created_by
        if newest_first:
            filters["sort_order"] = "desc"
        pages = max(1, -(-max_campaigns // 100))  # ceil
        campaigns, truncated = client.list_all_campaigns(max_pages=pages, **filters)
        _record_if_platform(is_platform)
        return {
            "campaigns": [asdict(c) for c in campaigns],
            "count": len(campaigns),
            "truncated": truncated,
        }

    @mcp.tool()
    def lemlist_get_campaign(campaign_id: str) -> dict:
        """Fetch full campaign details by ID."""
        client, is_platform = _client()
        result = client.get_campaign(campaign_id)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_get_campaign_stats(
        campaign_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        channels: Optional[list[str]] = None,
        ab_selected: Optional[str] = None,
        send_user: Optional[str] = None,
        full: bool = False,
    ) -> dict:
        """Campaign performance (leads reached, opened, replied, bounced…).

        Reads lemlist's own counters. Previously derived from one page of
        activities, which under-counted every campaign past 1000 events — the
        field names changed with the fix (`nbLeads`, `messagesSent`, `opened`,
        `replied`… instead of `emails_sent` & co).

        Args:
            start_date / end_date: ISO 8601 window. Defaults to "since 2015" →
                now, i.e. the campaign's whole life.
            channels: Any of `email`, `linkedin`, `others`.
            ab_selected: `A` or `B`, to read one side of a running A/B test.
            send_user: `usr_…|sender@email` — both halves required.
            full: Also return the per-step (`steps`) and per-channel
                (`perChannel`) breakdowns, dropped by default for size.
        """
        client, is_platform = _client()
        start, end = _default_window(start_date, end_date)
        result = client.get_campaign_stats_v2(
            campaign_id, start_date=start, end_date=end,
            channels=channels, ab_selected=ab_selected, send_user=send_user,
        )
        _record_if_platform(is_platform)
        return _project_stats(result, full=full)

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

        Only works on a lead still AWAITING REVIEW — lemlist answers
        `400 "lemrich is not available for lead reviewed"` once a lead has been
        reviewed, which is the state of every lead in a campaign without
        review-before-send. For anyone else, enrich the person with
        `lemlist_enrich` and write the result back yourself.

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
        input, data, found}`. `status` is `done`, `in-progress` or `not-found`.
        `data` is lemlist's raw payload; `found` is the digest to read — only
        the axes that actually carry a value (`email`, `email_status`
        `deliverable`/`undeliverable`, `phone`, `linkedin`). An axis key is
        present in `data` even when empty, so presence alone means nothing, and
        `notFound: false` has been seen on a payload with no number.

        A result can come back `done` with nothing in it: lemlist sometimes
        flips the status before the payload lands. Such an entry carries a
        `warning` and is NOT counted in `all_done` — poll it once more before
        concluding nothing was found (a poll costs no credits).
        """
        ids = [enrichment_id] if isinstance(enrichment_id, str) else list(enrichment_id)
        client, _ = _client()
        results = []
        for eid in ids:
            res = client.get_enrichment(eid)
            status = res.get("enrichmentStatus", "unknown")
            data = res.get("data") or {}
            row = {
                "enrichment_id": res.get("enrichmentId", eid),
                "status": status,
                # `not-found` est terminal aussi : re-poller ne le fera pas
                # apparaître, c'est un id inconnu de lemlist.
                "done": status in ("done", "not-found"),
                "input": res.get("input", {}),
                "data": data,
                "found": _found_digest(data),
            }
            if status == "done" and not row["found"]:
                # Observé en live : lemlist bascule parfois sur `done` AVANT que
                # la charge utile soit posée (un `data` vide, puis peuplé au
                # relevé suivant). Sans ce garde-fou, un agent lit « done + rien »
                # et conclut « pas trouvé » sur une donnée qui arrive juste après.
                # Un relevé ne coûte pas de crédit : autant le refaire une fois.
                row["warning"] = (
                    "done but empty — lemlist sometimes flips to done before the "
                    "payload lands. Poll once more (~15s) before concluding "
                    "nothing was found."
                )
            results.append(row)
        pending = [r["enrichment_id"] for r in results if not r["done"]]
        settling = [r["enrichment_id"] for r in results if r.get("warning")]
        # `all_done` ne parle QUE de ce qui tourne encore : un résultat
        # légitimement vide (personne introuvable) le resterait à jamais, et un
        # agent qui boucle sur `all_done` ne s'arrêterait plus. Le re-relevé
        # d'un `done` vide est une SUGGESTION, à faire une fois — pas une
        # condition de sortie.
        out = {"results": results, "all_done": not pending}
        if settling:
            out["recheck_suggested"] = settling
        if pending or settling:
            bits = []
            if pending:
                bits.append(f"{len(pending)} still running")
            if settling:
                bits.append(f"{len(settling)} done-but-empty (re-poll ONCE, "
                            "then treat as not found)")
            out["next_step"] = (
                ", ".join(bits) + " — call lemlist_enrich_result again in ~15-30s."
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
        if is_platform:
            # Un bulk est facturé À LA PERSONNE : la consommation est le nombre
            # d'entrées soumises, pas 1 pour l'appel (même règle que FullEnrich).
            access.record_platform_usage("lemlist", len(items))
        submitted = []
        for i, entry in enumerate(raw if isinstance(raw, list) else []):
            # `metadata` est renvoyé tel quel par lemlist, mais sa forme n'est
            # pas stable (leur propre exemple montre `{"id": ...}` ET une
            # chaîne nue) : on s'en sert quand il porte bien l'index qu'on a
            # posé, sinon on retombe sur la position — les entrées reviennent
            # dans l'ordre soumis.
            meta = entry.get("metadata")
            index = i
            if isinstance(meta, dict) and str(meta.get("index", "")).isdigit():
                index = int(meta["index"])
            row = {"index": index}
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

    # --- Gestion de campagne ---------------------------------------------------
    #
    # Trois tools à `op`, un tool nu. Le découpage n'est pas cosmétique : le
    # masquage par défaut (`DEFAULT_HIDDEN_TOOLS`) a le grain du TOOL, pas de
    # l'op. `start` — le seul geste ici qui mette des messages sur le fil — vit
    # donc à part, masqué ; le reste (créer, régler, dupliquer, mettre en pause,
    # valider) n'envoie rien et tient dans un tool visible par famille.

    @mcp.tool()
    def lemlist_campaign(
        op: Literal["create", "update", "pause", "duplicate", "statutes",
                    "reports", "batch_stats"],
        campaign_id: Optional[str] = None,
        campaign_ids: Optional[list[str]] = None,
        name: Optional[str] = None,
        timezone: Optional[str] = None,
        settings: Optional[dict] = None,
        sender_user_ids: Optional[list[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        channels: Optional[list[str]] = None,
        full: bool = False,
    ) -> dict:
        """Manage campaigns: create, configure, pause, duplicate, validate, report.

        Nothing here sends: a created or duplicated campaign lands in DRAFT.
        Putting messages on the wire is `lemlist_campaign_start` (hidden by
        default — enable it with `oto_enable_tool lemlist_campaign_start`).

        Args by op:
        - `create`: `name` (required), optional `timezone` (IANA, drives the
          auto-created schedule; server default `Europe/Paris`). Returns the
          campaign with `sequenceId` and `scheduleIds` — the two ids
          `lemlist_sequence` and `lemlist_schedule` need.
        - `update`: `campaign_id` + `name`, `sender_user_ids` (`usr_…`, the
          senders) and/or `settings` (raw PATCH body: `stopOnEmailReplied`,
          `stopOnMeetingBooked`, `stopOnLinkClicked`, `disableTrackOpen`,
          `disableTrackClick`, `disableTrackReply`, `tracking`, `onReplied`,
          `aiFeatures`…). Only the keys sent change.
        - `pause`: `campaign_id`. Stops the campaign advancing; already-scheduled
          leads are NOT recalled.
        - `duplicate`: `campaign_id` + optional `name`. Copies sequence, steps,
          schedules and AI templates into a fresh DRAFT (CRM settings excluded).
        - `statutes`: `campaign_id`. The validation the lemlist UI runs — read it
          BEFORE starting: `level` 3 blocks the launch (no sender, broken DNS),
          2 warns (daily limit, missing schedule), 1 informs.
        - `reports`: `campaign_ids`. One row per campaign in operator vocabulary
          (`emailsSent`, `emailsOpened`, `emailsReplied`, `senderNames`, `state`)
          — the shape for comparing campaigns.
        - `batch_stats`: `campaign_ids` (≤ 100) + optional `start_date`/`end_date`
          (defaults to the whole life), `channels` (`email`/`linkedin`/`others`).
          Same counters as `lemlist_get_campaign_stats`, in one call.

        `autoReview`/`autoReviewConditions` are refused on `create` and `update`:
        they make every added lead send immediately, which would turn
        `lemlist_create_lead` into a send path. Set them in the lemlist UI.
        """
        client, is_platform = _client()
        settings = dict(settings or {})

        if op == "create":
            if not name:
                raise _bad("`name` requis pour créer une campagne")
            _refuse_auto_review(settings)
            result = client.create_campaign(name, timezone=timezone)

        elif op == "update":
            if not campaign_id:
                raise _bad("`campaign_id` requis")
            _refuse_auto_review(settings)
            if name is not None:
                settings["name"] = name
            if sender_user_ids is not None:
                settings["sendUserIds"] = sender_user_ids
            if not settings:
                raise _bad(
                    "rien à mettre à jour — passe `name`, `sender_user_ids` "
                    "et/ou `settings`")
            result = client.update_campaign(campaign_id, settings)

        elif op == "pause":
            if not campaign_id:
                raise _bad("`campaign_id` requis")
            result = client.pause_campaign(campaign_id)

        elif op == "duplicate":
            if not campaign_id:
                raise _bad("`campaign_id` requis")
            result = client.duplicate_campaign(campaign_id, name=name)

        elif op == "statutes":
            if not campaign_id:
                raise _bad("`campaign_id` requis")
            result = client.get_campaign_statutes(campaign_id)

        elif op == "reports":
            if not campaign_ids:
                raise _bad("`campaign_ids` requis (liste d'ids de campagne)")
            result = {"reports": client.get_campaign_reports(campaign_ids)}

        elif op == "batch_stats":
            if not campaign_ids:
                raise _bad("`campaign_ids` requis (liste d'ids de campagne)")
            start, end = _default_window(start_date, end_date)
            result = client.get_batch_campaign_stats(
                campaign_ids, start_date=start, end_date=end, channels=channels)
            if not full:
                result = {**result, "results": [
                    _project_stats(r, full=False) for r in result.get("results", [])
                ]}

        else:
            raise _bad(
                f'op inconnu "{op}" — attendu: create, update, pause, duplicate, '
                "statutes, reports, batch_stats")

        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_campaign_start(campaign_id: str) -> dict:
        """Start (or resume) a campaign — lemlist begins sending.

        THE send gesture at campaign level: from here lemlist walks the sequence
        for every launched lead, to real people. A no-op if already running.

        Read `lemlist_campaign(op="statutes", …)` first — it names what would
        block or degrade the launch (missing sender, broken DNS, daily limit)
        with the same validation the UI runs.
        """
        client, is_platform = _client()
        result = client.start_campaign(campaign_id)
        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_sequence(
        op: Literal["get", "add_step", "update_step", "delete_step",
                    "ab_create", "ab_get", "ab_update", "ab_delete", "ab_winner"],
        campaign_id: Optional[str] = None,
        sequence_id: Optional[str] = None,
        step_id: Optional[str] = None,
        step: Optional[dict] = None,
        variant: Optional[str] = None,
    ) -> dict:
        """Read and edit the steps of a campaign sequence, and its A/B tests.

        A campaign owns a sequence (`seq_…`, returned by `create`) whose steps
        (`stp_…`) are the emails, LinkedIn actions and conditions it runs.

        Args by op:
        - `get`: `campaign_id`. Every sequence of the campaign with its steps —
          conditional steps branch into further sequences, so a campaign can
          hold several.
        - `add_step`: `sequence_id` + `step`. `step.type` is required, one of
          email, manual, phone, api, linkedinVisit, linkedinInvite,
          linkedinSend, linkedinVoiceNote, linkedinFollow, linkedinLikeLastPost,
          linkedinCommentLastPost, linkedinEndorse, linkedinWithdrawInvitation,
          sendToAnotherCampaign, conditional, whatsappMessage, sms. Common
          fields: `index` (insert position, appended when omitted), `delay`
          (days), `subject` + `message` (email), `title` (manual),
          `method` + `url` (api), `conditionKey` + `delayType` (conditional),
          `campaignId` (sendToAnotherCampaign).
        - `update_step`: `sequence_id` + `step_id` + `step`. `step.type` is
          required and must MATCH the existing step — it identifies the shape,
          it does not convert it. `images`/`videos` REPLACE what is there.
        - `delete_step`: `sequence_id` + `step_id`. Refused by lemlist while the
          campaign is running — pause it first.
        - `ab_create`: `sequence_id` + `step_id` (an EMAIL step). Creates
          variant B prefilled from A and STARTS the split. Email Pro plan.
        - `ab_get` / `ab_update` (`step` = the B fields: `subject`, `message`,
          `altMessage`, `cc`, `plainText`).
        - `ab_delete`: optional `variant` (default `B`). `A` promotes B to A.
        - `ab_winner`: `variant` (`A` or `B`) — the winner's template is then
          sent to every remaining lead.
        """
        client, is_platform = _client()

        if op == "get":
            if not campaign_id:
                raise _bad("`campaign_id` requis")
            result = {"sequences": client.get_sequences(campaign_id)}
        else:
            if not sequence_id:
                raise _bad("`sequence_id` requis (il vient de `lemlist_campaign` "
                           "op=create, ou de op=\"get\" ici)")
            if op == "add_step":
                if not step:
                    raise _bad("`step` requis (au minimum `{\"type\": …}`)")
                result = client.add_step(sequence_id, step)
            elif op in ("update_step", "delete_step", "ab_create", "ab_get",
                        "ab_update", "ab_delete", "ab_winner"):
                if not step_id:
                    raise _bad("`step_id` requis")
                if op == "update_step":
                    if not step:
                        raise _bad("`step` requis (et `step.type` doit correspondre "
                                   "au type existant)")
                    result = client.update_step(sequence_id, step_id, step)
                elif op == "delete_step":
                    result = client.delete_step(sequence_id, step_id)
                elif op == "ab_create":
                    result = client.create_ab_variant(sequence_id, step_id)
                elif op == "ab_get":
                    result = client.get_ab_variant(sequence_id, step_id)
                elif op == "ab_update":
                    if not step:
                        raise _bad("`step` requis (les champs de la variante B)")
                    result = client.update_ab_variant(sequence_id, step_id, step)
                elif op == "ab_delete":
                    result = client.delete_ab_variant(
                        sequence_id, step_id, variant=variant or "B")
                else:  # ab_winner
                    if not variant:
                        raise _bad("`variant` requis — 'A' ou 'B'")
                    result = client.select_ab_winner(sequence_id, step_id, variant)
            else:
                raise _bad(
                    f'op inconnu "{op}" — attendu: get, add_step, update_step, '
                    "delete_step, ab_create, ab_get, ab_update, ab_delete, ab_winner")

        _record_if_platform(is_platform)
        return result

    @mcp.tool()
    def lemlist_schedule(
        op: Literal["list", "get", "create", "update", "delete",
                    "for_campaign", "associate"],
        schedule_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        name: Optional[str] = None,
        timezone: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        weekdays: Optional[list[int]] = None,
        seconds_to_wait: Optional[int] = None,
        public: Optional[bool] = None,
    ) -> dict:
        """Manage sending windows (schedules) — days, hours, timezone, pacing.

        A schedule belongs to the TEAM, not to a campaign: several campaigns can
        share one, and a campaign can carry several. Creating a campaign
        auto-creates one and returns its id in `scheduleIds`.

        Args by op:
        - `list`: no argument. Every schedule of the team.
        - `get` / `delete`: `schedule_id`.
        - `create`: `name` (required) + `timezone` (IANA, default
          `Europe/Paris`), `start`/`end` (`HH:mm`, default 09:00-18:00),
          `weekdays` (1 = Monday … 7 = Sunday, default Mon-Fri),
          `seconds_to_wait` (pacing between two sends), `public` (offer it as a
          team template).
        - `update`: `schedule_id` + any of the same fields; only what is sent
          changes.
        - `for_campaign`: `campaign_id`. The schedules attached to a campaign.
        - `associate`: `campaign_id` + `schedule_id`. Attaches an existing
          window to a campaign.
        """
        client, is_platform = _client()

        if op == "list":
            result = client.list_schedules()
        elif op == "get":
            if not schedule_id:
                raise _bad("`schedule_id` requis")
            result = client.get_schedule(schedule_id)
        elif op == "create":
            if not name:
                raise _bad("`name` requis pour créer un planning")
            kwargs = {k: v for k, v in {
                "timezone": timezone, "start": start, "end": end,
                "weekdays": weekdays, "seconds_to_wait": seconds_to_wait,
                "public": public,
            }.items() if v is not None}
            result = client.create_schedule(name, **kwargs)
        elif op == "update":
            if not schedule_id:
                raise _bad("`schedule_id` requis")
            data = {k: v for k, v in {
                "name": name, "timezone": timezone, "start": start, "end": end,
                "weekdays": weekdays, "secondsToWait": seconds_to_wait,
                "public": public,
            }.items() if v is not None}
            if not data:
                raise _bad("rien à mettre à jour — passe au moins un champ")
            result = client.update_schedule(schedule_id, data)
        elif op == "delete":
            if not schedule_id:
                raise _bad("`schedule_id` requis")
            result = client.delete_schedule(schedule_id)
        elif op == "for_campaign":
            if not campaign_id:
                raise _bad("`campaign_id` requis")
            result = {"schedules": client.get_campaign_schedules(campaign_id)}
        elif op == "associate":
            if not (campaign_id and schedule_id):
                raise _bad("`campaign_id` ET `schedule_id` requis")
            result = client.associate_schedule(campaign_id, schedule_id)
        else:
            raise _bad(
                f'op inconnu "{op}" — attendu: list, get, create, update, delete, '
                "for_campaign, associate")

        _record_if_platform(is_platform)
        return result
