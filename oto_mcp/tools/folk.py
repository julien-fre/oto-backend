"""Folk CRM — groups, people, companies, deals, notes, interactions, reminders, webhooks.

Wrappe `oto.tools.folk.FolkClient` (API publique https://developer.folk.app).
Clé résolue par appel via `access.resolve_api_key("folk")` — provider byo-only
(user key posée sur /account, ou credential partagé de l'org active). Pas de
clé plateforme.

**Surface consolidée (ADR 0047 §Amendement, appliqué au connecteur folk)** : un
tool par OBJET métier, le verbe en paramètre `op` — 17 tools → 4. Ce qui a été
fusionné, et ce qui ne l'a PAS été (le critère est l'homogénéité des paramètres,
pas le comptage) :

- **`folk_record`** (search/get/create/update/delete/add_to_group) — les six
  verbes partagent le MÊME jeu de paramètres (`entity`, `group_id`,
  `object_type`, `id`/`ids`, `dry_run`) ; seul change le porteur de données
  (`filters` en lecture, `item`/`items` en création, `fields` en mise à jour).
  `entity` y joue le rôle que `module` joue chez Zoho : person | company | deal |
  note | interaction | reminder. Les ex-`folk_list_deals` / `folk_list_notes` /
  `folk_list_reminders` / `folk_get_reminder` y entrent SANS ajouter un
  paramètre : ce sont `op="search"` / `op="get"` sur une autre `entity`.
- **`folk_group`** (list/custom_fields) — reste à part : les groupes sont en
  **lecture seule** côté API Folk (donc ni `dry_run`, ni `id`/`ids`, ni verbe
  d'écriture), et `entity_type` n'est PAS `entity` (il qualifie un schéma de
  champs custom, il ne désigne pas un objet à écrire).
- **`folk_user`** (list/get) — reste à part : un membre du workspace n'est pas un
  record CRM (pas d'`entity`, pas de `group_id`/`object_type`, pas d'écriture) ;
  son unique paramètre `user_id` n'existe nulle part ailleurs.
- **`folk_webhook`** (list/create/update) — reste à part : ressource GLOBALE du
  workspace (ni `entity`, ni `group_id`/`object_type`, ni mode bulk — un
  workspace en a peu), avec son propre vocabulaire d'événements validé à
  l'entrée.

Surface : lecture/écriture **par entité** (`op="search"`/`"get"` prennent
`entity` = person|company|deal[|note|reminder]). `op="create"`/`"update"`/
`"delete"`/`"add_to_group"` couvrent aussi note/reminder (et interaction pour
create), et sont **solo OU bulk selon le param passé** : un singulier
(`item`/`id`) pour UN record → résultat direct ; un pluriel (`items`/`ids`, ≤50)
pour plusieurs → reçu allégé (compte + erreurs par item, jamais N corps de
réponse complets). Folk n'a d'endpoint batch nulle part — le mode bulk boucle sur
les méthodes single-record, en PARALLÈLE et à cadence plafonnée (`_bulk_run`),
pas en séquence avec une pause fixe (la latence réseau par appel dominait le
temps total, pas la cadence Folk).

⚠️ **Deux vocabulaires de champs différents cohabitent** : `op="create"` prend
des clés Python snake_case (`first_name`, `company_id`...) ; `op="update"` prend
les noms de champs bruts de l'API Folk en camelCase (`jobTitle`,
`customFieldValues`...). Ne pas transposer l'un vers l'autre — voir le docstring
de `folk_record`.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable qui NOMME l'op et
    l'argument manquant, jamais un fallback silencieux (les ops d'écriture de ce
    module touchent des données réelles : deviner à la place de l'appelant y
    coûte un record)."""
    if value is None:
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _merge_group_ids(current_groups, add, remove) -> list[dict]:
    """Fusionne la liste de groupes d'un record Folk et renvoie la liste COMPLÈTE
    au format API (`[{"id": ...}]`).

    L'API Folk est en *replace-all* sur les champs-listes (un PATCH `groups`
    écrase la liste entière) : pour ajouter/retirer un groupe sans perdre les
    autres, il faut relire les groupes actuels et renvoyer l'union résultante.
    Préserve l'ordre et déduplique.
    """
    remove_set = set(remove or [])
    result: list[str] = []
    for g in (current_groups or []):
        gid = g.get("id") if isinstance(g, dict) else g
        if gid and gid not in remove_set and gid not in result:
            result.append(gid)
    for gid in (add or []):
        if gid not in remove_set and gid not in result:
            result.append(gid)
    return [{"id": gid} for gid in result]


# --- dispatch par entité, partagé entre modes singulier et bulk -------------
#
# `_create_one`/`_update_one`/`_delete_one` portent la logique de l'op sur UN
# record : on l'extrait pour que le mode bulk l'appelle item-par-item sans
# dupliquer/diverger de la validation. Tous les trois acceptent `dry_run`
# (convention oto — cf. `email_send`, LinkedIn `send_message`/`connect`) : la
# validation tourne normalement, seul l'appel mutant final est sauté, remplacé
# par un aperçu.

