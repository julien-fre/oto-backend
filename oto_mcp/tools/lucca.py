"""Lucca — RH FR (lecture) : annuaire, absences, notes de frais, organisation.

Credential = clé API statique + sous-domaine de tenant, deux secrets. Résolu
par appel via `access.resolve_credential_fields("lucca")` — modèle générique
multi-champs (ADR 0011), même famille que silae. byo_user : chaque
cabinet/employeur pose sa propre clé Lucca ; ses données ne sont visibles que
par lui.

**Surface consolidée (ADR 0047 §Amendement)** : un tool par OBJET métier, le
verbe en paramètre `op` — `lucca_employee` (list/get), `lucca_absence`
(list/get, `date` REQUIS par Lucca sur list), `lucca_leave_request`
(list/get), `lucca_expense_claim` (list SEUL — Lucca n'expose aucun détail
par id sur cette ressource, cf. `oto.tools.lucca.LuccaClient`), `lucca_department`
(list/get) et `lucca_establishment` (list SEUL, base URL et pagination
différentes des cinq autres — cf. le client).

⚠️ **Lecture seule** : le client oto-core ne porte AUCUNE écriture pour Lucca —
rien à omettre ici, la frontière est déjà celle du client. La symétrie 1:1
ci-dessus (un tool par ressource, aucun arbitrage sur ce qu'on expose) vaut
TANT QUE le client amont reste en lecture seule : une première méthode
d'écriture (poser un congé, valider une note de frais) rouvre la question de
ce qu'on expose — la même symétrie ferait alors apparaître un tool d'écriture
par défaut, ce qui n'est pas du même ordre qu'une lecture.

⚠️ **Aucune politique de rédaction posée par défaut.** Le masquage de champs
(IBAN, numéro de sécu, nom…) est disponible à la frontière des tools
(`FieldRedactionMiddleware`, politique résolue par NAMESPACE `lucca` —
insensible au nom des tools), mais `field_filter_defaults.SERVER_DEFAULTS` ne
porte rien pour `lucca` : rien n'est redacté tant que l'org n'a pas posé sa
propre politique. L'annuaire (`lucca_employee`) et les notes de frais
(`lucca_expense_claim`) sont les deux surfaces les plus susceptibles de
porter des données personnelles.
"""
from __future__ import annotations

from typing import Literal, Optional

from .. import output_projection

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, egress
from ..connectors import verify as connector_verify


