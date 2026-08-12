"""Attio CRM — CRUD complet records + notes/tasks/lists/entries/comments/meta.

Couvre create/read/update/delete sur companies, people, deals, et
create/list/delete pour notes (l'API Attio ne permet pas d'éditer le corps
d'une note), et create/list/update/delete pour tasks (update limité à
`deadline_at`, `is_completed`, `linked_records`, `assignees` côté API).

Clé résolue par appel via `access.resolve_api_key("attio")`. Comme Attio
n'a pas de quota par défaut (cf. `access._QUOTA_DEFAULTS`), seuls les
admins (avec une `ATTIO_API_KEY` serveur) ou les users avec leur propre
clé posée sur `/account` peuvent appeler ces tools.

**Surface consolidée (ADR 0047 §Amendement, appliqué au connecteur attio)** : un
tool par OBJET métier, le verbe en paramètre `op` — 56 tools → 10. Attio est un
CRM À OBJETS : le motif `create_X`/`get_X`/`update_X`/`delete_X`/`list_X` s'y
répétait à l'identique sur une douzaine d'objets, avec les MÊMES paramètres à
chaque fois. C'est le cas nominal de l'amendement.

- `attio_record` (18 → 1) — companies/people/deals partagent la MÊME ressource
  côté client (`AttioResource`) et donc la même signature : l'objet devient un
  paramètre (`object=`), comme `module=` chez zoho.
- `attio_note` (4 → 1) · `attio_task` (5 → 1) · `attio_list` (5 → 1)
  · `attio_entry` (5 → 1) · `attio_workspace_member` (2 → 1)
  · `attio_meeting` (5 → 1, meeting + ses enregistrements + le transcript : tout
  est keyé par `meeting_id`) · `attio_object` (3 → 1) · `attio_attribute` (4 → 1).
- `attio_comment` (5 → 1) fusionne comments ET threads : un thread n'est que le
  fil d'une conversation de commentaires, les deux se désignent par le MÊME
  tuple d'ancrage (`parent_object`+`parent_record_id`, ou `list_id`+`entry_id`,
  ou `thread_id`) — supprimer un commentaire de tête supprime d'ailleurs le
  thread. Params recouvrants ⟹ un seul objet.

**Ce qui n'a PAS été fusionné**, et pourquoi (le critère est l'homogénéité des
paramètres, pas le comptage) :
- `attio_entry` reste séparé d'`attio_list`. Les deux partagent `list_id_or_slug`
  et rien d'autre : les ops de liste travaillent sur le CONTENEUR (`name`,
  `api_slug`, `workspace_access`), celles d'entrée sur son CONTENU (`entry_id`,
  `entry_values`, `parent_record_id`, `filter`, `sorts`,
  `overwrite_multiselect`). Fusionner produirait un `oneOf` de variantes
  disjointes — le poids de schéma des deux tools, plus l'ambiguïté.
- `attio_note` et `attio_task` restent séparés : ils ne partagent que `content`.
  Une note s'ancre par `parent_object`/`parent_record_id` + `title`, une tâche
  par `linked_object`/`linked_record_id` + `deadline`/`assignee_id`, et l'API ne
  permet pas les mêmes verbes (pas d'update de note).
- `attio_object` (définitions d'objets) et `attio_attribute` (schéma d'un objet
  OU d'une liste) restent séparés : leurs identifiants ne se recouvrent pas
  (`object_id_or_slug` vs le couple `target`+`identifier`, où `target` vaut
  "objects" OU "lists"). Les confondre ferait porter à un même `identifier` deux
  sens selon l'op.

⚠️ Ce module ÉCRIT sur un CRM RÉEL (données clients) : `op="create"`/`"update"`/
`"delete"` d'`attio_record`, `attio_note`, `attio_task`, `attio_list`,
`attio_entry` et `attio_comment`. Le défaut de CHAQUE tool est une LECTURE
(`op="list"`, `"query"` ou `"threads"`) — un appel sans `op` ne peut ni écrire ni
supprimer. Une op inconnue est refusée AVANT même la résolution de la clé.
"""
from __future__ import annotations

from typing import Literal, Optional, get_args

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access

