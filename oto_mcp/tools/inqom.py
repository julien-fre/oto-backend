"""Inqom — production comptable FR : dossiers, référentiels, balance, écritures, pièces.

Credential = clés d'application (`client_id`/`client_secret`) + identifiants du
compte Inqom au nom duquel le jeton agit (`username`/`password`), résolus par
appel via `access.resolve_credential_fields("inqom")` (ADR 0011). Le jeton porte
les droits de ce compte : ce qu'il ne voit pas, aucun outil ne le voit.

**Surface (ADR 0047 §Amendement)** :
- `inqom_company` — découverte, sans paramètre : les cabinets/PME accessibles ;
- `inqom_dossier` (list/get) — les dossiers d'un cabinet, la fiche d'un dossier ;
- `inqom_ref` (kind=accounts|journals|periods) — référentiels d'un dossier ;
- `inqom_balance` — balance sur une période ;
- `inqom_entry_line` (list/count) — lignes d'écriture paginées ;
- `inqom_entry_create` — ÉCRITURE, laissée seule : ses paramètres (`entries`,
  `dry_run`) sont disjoints de ceux de la lecture, et le nom porte le geste ;
- `inqom_document` — l'URL de téléchargement d'une pièce.

⚠️ **L'écriture comptable n'a pas de brouillon côté Inqom** : l'endpoint pose
immédiatement. D'où `dry_run=True` PAR DÉFAUT sur `inqom_entry_create` — l'aperçu
contrôle la forme et l'équilibre de chaque écriture et confronte ses journaux à
ceux du dossier, sans rien poser.

Hôte fixe (`api.inqom.com`) : aucun champ du credential ne désigne une
destination, donc pas de garde d'egress (`oto_mcp/egress.py`) à poser ici.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

_CHAMPS = ("client_id", "client_secret", "username", "password")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENTRIES_MAX = 100


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — erreur actionnable, jamais de repli."""
    if value is None or value == "":
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention.
    Testé sur `is not None` : `False` ou `0` fournis sont une intention aussi."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op='{op}' n'utilise pas {name} — {hint}")


def _champs(fields: dict) -> dict:
    """Les quatre champs, NON VIDES. Un champ vide passé au client retomberait sur
    la résolution de secrets locale (`require_secret`) au lieu d'être refusé."""
    vides = [n for n in _CHAMPS if not (fields.get(n) or "").strip()]
    if vides:
        raise ValueError(f"credential Inqom incomplet : {', '.join(vides)} vide(s)")
    return {n: fields[n] for n in _CHAMPS}


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Inqom : accès refusé (HTTP {status}) — clés d'application ou "
                "identifiants du compte invalides, ou droit qui manque à ce compte.")
    if status == 404:
        return "Inqom : introuvable (HTTP 404) — dossier ou ressource inexistant, ou hors des droits du compte."
    if status == 429:
        return "Inqom : trop de requêtes (429) — réessaie dans un instant."
    if status >= 500:
        return f"Inqom est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"Inqom a refusé la requête (HTTP {status}) : {str(e.body)[:400]}"


def _verify(fields: dict, config: dict | None = None) -> None:
    """Sonde « tester la connexion » : `list_companies()`, le plus petit appel
    authentifié sans paramètre. Une liste vide est un état possible (compte sans
    affectation), jamais un refus."""
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.inqom import InqomClient

    try:
        champs = _champs(fields)
    except ValueError as e:
        raise connector_verify.NonAutorise(str(e))
    try:
        InqomClient(**champs).list_companies()
    except UpstreamHTTPError as e:
        if e.status_code in (401, 403):
            raise connector_verify.NonAutorise(f"Inqom HTTP {e.status_code}: {e.body}")
        raise RuntimeError(f"Inqom HTTP {e.status_code}: {e.body}")


