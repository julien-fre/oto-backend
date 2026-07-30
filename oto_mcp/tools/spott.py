"""Spott — ATS/CRM des cabinets de recrutement (candidats, jobs, candidatures).

Wrappe `oto.tools.spott.client.SpottClient`. keyed `api_key` (header x-api-key),
byo-only (pas de clé plateforme) : chaque user/org connecte SON compte Spott.

Vocabulaire tenu de bout en bout : un **job** (l'API dit `vacancy` dans ses
chemins), un **candidate**, une **application** (le candidat sur un job, ou
spontanée vers un client), un **client** = l'entreprise cliente du cabinet avec
ses **client contacts**. Deux paginations cohabitent — `*_search` pagine par
`page`, les listes par `cursor`.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, connector_verify


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return ("Spott a rejeté la clé API (HTTP %d) — vérifie la clé configurée "
                "sur ce connecteur (Spott : Settings → API Keys)." % status)
    if status == 404:
        return f"Spott : enregistrement introuvable (404) — vérifie l'id. {e.body}"
    if status == 429:
        return "Spott : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"Spott est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Spott a refusé la requête (HTTP {status}): {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : `GET /users`, le plus petit appel
    authentifié de l'API (pas de quota consommé)."""
    from oto.tools.spott.client import SpottClient
    SpottClient(api_key=fields["key"]).list_users()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.spott.client import SpottClient

    connector_verify.register("spott", _verify)

    def _client() -> SpottClient:
        key, _ = access.resolve_api_key("spott")
        return SpottClient(api_key=key)

    def _call(method: str, *args, **kwargs) -> dict:
        """Appelle le client et traduit les refus amont en erreur d'outil lisible."""
        try:
            return getattr(_client(), method)(*args, **kwargs)
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- Candidats ----------------------------------------------------------

    @mcp.tool()
    def spott_candidates(
        limit: int = 25,
        cursor: Optional[str] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        list_ids: Optional[list[str]] = None,
        include: Optional[list[str]] = None,
    ) -> dict:
        """List candidates in Spott, newest page first (cursor pagination).

        Browsing/syncing tool. To FIND a specific person by name/email/phone, use
        `spott_people`; to filter on structured criteria, use
        `spott_search_candidates`.

        Args:
            limit: page size, max 50.
            cursor: `cursor` returned by the previous call.
            modified_since / modified_until: ISO-8601 bounds on last modification
                (incremental sync).
            list_ids: restrict to these Spott lists (max 25).
            include: extra relations — only `skills` is supported.
        """
        return _call("list_candidates", limit=limit, cursor=cursor,
                     modified_since=modified_since, modified_until=modified_until,
                     list_ids=list_ids, include=include)

    @mcp.tool()
    def spott_candidate(candidate_id: str) -> dict:
        """Fetch one candidate: identity, contact details, linked client contacts."""
        return _call("get_candidate", candidate_id)

    @mcp.tool()
    def spott_search_candidates(
        filters: Optional[list[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> dict:
        """Search candidates with structured filters (page pagination).

        Args:
            filters: list of `{type, operator, path, value}`. Native paths:
                `candidate.firstName` / `candidate.lastName` (type `text`,
                operators contains|equals|startsWith|notEquals),
                `candidate.mainContact` (`entitySelect`, in|notIn),
                `candidate.createdAt` (`date`, lessThanOrEqual|greaterThanOrEqual
                |equals|notEquals). Custom attributes use the `custom*` types
                (e.g. `{"type": "customText", …}`) with the attribute definition
                id as `path` — list definitions in the Spott UI.
            page: 0-based page index.
            page_size: results per page.
        """
        return _call("search_candidates", filters=filters, page=page,
                     page_size=page_size)

    @mcp.tool()
    def spott_create_candidate(candidate: dict) -> dict:
        """Create a candidate.

        Args:
            candidate: `firstName` and `lastName` are required. Optional:
                `emails` / `phoneNumbers` (each `{email|phoneNumber, purpose,
                isPrimary}`), `locations`, `socialMedia` (`{url, type}` with type
                LINKEDIN|TWITTER|FACEBOOK|INSTAGRAM), `education`,
                `workExperiences`, `certifications`, `languages`, `skills`
                (`[{id}]`), `compensation`, `status` (actively_looking |
                approachable_but_not_actively_looking | not_actively_looking |
                do_not_contact | do_not_poach), `customAttributes`,
                `mainContact` (`{userId}` — the owning recruiter).
        """
        return _call("create_candidate", candidate)

    @mcp.tool()
    def spott_update_candidate(candidate_id: str, patch: dict) -> dict:
        """Update a candidate — partial: only the fields present in `patch` change.

        Same field names as `spott_create_candidate`.
        """
        return _call("update_candidate", candidate_id, patch)

    # --- Jobs ---------------------------------------------------------------

    @mcp.tool()
    def spott_jobs(
        limit: int = 25,
        cursor: Optional[str] = None,
        company_ids: Optional[list[str]] = None,
        candidate_emails: Optional[list[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        include: Optional[list[str]] = None,
    ) -> dict:
        """List jobs (open roles; `vacancies` in Spott's API paths).

        Args:
            limit: page size, max 50.
            cursor: `cursor` returned by the previous call.
            company_ids: restrict to jobs of these client companies.
            candidate_emails: jobs these candidates applied to (max 25 emails).
            modified_since / modified_until: ISO-8601 bounds.
            include: extra relations — only `jobBoards` is supported.
        """
        return _call("list_jobs", limit=limit, cursor=cursor,
                     company_ids=company_ids, candidate_emails=candidate_emails,
                     modified_since=modified_since, modified_until=modified_until,
                     include=include)

    @mcp.tool()
    def spott_job(job_id: str) -> dict:
        """Fetch one job: details, custom attributes, metadata."""
        return _call("get_job", job_id)

    @mcp.tool()
    def spott_search_jobs(
        filters: Optional[list[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> dict:
        """Search jobs with structured filters (page pagination).

        Args:
            filters: list of `{type, operator, path, value}`. Native paths:
                `vacancy.name` / `vacancy.client.company.name` (`text`),
                `vacancy.client.company` / `vacancy.team` / `vacancy.stage`
                (`entitySelect`, in|notIn), `vacancy.stage.isOpen` (`boolean`) —
                that last one is how you get "currently open roles".
            page: 0-based page index.
            page_size: results per page.
        """
        return _call("search_jobs", filters=filters, page=page, page_size=page_size)

    # --- Candidatures -------------------------------------------------------

    @mcp.tool()
    def spott_applications(
        job_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
        candidate_emails: Optional[list[str]] = None,
        is_inbound: Optional[bool] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        include: Optional[list[str]] = None,
    ) -> dict:
        """List applications — the pipeline of who is on what.

        Pass `job_id` for one job's pipeline, or `candidate_id` for one
        candidate's applications (both job-linked and speculative ones, most
        recent activity first); those two are exclusive and return the full set
        (no cursor). With neither, lists everything with cursor pagination.

        Args:
            limit / cursor: pagination of the unfiltered listing (limit max 50).
            candidate_emails: restrict to these candidates (max 25 emails).
            is_inbound: true = inbound applications only.
            modified_since / modified_until: ISO-8601 bounds.
            include: `lastActivity`, `candidate.latestWorkExperience`,
                `candidate.locations`, `candidate.emailAddresses`,
                `candidate.phoneNumbers`, `vacancy.clientContactTeam`,
                `vacancy.jobBoards`.
        """
        if job_id and candidate_id:
            raise _bad("spott_applications: passe job_id OU candidate_id, pas les deux.")
        if candidate_id:
            return _call("applications_by_candidate", candidate_id)
        if job_id:
            return _call("applications_by_job", job_id)
        return _call("list_applications", limit=limit, cursor=cursor,
                     candidate_emails=candidate_emails, is_inbound=is_inbound,
                     modified_since=modified_since, modified_until=modified_until,
                     include=include)

    @mcp.tool()
    def spott_create_application(
        candidate_id: str,
        stage_id: str,
        job_id: Optional[str] = None,
        status_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> dict:
        """Put a candidate on a job — or on a client as a speculative application.

        Args:
            stage_id: starting pipeline stage — get ids from
                `spott_stages("applications")`.
            job_id: the job applied to. Omit it and pass `client_id` for a
                speculative application.
            status_id: status within the stage (optional).
            client_id: client company, for speculative applications.
        """
        return _call("create_application", candidate_id, stage_id, job_id=job_id,
                     status_id=status_id, client_id=client_id)

    @mcp.tool()
    def spott_move_application(
        application_id: str, stage_id: str, status_id: Optional[str] = None,
    ) -> dict:
        """Move an application to another stage of its job's pipeline.

        Args:
            stage_id: target stage — from `spott_stages("applications")`.
            status_id: status within the target stage (optional).
        """
        return _call("move_application", application_id, stage_id,
                     status_id=status_id)

    @mcp.tool()
    def spott_stages(
        entity: str = "applications", template_id: Optional[str] = None,
    ) -> dict:
        """List the ordered pipeline stages (with their ids and labels).

        Read this BEFORE creating or moving an application — stage ids are what
        the write tools expect.

        Args:
            entity: applications | vacancies | clients | opportunities.
            template_id: stages of one pipeline template (applications only).
        """
        return _call("pipeline_stages", entity, template_id=template_id)

    # --- Notes --------------------------------------------------------------

    @mcp.tool()
    def spott_notes(
        limit: int = 25,
        cursor: Optional[str] = None,
        candidate_id: Optional[str] = None,
        client_contact_id: Optional[str] = None,
        source: Optional[str] = None,
        label_ids: Optional[list[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
    ) -> dict:
        """List notes (cursor pagination), optionally about one candidate or
        client contact.

        Args:
            limit: page size, max 50.
            source: phone | phoneInbound | phoneOutbound | inPerson |
                onlineMeeting | callAttempted.
            label_ids: restrict to these note labels.
        """
        return _call("list_notes", limit=limit, cursor=cursor,
                     candidate_id=candidate_id, client_contact_id=client_contact_id,
                     source=source, label_ids=label_ids,
                     modified_since=modified_since, modified_until=modified_until)

    @mcp.tool()
    def spott_create_note(
        content: str,
        title: Optional[str] = None,
        links: Optional[list[dict]] = None,
        source: Optional[str] = None,
        label_ids: Optional[list[str]] = None,
    ) -> dict:
        """Write a note, optionally attached to records.

        Args:
            content: the note body.
            links: `[{"entityType": …, "entityId": …}]` — entityType among
                candidate, vacancy, client, application, clientContact,
                interview, opportunity. A call report on a candidate is
                `[{"entityType": "candidate", "entityId": "<id>"}]`.
            source: how the exchange happened — phone | phoneInbound |
                phoneOutbound | inPerson | onlineMeeting | callAttempted.
            label_ids: note labels to apply.
        """
        return _call("create_note", content, title=title, links=links,
                     source=source, label_ids=label_ids)

    # --- Clients (côté CRM du cabinet) --------------------------------------

    @mcp.tool()
    def spott_clients(
        limit: int = 25,
        cursor: Optional[str] = None,
        list_ids: Optional[list[str]] = None,
        filters: Optional[list[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> dict:
        """List client companies — or search them when `filters` is given.

        Args:
            limit / cursor / list_ids: plain listing (cursor pagination, limit
                max 50, up to 25 list ids).
            filters: structured filters — switches to search (page pagination).
                Native paths: `client.company.name` / `.domain` / `.description`
                (`text`), `client.stage` / `client.contacts` (`entitySelect`).
            page / page_size: pagination of the search variant.
        """
        if filters:
            return _call("search_clients", filters=filters, page=page,
                         page_size=page_size)
        return _call("list_clients", limit=limit, cursor=cursor, list_ids=list_ids)

    @mcp.tool()
    def spott_client(client_id: str) -> dict:
        """Fetch one client company: details, contacts, industry, size, funding,
        locations, hierarchies, custom attributes."""
        return _call("get_client", client_id)

    @mcp.tool()
    def spott_client_contacts(
        limit: int = 25,
        cursor: Optional[str] = None,
        client_ids: Optional[list[str]] = None,
        list_ids: Optional[list[str]] = None,
    ) -> dict:
        """List client contacts (the people you deal with at client companies).

        Args:
            limit: page size, max 50.
            client_ids: restrict to these client companies (max 25).
            list_ids: restrict to these Spott lists (max 25).
        """
        return _call("list_client_contacts", limit=limit, cursor=cursor,
                     client_ids=client_ids, list_ids=list_ids)

    @mcp.tool()
    def spott_placements(
        page: int = 0,
        page_size: int = 20,
        company_id: Optional[str] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
    ) -> dict:
        """List placements — closed deals, with candidate, company, job and fees.

        Args:
            page: 0-based page index (this endpoint pages, it has no cursor).
            page_size: results per page, max 100.
            company_id: restrict to one client company.
        """
        return _call("list_placements", page=page, page_size=page_size,
                     company_id=company_id, modified_since=modified_since,
                     modified_until=modified_until)

    # --- Transverse ---------------------------------------------------------

    @mcp.tool()
    def spott_people(query: str, limit: int = 25) -> dict:
        """Find a person across candidates AND client contacts, by full name,
        email or phone number. Fuzzy — tolerates partial input and typos.

        This is the "do we already know this person?" tool: run it before
        creating a candidate.

        Args:
            limit: max results, up to 100.
        """
        return _call("search_people", query, limit=limit)

    @mcp.tool()
    def spott_users(include_deactivated: bool = False) -> dict:
        """List Spott users (the recruiters). Their ids are what `mainContact` /
        `owner` fields expect. Active users only unless `include_deactivated`."""
        return _call("list_users", include_deactivated=include_deactivated)