# Axes de dispatch de `folk_record`, DÉCLARÉS au schéma (`Literal` → `enum` JSON).
# Depuis la consolidation, le verbe n'est plus dans le NOM du tool : sans enum, les
# valeurs admises n'existent que dans la prose de la docstring, et rien ne contraint
# le client. `_Entity` borne l'UNION des entités (= tout ce qu'au moins une op
# accepte) ; le sous-ensemble admis PAR op reste gardé par les tuples ci-dessous.
_Entity = Literal["person", "company", "deal", "note", "interaction", "reminder"]
_RecordOp = Literal["search", "get", "create", "update", "delete", "add_to_group"]

_SEARCH_ENTITIES = ("person", "company", "deal", "note", "reminder")
_GET_ENTITIES = ("person", "company", "deal", "reminder")
_CREATE_ENTITIES = ("person", "company", "deal", "note", "interaction", "reminder")
_UPDATE_ENTITIES = ("person", "company", "deal", "note", "reminder")
_DELETE_ENTITIES = ("person", "company", "deal", "note", "reminder")
_GROUP_ENTITIES = ("person", "company")

# Champs acceptés par `op="create"` par entité — miroir des paramètres nommés
# des méthodes `FolkClient.create_*` (snake_case Python, PAS les noms de
# champs API Folk en camelCase utilisés par `op="update"`/`fields`). Codé en
# dur plutôt qu'introspecté via `inspect.signature` : `create_person`/
# `create_company` acceptent `**kwargs` côté client, donc sans cette
# allow-list explicite un champ mal orthographié/mal casé (ex. `firstName` au
# lieu de `first_name`) serait avalé SILENCIEUSEMENT dans le payload envoyé à
# Folk sous le mauvais nom, plutôt que de lever une erreur. Une liste codée en
# dur reste aussi testable contre un `FolkClient` mocké (l'introspection de
# signature ne fonctionne pas sur un Mock sans `autospec`).
_CREATE_FIELDS = {
    "person": {"first_name", "last_name", "emails", "phones", "job_title",
               "company_name", "company_id", "group_ids", "urls", "description"},
    "company": {"name", "emails", "industry"},
    "deal": {"name", "people_ids", "company_ids", "custom_fields"},
    "note": {"entity_id", "content", "visibility"},
    "interaction": {"entity_id", "type", "title", "content", "date_time"},
    "reminder": {"entity_id", "name", "recurrence_rule", "visibility"},
}

# Filtres acceptés par `op="search"` sur note/reminder : Folk n'expose qu'un
# filtre par entité parente (`list_notes(entity_id=…)`). Contrairement à
# `list_people(**filters)`, ces méthodes ont une signature FERMÉE — un filtre
# inconnu lèverait un `TypeError` rendu en « erreur interne », là où l'appelant
# doit lire quel filtre existe.
_SUBRECORD_FILTERS = {"entity_id"}


def _get_one(c, entity: str, id: str, group_id: Optional[str] = None,
             object_type: str = "deals"):
    """Récupère l'état courant d'un record, pour diff/preview `dry_run`.

    Renvoie `None` pour `note` : Folk n'a PAS d'endpoint get-par-id pour les
    notes (`client.py` n'expose que list/create/update/delete) — un gap
    permanent de l'API, pas un raccourci d'implémentation. Les previews
    update/delete d'une note dégradent en conséquence (pas de diff possible)."""
    if entity == "person":
        return c.get_person(id)
    if entity == "company":
        return c.get_company(id)
    if entity == "deal":
        if not group_id:
            raise _bad("group_id requis pour entity='deal'.")
        return c.get_deal(group_id, id, object_type=object_type)
    if entity == "reminder":
        return c.get_reminder(id)
    return None


def _create_one(c, entity: str, fields: Optional[dict] = None,
                 group_id: Optional[str] = None,
                 object_type: str = "deals", dry_run: bool = False):
    """Crée UN record. `fields` = l'item de l'appelant, passé comme DICT.

    Surtout pas `**fields` : les clés de l'item viennent de l'agent, et l'une
    d'elles peut porter le nom d'un paramètre de cette fonction — `folk_record
    (op='create', entity='person', item={... 'group_id': 'grp_…'})` levait alors
    un `TypeError: got multiple values for keyword argument 'group_id'`, rendu à
    l'appelant en « erreur interne du serveur » là où il attendait le refus
    actionnable « champ inconnu pour entity='person' » que la validation juste
    en dessous sait produire (signal #353). Même famille que la collision des
    jetons de contexte : un argument métier mangé par un paramètre homonyme.
    Passer le dict ferme la collision par construction, pour toute clé future.
    """
    fields = dict(fields or {})
    if entity == "deal" and not group_id:
        raise _bad("group_id requis pour entity='deal'.")
    unknown = set(fields) - _CREATE_FIELDS.get(entity, set())
    if unknown:
        raise _bad(
            f"champ(s) inconnu(s) pour entity='{entity}' : {sorted(unknown)}. "
            f"Champs acceptés : {sorted(_CREATE_FIELDS.get(entity, set()))}. "
            f"Rappel : op='create' utilise des clés snake_case Python "
            f"(first_name, company_id...) — PAS les noms de champs API Folk "
            f"en camelCase (jobTitle, customFieldValues...) utilisés par op='update'.")
    if dry_run:
        preview = {"would_create": fields}
        if entity == "deal":
            preview.update(group_id=group_id, object_type=object_type)
        return preview
    if entity == "person":
        return c.create_person(**fields)
    if entity == "company":
        return c.create_company(**fields)
    if entity == "deal":
        return c.create_deal(group_id, object_type=object_type, **fields)
    if entity == "note":
        return c.create_note(**fields)
    if entity == "interaction":
        return c.create_interaction(**fields)
    if entity == "reminder":
        return c.create_reminder(**fields)
    raise _bad(f"entity doit être l'un de {_CREATE_ENTITIES}.")


