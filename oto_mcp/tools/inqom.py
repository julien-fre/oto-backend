"""Inqom — French accounting production, READ-ONLY: dossiers, reference data, balance,
entries, documents.

Credential = application keys (`client_id`/`client_secret`) + credentials of the
Inqom account on whose behalf the token acts (`username`/`password`), resolved per
call via `access.resolve_credential_fields("inqom")` (ADR 0011). The token carries
the rights of this account: what it does not see, no tool sees.

**Surface (ADR 0047 §Amendment)**:
- `inqom_company` — discovery, no parameter: the accessible firms/SMEs;
- `inqom_dossier` (list/get) — a firm's dossiers, a dossier's record;
- `inqom_ref` (kind=accounts|journals|periods) — a dossier's reference data;
- `inqom_balance` — balance over a period;
- `inqom_entry_line` (list/count) — paginated entry lines, or filtered by
  account prefixes;
- `inqom_entry_create` — the NOT WIRED write: it returns the named refusal
  `inqom_write_not_wired`, which describes the entries it would have created;
- `inqom_document` — the download URL of a document.

**No write is wired** (decision of 30/09/2026, same treatment as PayFit):
`inqom_entry_create` does not resolve the key, does not build the client and never
calls Inqom, whatever the argument — `ecriture_non_cablee.refus`. Accounting
entry is done in Inqom itself.

**Prefix filter**: the API only filters on ONE exact account. For "accounts
6 and 7", the tool reads all the lines of the period and keeps those
whose account starts with a prefix. Reading everything has a second use: an expense
line does not carry its supplier, it is on the third-party line (40x/41x)
of the same entry, which the tool attaches (`third_party_accounts`).

Fixed host (`api.inqom.com`): no credential field designates a
destination, so no egress guard (`oto_mcp/egress.py`) to set here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError
from . import ecriture_non_cablee

_NAME = "inqom"
_CHAMPS = ("client_id", "client_secret", "username", "password")
# Entries described in the refusal: beyond that, a count. The refusal is logged.
_DECRITES_MAX = 20
_PAGE = 1000  # lines per page, on the Inqom side as on the filter side
_SCAN_PAGES_MAX = 50  # bound of a filtered read: beyond it, narrow the period
_TIERS = ("40", "41")  # third-party accounts: suppliers, customers


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _need(value, name: str, op: str):
    """Required argument for THIS op — actionable error, never a fallback."""
    if value is None or value == "":
        raise _bad(f"op='{op}' requires {name}")
    return value


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """An argument provided that THIS op does not use is an error of intent.
    Tested on `is not None`: a provided `False` or `0` is an intent too."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op='{op}' does not use {name} — {hint}")


def _champs(fields: dict) -> dict:
    """The four fields, NON-EMPTY. An empty field passed to the client would raise a
    `MissingCredential` there in the lib's name: we refuse it here, in the connector's name."""
    vides = [n for n in _CHAMPS if not (fields.get(n) or "").strip()]
    if vides:
        raise ValueError(f"incomplete Inqom credential: {', '.join(vides)} empty")
    return {n: fields[n] for n in _CHAMPS}