# Ops de chaque objet, dans l'ordre lectures → écritures. Source unique : le
# SCHÉMA MCP (`Literal` → `enum` JSON : depuis la consolidation le verbe n'est
# plus dans le nom du tool, donc c'est l'enum qui l'annonce au client), la
# validation d'entrée ET le message de refus en dérivent — une op ajoutée ne peut
# pas être acceptée sans être annoncée (ni l'inverse).
_RecordObject = Literal["companies", "people", "deals"]
_RecordOp = Literal["list", "get", "search", "create", "update", "delete"]
_NoteOp = Literal["list", "get", "create", "delete"]
_TaskOp = Literal["list", "get", "create", "update", "delete"]
_ListOp = Literal["list", "get", "views", "create", "update"]
_EntryOp = Literal["query", "get", "create", "update", "delete"]
_MemberOp = Literal["list", "get"]
_CommentOp = Literal["threads", "thread", "get", "create", "delete"]
_MeetingOp = Literal["list", "get", "recordings", "recording", "transcript"]
_ObjectOp = Literal["list", "get", "views"]
_AttributeOp = Literal["list", "get", "options", "statuses"]

_RECORD_OBJECTS = get_args(_RecordObject)
_RECORD_OPS = get_args(_RecordOp)
_NOTE_OPS = get_args(_NoteOp)
_TASK_OPS = get_args(_TaskOp)
_LIST_OPS = get_args(_ListOp)
_ENTRY_OPS = get_args(_EntryOp)
_MEMBER_OPS = get_args(_MemberOp)
_COMMENT_OPS = get_args(_CommentOp)
_MEETING_OPS = get_args(_MeetingOp)
_OBJECT_OPS = get_args(_ObjectOp)
_ATTRIBUTE_OPS = get_args(_AttributeOp)