def _update_one(c, entity: str, id: str, fields: Optional[dict] = None,
                 group_id: Optional[str] = None, object_type: str = "deals",
                 add_to_groups: Optional[list[str]] = None,
                 remove_from_groups: Optional[list[str]] = None,
                 dry_run: bool = False):
    fields = dict(fields or {})
    current = None
    if add_to_groups or remove_from_groups or dry_run:
        current = _get_one(c, entity, id, group_id=group_id, object_type=object_type)
    if add_to_groups or remove_from_groups:
        if entity not in _GROUP_ENTITIES:
            raise _bad("add_to_groups/remove_from_groups ne valent que pour "
                       "entity='person' ou 'company'.")
        if "groups" in fields:
            raise _bad("Ne pas passer 'groups' dans fields en même temps que "
                       "add_to_groups/remove_from_groups.")
        fields["groups"] = _merge_group_ids(
            (current or {}).get("groups"), add_to_groups, remove_from_groups)
    if not fields:
        raise _bad("Rien à mettre à jour : fournir `fields` et/ou "
                   "add_to_groups/remove_from_groups.")
    if dry_run:
        if current is not None:
            return {"id": id, "changes": {k: {"from": current.get(k), "to": v}
                                          for k, v in fields.items()}}
        return {"id": id, "fields": fields, "current_available": False}
    if entity == "person":
        return c.update_person(id, **fields)
    if entity == "company":
        return c.update_company(id, **fields)
    if entity == "deal":
        if not group_id:
            raise _bad("group_id requis pour entity='deal'.")
        return c.update_deal(group_id, id, object_type=object_type, **fields)
    if entity == "note":
        return c.update_note(id, **fields)
    if entity == "reminder":
        return c.update_reminder(id, **fields)
    raise _bad(f"entity doit être l'un de {_UPDATE_ENTITIES}.")


def _delete_one(c, entity: str, id: str, group_id: Optional[str] = None,
                 object_type: str = "deals", dry_run: bool = False):
    if dry_run:
        current = _get_one(c, entity, id, group_id=group_id, object_type=object_type)
        if current is not None:
            return {"id": id, "would_delete": current}
        return {"id": id, "would_delete": None, "current_available": False}
    if entity == "person":
        return c.delete_person(id)
    if entity == "company":
        return c.delete_company(id)
    if entity == "deal":
        if not group_id:
            raise _bad("group_id requis pour entity='deal'.")
        return c.delete_deal(group_id, id, object_type=object_type)
    if entity == "note":
        return c.delete_note(id)
    if entity == "reminder":
        return c.delete_reminder(id)
    raise _bad(f"entity doit être l'un de {_DELETE_ENTITIES}.")


# 50 reste une limite d'ergonomie d'appel (pas de constat précis dérrière),
# indépendante de la cadence ci-dessous.
_BULK_MAX_ITEMS = 50

# Folk documente 600 req/min (10 req/s) par clé. Le goulot d'un lot n'est PAS
# cette cadence — c'est la latence réseau par appel, non recouverte tant que
# les appels étaient séquentiels (un délai de courtoisie fixe entre appels
# n'accélère rien, il ajoute juste une pause après une attente déjà payée).
# `_BULK_CONCURRENCY` appels en vol en parallèle recouvrent cette latence ;
# `_RateLimiter` plafonne la cadence D'ENVOI combinée (tous workers confondus)
# à ~8 req/s, sous les 10 req/s documentés avec marge pour le trafic
# concurrent d'autres appels sur la même clé. `_request` gère déjà les 429
# (retry sur Retry-After) : le régulateur vise à rester sous la limite en
# usage normal, pas à s'y substituer.
_BULK_CONCURRENCY = 6
_BULK_MIN_INTERVAL_S = 0.125  # ~8 req/s


class _RateLimiter:
    """Espace les DISPATCHES d'appel à un intervalle minimum PARTAGÉ entre tous
    les workers — un délai par-worker ne suffirait pas : N workers respectant
    chacun leur propre délai peuvent quand même émettre N fois plus vite que
    prévu au global."""

    def __init__(self, min_interval_s: float):
        self._min_interval = min_interval_s
        self._lock = Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start_at = max(now, self._next_at)
            self._next_at = start_at + self._min_interval
        delay = start_at - now
        if delay > 0:
            time.sleep(delay)


def _bulk_fatal(exc: Exception) -> bool:
    """Erreurs d'auth/connexion : on abandonne tout le lot (répéter la même
    erreur N fois ne sert à rien). Tout le reste (un enregistrement rejeté,
    422 Folk…) reste une erreur PAR ITEM qui n'interrompt pas le lot."""
    from oto.tools.common.errors import UpstreamHTTPError
    import requests
    if isinstance(exc, UpstreamHTTPError):
        return exc.status_code in (401, 403)
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