def _upstream_message(e) -> str:
    status = e.status_code
    if status in (401, 403):
        return (f"Inqom: access denied (HTTP {status}) — invalid application keys or "
                "account credentials, or a right missing from this account.")
    if status == 404:
        return "Inqom: not found (HTTP 404) — dossier or resource does not exist, or is outside the account's rights."
    if status == 429:
        return "Inqom: too many requests (429) — retry in a moment."
    if status >= 500:
        return f"Inqom is temporarily unavailable (HTTP {status}) — retry later."
    return f"Inqom refused the request (HTTP {status}): {str(e.body)[:400]}"


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe: `list_companies()`, the smallest authenticated
    call with no parameter. An empty list is a possible state (account with no
    assignment), never a refusal."""
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


def _total(lines: list, sens: str) -> Optional[str]:
    """Sum of the `sens` amounts (DebitAmount|CreditAmount) of the lines, or None if an
    amount is unreadable: the description refuses nothing, it describes what can be read."""
    total = Decimal(0)
    for ln in lines:
        v = ln.get(sens) if isinstance(ln, dict) else None
        if v is None:
            continue
        try:
            total += Decimal(str(v))
        except (InvalidOperation, ValueError):
            return None
    return str(total)


def _ecritures_decrites(entries) -> list[dict]:
    """What each entry would have posted: journal, date, reference, line count
    and totals. Tolerant — the refusal does not depend on the shape of the arguments. The
    labels and accounts of the lines do not appear: the refusal is logged."""
    if not isinstance(entries, list):
        return []
    out = []
    for e in entries[:_DECRITES_MAX]:
        if not isinstance(e, dict):
            continue
        lines = e.get("Lines") if isinstance(e.get("Lines"), list) else []
        out.append({k: v for k, v in {
            "JournalId": e.get("JournalId"), "Date": e.get("Date"),
            "EntryRef": e.get("EntryRef"), "lignes": len(lines),
            "total_debit": _total(lines, "DebitAmount"),
            "total_credit": _total(lines, "CreditAmount"),
        }.items() if v is not None})
    return out


def _prefixes(values) -> tuple[str, ...]:
    """The requested prefixes, cleaned: an empty list or a blank prefix is an
    error, not "all accounts"."""
    if not isinstance(values, list) or not values:
        raise _bad("account_prefixes: a non-empty list of prefixes is required (e.g. [\"6\", \"7\"])")
    nets = tuple(str(v).strip() for v in values)
    if not all(nets):
        raise _bad("account_prefixes: empty prefix")
    return nets


def _filtrer(lines: list[dict], prefixes: tuple[str, ...]) -> list[dict]:
    """The lines whose account starts with a prefix, sorted by entry date,
    each with the third-party accounts of ITS entry (`third_party_accounts`).
    `lines` must be the ENTIRE period: a cut entry would lose its third party."""
    tiers: dict = {}
    for ln in lines:
        compte = str(ln.get("AccountNumber") or "")
        if compte.startswith(_TIERS):
            tiers.setdefault((ln.get("Entry") or {}).get("Id"), set()).add(compte)
    gardees = [
        {**ln, "third_party_accounts": sorted(tiers.get((ln.get("Entry") or {}).get("Id"), ()))}
        for ln in lines if str(ln.get("AccountNumber") or "").startswith(prefixes)]
    return sorted(gardees, key=lambda ln: (str((ln.get("Entry") or {}).get("Date") or ""),
                                           (ln.get("Entry") or {}).get("Id") or 0,
                                           ln.get("Id") or 0))


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
            _refuse_ignored(op, "only exists on op='get'", dossier_id=dossier_id)
            cid = _need(company_id, "company_id", op)
            return {"dossiers": _run(lambda: client.list_dossiers(cid))}
        if op == "get":
            _refuse_ignored(op, "only exists on op='list'", company_id=company_id)
            did = _need(dossier_id, "dossier_id", op)
            return {"dossier": _run(lambda: client.get_dossier(did))}
        raise _bad("op must be 'list' or 'get'")

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
        _refuse_ignored(f"kind={kind}", "only exists on kind='accounts'",
                        number_prefix=number_prefix, account_type=account_type)
        if kind == "journals":
            return {"journals": _run(lambda: client.list_journals(dossier_id))}
        if kind == "periods":
            return {"periods": _run(lambda: client.list_accounting_periods(dossier_id))}
        raise _bad("kind must be 'accounts', 'journals' or 'periods'")

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

    def _periode_entiere(client, dossier_id: int, start_date: str, end_date: str,
                         journal_id: Optional[int]) -> list[dict]:
        """All the lines of the period, page after page. The upstream count bounds
        it in advance; the loop stops at the first incomplete page."""
        total = _run(lambda: client.count_entry_lines(
            dossier_id, start_date, end_date)).get("TotalPagesCount") or 0
        if total > _SCAN_PAGES_MAX:
            raise _bad(f"account_prefixes: the period has {total} pages of {_PAGE} "
                       f"lines, beyond the {_SCAN_PAGES_MAX} that a filtered read "
                       "walks — narrow start_date / end_date (a month, a quarter)")
        lines: list[dict] = []
        for n in range(1, _SCAN_PAGES_MAX + 1):
            lot = _run(lambda: client.list_entry_lines(
                dossier_id, start_date, end_date, n, journal_id=journal_id)
            ).get("EntryLines") or []
            lines.extend(lot)
            if len(lot) < _PAGE:
                return lines
        raise _bad(f"account_prefixes: more than {_SCAN_PAGES_MAX} pages read without the end of "
                   "the period — narrow start_date / end_date")

    @mcp.tool()
    def inqom_entry_line(
        dossier_id: int,
        start_date: str,
        end_date: str,
        op: Literal["list", "count"] = "list",
        page_number: Optional[int] = None,
        account_number: Optional[str] = None,
        account_prefixes: Optional[list[str]] = None,
        journal_id: Optional[int] = None,
    ) -> dict:
        """Entry lines of a dossier dated in a period — one page, or the size.

        `op`:
        - **"list"** (default): ONE page (≤ 1000 lines) — `page_number` starts
          at 1 (default). A full page means there may be more: use op="count".
        - **"count"**: `TotalLinesCount` and `TotalPagesCount` for the same
          period and accounts.

        **Several accounts at once** — `account_prefixes=["6", "7"]` (a P&L),
        `["401"]` (suppliers), `["606", "613"]`: lines whose account starts with
        one of them, sorted by entry date, paginated by 1000 over that result
        (`count` gives its size). Each line gains `third_party_accounts`: the
        40x/41x accounts of the SAME entry — an expense line does not carry its
        supplier, the supplier is on that other line. Reads the whole period
        upstream, so keep it to a month or a quarter.

        Args:
            dossier_id: the dossier.
            start_date / end_date: yyyy-MM-dd.
            op: list (default) | count.
            page_number: op="list" only — 1-indexed, default 1.
            account_number: both ops — one exact account.
            account_prefixes: both ops — account number prefixes; excludes
                `account_number`.
            journal_id: op="list" only — one journal.
        """
        if account_prefixes is not None and account_number is not None:
            raise _bad("account_number and account_prefixes are mutually exclusive — one exact account "
                       "OR prefixes")
        client = _client()
        if op == "count":
            _refuse_ignored(op, "only exists on op='list'",
                            page_number=page_number, journal_id=journal_id)
        elif op != "list":
            raise _bad("op must be 'list' or 'count'")
        page = 1 if page_number is None else page_number
        if account_prefixes is not None:
            prefixes = _prefixes(account_prefixes)
            if page < 1:
                raise _bad(f"page_number starts at 1 — got {page}")
            lines = _filtrer(_periode_entiere(client, dossier_id, start_date, end_date,
                                              journal_id), prefixes)
            count = {"TotalLinesCount": len(lines), "TotalPagesCount": -(-len(lines) // _PAGE)}
            if op == "count":
                return {"count": count}
            return {"page": {"EntryLines": lines[(page - 1) * _PAGE:page * _PAGE],
                             "CurrentPage": page}, "count": count}
        if op == "count":
            return {"count": _run(lambda: client.count_entry_lines(
                dossier_id, start_date, end_date, account_number=account_number))}
        return {"page": _run(lambda: client.list_entry_lines(
            dossier_id, start_date, end_date, page,
            account_number=account_number, journal_id=journal_id))}

    @mcp.tool()
    def inqom_entry_create(
        dossier_id: Optional[int] = None,
        entries: Optional[list[dict]] = None,
    ) -> dict:
        """Creating accounting entries in Inqom is NOT wired: this connector is
        read-only. The call never reaches Inqom — nothing is posted, whatever the
        arguments — and answers the named refusal `inqom_write_not_wired`, saying
        which entries it would have created (journal, date, reference, line count,
        totals). Entries are posted in Inqom itself.

        Args:
            dossier_id: the dossier the entries were meant for.
            entries: the entries that would have been created, each
                `{"JournalId": int, "Date": "yyyy-MM-dd", "Lines": [...],
                "EntryRef"?: str}`.
        """
        decrites = _ecritures_decrites(entries)
        n = len(entries) if isinstance(entries, list) else 0
        raise ecriture_non_cablee.refus(
            _NAME, "Inqom", "create",
            f"created {n} accounting entry(ies)"
            + (f" in dossier {dossier_id}" if dossier_id is not None else ""),
            ecritures=decrites, ecritures_non_decrites=(n - len(decrites)) or None)

    @mcp.tool()
    def inqom_document(dossier_id: int, document_id: int) -> dict:
        """The download URL of an accounting document — `document_id` is
        the `AccountingDocument.Id` carried by an entry line.

        Args:
            dossier_id: the dossier.
            document_id: the accounting document id.
        """
        return {"document": _run(lambda: _client().get_accounting_document(
            dossier_id, document_id))}