def _one_of(name: str, values: tuple[str, ...]) -> str:
    """Message de refus DÉRIVÉ de la liste des valeurs admises — jamais recopié à
    la main : une op ajoutée au tuple s'annonce toute seule."""
    quoted = [f"'{v}'" for v in values]
    return f"{name} doit être " + ", ".join(quoted[:-1]) + " ou " + quoted[-1]


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback.

    Une valeur VIDE compte comme absente : `attributes={}` sur `op='create'`
    créerait un record vide dans le CRM, et sur `op='update'` un PATCH qui ne
    change rien — deux écritures qui passeraient pour un succès alors que rien
    n'a été demandé.
    """
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        raise _bad(f"op='{op}' requiert {name}")
    return value


def register(mcp: FastMCP) -> None:
    from oto.tools.attio.client import AttioClient

    def _client() -> tuple[AttioClient, bool]:
        key, is_platform = access.resolve_api_key("attio")
        return AttioClient(api_key=key), is_platform

    def _record_if_platform(is_platform: bool) -> None:
        if is_platform:
            access.record_platform_usage("attio")

    # --- records : companies / people / deals ------------------------------

    @mcp.tool()
    def attio_record(
        object: _RecordObject,
        op: _RecordOp = "list",
        record_id: Optional[str] = None,
        attributes: Optional[dict] = None,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """A CRM record — company, person or deal: list, read, search, create,
        update, delete.

        `object` picks the Attio object the record lives in: "companies",
        "people" or "deals".

        `op`:
        - **"list"** (default): list records of that object in the Attio CRM
          workspace. Paginated (`limit` / `offset`).
        - **"get"**: fetch a record by its Attio record ID.
        - **"search"**: search by free-text `query` — matches name/domain/etc.
          for companies, name/email/etc. for people.
        - **"create"** — ⚠️ WRITES: create a record from `attributes`.
        - **"update"** — ⚠️ WRITES: update a record (PATCH — multiselect values
          are appended).
        - **"delete"** — ⚠️ WRITES: delete a record by ID. Irreversible.

        Args:
            object: companies | people | deals.
            op: list (default) | get | search | create | update | delete.
            record_id: op="get"/"update"/"delete" — Attio record ID.
            attributes: op="create"/"update" — Attio attribute dict. Keys are the
                slugs of that object in the workspace; each value follows Attio's
                value format (typically a list, e.g.
                `{"name": [{"value": "Acme"}]}`). On update, same slugs →
                value(s), Attio value format.
                - companies: `name`, `domains`, `description`, `categories`, etc.
                - people: `name`, `email_addresses`, `phone_numbers`, `company`,
                  `job_title`, etc.
                - deals — champs clés : `name` (str ou [{"value": ...}]), `stage`
                  (titre du status, ex "Lead" | "In Discussion" | "Proposal" |
                  "Active"), `owner` (actor-reference — auto-rempli avec le 1er
                  workspace member si omis), `associated_company` /
                  `associated_people` ([{"target_object": "companies",
                  "target_record_id": ...}]), et les customs workspace : `slug`
                  (unique, kebab-case), `tjm` ({"currency_value": N} — type
                  currency), `via`, `debut`/`fin` (date).
            query: op="search" — free-text query.
            limit: op="list"/"search" — max records (default 50).
            offset: op="list" — pagination offset.
        """
        # Refus AVANT toute résolution de credential : une op (ou un objet)
        # inconnu n'atteint jamais le client — donc jamais, par un chemin dérivé,
        # une écriture sur le CRM.
        if op not in _RECORD_OPS:
            raise _bad(_one_of("op", _RECORD_OPS))
        if object not in _RECORD_OBJECTS:
            raise _bad(_one_of("object", _RECORD_OBJECTS))
        client, is_platform = _client()
        resource = getattr(client, object)

        if op == "list":
            result = resource.list(limit=limit, offset=offset)
        elif op == "get":
            result = resource.get(_need(record_id, "record_id", op))
        elif op == "search":
            result = resource.search(query=_need(query, "query", op), limit=limit)
        elif op == "create":
            values = dict(_need(attributes, "attributes", op))
            # `owner` est obligatoire côté workspace pour un deal : sans lui la
            # création échoue. On retombe sur le 1er workspace member plutôt que
            # de faire échouer l'agent sur un champ qu'il ne peut pas deviner.
            if object == "deals" and "owner" not in values:
                members = client.workspace_members.list().get("data", [])
                if members:
                    values["owner"] = [{
                        "referenced_actor_type": "workspace-member",
                        "referenced_actor_id": members[0]["id"]["workspace_member_id"],
                    }]
            result = resource.create(**values)
        elif op == "update":
            result = resource.update(_need(record_id, "record_id", op),
                                     **_need(attributes, "attributes", op))
        elif op == "delete":
            result = resource.delete(_need(record_id, "record_id", op))
        else:
            # Structurellement inatteignable (garde d'entrée ci-dessus) — filet
            # contre un `return None` implicite si une op était ajoutée au tuple
            # sans sa branche : mieux vaut refuser que rendre « rien » pour un
            # succès. Même filet dans chaque tool ci-dessous.
            raise _bad(_one_of("op", _RECORD_OPS))

        _record_if_platform(is_platform)
        return result

    # --- notes ------------------------------------------------------------

    @mcp.tool()
    def attio_note(
        op: _NoteOp = "list",
        note_id: Optional[str] = None,
        parent_object: Optional[str] = None,
        parent_record_id: Optional[str] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
    ) -> dict:
        """A note attached to a record — list, read, create, delete.

        `op`:
        - **"list"** (default): list notes — optionally scoped to a parent record
          (`parent_object` + `parent_record_id`).
        - **"get"**: get a single note by ID (including markdown content).
        - **"create"** — ⚠️ WRITES: create a note attached to a record.
        - **"delete"** — ⚠️ WRITES: delete a note by ID. Irreversible. The Attio
          API does not support editing a note body — to change a note, delete it
          and create a new one.

        Args:
            op: list (default) | get | create | delete.
            note_id: op="get"/"delete" — the note ID.
            parent_object: companies | people | deals. Optional on op="list"
                (scopes the listing), required on op="create".
            parent_record_id: record ID under that object. Optional on op="list",
                required on op="create" (the record to attach the note to).
            title: op="create" — note title.
            content: op="create" — markdown body.
        """
        if op not in _NOTE_OPS:
            raise _bad(_one_of("op", _NOTE_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.notes.list(
                parent_object=parent_object, parent_record_id=parent_record_id,
            )
        elif op == "get":
            result = client.notes.get(_need(note_id, "note_id", op))
        elif op == "create":
            result = client.notes.create(
                parent_object=_need(parent_object, "parent_object", op),
                parent_record_id=_need(parent_record_id, "parent_record_id", op),
                title=_need(title, "title", op),
                content=_need(content, "content", op),
            )
        elif op == "delete":
            result = client.notes.delete(_need(note_id, "note_id", op))
        else:
            raise _bad(_one_of("op", _NOTE_OPS))

        _record_if_platform(is_platform)
        return result

    # --- tasks ------------------------------------------------------------

    @mcp.tool()
    def attio_task(
        op: _TaskOp = "list",
        task_id: Optional[str] = None,
        content: Optional[str] = None,
        deadline: Optional[str] = None,
        completed: Optional[bool] = None,
        is_completed: Optional[bool] = None,
        assignee_id: Optional[str] = None,
        linked_object: Optional[str] = None,
        linked_record_id: Optional[str] = None,
    ) -> dict:
        """A task — list, read, create, update, delete.

        `op`:
        - **"list"** (default): list tasks — optionally filtered by completion
          status (`completed`).
        - **"get"**: get a single task by ID.
        - **"create"** — ⚠️ WRITES: create a task, optionally linked to a record.
        - **"update"** — ⚠️ WRITES: update a task. The Attio API only allows
          changing `deadline_at`, `is_completed`, `assignees`, `linked_records` —
          the task text itself cannot be edited.
        - **"delete"** — ⚠️ WRITES: delete a task by ID. Irreversible.

        Args:
            op: list (default) | get | create | update | delete.
            task_id: op="get"/"update"/"delete" — the Attio task ID.
            content: op="create" — task description (max 2000 chars).
            deadline: op="create"/"update" — ISO datetime or YYYY-MM-DD.
            completed: op="list" — filter on completion status (True/False).
                This one FILTERS; to change a task's status use `is_completed`.
            is_completed: op="update" — mark as done/not done.
            assignee_id: workspace member ID (op="update", and optional on
                op="create" — defaults to the first workspace member).
            linked_object: companies | people (also deals on op="update") — pair
                with `linked_record_id`.
            linked_record_id: record ID under that object.
        """
        if op not in _TASK_OPS:
            raise _bad(_one_of("op", _TASK_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.tasks.list(completed=completed)
        elif op == "get":
            result = client.tasks.get(_need(task_id, "task_id", op))
        elif op == "create":
            result = client.tasks.create(
                content=_need(content, "content", op),
                deadline=deadline,
                assignee_id=assignee_id,
                linked_object=linked_object,
                linked_record_id=linked_record_id,
            )
        elif op == "update":
            result = client.tasks.update(
                _need(task_id, "task_id", op),
                deadline=deadline,
                is_completed=is_completed,
                assignee_id=assignee_id,
                linked_object=linked_object,
                linked_record_id=linked_record_id,
            )
        elif op == "delete":
            result = client.tasks.delete(_need(task_id, "task_id", op))
        else:
            raise _bad(_one_of("op", _TASK_OPS))

        _record_if_platform(is_platform)
        return result

    # --- lists ------------------------------------------------------------

    @mcp.tool()
    def attio_list(
        op: _ListOp = "list",
        list_id_or_slug: Optional[str] = None,
        name: Optional[str] = None,
        parent_object: Optional[str] = None,
        api_slug: Optional[str] = None,
        workspace_access: str = "full-access",
        attributes: Optional[dict] = None,
    ) -> dict:
        """An Attio list (a saved collection of records) — list, read, create,
        update, and read its saved views.

        This is the CONTAINER. What is IN a list (adding/removing/querying
        records) is `attio_entry`.

        `op`:
        - **"list"** (default): list all Attio lists accessible to the token.
        - **"get"**: get a single list by ID or slug.
        - **"views"**: list the saved views of a list.
        - **"create"** — ⚠️ WRITES: create a new list. `api_slug` et
          `workspace_member_access` sont requis par l'API Attio ; le client les
          dérive/défaut automatiquement (slug depuis le nom, accès membre vide).
        - **"update"** — ⚠️ WRITES: update an existing list (name, api_slug,
          access controls), via `attributes`.

        Args:
            op: list (default) | get | views | create | update.
            list_id_or_slug: op="get"/"views"/"update" — the target list.
            name: op="create" — display name.
            parent_object: op="create" — object slug the list targets
                (companies | people | deals | custom).
            api_slug: op="create" — optional API slug (auto-derived if omitted).
            workspace_access: op="create" — full-access | read-and-write |
                read-only.
            attributes: op="update" — fields to change (name, api_slug, access
                controls).
        """
        if op not in _LIST_OPS:
            raise _bad(_one_of("op", _LIST_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.lists.list()
        elif op == "get":
            result = client.lists.get(_need(list_id_or_slug, "list_id_or_slug", op))
        elif op == "views":
            result = client.lists.views(_need(list_id_or_slug, "list_id_or_slug", op))
        elif op == "create":
            result = client.lists.create(
                name=_need(name, "name", op),
                parent_object=_need(parent_object, "parent_object", op),
                api_slug=api_slug,
                workspace_access=workspace_access,
            )
        elif op == "update":
            result = client.lists.update(
                _need(list_id_or_slug, "list_id_or_slug", op),
                **_need(attributes, "attributes", op),
            )
        else:
            raise _bad(_one_of("op", _LIST_OPS))

        _record_if_platform(is_platform)
        return result

    # --- entries (list membership) ----------------------------------------

    @mcp.tool()
    def attio_entry(
        list_id_or_slug: str,
        op: _EntryOp = "query",
        entry_id: Optional[str] = None,
        parent_object: Optional[str] = None,
        parent_record_id: Optional[str] = None,
        entry_values: Optional[dict] = None,
        filter: Optional[dict] = None,
        sorts: Optional[list] = None,
        overwrite_multiselect: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """An entry in a list — i.e. a record's membership of that list: query,
        read, add, update, remove.

        `op`:
        - **"query"** (default): query entries in the list, with optional
          filter/sort.
        - **"get"**: get a single list entry by ID.
        - **"create"** — ⚠️ WRITES: add a record to the list as a new entry.
        - **"update"** — ⚠️ WRITES: update list entry values. PATCH appends
          multiselect by default; pass `overwrite_multiselect=True` for PUT.
        - **"delete"** — ⚠️ WRITES: remove a record from the list by deleting its
          entry. Irreversible.

        Args:
            list_id_or_slug: the target list (required for every op).
            op: query (default) | get | create | update | delete.
            entry_id: op="get"/"update"/"delete" — the entry ID.
            parent_object: op="create" — companies | people | deals | custom slug.
            parent_record_id: op="create" — ID of the record (company/person/deal)
                to add.
            entry_values: op="create" — optional list-specific attribute values ;
                op="update" — the values to write.
            filter: op="query" — Attio filter object (e.g. `{"name": "Acme"}`).
            sorts: op="query" — list of sort dicts.
            overwrite_multiselect: op="update" — True switches PATCH to PUT
                (overwrites multiselect instead of appending).
            limit: op="query" — max entries (default 50).
            offset: op="query" — pagination offset.
        """
        if op not in _ENTRY_OPS:
            raise _bad(_one_of("op", _ENTRY_OPS))
        client, is_platform = _client()

        if op == "query":
            result = client.entries.query(
                list_id_or_slug, filter=filter, sorts=sorts,
                limit=limit, offset=offset,
            )
        elif op == "get":
            result = client.entries.get(list_id_or_slug,
                                        _need(entry_id, "entry_id", op))
        elif op == "create":
            result = client.entries.create(
                list_id_or_slug,
                parent_record_id=_need(parent_record_id, "parent_record_id", op),
                parent_object=_need(parent_object, "parent_object", op),
                entry_values=entry_values,
            )
        elif op == "update":
            result = client.entries.update(
                list_id_or_slug,
                _need(entry_id, "entry_id", op),
                entry_values=_need(entry_values, "entry_values", op),
                overwrite_multiselect=overwrite_multiselect,
            )
        elif op == "delete":
            result = client.entries.delete(list_id_or_slug,
                                           _need(entry_id, "entry_id", op))
        else:
            raise _bad(_one_of("op", _ENTRY_OPS))

        _record_if_platform(is_platform)
        return result

    # --- workspace members ------------------------------------------------

    @mcp.tool()
    def attio_workspace_member(
        op: _MemberOp = "list",
        workspace_member_id: Optional[str] = None,
    ) -> dict:
        """A workspace member (a human with access to the Attio workspace) —
        list, read.

        Their IDs are what `attio_comment` wants as `author_id` and `attio_task`
        as `assignee_id`.

        `op`:
        - **"list"** (default): list all workspace members.
        - **"get"**: get a single workspace member by ID.

        Args:
            op: list (default) | get.
            workspace_member_id: op="get" — the member ID.
        """
        if op not in _MEMBER_OPS:
            raise _bad(_one_of("op", _MEMBER_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.workspace_members.list()
        elif op == "get":
            result = client.workspace_members.get(
                _need(workspace_member_id, "workspace_member_id", op))
        else:
            raise _bad(_one_of("op", _MEMBER_OPS))

        _record_if_platform(is_platform)
        return result

    # --- comments & threads -----------------------------------------------

    @mcp.tool()
    def attio_comment(
        op: _CommentOp = "threads",
        thread_id: Optional[str] = None,
        comment_id: Optional[str] = None,
        content: Optional[str] = None,
        author_id: Optional[str] = None,
        parent_object: Optional[str] = None,
        parent_record_id: Optional[str] = None,
        list_id: Optional[str] = None,
        entry_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """A comment and the thread it lives in — browse threads, read a thread,
        read/post/delete a comment.

        `op`:
        - **"threads"** (default): list comment threads, filtered by parent record
          or list entry. ⚠️ The Attio API returns **400 without a filter** —
          always scope the listing by `parent_object` + `parent_record_id` (or by
          `list_id` + `entry_id`); an unfiltered call is refused here rather than
          upstream.
        - **"thread"**: get a thread with all its comments (`thread_id`).
        - **"get"**: get a single comment by ID.
        - **"create"** — ⚠️ WRITES: create a comment — either replying in a thread
          or starting one on a record/entry. Provide one of: `thread_id`,
          (`parent_object` + `parent_record_id`), or (`list_id` + `entry_id`).
        - **"delete"** — ⚠️ WRITES: delete a comment. If it heads a thread, the
          whole thread is deleted.

        Args:
            op: threads (default) | thread | get | create | delete.
            thread_id: op="thread" — the thread to read; op="create" — reply in
                that thread.
            comment_id: op="get"/"delete" — the comment ID.
            content: op="create" — the comment body.
            author_id: op="create" — a workspace_member_id (see
                `attio_workspace_member`).
            parent_object: companies | people | deals — anchor of the thread
                (op="threads" filter, op="create" target).
            parent_record_id: record ID under that object.
            list_id: anchor on a list entry, paired with `entry_id`.
            entry_id: list entry ID, paired with `list_id`.
            limit: op="threads" — max threads (default 50).
            offset: op="threads" — pagination offset.
        """
        if op not in _COMMENT_OPS:
            raise _bad(_one_of("op", _COMMENT_OPS))
        client, is_platform = _client()

        if op == "threads":
            # Gotcha empirique : `GET /threads` sans filtre répond 400. On le dit
            # ici, actionnable, plutôt que de laisser remonter l'erreur opaque.
            if not (parent_object or parent_record_id or list_id or entry_id):
                raise _bad(
                    "op='threads' requiert un filtre de parent : parent_object + "
                    "parent_record_id (ou list_id + entry_id) — l'API Attio "
                    "répond 400 sur une liste de threads non filtrée.")
            result = client.threads.list(
                parent_object=parent_object,
                parent_record_id=parent_record_id,
                list_id=list_id,
                entry_id=entry_id,
                limit=limit,
                offset=offset,
            )
        elif op == "thread":
            result = client.threads.get(_need(thread_id, "thread_id", op))
        elif op == "get":
            result = client.comments.get(_need(comment_id, "comment_id", op))
        elif op == "create":
            result = client.comments.create(
                content=_need(content, "content", op),
                author_id=_need(author_id, "author_id", op),
                thread_id=thread_id,
                parent_object=parent_object,
                parent_record_id=parent_record_id,
                list_id=list_id,
                entry_id=entry_id,
            )
        elif op == "delete":
            result = client.comments.delete(_need(comment_id, "comment_id", op))
        else:
            raise _bad(_one_of("op", _COMMENT_OPS))

        _record_if_platform(is_platform)
        return result

    # --- meetings / call recordings / transcripts -------------------------

    @mcp.tool()
    def attio_meeting(
        op: _MeetingOp = "list",
        meeting_id: Optional[str] = None,
        call_recording_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """A meeting (calendar event synced into Attio) and its call recordings.

        `op`:
        - **"list"** (default): list meetings. Paginated (`limit` / `offset`).
        - **"get"**: get a single meeting by ID.
        - **"recordings"**: list the call recordings of a meeting.
        - **"recording"**: get a single call recording by ID.
        - **"transcript"**: get the transcript text of a call recording.

        Read-only: Attio does not expose writes on meetings or recordings.

        Args:
            op: list (default) | get | recordings | recording | transcript.
            meeting_id: every op but "list" — the meeting ID.
            call_recording_id: op="recording"/"transcript" — the recording ID.
            limit: op="list" — max meetings (default 50).
            offset: op="list" — pagination offset.
        """
        if op not in _MEETING_OPS:
            raise _bad(_one_of("op", _MEETING_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.meetings.list(limit=limit, offset=offset)
        elif op == "get":
            result = client.meetings.get(_need(meeting_id, "meeting_id", op))
        elif op == "recordings":
            result = client.call_recordings.list(_need(meeting_id, "meeting_id", op))
        elif op == "recording":
            result = client.call_recordings.get(
                _need(meeting_id, "meeting_id", op),
                _need(call_recording_id, "call_recording_id", op))
        elif op == "transcript":
            result = client.call_recordings.transcript(
                _need(meeting_id, "meeting_id", op),
                _need(call_recording_id, "call_recording_id", op))
        else:
            raise _bad(_one_of("op", _MEETING_OPS))

        _record_if_platform(is_platform)
        return result

    # --- meta : objects ---------------------------------------------------

    @mcp.tool()
    def attio_object(
        op: _ObjectOp = "list",
        object_id_or_slug: Optional[str] = None,
    ) -> dict:
        """An object definition in the workspace (system or custom) — list, read,
        and read its saved views.

        `op`:
        - **"list"** (default): list all objects (system + custom) defined in the
          workspace. Useful for an LLM to discover what record types exist beyond
          the standard companies/people/deals (e.g. custom objects like
          "products"). Note that `attio_record` only does CRUD on the three
          standard objects.
        - **"get"**: get a single object definition by ID or slug.
        - **"views"**: list the saved views of an object.

        Read-only.

        Args:
            op: list (default) | get | views.
            object_id_or_slug: op="get"/"views" — object ID or slug
                (e.g. "companies").
        """
        if op not in _OBJECT_OPS:
            raise _bad(_one_of("op", _OBJECT_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.objects.list()
        elif op == "get":
            result = client.objects.get(
                _need(object_id_or_slug, "object_id_or_slug", op))
        elif op == "views":
            result = client.objects.views(
                _need(object_id_or_slug, "object_id_or_slug", op))
        else:
            raise _bad(_one_of("op", _OBJECT_OPS))

        _record_if_platform(is_platform)
        return result

    # --- meta : attributes ------------------------------------------------

    @mcp.tool()
    def attio_attribute(
        target: str,
        identifier: str,
        op: _AttributeOp = "list",
        attribute: Optional[str] = None,
    ) -> dict:
        """An attribute (the schema) of an object or of a list — list, read, and
        read the options/statuses of a select/status attribute.

        `op`:
        - **"list"** (default): list attributes (schema) on an object or list.
        - **"get"**: get a single attribute definition.
        - **"options"**: list the select options for a select-type attribute.
        - **"statuses"**: list the statuses for a status-type attribute.

        Read-only.

        Args:
            target: "objects" or "lists" — which family `identifier` belongs to.
            identifier: object/list ID or slug (e.g. "companies").
            op: list (default) | get | options | statuses.
            attribute: op="get"/"options"/"statuses" — attribute ID or slug.
        """
        if op not in _ATTRIBUTE_OPS:
            raise _bad(_one_of("op", _ATTRIBUTE_OPS))
        client, is_platform = _client()

        if op == "list":
            result = client.attributes.list(target, identifier)
        elif op == "get":
            result = client.attributes.get(target, identifier,
                                           _need(attribute, "attribute", op))
        elif op == "options":
            result = client.attributes.options(target, identifier,
                                               _need(attribute, "attribute", op))
        elif op == "statuses":
            result = client.attributes.statuses(target, identifier,
                                                _need(attribute, "attribute", op))
        else:
            raise _bad(_one_of("op", _ATTRIBUTE_OPS))

        _record_if_platform(is_platform)
        return result