def _bulk_run(items: list, fn) -> list[tuple[int, bool, object]]:
    """Exécute `fn(item)` pour chaque item EN PARALLÈLE (jusqu'à
    `_BULK_CONCURRENCY` appels HTTP en vol, cadence combinée plafonnée par
    `_RateLimiter`) plutôt qu'en séquence avec une pause fixe après chaque
    appel — c'est la latence réseau par appel qui dominait le temps total, pas
    la cadence Folk, et une boucle séquentielle ne pouvait jamais la recouvrir.

    Renvoie une liste de `(index, ok, valeur_ou_message_erreur)` — comme avant
    mais PAS nécessairement dans l'ordre de soumission : chaque appelant ne se
    fie qu'à l'`index` porté par le tuple, jamais à la position dans la liste
    (vérifié aux 4 call-sites). Une erreur FATALE (auth/connexion) annule les
    appels pas encore démarrés et relève l'exception — même contrat qu'avant
    (le lot entier est perdu, pas de reçu partiel), simplement détecté plus
    tôt grâce au parallélisme."""
    if len(items) > _BULK_MAX_ITEMS:
        raise _bad(f"trop d'éléments ({len(items)}) — max {_BULK_MAX_ITEMS} par appel, "
                   f"découper en plusieurs appels.")
    limiter = _RateLimiter(_BULK_MIN_INTERVAL_S)
    results: list[Optional[tuple[int, bool, object]]] = [None] * len(items)

    def _run_one(item):
        limiter.wait()
        return fn(item)

    pool = ThreadPoolExecutor(max_workers=min(_BULK_CONCURRENCY, len(items)))
    futures = {pool.submit(_run_one, item): i for i, item in enumerate(items)}
    try:
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = (i, True, future.result())
            except Exception as e:
                if _bulk_fatal(e):
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                results[i] = (i, False, str(e))
    finally:
        pool.shutdown(wait=True)
    return [r for r in results if r is not None]