def _montant(value, where: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _bad(f"{where} : montant illisible {value!r}")
    if d < 0:
        raise _bad(f"{where} : montant négatif {value!r} — porter le sens par DebitAmount/CreditAmount")
    return d


def _controle_entries(entries) -> list[dict]:
    """Forme et équilibre de chaque écriture, AVANT tout appel. Rend un résumé
    par écriture (journal, date, lignes, totaux)."""
    if not isinstance(entries, list) or not entries:
        raise _bad("entries : une liste non vide d'écritures est requise")
    if len(entries) > _ENTRIES_MAX:
        raise _bad(f"entries : {_ENTRIES_MAX} écritures au plus par appel ({len(entries)} reçues)")
    resume = []
    for i, e in enumerate(entries):
        w = f"entries[{i}]"
        if not isinstance(e, dict):
            raise _bad(f"{w} : objet attendu")
        if e.get("JournalId") is None:
            raise _bad(f"{w} : JournalId requis")
        if not isinstance(e.get("Date"), str) or not _DATE.match(e["Date"]):
            raise _bad(f"{w} : Date requise au format yyyy-MM-dd")
        lines = e.get("Lines")
        if not isinstance(lines, list) or len(lines) < 2:
            raise _bad(f"{w} : Lines doit compter au moins deux lignes")
        debit = credit = Decimal(0)
        for j, ln in enumerate(lines):
            wl = f"{w}.Lines[{j}]"
            if not isinstance(ln, dict):
                raise _bad(f"{wl} : objet attendu")
            for req in ("AccountNumber", "Label", "Currency"):
                if not ln.get(req):
                    raise _bad(f"{wl} : {req} requis")
            d, c = ln.get("DebitAmount"), ln.get("CreditAmount")
            if (d is None) == (c is None):
                raise _bad(f"{wl} : exactement un de DebitAmount / CreditAmount")
            if d is not None:
                debit += _montant(d, wl)
            else:
                credit += _montant(c, wl)
        if debit != credit:
            raise _bad(f"{w} : écriture déséquilibrée — débit {debit} ≠ crédit {credit} "
                       f"(écart {debit - credit}) ; rien n'est posé")
        resume.append({"index": i, "JournalId": e["JournalId"], "Date": e["Date"],
                       "EntryRef": e.get("EntryRef"), "lines": len(lines),
                       "total_debit": str(debit), "total_credit": str(credit)})
    return resume


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.inqom import InqomClient

    connector_verify.register("inqom", _verify)

    def _client() -> InqomClient:
        try:
            champs = _champs(access.resolve_credential_fields("inqom"))
        except ValueError as e:
            raise _bad(str(e))
        return InqomClient(**champs)

    def _run(fn):
        try:
            return fn()
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))
        except ValueError as e:
            raise _bad(str(e))

    @mcp.tool()
    def inqom_company() -> dict:
        """The firms (cabinets) and SMEs the connected Inqom account can access —
        start here to get a `company_id`.

        For `AccessType == "Company"` the company id is `Id`; for
        `AccessType == "Pme"` it is `CompanyId`.
        """
        return {"companies": _run(lambda: _client().list_companies())}

    @mcp.tool()
    def inqom_dossier(
        op: Literal["list", "get"] = "list",
        company_id: Optional[int] = None,
        dossier_id: Optional[int] = None,
    ) -> dict:
        """An accounting dossier (one client company's books) — the list for a
        firm, or one dossier's general record.

        `op`:
        - **"list"** (default): the dossiers of `company_id` the account is
          assigned to (all of them for a firm's system account). Each carries
          `Id` (the `dossier_id` of every other tool), `Name`, `Siren`, `Status`.
        - **"get"**: the general section of a dossier's permanent file
          (identity, SIREN/SIRET, legal form, addresses, accounting settings).

        Args:
            op: list (default) | get.
            company_id: op="list" only, required — from `inqom_company`.
            dossier_id: op="get" only, required.
        """
        client = _client()
        if op == "list":
            _refuse_ignored(op, "n'existe que sur op='get'", dossier_id=dossier_id)
            cid = _need(company_id, "company_id", op)
            return {"dossiers": _run(lambda: client.list_dossiers(cid))}
        if op == "get":
            _refuse_ignored(op, "n'existe que sur op='list'", company_id=company_id)
            did = _need(dossier_id, "dossier_id", op)
            return {"dossier": _run(lambda: client.get_dossier(did))}
        raise _bad("op doit être 'list' ou 'get'")

    @mcp.tool()
    def inqom_ref(
        kind: Literal["accounts", "journals", "periods"],
        dossier_id: int,
        number_prefix: Optional[str] = None,
        account_type: Optional[Literal["All", "Impactable"]] = None,
    ) -> dict:
        """Reference data of a dossier.

        `kind`:
        - **"accounts"**: the chart of accounts. Third parties (tiers) are
          auxiliary accounts: filter with `number_prefix` ("401" suppliers,
          "411" customers).
        - **"journals"**: the journals, with the `Id` that entries need.
        - **"periods"**: the fiscal years (dates, `Locked`).

        Args:
            kind: accounts | journals | periods.
            dossier_id: the dossier (`inqom_dossier`).
            number_prefix: kind="accounts" only — keep numbers starting with it.
            account_type: kind="accounts" only — "Impactable" (postable only) or "All".
        """
        client = _client()
        if kind == "accounts":
            rows = _run(lambda: client.list_accounts(
                dossier_id, number_prefix=number_prefix, account_type=account_type))
            return {"accounts": rows}
        _refuse_ignored(f"kind={kind}", "n'existe que sur kind='accounts'",
                        number_prefix=number_prefix, account_type=account_type)
        if kind == "journals":
            return {"journals": _run(lambda: client.list_journals(dossier_id))}
        if kind == "periods":
            return {"periods": _run(lambda: client.list_accounting_periods(dossier_id))}
        raise _bad("kind doit être 'accounts', 'journals' ou 'periods'")

    @mcp.tool()
    def inqom_balance(
        dossier_id: int,
        start_date: str,
        end_date: str,
        account_numbers: Optional[list[str]] = None,
        balance_scope: Optional[Literal["Impacted", "All"]] = None,
        fields: Optional[list] = None,
    ) -> dict:
        """Trial balance of a dossier over a period: per account, debit/credit
        totals and balances, brought-forward amounts, unlettered line count.

        The per-third-party breakdown each account carries (`AuxiliaryBalance`)
        is DROPPED by default — `AuxiliaryBalance_length` says how many entries
        were cut. Pass `fields=["*"]` for the raw rows.

        Args:
            dossier_id: the dossier.
            start_date / end_date: yyyy-MM-dd.
            account_numbers: restrict to these accounts.
            balance_scope: "Impacted" (accounts with movements in the period —
                Inqom's default) or "All".
            fields: omit for the trimmed view; `["*"]` for raw rows; a list of
                names for exactly those.
        """
        rows = _run(lambda: _client().list_balances(
            dossier_id, start_date, end_date, account_numbers=account_numbers,
            balance_scope=balance_scope))
        rows, notice = output_projection.summarize(
            rows, body_fields=("AuxiliaryBalance",), fields=fields, always=("Account",))
        return {"balances": rows, **({"projection": notice} if notice else {})}

    @mcp.tool()
    def inqom_entry_line(
        dossier_id: int,
        start_date: str,
        end_date: str,
        op: Literal["list", "count"] = "list",
        page_number: Optional[int] = None,
        account_number: Optional[str] = None,
        journal_id: Optional[int] = None,
    ) -> dict:
        """Entry lines of a dossier dated in a period — one page, or the size.

        `op`:
        - **"list"** (default): ONE page (≤ 1000 lines) — `page_number` starts
          at 1 (default). A full page means there may be more: use op="count".
        - **"count"**: `TotalLinesCount` and `TotalPagesCount` for the same
          period and account.

        Args:
            dossier_id: the dossier.
            start_date / end_date: yyyy-MM-dd.
            op: list (default) | count.
            page_number: op="list" only — 1-indexed, default 1.
            account_number: both ops — one account.
            journal_id: op="list" only — one journal.
        """
        client = _client()
        if op == "list":
            rows = _run(lambda: client.list_entry_lines(
                dossier_id, start_date, end_date,
                1 if page_number is None else page_number,
                account_number=account_number, journal_id=journal_id))
            return {"page": rows}
        if op == "count":
            _refuse_ignored(op, "n'existe que sur op='list'",
                            page_number=page_number, journal_id=journal_id)
            return {"count": _run(lambda: client.count_entry_lines(
                dossier_id, start_date, end_date, account_number=account_number))}
        raise _bad("op doit être 'list' ou 'count'")

    @mcp.tool()
    def inqom_entry_create(
        dossier_id: int,
        entries: list[dict],
        dry_run: bool = True,
    ) -> dict:
        """⚠️ WRITES accounting entries into an Inqom dossier. Inqom posts them
        at once — there is no draft state.

        **dry_run is True by default**: nothing is posted; the tool checks each
        entry (required fields, dates, one amount per line, debits = credits),
        compares its `JournalId` with the dossier's journals, and returns the
        summary. Show it to the human — journal, date, every line with account
        and amount — and post only on their go, with `dry_run=False`. An
        unbalanced or malformed entry is refused in both modes.

        Args:
            dossier_id: the dossier.
            entries: 1-100 entries, each `{"JournalId": int, "Date": "yyyy-MM-dd",
                "Lines": [{"AccountNumber": "606100", "Label": "...",
                "Currency": "EUR", "DebitAmount": 120.5}, {... "CreditAmount": 120.5}],
                "EntryRef"?: str, "ExternalId"?: str,
                "Document"?: {"Reference"?: str, "Date": "yyyy-MM-dd"},
                "DueDate"?: "yyyy-MM-dd"}`. Journal ids come from
                `inqom_ref(kind="journals")`, account numbers from
                `inqom_ref(kind="accounts")`.
            dry_run: default True = check and preview only. False posts.
        """
        resume = _controle_entries(entries)
        client = _client()
        if dry_run:
            journaux = _run(lambda: client.list_journals(dossier_id))
            connus = {j.get("Id") for j in journaux if isinstance(j, dict)}
            avert = [f"entries[{r['index']}] : JournalId {r['JournalId']} absent des "
                     "journaux du dossier — l'appel réel serait refusé"
                     for r in resume if r["JournalId"] not in connus]
            return {
                "dry_run": True, "dossier_id": dossier_id, "entries": resume,
                "warnings": avert,
                "note": (f"Rien n'est posé. dry_run=False pose {len(resume)} écriture(s) "
                         "immédiatement, sans brouillon."),
            }
        inserted = _run(lambda: client.create_entries(dossier_id, entries))
        return {"dry_run": False, "dossier_id": dossier_id, "inserted": inserted}

    @mcp.tool()
    def inqom_document(dossier_id: int, document_id: int) -> dict:
        """The download URL of an accounting document (pièce) — `document_id` is
        the `AccountingDocument.Id` carried by an entry line.

        Args:
            dossier_id: the dossier.
            document_id: the accounting document id.
        """
        return {"document": _run(lambda: _client().get_accounting_document(
            dossier_id, document_id))}
