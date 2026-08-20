"""Apollo.io — B2B prospection (organizations, people, job postings, sequences,
one-off emails, conversations).

Wrappe `oto.tools.apollo.ApolloClient`. Deux régimes de clé selon ce qu'un
endpoint interroge, PAS selon lecture/écriture :

- **Base partagée Apollo** (`mixed_companies/search`, `mixed_people/api_search`,
  `people/match`, `organizations/*`) : `access.resolve_api_key("apollo")` — user
  key (`/account`) prioritaire, sinon clé plateforme (free-tier, quota daily =
  `default_quota` par user/jour). N'importe quelle clé rend la MÊME base (~28M
  entreprises) → une clé plateforme mutualisée y est sans risque. Le quota
  plateforme métré = les **crédits Apollo** (`people/match`, qui révèle un
  contact) ; recherche org/people et job postings ne consomment pas de crédit →
  non métrés.
- **Espace de travail DU PROPRIÉTAIRE de la clé** (séquences, emails, boîtes
  connectées, conversations — TOUT ce qui a été ajouté dans ce module) :
  `access.resolve_credential("apollo", want="byo")`, JAMAIS `resolve_api_key`.
  Ce n'est pas une distinction lecture/écriture — `apollo_email(op="search")` en
  lecture rend `body_html`/`body_text` des emails ENVOYÉS PAR le propriétaire de
  la clé ; `apollo_email_accounts` rend SES boîtes (signature HTML, score de
  délivrabilité). Une clé plateforme mutualisée y exposerait les données privées
  de son propriétaire à n'importe quel autre user d'oto. Et pour l'écriture
  spécifiquement (enrôler des contacts, envoyer un email) : un envoi sur cette
  clé partirait en plus depuis SA boîte, vers SES contacts — même verrou que
  Lightfield `send_email` (oto-core 97c53ce, autorisé par le mainteneur le
  19/08/2026 à deux conditions : le connecteur n'existe que si une org pose SA
  clé, et l'envoi part d'une boîte que le propriétaire de cette clé a lui-même
  connectée — condition #2 portée ici par le verrou local
  `send_email_from_email_account_id` côté client oto-core). Les conversations
  (transcripts d'appels/visios réels) sont byo-only pour la même raison
  d'espace privé, plus un coût crédit conditionnel (1 si insights IA, 0 sinon)
  pas métrable a priori côté quota plateforme.

⚠️ Doc Apollo (pas vérifié depuis cet environnement, pas de clé disponible ici) :
`add_contact_ids` et `/emailer_messages/{id}/activities` (stats email) exigent
une clé « Master » et 403 sinon — à confirmer côté Julien avec une clé réelle.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def register(mcp: FastMCP) -> None:
    from oto.tools.apollo.client import ApolloClient

    def _client() -> tuple[ApolloClient, bool]:
        key, is_platform = access.resolve_api_key("apollo")
        return ApolloClient(api_key=key), is_platform

    def _client_byo() -> ApolloClient:
        """Client résolu SANS palier plateforme — pour tout appel qui écrit
        (enrôlement, envoi) ou lit des données sensibles (conversations).

        Apollo est le premier connecteur à mélanger les deux régimes dans le
        MÊME module (recherche/enrichissement = platform_key_open, tout le
        reste = byo-only) : le message générique de `resolve_credential`
        (« Aucun credential configuré pour toi ») serait trompeur pour un
        user qui voit déjà apollo_search_organizations fonctionner via la clé
        plateforme et ne comprendrait pas pourquoi CET appel-ci le refuse."""
        try:
            return ApolloClient(api_key=access.resolve_credential("apollo", want="byo").key)
        except McpError as e:
            msg = e.error.message or ""
            if "Aucun credential" in msg:
                # Seul CE message générique (absence totale de credential BYO) est
                # ambigu ici — les autres (multi-compte, compte introuvable) sont
                # déjà précis et n'ont rien à voir avec platform vs byo.
                raise McpError(ErrorData(
                    code=INVALID_PARAMS,
                    message=(
                        f"{msg} — ceci ne concerne QUE les séquences/emails/"
                        "conversations (tes propres données) : la recherche et "
                        "l'enrichissement Apollo restent utilisables sans ta propre clé.")))
            raise

    @mcp.tool()
    def apollo_search_organizations(
        name: Optional[str] = None,
        domain: Optional[str] = None,
        country: Optional[str] = None,
        employee_ranges: Optional[list[str]] = None,
        revenue_min: Optional[int] = None,
        revenue_max: Optional[int] = None,
        locations: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        technologies: Optional[list[str]] = None,
        org_ids: Optional[list[str]] = None,
        per_page: int = 10,
        page: int = 1,
    ) -> dict:
        """Find companies by firmographics — the CHEAP way to qualify a list.

        Costs 1 Apollo credit per PAGE (up to 100 results), where enrichment costs
        1 credit per COMPANY: filter here, enrich only what you keep.

        ⚠️ Results carry revenue and headcount GROWTH, but NOT the headcount itself
        — that's why you filter by `employee_ranges` instead of reading a number.
        For the exact headcount and its per-department split, enrich (see
        apollo_enrich_organization / apollo_bulk_enrich_organizations).

        Args:
            name: company name.
            domain: company domain.
            country: HQ country (shorthand for `locations`).
            employee_ranges: headcount brackets "min,max", e.g. ["11,50", "51,200"].
            revenue_min / revenue_max: annual revenue bounds.
            locations: HQ cities/regions/countries.
            keywords: activity keywords.
            technologies: technology uids in use, e.g. ["salesforce"].
            org_ids: Apollo organization ids.
            per_page: results per page (≤100). page: page number.
        """
        client, _ = _client()
        return client.search_organizations(
            name=name, domain=domain, country=country, per_page=per_page, page=page,
            employee_ranges=employee_ranges, revenue_min=revenue_min,
            revenue_max=revenue_max, locations=locations, keywords=keywords,
            technologies=technologies, org_ids=org_ids)

    @mcp.tool()
    def apollo_enrich_organization(domain: str) -> dict:
        """Enrich a company from its domain (firmographics, size, industry…).

        Returns the exact `estimated_num_employees`, its per-department split
        (`departmental_head_count`), 6/12/24-month headcount growth, revenue,
        founding year and tech stack. Costs 1 Apollo credit. For several companies
        at once, prefer apollo_bulk_enrich_organizations (same cost, 10× fewer calls).
        """
        client, _ = _client()
        return client.enrich_organization(domain)

    @mcp.tool()
    def apollo_bulk_enrich_organizations(domains: list[str]) -> dict:
        """Enrich UP TO 10 companies in a single call — same fields as
        apollo_enrich_organization (headcount, per-department split, growth, revenue).

        Costs 1 Apollo credit per company (a batch saves CALLS, not credits: the
        enrich rate limit is 600/h, so batching divides your call budget by 10).
        Over 10 domains, split into batches yourself — the API refuses more.
        """
        client, _ = _client()
        return client.bulk_enrich_organizations(domains)

    @mcp.tool()
    def apollo_search_people(
        domains: Optional[list[str]] = None,
        org_ids: Optional[list[str]] = None,
        titles: Optional[list[str]] = None,
        seniorities: Optional[list[str]] = None,
        person_locations: Optional[list[str]] = None,
        organization_locations: Optional[list[str]] = None,
        per_page: int = 25,
        page: int = 1,
    ) -> dict:
        """Search people by company domains/ids, titles, seniorities, location (net-new).

        Returns identities WITHOUT email/phone — reveal a contact with
        apollo_match_person (which costs an Apollo credit).

        ⚠️ LAST NAMES COME BACK OBFUSCATED here ("Vi***l"). To reveal someone you
        found, pass the `id` of the result as `person_id` to apollo_match_person —
        NEVER first name + company, which matches nobody: Apollo then mints an empty
        record and charges the credit anyway.

        ⚠️ A DOMAIN IS WORLDWIDE. On a subsidiary of an international group, the
        domain is shared across every country: franke.com returns 1887 profiles,
        verifone.com 3282, sonova.com 3147 — targeting the French entity by domain
        alone means revealing at random, one credit each, mostly on the wrong
        country. Add `person_locations=["France"]`. Same for the reverse case: a
        French head office with expatriates is `organization_locations`.

        Args:
            domains: company domains, e.g. ["acme.com"].
            org_ids: Apollo organization ids (from apollo_enrich_organization).
            titles: job-title keywords, e.g. ["directeur financier", "CFO"].
            seniorities: e.g. ["c_suite", "founder", "owner", "director", "manager"].
            person_locations: where the PERSON is — country, region or city as
                Apollo spells it, e.g. ["France"], ["Paris, France"]. THE filter
                for a national subsidiary of a global domain.
            organization_locations: where their EMPLOYER's site is (≠ the person's
                own location: a French-based employee of a German site matches
                person_locations=["France"], not organization_locations).
        """
        client, _ = _client()
        return client.search_people(
            domains=domains, org_ids=org_ids,
            titles=titles, seniorities=seniorities,
            person_locations=person_locations,
            organization_locations=organization_locations,
            per_page=per_page, page=page)

    @mcp.tool()
    def apollo_match_person(
        person_id: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        org_name: Optional[str] = None,
    ) -> dict:
        """Match a single person (enrichment). Returns {} if no match.

        Pass the strongest identifier you have. Coming from apollo_search_people, that
        is `person_id` = the `id` of the search result — search obfuscates last names,
        so the id is the ONLY reliable handle on someone you just found. Otherwise:
        email or linkedin_url, or a FULL name (first + last) with the company.

        ⚠️ Costs 1 Apollo credit per call, charged even when nothing matches: a weak
        identifier (first name + company) makes Apollo mint an EMPTY record rather than
        return nothing. Such an answer carries `person._stub: true` — treat it as a
        failure, not as data. Calls with no usable identifier are refused before the
        credit is spent.
        """
        client, is_platform = _client()
        try:
            result = client.match_person(
                person_id=person_id, linkedin_url=linkedin_url, email=email,
                first_name=first_name, last_name=last_name, name=name,
                domain=domain, org_name=org_name) or {}
        except ValueError as e:
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        if is_platform:
            access.record_platform_usage("apollo")
        return result

    @mcp.tool()
    def apollo_job_postings(org_id: str) -> dict:
        """List active job postings for an Apollo organization id (hiring signal)."""
        client, _ = _client()
        return client.get_job_postings(org_id)

    # ------------------------------------------------------------------
    # Email accounts & schedules — prérequis en lecture, 0 crédit, mais BYO
    # ONLY : la liste rend les boîtes/plannings DU PROPRIÉTAIRE de la clé
    # (signature HTML, score de délivrabilité, seuils quotidiens...) — la clé
    # plateforme appartient à quelqu'un d'autre, sa boîte n'a rien à faire là.
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_email_accounts() -> dict:
        """List the mailboxes connected to THIS Apollo account (BYO key only —
        this is the key owner's own inbox configuration, never the platform key's).

        Get the `id` to pass as `send_email_from_email_account_id` to
        apollo_sequence_contacts(op="add").
        """
        client = _client_byo()
        return client.list_email_accounts()

    @mcp.tool()
    def apollo_email_schedules() -> dict:
        """List the send schedules configured on THIS Apollo team (BYO key only).

        Get the `id` to pass as `emailer_schedule_id` to
        apollo_sequence(op="create") — required, creation fails without it.
        """
        client = _client_byo()
        return client.list_email_schedules()

    # ------------------------------------------------------------------
    # Sequences
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_sequence(
        op: Literal["search", "create", "update", "activate", "deactivate", "archive"],
        sequence_id: Optional[str] = None,
        name: Optional[str] = None,
        emailer_schedule_id: Optional[str] = None,
        active: Optional[bool] = None,
        label_names: Optional[list[str]] = None,
        folder_id: Optional[str] = None,
        max_emails_per_day: Optional[int] = None,
        cc_emails: Optional[str] = None,
        bcc_emails: Optional[str] = None,
        emailer_steps: Optional[list[dict]] = None,
        per_page: int = 25,
        page: int = 1,
        dry_run: bool = False,
    ) -> dict:
        """Manage Apollo sequences (search/create/update/activate/deactivate/archive).

        Args by op:
        - `search`: `name` (partial match), `per_page`, `page`. BYO key only — this
          lists the key owner's OWN sequences (names + open/reply rates).
        - `create`: `name` + `emailer_schedule_id` REQUIRED (get one from
          apollo_email_schedules — the API refuses creation without it). Optional
          `active` (default False — prefer op="activate" once steps/templates are
          reviewed), `label_names`, `folder_id`, `max_emails_per_day`, `emailer_steps`
          (nested step/template definitions — see Apollo docs). BYO key only.
        - `update`: `sequence_id` + any of `name`, `active`, `emailer_schedule_id`,
          `label_names`, `max_emails_per_day`, `cc_emails`, `bcc_emails`,
          `emailer_steps` (include `id` per step to MODIFY it, omit to CREATE one).
          BYO key only.
        - `activate` / `deactivate`: `sequence_id`. BYO key only.
        - `archive`: `sequence_id`. BYO key only. Supports `dry_run=True` (no
          get-by-id exists on this endpoint — the preview cannot show a real diff,
          just echoes the intended action).

        `dry_run=True` on `create`/`update`/`activate`/`deactivate`/`archive`
        validates but skips the actual API call.
        """
        if op not in ("search", "create", "update", "activate", "deactivate", "archive"):
            raise _bad(f'op inconnu "{op}" — attendu: search, create, update, activate, '
                       'deactivate, archive')

        client = _client_byo()
        if op == "search":
            return client.search_sequences(name=name, per_page=per_page, page=page)

        try:
            if op == "create":
                if not name or not emailer_schedule_id:
                    raise ValueError("name et emailer_schedule_id requis pour créer une séquence")
                if dry_run:
                    return {"dry_run": True, "action": "create", "name": name,
                            "emailer_schedule_id": emailer_schedule_id,
                            "active": bool(active), "label_names": label_names or []}
                return client.create_sequence(
                    name=name, emailer_schedule_id=emailer_schedule_id,
                    active=bool(active), label_names=label_names, folder_id=folder_id,
                    max_emails_per_day=max_emails_per_day, emailer_steps=emailer_steps)

            if not sequence_id:
                raise ValueError("sequence_id requis")

            if op == "update":
                fields: dict[str, Any] = {}
                if name is not None:
                    fields["name"] = name
                if active is not None:
                    fields["active"] = active
                if emailer_schedule_id is not None:
                    fields["emailer_schedule_id"] = emailer_schedule_id
                if label_names is not None:
                    fields["label_names"] = label_names
                if max_emails_per_day is not None:
                    fields["max_emails_per_day"] = max_emails_per_day
                if cc_emails is not None:
                    fields["cc_emails"] = cc_emails
                if bcc_emails is not None:
                    fields["bcc_emails"] = bcc_emails
                if emailer_steps is not None:
                    fields["emailer_steps"] = emailer_steps
                if dry_run:
                    return {"dry_run": True, "action": "update", "sequence_id": sequence_id,
                            "changes": fields, "current_available": False}
                return client.update_sequence(sequence_id, **fields)

            if op == "activate":
                if dry_run:
                    return {"dry_run": True, "action": "activate", "sequence_id": sequence_id}
                return client.activate_sequence(sequence_id)

            if op == "deactivate":
                if dry_run:
                    return {"dry_run": True, "action": "deactivate", "sequence_id": sequence_id}
                return client.deactivate_sequence(sequence_id)

            if op == "archive":
                if dry_run:
                    return {"dry_run": True, "action": "archive", "sequence_id": sequence_id,
                            "current_available": False}
                return client.archive_sequence(sequence_id)
        except ValueError as e:
            raise _bad(str(e))

    @mcp.tool()
    def apollo_sequence_contacts(
        op: Literal["add", "update_status", "activity"],
        sequence_id: Optional[str] = None,
        contact_ids: Optional[list[str]] = None,
        label_names: Optional[list[str]] = None,
        send_email_from_email_account_id: Optional[str] = None,
        send_email_from_email_address: Optional[str] = None,
        status: Optional[str] = None,
        emailer_campaign_ids: Optional[list[str]] = None,
        mode: Optional[Literal["mark_as_finished", "remove", "stop"]] = None,
        contact_id: Optional[str] = None,
        per_page: int = 50,
        dry_run: bool = False,
    ) -> dict:
        """Enroll/update/inspect contacts in Apollo sequences. BYO key only for
        `add`/`update_status` (starts or stops an automated campaign to real
        people); `activity` (read) accepts the platform key.

        Args by op:
        - `add`: `sequence_id` + `send_email_from_email_account_id` (id of a
          CONNECTED mailbox — get one from apollo_email_accounts; REQUIRED, refused
          locally without it) + `contact_ids` or `label_names` (at least one). The
          HIGHEST-RISK call here: it starts a multi-step automated campaign to real
          people, not a single send. Supports `dry_run=True` (echoes the request,
          no get-by-id available to show a real diff).
        - `update_status`: `emailer_campaign_ids` + `contact_ids` + `mode`
          (`mark_as_finished`, `remove`, or `stop`). Supports `dry_run=True`.
        - `activity`: `contact_id` (required) + optional `sequence_id` filter +
          `per_page` (1-50, most recent events, not pagination). 0 credit, but BYO
          key only — the events are the key owner's OWN sequences.
        """
        if op not in ("add", "update_status", "activity"):
            raise _bad(f'op inconnu "{op}" — attendu: add, update_status, activity')

        if op == "activity":
            if not contact_id:
                raise _bad("contact_id requis pour op=activity")
            client = _client_byo()
            try:
                return client.get_contact_sequence_activity(
                    contact_id, sequence_id=sequence_id, per_page=per_page)
            except ValueError as e:
                raise _bad(str(e))

        client = _client_byo()
        try:
            if op == "add":
                if not sequence_id:
                    raise ValueError("sequence_id requis")
                if not send_email_from_email_account_id:
                    raise ValueError(
                        "send_email_from_email_account_id requis — obtiens-le via "
                        "apollo_email_accounts()")
                if not contact_ids and not label_names:
                    raise ValueError("contact_ids ou label_names requis")
                if dry_run:
                    return {"dry_run": True, "action": "add", "sequence_id": sequence_id,
                            "send_email_from_email_account_id": send_email_from_email_account_id,
                            "contact_ids": contact_ids or [], "label_names": label_names or []}
                return client.add_contacts_to_sequence(
                    sequence_id, send_email_from_email_account_id,
                    contact_ids=contact_ids, label_names=label_names,
                    send_email_from_email_address=send_email_from_email_address,
                    status=status)

            if op == "update_status":
                if not emailer_campaign_ids:
                    raise ValueError("emailer_campaign_ids requis")
                if not contact_ids:
                    raise ValueError("contact_ids requis")
                if mode not in ("mark_as_finished", "remove", "stop"):
                    raise ValueError('mode doit être "mark_as_finished", "remove" ou "stop"')
                if dry_run:
                    return {"dry_run": True, "action": "update_status", "mode": mode,
                            "emailer_campaign_ids": emailer_campaign_ids,
                            "contact_ids": contact_ids}
                return client.update_sequence_contact_status(
                    emailer_campaign_ids, contact_ids, mode)
        except ValueError as e:
            raise _bad(str(e))

    # ------------------------------------------------------------------
    # One-off emails
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_email(
        op: Literal["draft", "send", "status", "search", "content", "stats"],
        contact_id: Optional[str] = None,
        subject: Optional[str] = None,
        body_html: Optional[str] = None,
        recipients: Optional[list[dict]] = None,
        in_response_to_emailer_message_id: Optional[str] = None,
        emailer_template_id: Optional[str] = None,
        attachment_ids: Optional[list[str]] = None,
        enable_tracking: Optional[bool] = None,
        outreach_task_id: Optional[str] = None,
        message_id: Optional[str] = None,
        surface: Optional[str] = None,
        stats: Optional[list[str]] = None,
        reply_classes: Optional[list[str]] = None,
        sequence_ids: Optional[list[str]] = None,
        exclude_sequence_ids: Optional[list[str]] = None,
        keywords: Optional[str] = None,
        date_range_mode: Optional[str] = None,
        date_min: Optional[str] = None,
        date_max: Optional[str] = None,
        per_page: int = 25,
        page: int = 1,
        ids: Optional[list[str]] = None,
        body_format: str = "plain",
        dry_run: bool = False,
    ) -> dict:
        """One-off emails (outside sequences). `draft` and `send` are ALWAYS two
        distinct steps — never collapsed — so nothing confuses "prepare" and "send".
        BYO key only on EVERY op: `search`/`content`/`stats` read the key owner's OWN
        sent mailbox (bodies included), not Apollo's shared database — unlike
        organization/people search, a platform key here would leak someone else's inbox.

        Args by op:
        - `draft`: `contact_id` required UNLESS `in_response_to_emailer_message_id`
          is set (thread reply). Optional `subject`, `body_html`, `recipients`
          (`[{"email":, "contact_id":, "recipient_type_cd": "to"|"cc"|"bcc"}]`),
          `emailer_template_id`, `attachment_ids`, `enable_tracking`,
          `outreach_task_id`. Does NOT send.
        - `send`: `message_id` (from `draft`'s response) + optional `surface`.
          IRREVERSIBLE — the only gesture here that reaches a real person by direct
          email. Supports `dry_run=True` (validates message_id is set, does not call).
        - `status`: `message_id` — poll send status.
        - `search`: `stats`, `reply_classes`, `sequence_ids`, `exclude_sequence_ids`,
          `keywords`, `date_range_mode` (`due_at`/`completed_at`), `date_min`/`date_max`
          (`YYYY-MM-DD`), `per_page`, `page`.
        - `content`: `ids` (up to 10 SENT email ids) + `body_format` (`plain`/`html`).
        - `stats`: `message_id` — opens/clicks. ⚠️ Apollo doc says this needs a
          Master API key, not verified from this environment.
        """
        if op not in ("draft", "send", "status", "search", "content", "stats"):
            raise _bad(f'op inconnu "{op}" — attendu: draft, send, status, search, '
                       'content, stats')

        client = _client_byo()
        try:
            if op == "draft":
                if not contact_id and not in_response_to_emailer_message_id:
                    raise ValueError(
                        "contact_id requis, sauf en réponse à un fil "
                        "(in_response_to_emailer_message_id)")
                return client.create_email_draft(
                    contact_id=contact_id, subject=subject, body_html=body_html,
                    recipients=recipients,
                    in_response_to_emailer_message_id=in_response_to_emailer_message_id,
                    emailer_template_id=emailer_template_id,
                    attachment_ids=attachment_ids, enable_tracking=enable_tracking,
                    outreach_task_id=outreach_task_id)

            if op == "send":
                if not message_id:
                    raise ValueError("message_id requis")
                if dry_run:
                    return {"dry_run": True, "action": "send", "message_id": message_id}
                return client.send_email_now(message_id, surface=surface)

            if op == "status":
                if not message_id:
                    raise ValueError("message_id requis")
                return client.check_email_send_status(message_id)
            if op == "search":
                return client.search_emails(
                    stats=stats, reply_classes=reply_classes, sequence_ids=sequence_ids,
                    exclude_sequence_ids=exclude_sequence_ids, keywords=keywords,
                    date_range_mode=date_range_mode, date_min=date_min, date_max=date_max,
                    per_page=per_page, page=page)
            if op == "content":
                if not ids:
                    raise ValueError("ids requis (au moins un)")
                return client.get_email_content(ids, body_format=body_format)
            if op == "stats":
                if not message_id:
                    raise ValueError("message_id requis")
                return client.get_email_stats(message_id)
        except ValueError as e:
            raise _bad(str(e))

    # ------------------------------------------------------------------
    # Conversations — byo-only sur TOUS les ops : transcripts d'appels/visios
    # réels + coût crédit conditionnel (imprévisible, pas métrable a priori).
    # ------------------------------------------------------------------

    @mcp.tool()
    def apollo_conversation(
        op: Literal["search", "get", "export", "export_status"],
        conversation_id: Optional[str] = None,
        conversation_type: Optional[str] = None,
        account_id: Optional[str] = None,
        contact_ids: Optional[list[str]] = None,
        tag_ids: Optional[list[str]] = None,
        tracker_ids: Optional[list[str]] = None,
        organization_ids: Optional[list[str]] = None,
        date_range: Optional[dict] = None,
        per_page: int = 25,
        page: int = 1,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        email: Optional[str] = None,
        export_id: Optional[str] = None,
    ) -> dict:
        """Recorded conversations (calls/video meetings) — transcripts, recordings,
        participants. BYO key only (all ops): real recorded conversations, and the
        credit cost is conditional (1 if the conversation has AI insights, 0
        otherwise) so it can't be metered against a platform quota up front.

        Args by op:
        - `search`: `conversation_type` (`video_conference`/`phone_call`),
          `account_id`, `contact_ids`, `tag_ids`, `tracker_ids`, `organization_ids`,
          `date_range` (`{"start":, "end":}` ISO 8601), `per_page`, `page`.
        - `get`: `conversation_id`. ⚠️ 1 Apollo credit IF the conversation has AI
          insights, 0 otherwise — not knowable before the call.
        - `export`: `start_time` + `end_time` (ISO 8601, GMT) + `email` (team member
          to notify). ASYNC — returns `export_id`; does not wait for completion.
          Poll with `export_status`.
        - `export_status`: `export_id` (from `export`) — returns `redirect_url`
          once ready.
        """
        if op not in ("search", "get", "export", "export_status"):
            raise _bad(f'op inconnu "{op}" — attendu: search, get, export, export_status')

        client = _client_byo()
        try:
            if op == "search":
                return client.search_conversations(
                    conversation_type=conversation_type, account_id=account_id,
                    contact_ids=contact_ids, tag_ids=tag_ids, tracker_ids=tracker_ids,
                    organization_ids=organization_ids, date_range=date_range,
                    per_page=per_page, page=page)
            if op == "get":
                if not conversation_id:
                    raise ValueError("conversation_id requis")
                return client.get_conversation(conversation_id)
            if op == "export":
                if not start_time or not end_time or not email:
                    raise ValueError("start_time, end_time et email requis")
                return client.export_conversations(start_time, end_time, email)
            if op == "export_status":
                if not export_id:
                    raise ValueError("export_id requis")
                return client.get_conversations_export(export_id)
        except ValueError as e:
            raise _bad(str(e))