def register(mcp: FastMCP) -> None:
    from oto.tools.folk.client import FolkClient, WEBHOOK_EVENT_TYPES

    def _client() -> FolkClient:
        key, _ = access.resolve_api_key("folk")
        # Rédaction des champs sensibles : plus au niveau client — appliquée à la
        # frontière des tools par `FieldRedactionMiddleware` (policy de l'org active).
        return FolkClient(api_key=key)

    def _validate_subscribed_events(events: list) -> None:
        if not events:
            raise _bad("subscribed_events : au moins un événement requis.")
        for e in events:
            event_type = (e or {}).get("eventType")
            if event_type not in WEBHOOK_EVENT_TYPES:
                raise _bad(
                    f"eventType invalide : {event_type!r}. Valeurs valides : "
                    + ", ".join(sorted(WEBHOOK_EVENT_TYPES))
                )

    # --- le record CRM : un tool, le verbe en `op` ---------------------------
    #
    # `op` par défaut = "search", une LECTURE : aucune op d'écriture n'est
    # atteignable sans l'avoir nommée. Les quatre ops mutantes
    # (create/update/delete/add_to_group) prennent une paire de params
    # mutuellement exclusifs : le singulier (un seul record, résultat/preview
    # renvoyé directement) OU le pluriel (jusqu'à 50, reçu bulk). Folk n'a
    # d'endpoint batch nulle part (vérifié sur ce connecteur, le MCP officiel
    # Folk, et un MCP tiers) — le pluriel boucle sur les méthodes
    # single-record, en parallèle à cadence plafonnée (`_bulk_run`) et renvoie
    # un reçu allégé, jamais N corps de réponse complets.

    @mcp.tool()
    def folk_record(
        entity: _Entity,
        op: _RecordOp = "search",
        id: Optional[str] = None,
        ids: Optional[list[str]] = None,
        item: Optional[dict] = None,
        items: Optional[list[dict]] = None,
        fields: Optional[dict] = None,
        filters: Optional[dict] = None,
        max_results: int = 100,
        add_to_groups: Optional[list[str]] = None,
        remove_from_groups: Optional[list[str]] = None,
        group_id: Optional[str] = None,
        object_type: str = "deals",
        dry_run: bool = False,
    ) -> dict:
        """A Folk CRM record — search, read, create, update, delete, add to a group.

        `entity` scopes every op (person | company | deal | note | interaction |
        reminder); `op` picks the verb:

        - **"search"** (default): search records of that entity. Fetches ALL
          matching pages — always pass `filters` on a large workspace. Works on
          person, company, deal, note and reminder.
        - **"get"**: fetch one record by ID (full record). person, company, deal
          and reminder only — Folk has NO get-by-id endpoint for notes.
        - **"create"**: create one (`item`) or several (`items`, ≤50) records.
        - **"update"**: PATCH one (`id`) or several (`items`, ≤50) records —
          only the given fields change.
        - **"delete"**: delete one (`id`) or several (`ids`, ≤50) records.
          Irreversible.
        - **"add_to_group"**: add one (`id`) or several (`ids`, ≤50) existing
          people/companies to ONE group (`group_id` = the target group). The
          inverse of `op="update"`'s `add_to_groups` (which batches *groups* for
          *one* record) — this batches *records* into *one* group. Reads each
          record's current groups and writes back the union (Folk's `groups`
          field is replace-all on PATCH), so existing group membership is
          preserved. A record already in the group is a no-op success, not an
          error.

        The four write ops are **solo OR bulk depending on which param you
        pass**: exactly one of `item`/`items` (create), `id`/`items` (update),
        `id`/`ids` (delete, add_to_group) is required. Solo returns the record
        (or its dry_run preview) directly; bulk returns a receipt (count +
        per-item errors), never N full response bodies.

        ⚠️ **Two different field vocabularies coexist.** `op="create"` field
        names are Python **snake_case** parameter names (`first_name`,
        `company_id`...), forwarded directly to the client — NOT Folk's raw
        camelCase API field vocabulary (`jobTitle`, `customFieldValues`...) that
        `op="update"`'s `fields` uses. An unrecognized create field name raises
        immediately (listing the accepted ones), it is never silently dropped or
        sent under the wrong name. Don't mix the two conventions.

        Per-entity field shape for op="create" (same for `item` and each entry
        of `items`, `*` = required, snake_case — see the warning above):
            person: {first_name*, last_name, emails, phones, job_title,
                company_name, company_id, group_ids, urls, description}
            company: {name*, emails, industry}
            deal: {name*, people_ids, company_ids, custom_fields}
            note: {entity_id*, content*, visibility}
            interaction: {entity_id*, type*, title*, content, date_time}
            reminder: {entity_id*, name*, recurrence_rule*, visibility}

        Args:
            entity: "person", "company", "deal", "note", "interaction" or
                "reminder" — see each op for the ones it accepts (interactions
                have no update/delete endpoint in Folk, notes have no get-by-id,
                add_to_group is person/company only).
            op: search (default) | get | create | update | delete | add_to_group.
            id: the record ID (the deal_id for a deal, rmd_… for a reminder) —
                op="get", and solo mode of update/delete/add_to_group.
            ids: record IDs — bulk mode of delete/add_to_group (deal IDs for
                entity="deal").
            item: op="create" solo — fields for ONE record, see the per-entity
                shape below.
            items: op="create" bulk — fields for MULTIPLE records, same shape as
                `item`, one dict per record. op="update" bulk — one
                `{"id", "fields", "add_to_groups", "remove_from_groups"}` per
                record, same field vocabulary as below.
            fields: op="update" solo — Folk API field names, camelCase (e.g.
                {"jobTitle": "CTO"}, {"industry": "SaaS"}, ou champs custom d'un
                deal). Optionnel si seuls `add_to_groups`/`remove_from_groups`
                sont fournis.
                **Champs CUSTOM d'une person/company** (ex. Status d'un groupe) :
                les passer SOUS `customFieldValues`, keyés par group_id —
                `{"customFieldValues": {"<group_id>": {"Status": "Follow-up"}}}`.
                Un champ custom passé à plat (`{"Status": …}`) est rejeté (422
                "Unrecognized key"). La structure se découvre via op="search"
                (customFieldValues groupée par group_id).
            filters: op="search" — Field → value, matched with `like` (e.g.
                {"fullName": "Dupont", "emails": "@otomata.tech"} for people,
                {"name": "Otomata"} for companies). For another operator, pass
                {field: {op: value}} — op ∈ eq, not_eq, like, not_like, empty,
                not_empty, gt (dates), in / not_in (relations). For `note` and
                `reminder`, Folk only has ONE filter: {"entity_id": "<id>"} (the
                person/company/deal the note or reminder hangs off).
            max_results: op="search" — truncate the response (default 100).
                `count` reports the REAL total, so a `count` above the number of
                `results` means the list was cut.
            add_to_groups: op="update" — rattacher une **person** ou **company**
                À des groupes (`folk_group` pour les IDs), sans toucher ses
                autres groupes — solo mode only.
            remove_from_groups: op="update" — détacher une **person** ou
                **company** DE des groupes, sans toucher ses autres groupes —
                solo mode only.
            group_id: the group concerned by the call, meaning set by op/entity —
                op="search" on `person`/`company`: LIST THE MEMBERS of that group
                (get its id from `folk_group`) — e.g. audit the "Leads" pipeline;
                op="add_to_group": the TARGET group the record(s) join;
                REQUIRED for `entity="deal"` on every other op (the group where
                the deal lives — on create, all record(s) land in this one group,
                Folk deals aren't creatable across groups in a single call). Ne
                PAS le passer pour person/company hors des deux cas ci-dessus.
            object_type: custom-object collection name (default "deals") —
                `deal` only, i.e. the deals OR any other custom object
                collection of the group.
            dry_run: write ops only — n'écrit RIEN. create: preview
                `would_create`, zéro appel réseau. update / add_to_group : relit
                l'état courant et renvoie un diff `{"changes": {field: {"from",
                "to"}}}` (solo) ou `would_update`/`would_add` (bulk). delete :
                relit chaque record et renvoie `would_delete` (le record actuel),
                pour vérifier ce qui serait détruit avant de le faire. Pour
                `entity="note"` (pas de get-par-id côté Folk), dégrade en
                `{"fields": ..., "current_available": False}` (update) ou un
                record `None` + `"current_available": False` (delete) — aperçu
                sans le "from".

        Returns:
            search: {"entity", "count", "results"}.
            get: the record.
            create solo: the created record, or {"dry_run": true, "would_create": {...}}.
            create bulk: {"total", "succeeded", "created": [{"index","id"}],
                "failed": [...]}, or dry_run: {"dry_run": true, "total",
                "would_create": [...], "failed": [...]}.
            update/add_to_group solo: the updated record, or {"dry_run": true,
                "id", "changes"|"fields", ...}.
            update/add_to_group bulk: {"total", "succeeded", "failed":
                [{"index","id","error"}]}, or dry_run: {"dry_run": true, "total",
                "would_update"|"would_add": [...], "failed": [...]}.
            delete solo: {} (or {"dry_run": true, "id", "would_delete", ...}).
            delete bulk: {"total", "succeeded", "failed": [{"index","id","error"}]},
                or dry_run: {"dry_run": true, "total", "would_delete": [...],
                "failed": [...]}.
        """
        if op == "search":
            if entity not in _SEARCH_ENTITIES:
                raise _bad(f"op='search' : entity doit être l'un de {_SEARCH_ENTITIES}.")
            f = dict(filters or {})
            if entity in ("note", "reminder"):
                unknown = set(f) - _SUBRECORD_FILTERS
                if unknown:
                    raise _bad(
                        f"op='search' entity='{entity}' : filtre(s) inconnu(s) "
                        f"{sorted(unknown)} — Folk n'expose que "
                        f"{sorted(_SUBRECORD_FILTERS)} sur les notes/rappels.")
                if group_id:
                    raise _bad(
                        f"op='search' entity='{entity}' : Folk ne filtre pas les "
                        "notes/rappels par groupe — passer "
                        "filters={'entity_id': '<person/company/deal id>'}.")
            if entity == "deal" and not group_id:
                raise _bad("group_id requis pour entity='deal'.")
            if entity in _GROUP_ENTITIES and group_id:
                # Appartenance à un groupe : le client traduit en filter[groups][in][id].
                f["groups"] = group_id
            c = _client()
            if entity == "person":
                found = c.list_people(**f)
            elif entity == "company":
                found = c.list_companies(**f)
            elif entity == "deal":
                found = c.list_deals(group_id, object_type=object_type, **f)
            elif entity == "note":
                found = c.list_notes(**f)
            else:
                found = c.list_reminders(**f)
            return {"entity": entity, "count": len(found),
                    "results": found[:max_results]}

        if op == "get":
            if entity not in _GET_ENTITIES:
                raise _bad(
                    f"op='get' : entity doit être l'un de {_GET_ENTITIES} — Folk "
                    "n'a pas d'endpoint get-par-id pour les notes (les lister : "
                    "op='search', entity='note').")
            _need(id, "id", op)
            if entity == "deal" and not group_id:
                raise _bad("group_id requis pour entity='deal'.")
            return _get_one(_client(), entity, id, group_id=group_id,
                            object_type=object_type)

        if op == "create":
            if (item is None) == (items is None):
                raise _bad("op='create' : fournir soit `item` (un seul record) soit "
                           "`items` (plusieurs) — pas les deux, pas ni l'un ni l'autre.")
            if entity not in _CREATE_ENTITIES:
                raise _bad(f"op='create' : entity doit être l'un de {_CREATE_ENTITIES}.")
            if entity == "deal" and not group_id:
                raise _bad("group_id requis pour entity='deal'.")
            c = _client()
            if item is not None:
                result = _create_one(c, entity, item, group_id=group_id,
                                     object_type=object_type, dry_run=dry_run)
                return {"dry_run": True, **result} if dry_run else result
            results = _bulk_run(
                items, lambda it: _create_one(c, entity, it, group_id=group_id,
                                              object_type=object_type,
                                              dry_run=dry_run))
            failed = [{"index": i, "error": val} for i, ok, val in results if not ok]
            if dry_run:
                would_create = [{"index": i, **val} for i, ok, val in results if ok]
                return {"dry_run": True, "total": len(items),
                        "would_create": would_create, "failed": failed}
            created = [{"index": i, "id": val.get("id")} for i, ok, val in results if ok]
            return {"total": len(items), "succeeded": len(created),
                    "created": created, "failed": failed}

        if op == "update":
            if (id is None) == (items is None):
                raise _bad("op='update' : fournir soit `id` (+ fields/add_to_groups/"
                           "remove_from_groups) pour UN record, soit `items` pour "
                           "plusieurs — pas les deux, pas ni l'un ni l'autre.")
            if entity not in _UPDATE_ENTITIES:
                raise _bad(f"op='update' : entity doit être l'un de {_UPDATE_ENTITIES} "
                           "(interactions have no update endpoint in Folk).")
            if entity == "deal" and not group_id:
                raise _bad("group_id requis pour entity='deal'.")
            c = _client()
            if id is not None:
                result = _update_one(
                    c, entity, id, fields=fields, group_id=group_id,
                    object_type=object_type, add_to_groups=add_to_groups,
                    remove_from_groups=remove_from_groups, dry_run=dry_run)
                return {"dry_run": True, **result} if dry_run else result

            def _one(it):
                if "id" not in it:
                    raise _bad("chaque item doit contenir 'id'.")
                return _update_one(
                    c, entity, it["id"], fields=it.get("fields"),
                    group_id=group_id, object_type=object_type,
                    add_to_groups=it.get("add_to_groups"),
                    remove_from_groups=it.get("remove_from_groups"),
                    dry_run=dry_run)

            results = _bulk_run(items, _one)
            failed = [{"index": i, "id": items[i].get("id"), "error": val}
                      for i, ok, val in results if not ok]
            if dry_run:
                would_update = [{"index": i, **val} for i, ok, val in results if ok]
                return {"dry_run": True, "total": len(items),
                        "would_update": would_update, "failed": failed}
            return {"total": len(items), "succeeded": len(items) - len(failed),
                    "failed": failed}

        if op == "delete":
            if (id is None) == (ids is None):
                raise _bad("op='delete' : fournir soit `id` (un seul record) soit "
                           "`ids` (plusieurs) — pas les deux, pas ni l'un ni l'autre.")
            if entity not in _DELETE_ENTITIES:
                raise _bad(f"op='delete' : entity doit être l'un de {_DELETE_ENTITIES} "
                           "(interactions have no delete endpoint in Folk).")
            if entity == "deal" and not group_id:
                raise _bad("group_id requis pour entity='deal'.")
            c = _client()
            if id is not None:
                result = _delete_one(c, entity, id, group_id=group_id,
                                     object_type=object_type, dry_run=dry_run)
                return {"dry_run": True, **result} if dry_run else result
            results = _bulk_run(
                ids, lambda rid: _delete_one(c, entity, rid, group_id=group_id,
                                             object_type=object_type, dry_run=dry_run))
            failed = [{"index": i, "id": ids[i], "error": val}
                      for i, ok, val in results if not ok]
            if dry_run:
                would_delete = [{"index": i, **val} for i, ok, val in results if ok]
                return {"dry_run": True, "total": len(ids),
                        "would_delete": would_delete, "failed": failed}
            return {"total": len(ids), "succeeded": len(ids) - len(failed),
                    "failed": failed}

        if op == "add_to_group":
            # Écrit par `_update_one(add_to_groups=[group_id])` : c'est lui qui
            # relit les groupes actuels et réécrit l'union (`groups` est
            # replace-all sur un PATCH Folk). Le contrat rendu à l'appelant est
            # dans le docstring — ici on ne fait que valider et router.
            _need(group_id, "group_id", op)
            if (id is None) == (ids is None):
                raise _bad("op='add_to_group' : fournir soit `id` (un seul record) "
                           "soit `ids` (plusieurs) — pas les deux, pas ni l'un ni "
                           "l'autre.")
            if entity not in _GROUP_ENTITIES:
                raise _bad(f"op='add_to_group' : entity doit être l'un de "
                           f"{_GROUP_ENTITIES}.")
            c = _client()
            if id is not None:
                result = _update_one(c, entity, id, add_to_groups=[group_id],
                                     dry_run=dry_run)
                return {"dry_run": True, **result} if dry_run else result
            results = _bulk_run(
                ids, lambda rid: _update_one(c, entity, rid, add_to_groups=[group_id],
                                             dry_run=dry_run))
            failed = [{"index": i, "id": ids[i], "error": val}
                      for i, ok, val in results if not ok]
            if dry_run:
                would_add = [{"index": i, **val} for i, ok, val in results if ok]
                return {"dry_run": True, "total": len(ids), "would_add": would_add,
                        "failed": failed}
            return {"total": len(ids), "succeeded": len(ids) - len(failed),
                    "failed": failed}

        raise _bad("op doit être 'search', 'get', 'create', 'update', 'delete' "
                   "ou 'add_to_group'")

    # --- groups (lecture seule côté API Folk) --------------------------------

    @mcp.tool()
    def folk_group(
        op: Literal["list", "custom_fields"] = "list",
        group_id: Optional[str] = None,
        entity_type: str = "person",
    ) -> dict:
        """A Folk group (a folder of people/companies/deals) — list them, or read
        the custom fields defined on one.

        `op`:
        - **"list"** (default): list all groups in the Folk workspace.
        - **"custom_fields"**: list the custom fields defined on a group for an
          entity type (`group_id` + `entity_type`).

        Note: the Folk API is read-only on groups — there is no endpoint to
        create one. A new group must be created by the user in the Folk app,
        then referenced here by its ID.

        Args:
            op: list (default) | custom_fields.
            group_id: op="custom_fields" — Folk group ID.
            entity_type: op="custom_fields" — "person" or "company".
        """
        if op == "list":
            return {"groups": _client().list_groups()}
        if op == "custom_fields":
            _need(group_id, "group_id", op)
            return {"custom_fields": _client().get_group_custom_fields(
                group_id, entity_type)}
        raise _bad("op doit être 'list' ou 'custom_fields'")

    # --- users (membres du workspace, lecture seule) ------------------------

    @mcp.tool()
    def folk_user(op: Literal["list", "get"] = "list", user_id: str = "me") -> dict:
        """A Folk workspace user (member) — list them, or fetch one.

        `op`:
        - **"list"** (default): list the workspace users (members) — useful to
          resolve owners/assignees.
        - **"get"**: fetch a workspace user by ID. `user_id="me"` (default)
          returns the authenticated user — call it to attribute an action to the
          current user.

        Args:
            op: list (default) | get.
            user_id: op="get" — the user ID, or "me" (default).
        """
        if op == "list":
            return {"users": _client().list_users()}
        if op == "get":
            return _client().get_user(user_id)
        raise _bad("op doit être 'list' ou 'get'")

    # --- webhooks -------------------------------------------------------------
    #
    # Ressource globale (pas d'`entity`, pas de group_id/object_type, pas de
    # mode bulk — un workspace en a peu). `dry_run` suit la même convention que
    # `folk_record` (preview `would_create` en création, diff `changes` en
    # update, aucun appel réseau mutant).

    @mcp.tool()
    def folk_webhook(
        op: Literal["list", "create", "update"] = "list",
        webhook_id: Optional[str] = None,
        name: Optional[str] = None,
        target_url: Optional[str] = None,
        subscribed_events: Optional[list[dict]] = None,
        fields: Optional[dict] = None,
        dry_run: bool = False,
    ) -> dict:
        """A Folk webhook — list, create, update. Folk POSTs an event payload to
        `target_url` each time one of the subscribed events fires.

        `op`:
        - **"list"** (default): list all webhooks configured on this Folk
          workspace: target URL, status, and which events/filters each one
          subscribes to.
        - **"create"**: create a webhook (`name` + `target_url` +
          `subscribed_events`).
        - **"update"**: PATCH a webhook (`webhook_id` + `fields`) — only the
          given fields change.

        Before creating with a filter, call `folk_group` (op="list" for
        `groupId`, op="custom_fields" for the custom field name used in `path`)
        to get real workspace values — don't guess them.

        Args:
            op: list (default) | create | update.
            webhook_id: op="update" — the webhook ID (wbk_…, from op="list").
            name: op="create" — friendly name (max 255 chars).
            target_url: op="create" — public HTTPS URL that will receive the
                event (max 2048 chars).
            subscribed_events: op="create" — 1-20 items, each
                `{"eventType": ..., "filter": {...}}`.
                eventType — one per entity, by lifecycle:
                  person: created, updated, deleted, groups_updated,
                    workspace_interaction_metadata_updated
                  company: created, updated, deleted, groups_updated
                  object (deals AND any custom object_type): created, updated, deleted
                  note: created, updated, deleted
                  reminder: created, updated, deleted, triggered
                (full values are "person.created", "object.updated", etc.)
                filter (optional, all keys optional):
                  groupId — only for entities in this group (`folk_group`).
                    For object.* this is a sibling of `path`, never repeated
                    inside it.
                  objectType — for object.* events, scope to one collection
                    (e.g. "Deals" vs a custom object_type — Folk's own example
                    uses the display name, NOT necessarily the lowercase slug
                    accepted by `folk_record(entity="deal", object_type=...)`;
                    unconfirmed against a live workspace, verify before relying
                    on it).
                  path + value — for *.updated events, fire only when the
                    attribute at `path` changes to `value`. `path` covers both
                    plain attributes and custom fields, and its shape differs
                    by entity:
                      plain attribute (any entity): `["firstName"]`, `["name"]`
                      person/company custom field:
                        `["customFieldValues", groupId, fieldName]` (3 segments
                        — the group id is repeated here, inside the path)
                      object/deal custom field:
                        `["customFieldValues", fieldName]` (2 segments — no
                        group id in path; use the sibling `filter.groupId`
                        instead)
                    fieldName is the field's `name` from
                    `folk_group(op="custom_fields")` (custom fields have no
                    separate id — `name` IS the identifier Folk matches on).
            fields: op="update" — Folk's raw API field names, camelCase — same
                vocabulary caveat as `folk_record(op="update")`: name, targetUrl,
                subscribedEvents (REPLACES the full list, not a merge/add — call
                op="list" first and resend the existing entries you want to
                keep), status ("active"|"inactive" — pause without deleting).
                Same eventType/filter shape as op="create".
            dry_run: if true, writes nothing — op="create" returns a preview
                (`would_create`), zero network calls; op="update" returns a diff
                `{"changes": {field: {"from", "to"}}}` against the current
                webhook.

        Note: on create, the response's `signingSecret` is returned in FULL only
        there — Folk only ever shows a redacted version afterwards, so save it
        now if you need to verify payload signatures.

        Note: filters only exist through this API — editing a webhook's events
        from Folk's own settings UI afterwards silently drops them.
        """
        if op == "list":
            return {"webhooks": _client().list_webhooks()}

        if op == "create":
            _need(name, "name", op)
            _need(target_url, "target_url", op)
            _need(subscribed_events, "subscribed_events", op)
            _validate_subscribed_events(subscribed_events)
            if dry_run:
                return {"dry_run": True, "would_create": {
                    "name": name, "targetUrl": target_url,
                    "subscribedEvents": subscribed_events,
                }}
            return _client().create_webhook(name, target_url, subscribed_events)

        if op == "update":
            _need(webhook_id, "webhook_id", op)
            if not fields:
                raise _bad("op='update' requiert fields : au moins un champ à mettre "
                           "à jour (name, targetUrl, subscribedEvents, status).")
            if "subscribedEvents" in fields:
                _validate_subscribed_events(fields["subscribedEvents"])
            c = _client()
            if dry_run:
                current = c.get_webhook(webhook_id)
                return {"dry_run": True, "id": webhook_id,
                        "changes": {k: {"from": current.get(k), "to": v}
                                    for k, v in fields.items()}}
            return c.update_webhook(webhook_id, **fields)

        raise _bad("op doit être 'list', 'create' ou 'update'")