def _base_url(domain: str) -> str:
    """L'URL que le client construira lui-même — recalculée ICI pour que la garde
    d'egress (`oto_mcp/egress.py`) juge la VRAIE destination avant tout octet
    réseau. `domain` vient du credential d'une org : c'est exactement le cas
    qu'un huitième connecteur à hôte libre doit garder (`tests/test_egress_guard.py`)."""
    return f"https://{domain}.ilucca.net"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable, jamais de fallback."""
    if value is None or value == "":
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention,
    pas un détail — même raison que silae/ahrefs : un silence rendrait un
    résultat plausible mais à côté de la demande."""
    for name, value in provided.items():
        if value is not None and value != "":
            raise _bad(f"op='{op}' n'utilise pas {name} — {hint}")


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return f"Lucca : accès refusé (HTTP {status}) — clé API ou sous-domaine invalide."
    if status == 429:
        return "Lucca : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"Lucca est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Lucca a refusé la requête (HTTP {status}) : {e.body}"


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » (otomata-tech/oto#69).

    `list_departments()` : le plus petit appel du client sans paramètre
    requis — contrairement à `list_leaves` (exige `date`) ou
    `list_establishments` (base URL différente, pourrait être hors périmètre
    de la clé sans que ce soit un défaut d'auth). Une liste VIDE est un état
    normal (compte fraîchement créé), jamais un refus."""
    from oto.tools.lucca import LuccaClient
    from oto.tools.common.errors import UpstreamHTTPError

    egress.check_url(_base_url(fields["domain"]), connector="lucca")
    try:
        LuccaClient(api_key=fields["api_key"], domain=fields["domain"]).list_departments()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Lucca HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Lucca: {e.body}")


def register(mcp: FastMCP) -> None:
    from oto.tools.lucca import LuccaClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("lucca", _verify)

    def _client() -> LuccaClient:
        creds = access.resolve_credential_fields("lucca")
        # Pas de `field_filter` explicite : le défaut du client lit un fichier
        # YAML LOCAL (~/.otomata/config.yaml) qui n'existe pas sur le serveur,
        # donc no-op — la rédaction pour le backend passe par
        # `FieldRedactionMiddleware` (cf. docstring de module), pas par ici.
        egress.check_url(_base_url(creds.get("domain") or ""), connector="lucca")
        return LuccaClient(api_key=creds.get("api_key"), domain=creds.get("domain"))

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- Annuaire (directory) ---

    @mcp.tool()
    def lucca_employee(
        op: Literal["list", "get"] = "list",
        user_id: Optional[str] = None,
        fields: Optional[str] = None,
        mail: Optional[str] = None,
        login: Optional[str] = None,
        former_employees: Optional[bool] = None,
        offset: int = 0,
        limit: int = 1000,
    ) -> dict:
        """An employee of the Lucca directory — the roster, or one employee.

        `op`:
        - **"list"** (default): employees reachable with the API key.
        - **"get"**: one employee by id (`user_id`).

        Args:
            op: list (default) | get.
            user_id: op="get" only — the employee's Lucca id.
            fields: OData-style field selection (both ops), e.g.
                "id,firstName,lastName,legalEntity[id,name]".
            mail: op="list" only — exact match, or "like,..." for partial match.
            login: op="list" only — exact match.
            former_employees: op="list" only — include former employees
                (default: current only).
            offset / limit: op="list" only — pagination (Lucca caps limit at 1000).
        """
        client = _client()

        if op == "list":
            rows = _run(lambda: client.list_users(
                offset=offset, limit=limit, fields=fields, mail=mail, login=login,
                former_employees=former_employees))
            return {"employees": rows}
        if op == "get":
            _refuse_ignored(op, "n'existe que sur op='list'",
                            mail=mail, login=login, former_employees=former_employees)
            row = _run(lambda: client.get_user(
                _need(user_id, "user_id", op), fields=fields))
            return {"employee": row}
        raise _bad("op doit être 'list' ou 'get'")

    # --- Absences (Timmi Absences) ---

    @mcp.tool()
    def lucca_absence(
        op: Literal["list", "get"] = "list",
        leave_id: Optional[str] = None,
        date: Optional[str] = None,
        owner_id: Optional[list] = None,
        department_id: Optional[list] = None,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[list] = None,
    ) -> dict:
        """A posted absence (half-day leave record) — the list, or one record.

        `op`:
        - **"list"** (default): leaves in a date range. `date` is REQUIRED by
          Lucca — there is no unfiltered "all leaves". `comment` (Lucca's free-text
          field on a leave, no documented length limit) is DROPPED by default —
          `comment_length` says how much was cut. Pass `fields=["*"]` for the raw
          record, or name the fields you want (`comment` included) to get exactly
          those.
        - **"get"**: one leave by id (`leave_id`) — always the full record.

        Args:
            op: list (default) | get.
            leave_id: op="get" only — the leave's Lucca id.
            date: op="list" only, REQUIRED — "yyyy-mm-dd" (exact day),
                "since,yyyy-mm-dd", "until,yyyy-mm-dd", or
                "between,yyyy-mm-dd,yyyy-mm-dd".
            owner_id: op="list" only — filter by employee id(s).
            department_id: op="list" only — filter by department id(s).
            offset / limit: op="list" only — pagination (Lucca caps limit at 1000).
            fields: op="list" only — omit for the trimmed view (no `comment`);
                `["*"]` for the raw record; a list of names for exactly those.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "n'existe que sur op='get'", leave_id=leave_id)
            rows = _run(lambda: client.list_leaves(
                _need(date, "date", op), offset=offset, limit=limit,
                owner_id=owner_id, department_id=department_id))
            rows, notice = output_projection.summarize(
                rows, body_fields=("comment",), fields=fields, always=("id",))
            return {"leaves": rows, **({"projection": notice} if notice else {})}
        if op == "get":
            _refuse_ignored(op, "n'existe que sur op='list'",
                            date=date, owner_id=owner_id, department_id=department_id)
            row = _run(lambda: client.get_leave(_need(leave_id, "leave_id", op)))
            return {"leave": row}
        raise _bad("op doit être 'list' ou 'get'")

    # --- Demandes de congé (le workflow d'approbation, distinct des absences) ---

    @mcp.tool()
    def lucca_leave_request(
        op: Literal["list", "get"] = "list",
        leave_request_id: Optional[str] = None,
    ) -> dict:
        """A leave REQUEST — the workflow object behind an absence, distinct
        from the posted leave record itself (`lucca_absence`).

        `op`:
        - **"list"** (default): all leave requests. ⚠️ Lucca's own OpenAPI spec
          documents NO query parameters on this endpoint at all — no paging,
          no filter (verified against developers.luccasoftware.com,
          2026-09-15). It always returns everything the API key reaches.
        - **"get"**: one leave request by id (`leave_request_id`).

        Args:
            op: list (default) | get.
            leave_request_id: op="get" only — the leave request's Lucca id.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "n'existe que sur op='get'",
                            leave_request_id=leave_request_id)
            rows = _run(lambda: client.list_leave_requests())
            return {"leave_requests": rows}
        if op == "get":
            row = _run(lambda: client.get_leave_request(
                _need(leave_request_id, "leave_request_id", op)))
            return {"leave_request": row}
        raise _bad("op doit être 'list' ou 'get'")

    # --- Notes de frais (Cleemy Expenses) ---

    @mcp.tool()
    def lucca_expense_claim(
        owner_id: Optional[list] = None,
        status_id: Optional[str] = None,
        declared_on: Optional[str] = None,
        order_by: Optional[str] = None,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[list] = None,
    ) -> dict:
        """Expense claims (notes de frais). List only — Lucca's legacy v3 API
        documents no detail-by-id endpoint for this resource (verified
        2026-09-15): adding a "get" op would promise something Lucca doesn't have.

        ⚠️ Unlike `lucca_absence`, this resource's schema (verified against
        Lucca's own OpenAPI spec, 2026-09-15) has NO free-text field — only ids,
        dates, enums and a `name` capped at 255 chars. `fields` is offered for
        symmetry and to let a caller restrict to exactly the columns it needs,
        but nothing is dropped by default: there is no verbose field to cut.

        Args:
            owner_id: Filter by employee id(s).
            status_id: Numeric id (1-9) or name — Created, PartiallyApproved,
                Approved, Controlled, ApprovedAndControlled, PaymentInitiated,
                Paid, Refused, Cancelled.
            declared_on: `"{comparator},{date}"`, e.g. "between,2026-01-01,2026-01-31".
            order_by: `"{field},{'asc'|'desc'}"`, e.g. "declaredOn,desc".
            offset / limit: pagination (Lucca caps limit at 1000).
            fields: restrict to exactly these fields (no default reduction — see above).
        """
        rows = _run(lambda: _client().list_expense_claims(
            offset=offset, limit=limit, owner_id=owner_id, status_id=status_id,
            declared_on=declared_on, order_by=order_by))
        rows, notice = output_projection.summarize(
            rows, body_fields=(), fields=fields, always=("id",))
        return {"expense_claims": rows, **({"projection": notice} if notice else {})}

    # --- Organisation : départements ---

    @mcp.tool()
    def lucca_department(
        op: Literal["list", "get"] = "list",
        department_id: Optional[str] = None,
        head_id: Optional[int] = None,
        parent_id: Optional[int] = None,
        offset: int = 0,
        limit: int = 1000,
        fields: Optional[list] = None,
    ) -> dict:
        """A department of the org chart — the list, or one department.

        `op`:
        - **"list"** (default): departments reachable with the API key. The
          member rosters Lucca embeds on each department (`users`,
          `currentUsers` — every employee of that department, nested in full)
          are DROPPED by default — `users_length`/`currentUsers_length` say
          how many were cut. Pass `fields=["*"]` for the raw record (rosters
          included), or name the fields you want.
        - **"get"**: one department by id (`department_id`) — always the full
          record, rosters included.

        Args:
            op: list (default) | get.
            department_id: op="get" only — the department's Lucca id.
            head_id: op="list" only — filter by department head's employee id.
            parent_id: op="list" only — filter by parent department id.
            offset / limit: op="list" only — pagination (Lucca caps limit at 1000).
            fields: op="list" only — omit for the trimmed view (no rosters);
                `["*"]` for the raw record; a list of names for exactly those.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "n'existe que sur op='get'", department_id=department_id)
            rows = _run(lambda: client.list_departments(
                offset=offset, limit=limit, head_id=head_id, parent_id=parent_id))
            rows, notice = output_projection.summarize(
                rows, body_fields=("users", "currentUsers"), fields=fields,
                always=("id", "name"))
            return {"departments": rows, **({"projection": notice} if notice else {})}
        if op == "get":
            _refuse_ignored(op, "n'existe que sur op='list'",
                            head_id=head_id, parent_id=parent_id)
            row = _run(lambda: client.get_department(
                _need(department_id, "department_id", op)))
            return {"department": row}
        raise _bad("op doit être 'list' ou 'get'")

    # --- Organisation : établissements ---

    @mcp.tool()
    def lucca_establishment(
        ids: Optional[list] = None,
        legal_unit_id: Optional[list] = None,
        search: Optional[str] = None,
        is_archived: Optional[bool] = None,
        page: int = 1,
        limit: int = 10,
        fields: Optional[list] = None,
    ) -> dict:
        """Establishments (legal entities / sites). List only — Lucca documents
        no detail-by-id endpoint for this resource (verified 2026-09-15).

        ⚠️ Different from the other tools of this module: this endpoint lives
        under a different base path (organization structure API, not
        `/api/v3/...`) and paginates by PAGE (1-indexed, default size 10),
        not by offset.

        The nested `legalUnit` object (the establishment's legal entity, with
        its own id/name/code/activity code…) is DROPPED by default —
        `legalUnit_length` says how much was cut, and `legalUnitId` is kept so
        you can still address it. Pass `fields=["*"]` for the raw record.

        Args:
            ids: Establishment id(s).
            legal_unit_id: Legal unit id(s).
            search: Name search.
            is_archived: Filter archived / active establishments (omitted =
                Lucca's own default).
            page / limit: 1-indexed pagination — Lucca's default page size
                for this endpoint is 10, not 1000 like the rest of the client.
            fields: omit for the trimmed view (no `legalUnit`); `["*"]` for
                the raw record; a list of names for exactly those.
        """
        rows = _run(lambda: _client().list_establishments(
            page=page, limit=limit, ids=ids, legal_unit_id=legal_unit_id,
            search=search, is_archived=is_archived))
        rows, notice = output_projection.summarize(
            rows, body_fields=("legalUnit",), fields=fields,
            always=("id", "name", "legalUnitId"))
        return {"establishments": rows, **({"projection": notice} if notice else {})}
