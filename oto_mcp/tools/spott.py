"""Spott — ATS/CRM des cabinets de recrutement (candidats, jobs, candidatures).

Wrappe `oto.tools.spott.client.SpottClient`. keyed `api_key` (header x-api-key),
byo-only (pas de clé plateforme) : chaque user/org connecte SON compte Spott.

Vocabulaire tenu de bout en bout : un **job** (l'API dit `vacancy` dans ses
chemins), un **candidate**, une **application** (le candidat sur un job, ou
spontanée vers un client), un **client** = l'entreprise cliente du cabinet avec
ses **client contacts**. Deux paginations cohabitent — la recherche (`op="search"`)
pagine par `page`, les listes par `cursor`.

**Surface consolidée (ADR 0047 §Amendement, appliqué au connecteur spott)** : un
tool par OBJET métier, le verbe en paramètre `op` — `spott_candidate` (list/get/
search/create/update), `spott_job` (list/get/search), `spott_application`
(list/create/move), `spott_note` (list/create), `spott_client` (list/get/search/
contacts). Ces cinq objets partagent le même socle de paramètres (limit/cursor/
modified_since/modified_until + filters/page/page_size pour la recherche), d'où la
fusion.

Quatre tools restent SEULS — leurs paramètres ne recouvrent pas ceux de leurs
voisins, et un `oneOf` de variantes disjointes pèserait ce que pèsent les tools
séparés (critère = homogénéité des paramètres, pas le comptage) :
- `spott_stages` : le pipeline n'est pas une facette d'un objet, c'est le
  référentiel qui les traverse (`entity` = applications | vacancies | clients |
  opportunities) ; ses deux paramètres (`entity`, `template_id`) n'existent nulle
  part ailleurs, et ses ids alimentent les écritures de `spott_application` ;
- `spott_people` : recherche floue TRANSVERSE (candidats ∪ contacts clients) sur
  un `query` en texte libre — ni pagination, ni filtres structurés, aucune cible ;
- `spott_placements` : pagination par **page** sans curseur (l'endpoint n'en a
  pas), filtre `company_id` au singulier — le socle des listes ne s'y applique pas ;
- `spott_users` : découverte sans cible (un seul booléen), qui produit les ids que
  `mainContact`/`owner` consomment — même cas que `zoho_modules`.

⚠️ Ce module ÉCRIT dans l'ATS du cabinet : `spott_candidate` op="create"/"update",
`spott_application` op="create"/"move", `spott_note` op="create". Deux invariants
tenus ici : le défaut d'`op` est TOUJOURS une lecture (`list`) — un appel sans `op`
ne peut ni créer ni modifier ; et un argument obligatoire manquant lève une erreur
qui NOMME l'op et l'argument, jamais un fallback qui inventerait une donnée.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

# Ops par objet, dans l'ordre lectures → écritures. Source unique : la validation
# d'entrée ET le message de refus en dérivent, donc une op ajoutée ne peut pas être
# acceptée sans être annoncée (ni l'inverse).
_CANDIDATE_OPS = ("list", "get", "search", "create", "update")
_JOB_OPS = ("list", "get", "search")
_APPLICATION_OPS = ("list", "create", "move")
_NOTE_OPS = ("list", "create")
_CLIENT_OPS = ("list", "get", "search", "contacts")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _ops_error(ops: tuple[str, ...]) -> str:
    """Message de refus qui NOMME les ops valides (jamais un fallback muet)."""
    quoted = [f"'{o}'" for o in ops]
    return "op doit être " + ", ".join(quoted[:-1]) + " ou " + quoted[-1]


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback.

    Une valeur VIDE compte comme absente : `content=""` sur `op='create'` créerait
    une note vide dans l'ATS du client, et `candidate={}` un candidat fantôme —
    deux écritures réelles que personne n'a demandées.
    """
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _no_filters(op: str, filters) -> None:
    """`filters` n'a de sens que sur `op='search'` — l'ignorer en silence rendrait
    une liste complète que l'agent prendrait pour un résultat filtré."""
    if filters:
        raise _bad(f"op='{op}' n'accepte pas `filters` — utilise op='search' pour "
                   "chercher par filtres structurés.")


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
    def spott_candidate(
        op: Literal["list", "get", "search", "create", "update"] = "list",
        candidate_id: Optional[str] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        list_ids: Optional[list[str]] = None,
        include: Optional[list[str]] = None,
        filters: Optional[list[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
        candidate: Optional[dict] = None,
        patch: Optional[dict] = None,
    ) -> dict:
        """A candidate in Spott — list, read, search, create, update.

        `op`:
        - **"list"** (default): list candidates, newest page first (cursor
          pagination). Browsing/syncing tool. To FIND a specific person by
          name/email/phone, use `spott_people`; to filter on structured criteria,
          use op="search".
        - **"get"**: fetch one candidate (`candidate_id`): identity, contact
          details, linked client contacts.
        - **"search"**: search candidates with structured filters (page
          pagination).
        - **"create"** — ⚠️ WRITES: create a candidate (`candidate`).
        - **"update"** — ⚠️ WRITES: update a candidate (`candidate_id` + `patch`)
          — partial: only the fields present in `patch` change.

        Args:
            op: list (default) | get | search | create | update.
            candidate_id: op="get"/"update" — the candidate id.
            limit: op="list" — page size, max 50.
            cursor: op="list" — `cursor` returned by the previous call.
            modified_since / modified_until: op="list" — ISO-8601 bounds on last
                modification (incremental sync).
            list_ids: op="list" — restrict to these Spott lists (max 25).
            include: op="list" — extra relations; only `skills` is supported.
            filters: op="search" — list of `{type, operator, path, value}`. Native
                paths: `candidate.firstName` / `candidate.lastName` (type `text`,
                operators contains|equals|startsWith|notEquals),
                `candidate.mainContact` (`entitySelect`, in|notIn),
                `candidate.createdAt` (`date`, lessThanOrEqual|greaterThanOrEqual
                |equals|notEquals). Custom attributes use the `custom*` types
                (e.g. `{"type": "customText", …}`) with the attribute definition
                id as `path` — list definitions in the Spott UI.
            page: op="search" — 0-based page index.
            page_size: op="search" — results per page.
            candidate: op="create" — `firstName` and `lastName` are required.
                Optional: `emails` / `phoneNumbers` (each `{email|phoneNumber,
                purpose, isPrimary}`), `locations`, `socialMedia` (`{url, type}`
                with type LINKEDIN|TWITTER|FACEBOOK|INSTAGRAM), `education`,
                `workExperiences`, `certifications`, `languages`, `skills`
                (`[{id}]`), `compensation`, `status` (actively_looking |
                approachable_but_not_actively_looking | not_actively_looking |
                do_not_contact | do_not_poach), `customAttributes`,
                `mainContact` (`{userId}` — the owning recruiter).
            patch: op="update" — same field names as `candidate`, partial.
        """
        # Refus AVANT toute résolution de credential : une op inconnue n'atteint
        # jamais le client — donc jamais, par un chemin dérivé, une écriture.
        if op not in _CANDIDATE_OPS:
            raise _bad(_ops_error(_CANDIDATE_OPS))

        if op == "list":
            _no_filters(op, filters)
            return _call("list_candidates", limit=limit, cursor=cursor,
                         modified_since=modified_since,
                         modified_until=modified_until,
                         list_ids=list_ids, include=include)
        if op == "get":
            return _call("get_candidate", _need(candidate_id, "candidate_id", op))
        if op == "search":
            return _call("search_candidates", filters=filters, page=page,
                         page_size=page_size)
        if op == "create":
            return _call("create_candidate", _need(candidate, "candidate", op))
        if op == "update":
            return _call("update_candidate",
                         _need(candidate_id, "candidate_id", op),
                         _need(patch, "patch", op))
        # Structurellement inatteignable (garde d'entrée ci-dessus) — filet contre
        # un `return None` implicite si une op était ajoutée à `_CANDIDATE_OPS`
        # sans sa branche : mieux vaut refuser que rendre « rien » pour un succès.
        raise _bad(_ops_error(_CANDIDATE_OPS))

    # --- Jobs ---------------------------------------------------------------

    @mcp.tool()
    def spott_job(
        op: Literal["list", "get", "search"] = "list",
        job_id: Optional[str] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
        company_ids: Optional[list[str]] = None,
        candidate_emails: Optional[list[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        include: Optional[list[str]] = None,
        filters: Optional[list[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> dict:
        """A job in Spott — open roles (`vacancies` in Spott's API paths).

        `op`:
        - **"list"** (default): list jobs (cursor pagination).
        - **"get"**: fetch one job (`job_id`): details, custom attributes,
          metadata.
        - **"search"**: search jobs with structured filters (page pagination).

        Args:
            op: list (default) | get | search.
            job_id: op="get" — the job id.
            limit: op="list" — page size, max 50.
            cursor: op="list" — `cursor` returned by the previous call.
            company_ids: op="list" — restrict to jobs of these client companies.
            candidate_emails: op="list" — jobs these candidates applied to
                (max 25 emails).
            modified_since / modified_until: op="list" — ISO-8601 bounds.
            include: op="list" — extra relations; only `jobBoards` is supported.
            filters: op="search" — list of `{type, operator, path, value}`. Native
                paths: `vacancy.name` / `vacancy.client.company.name` (`text`),
                `vacancy.client.company` / `vacancy.team` / `vacancy.stage`
                (`entitySelect`, in|notIn), `vacancy.stage.isOpen` (`boolean`) —
                that last one is how you get "currently open roles".
            page: op="search" — 0-based page index.
            page_size: op="search" — results per page.
        """
        if op not in _JOB_OPS:
            raise _bad(_ops_error(_JOB_OPS))

        if op == "list":
            _no_filters(op, filters)
            return _call("list_jobs", limit=limit, cursor=cursor,
                         company_ids=company_ids,
                         candidate_emails=candidate_emails,
                         modified_since=modified_since,
                         modified_until=modified_until, include=include)
        if op == "get":
            return _call("get_job", _need(job_id, "job_id", op))
        if op == "search":
            return _call("search_jobs", filters=filters, page=page,
                         page_size=page_size)
        raise _bad(_ops_error(_JOB_OPS))

    # --- Candidatures -------------------------------------------------------

    @mcp.tool()
    def spott_application(
        op: Literal["list", "create", "move"] = "list",
        job_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        application_id: Optional[str] = None,
        stage_id: Optional[str] = None,
        status_id: Optional[str] = None,
        client_id: Optional[str] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
        candidate_emails: Optional[list[str]] = None,
        is_inbound: Optional[bool] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        include: Optional[list[str]] = None,
    ) -> dict:
        """An application in Spott — the pipeline of who is on what.

        `op`:
        - **"list"** (default): list applications. Pass `job_id` for one job's
          pipeline, or `candidate_id` for one candidate's applications (both
          job-linked and speculative ones, most recent activity first); those two
          are exclusive and return the full set (no cursor) — the listing
          arguments below (limit/cursor/candidate_emails/is_inbound/modified_*/
          include) then do NOT apply. With neither, lists everything with cursor
          pagination.
        - **"create"** — ⚠️ WRITES: put a candidate on a job (`candidate_id` +
          `stage_id` + `job_id`) — or on a client as a speculative application
          (omit `job_id`, pass `client_id`).
        - **"move"** — ⚠️ WRITES: move an application (`application_id`) to
          another stage of its job's pipeline (`stage_id`).

        Stage ids come from `spott_stages("applications")` — read it BEFORE
        creating or moving an application.

        Args:
            op: list (default) | create | move.
            job_id: op="list" — one job's pipeline (exclusive with
                `candidate_id`); op="create" — the job applied to (omit it and
                pass `client_id` for a speculative application).
            candidate_id: op="list" — one candidate's applications (exclusive
                with `job_id`); op="create" — the candidate who applies.
            application_id: op="move" — the application to move.
            stage_id: op="create" — starting pipeline stage; op="move" — target
                stage. From `spott_stages("applications")`.
            status_id: op="create"/"move" — status within the stage (optional).
            client_id: op="create" — client company, for speculative
                applications.
            limit / cursor: op="list" — pagination of the unfiltered listing
                (limit max 50).
            candidate_emails: op="list" — restrict to these candidates (max 25
                emails).
            is_inbound: op="list" — true = inbound applications only.
            modified_since / modified_until: op="list" — ISO-8601 bounds.
            include: op="list" — `lastActivity`,
                `candidate.latestWorkExperience`, `candidate.locations`,
                `candidate.emailAddresses`, `candidate.phoneNumbers`,
                `vacancy.clientContactTeam`, `vacancy.jobBoards`.
        """
        if op not in _APPLICATION_OPS:
            raise _bad(_ops_error(_APPLICATION_OPS))

        if op == "list":
            if job_id and candidate_id:
                raise _bad("spott_application op='list' : passe job_id OU "
                           "candidate_id, pas les deux.")
            if candidate_id:
                return _call("applications_by_candidate", candidate_id)
            if job_id:
                return _call("applications_by_job", job_id)
            return _call("list_applications", limit=limit, cursor=cursor,
                         candidate_emails=candidate_emails, is_inbound=is_inbound,
                         modified_since=modified_since,
                         modified_until=modified_until, include=include)
        if op == "create":
            return _call("create_application",
                         _need(candidate_id, "candidate_id", op),
                         _need(stage_id, "stage_id", op),
                         job_id=job_id, status_id=status_id, client_id=client_id)
        if op == "move":
            return _call("move_application",
                         _need(application_id, "application_id", op),
                         _need(stage_id, "stage_id", op),
                         status_id=status_id)
        raise _bad(_ops_error(_APPLICATION_OPS))

    @mcp.tool()
    def spott_stages(
        entity: Literal["applications", "vacancies", "clients",
                        "opportunities"] = "applications",
        template_id: Optional[str] = None,
    ) -> dict:
        """List the ordered pipeline stages (with their ids and labels).

        Read this BEFORE creating or moving an application — stage ids are what
        the write ops of `spott_application` expect.

        Args:
            entity: applications | vacancies | clients | opportunities.
            template_id: stages of one pipeline template (applications only).
        """
        return _call("pipeline_stages", entity, template_id=template_id)

    # --- Notes --------------------------------------------------------------

    @mcp.tool()
    def spott_note(
        op: Literal["list", "create"] = "list",
        limit: int = 25,
        cursor: Optional[str] = None,
        candidate_id: Optional[str] = None,
        client_contact_id: Optional[str] = None,
        source: Optional[str] = None,
        label_ids: Optional[list[str]] = None,
        modified_since: Optional[str] = None,
        modified_until: Optional[str] = None,
        content: Optional[str] = None,
        title: Optional[str] = None,
        links: Optional[list[dict]] = None,
    ) -> dict:
        """A note in Spott — list, or write one attached to records.

        `op`:
        - **"list"** (default): list notes (cursor pagination), optionally about
          one candidate or client contact.
        - **"create"** — ⚠️ WRITES: write a note (`content`), optionally attached
          to records (`links`).

        Args:
            op: list (default) | create.
            limit: op="list" — page size, max 50.
            cursor: op="list" — `cursor` returned by the previous call.
            candidate_id: op="list" — notes about this candidate.
            client_contact_id: op="list" — notes about this client contact.
            source: how the exchange happened — phone | phoneInbound |
                phoneOutbound | inPerson | onlineMeeting | callAttempted.
                Filters on op="list", records the channel on op="create".
            label_ids: note labels — restrict to these on op="list", apply these
                on op="create".
            modified_since / modified_until: op="list" — ISO-8601 bounds.
            content: op="create" — the note body.
            title: op="create" — the note title.
            links: op="create" — `[{"entityType": …, "entityId": …}]` —
                entityType among candidate, vacancy, client, application,
                clientContact, interview, opportunity. A call report on a
                candidate is `[{"entityType": "candidate", "entityId": "<id>"}]`.
        """
        if op not in _NOTE_OPS:
            raise _bad(_ops_error(_NOTE_OPS))

        if op == "list":
            return _call("list_notes", limit=limit, cursor=cursor,
                         candidate_id=candidate_id,
                         client_contact_id=client_contact_id,
                         source=source, label_ids=label_ids,
                         modified_since=modified_since,
                         modified_until=modified_until)
        if op == "create":
            return _call("create_note", _need(content, "content", op),
                         title=title, links=links, source=source,
                         label_ids=label_ids)
        raise _bad(_ops_error(_NOTE_OPS))

    # --- Clients (côté CRM du cabinet) --------------------------------------

    @mcp.tool()
    def spott_client(
        op: Literal["list", "get", "search", "contacts"] = "list",
        client_id: Optional[str] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
        list_ids: Optional[list[str]] = None,
        client_ids: Optional[list[str]] = None,
        filters: Optional[list[dict]] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> dict:
        """A client company of the agency — list, read, search, or list its
        contacts (the people you deal with there).

        `op`:
        - **"list"** (default): list client companies (cursor pagination).
        - **"get"**: fetch one client (`client_id`): details, contacts, industry,
          size, funding, locations, hierarchies, custom attributes.
        - **"search"**: search client companies with structured filters (page
          pagination).
        - **"contacts"**: list client contacts, optionally restricted to some
          client companies.

        Args:
            op: list (default) | get | search | contacts.
            client_id: op="get" — the client company id.
            limit: op="list"/"contacts" — page size, max 50.
            cursor: op="list"/"contacts" — `cursor` returned by the previous call.
            list_ids: op="list"/"contacts" — restrict to these Spott lists
                (max 25).
            client_ids: op="contacts" — restrict to these client companies
                (max 25).
            filters: op="search" — structured filters. Native paths:
                `client.company.name` / `.domain` / `.description` (`text`),
                `client.stage` / `client.contacts` (`entitySelect`).
            page: op="search" — 0-based page index.
            page_size: op="search" — results per page.
        """
        if op not in _CLIENT_OPS:
            raise _bad(_ops_error(_CLIENT_OPS))

        if op == "list":
            _no_filters(op, filters)
            return _call("list_clients", limit=limit, cursor=cursor,
                         list_ids=list_ids)
        if op == "get":
            return _call("get_client", _need(client_id, "client_id", op))
        if op == "search":
            return _call("search_clients", filters=filters, page=page,
                         page_size=page_size)
        if op == "contacts":
            _no_filters(op, filters)
            return _call("list_client_contacts", limit=limit, cursor=cursor,
                         client_ids=client_ids, list_ids=list_ids)
        raise _bad(_ops_error(_CLIENT_OPS))

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
        creating a candidate (`spott_candidate` op="create").

        Args:
            limit: max results, up to 100.
        """
        return _call("search_people", query, limit=limit)

    @mcp.tool()
    def spott_users(include_deactivated: bool = False) -> dict:
        """List Spott users (the recruiters). Their ids are what `mainContact` /
        `owner` fields expect. Active users only unless `include_deactivated`."""
        return _call("list_users", include_deactivated=include_deactivated)
