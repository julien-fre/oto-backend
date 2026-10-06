"""Silae — French payroll (read-only).

Credential = OAuth2 client-credentials (Azure AD B2C), three secrets
(client_id + client_secret + subscription_key). Resolved per call via
`access.resolve_credential_fields("silae")` — generic multi-field model
(ADR 0011). byo_user: each payroll cabinet / employer enters its own Silae API
credentials; its payroll is visible only to it.

Read-only surface (dossiers, employees, payslips, variables awaiting entry).
The write operations (adding a bonus/hours, confirming staged entries) stay out
of the agent for now — entering payroll is a sensitive act. ⚠️ `SilaeClient` DOES
carry them (`ajouter_element_variable`, `ajouter_prime`, `ajouter_heures`,
`confirmer_saisies`): no tool here reaches them, and
`tests/test_silae_op_dispatch.py` VERIFIES it (every op played, the four
write methods `assert_not_called`, plus a static check of the module).
Exposing a write = an explicit act, not a side effect of a refactor.

**Consolidated surface (ADR 0047 §Amendment, applied to the silae connector on
2026-08-11)**: one tool per business OBJECT, the verb in the `op` parameter —
`silae_dossier` (list/numbers/info/current_period), `silae_employee`
(list/get/jobs, all scoped by `numero_dossier`) and `silae_payslip`
(list/header/lines/totals, all scoped by `numero_dossier` + `periode`).
`silae_variables_to_enter` stays ALONE and **unchanged**: a single capability (a
single-valued `op` is not a verb), and it is the Silae resource
(`v1/Variables/*`) where ALL the unexposed writes live — keeping it
apart keeps the read/write boundary visible.

⚠️ **Redaction of bank details is NOT active by default.** The
IBAN/BIC/RIB masking is available at the tools' boundary
(`FieldRedactionMiddleware`, policy resolved by NAMESPACE `silae` — hence
insensitive to tool names: this renaming does not break it), but
`field_filter_defaults.SERVER_DEFAULTS` carries **nothing for `silae`**: nothing
is redacted until the org has set a policy (`bank_details` template,
applicable in 1 click; `connector_field_schema` declares silae's PII floor —
iban/bic/rib/salaire/numeroSecu/dateNaissance/nom/prenom). Payslips
therefore reach the agent **in clear** by default. *(This docstring claimed
the opposite until 2026-08-11 — "the redaction is applied … server default in
`field_filter_defaults.SERVER_DEFAULTS`" — that was false: SERVER_DEFAULTS = {}.)*

⚠️ **The Silae API does not raise: it returns the error in the body.**
`SilaeClient.call` returns `{"error": "<status>", "details": …, "status_code": …}`
on HTTP failure, `{"error": "<exception>"}` on network error, and
`{"error": "Max retries exceeded"}` when attempts run out — a tool here can
therefore return an error dict with HTTP 200. Check the `error` key before
concluding "empty dossier". (401 = token invalidation then retry; 429 =
exponential backoff — handled in the client, not here.)

⚠️ **The subscription key BOUNDS the perimeter**: sent as
`Ocp-Apim-Subscription-Key`, it scopes the dossiers AND the functions
that can be reached. A dossier missing from `silae_dossier(op="list")` does not
necessarily not exist — it may be outside the key's perimeter.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify


def _verify(fields: dict, config: dict | None = None) -> None:
    """"Test the connection" probe — otomata-tech/oto#69. Covers `auth` ONLY.

    `POST v1/Dossiers/ListeDossiers` (already in the client — `list_dossiers`),
    the smallest call available: Silae exposes neither `/me` nor a balance. The token
    mint (client_id/client_secret) raises NATURALLY
    (`resp.raise_for_status()`) on those two fields — but `call()` itself NEVER
    RAISES on an HTTP refusal (`{"error", "status_code"}` dict, already noted
    in this module's docstring): a wrong or too narrow `subscription_key` would
    fail `list_dossiers()` WITHOUT the token mint
    signalling it, hence the explicit read below.

    **Authenticated ≠ usable** (class oto#69): the `subscription_key` scopes
    which dossiers/functions are reachable — an EMPTY list (`[]`) is a
    normal state (freshly created account), never a refusal.
    """
    from oto.tools.silae import SilaeClient

    infos = SilaeClient(
        client_id=fields["client_id"], client_secret=fields["client_secret"],
        subscription_key=fields["subscription_key"],
    ).list_dossiers()
    if not (isinstance(infos, dict) and "error" in infos):
        return
    code = infos.get("status_code")
    detail = str(infos.get("details") or infos["error"])[:300]
    if code in (401, 403):
        raise connector_verify.NonAutorise(f"Silae HTTP {code}: {detail}")
    raise RuntimeError(f"Silae: {detail}")


def register(mcp: FastMCP) -> None:
    from oto.tools.silae import SilaeClient

    connector_verify.register("silae", _verify)

    def _client() -> SilaeClient:
        creds = access.resolve_credential_fields("silae")
        # Redaction (IBAN/BIC/RIB mask) applied at the tools' boundary by
        # `FieldRedactionMiddleware` — and ONLY if the org has set a policy
        # (see the warning in the module docstring); no longer at client level.
        return SilaeClient(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            subscription_key=creds.get("subscription_key"),
        )

    def _bad(msg: str) -> McpError:
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    def _need(value, name: str, op: str):
        """Mandatory argument for THIS op — actionable error, never a fallback.
        An empty string counts as absent: Silae treats `""` as "all
        employees", so letting it through on a single-employee op would return a
        plausible and wrong result."""
        if value is None or value == "":
            raise _bad(f"op='{op}' requires {name}")
        return value

    def _refuse_ignored(op: str, hint: str, **provided) -> None:
        """A provided argument that THIS op does not use is an error of intent,
        not a detail. Silence is the real risk of consolidating by `op`:
        `silae_dossier(numero_dossier="001")` without op would return the COMPLETE list of
        dossiers — a credible result, beside the request. So we name the op that
        honours the argument."""
        for name, value in provided.items():
            if value is not None and value != "":
                raise _bad(f"op='{op}' does not use {name} — {hint}")

    # --- Dossiers (payroll files) ---

    @mcp.tool()
    def silae_dossier(
        op: Literal["list", "numbers", "info", "current_period"] = "list",
        numero_dossier: Optional[str] = None,
    ) -> object:
        """A payroll dossier (folder) — what the key reaches, or one dossier's payroll.

        `op`:
        - **"list"** (default): the payroll dossiers reachable with the API key.
        - **"numbers"**: just the dossier numbers reachable with the API key —
          lighter than "list" when all you need is a `numero_dossier` to feed the
          other tools.
        - **"info"**: detailed payroll information for a dossier (`numero_dossier`).
        - **"current_period"**: the current OPEN payroll period of a dossier
          (`numero_dossier`) — that's the value to pass as `periode` to
          `silae_payslip`.

        Args:
            op: list (default) | numbers | info | current_period.
            numero_dossier: op="info"/"current_period" — dossier (folder) number.
                Refused on "list"/"numbers", which enumerate everything the
                subscription key reaches and would silently ignore the filter.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "use op='info' for a specific dossier",
                            numero_dossier=numero_dossier)
            return client.list_dossiers()
        if op == "numbers":
            _refuse_ignored(op, "use op='info' for a specific dossier",
                            numero_dossier=numero_dossier)
            return client.list_numeros_dossiers()
        if op == "info":
            return client.dossier_infos(_need(numero_dossier, "numero_dossier", op))
        if op == "current_period":
            return client.dossier_periode_en_cours(
                _need(numero_dossier, "numero_dossier", op))
        raise _bad("op must be 'list', 'numbers', 'info' or 'current_period'")

    # --- Employees ---

    @mcp.tool()
    def silae_employee(
        numero_dossier: str,
        op: Literal["list", "get", "jobs"] = "list",
        matricule_salarie: Optional[str] = None,
        type_emplois: Optional[int] = None,
    ) -> object:
        """An employee of a dossier — the roster, one employee, or their jobs.

        `op`:
        - **"list"** (default): list the employees of a dossier.
        - **"get"**: fetch one employee by registration number (matricule).
        - **"jobs"**: list an employee's jobs/positions (emplois). Omit
          `matricule_salarie` to get them for every employee of the dossier.

        Args:
            numero_dossier: Dossier (folder) number — required by every op.
            op: list (default) | get | jobs.
            matricule_salarie: Employee registration number. Required by "get";
                optional on "jobs" (omitted = all employees); refused on "list",
                which returns the whole roster and would ignore it.
            type_emplois: op="jobs" — 0 = current jobs only (default),
                1 = current + archived.
        """
        client = _client()

        if op == "list":
            _refuse_ignored(op, "use op='get' for a specific employee",
                            matricule_salarie=matricule_salarie,
                            type_emplois=type_emplois)
            return client.list_salaries(numero_dossier)
        if op == "get":
            _refuse_ignored(op, "type_emplois only applies to op='jobs'",
                            type_emplois=type_emplois)
            return client.salarie_matricule(
                numero_dossier, _need(matricule_salarie, "matricule_salarie", op))
        if op == "jobs":
            return client.list_salarie_emplois(
                numero_dossier, matricule_salarie or "", type_emplois or 0)
        raise _bad("op must be 'list', 'get' or 'jobs'")

    # --- Payslips ---

    @mcp.tool()
    def silae_payslip(
        numero_dossier: str,
        periode: str,
        op: Literal["list", "header", "lines", "totals"] = "list",
        matricule_salarie: Optional[str] = None,
    ) -> object:
        """A payslip (bulletin) of a period — the payslips, or one payslip's detail.

        `op`:
        - **"list"** (default): retrieve payslips for a period — the whole dossier,
          or one employee when `matricule_salarie` is given.
        - **"header"**: payslip header for one employee/period.
        - **"lines"**: payslip lines (lignes) for one employee/period.
        - **"totals"**: payslip cumulative totals (cumuls) for one employee/period.

        Args:
            numero_dossier: Dossier (folder) number — required by every op.
            periode: Payroll period (e.g. "2026-05") — required by every op.
                `silae_dossier(op="current_period")` gives the open one.
            op: list (default) | header | lines | totals.
            matricule_salarie: Employee matricule. Optional on "list" (omitted =
                all employees of the dossier); REQUIRED by "header", "lines" and
                "totals", which each describe ONE payslip.
        """
        client = _client()

        if op == "list":
            return client.bulletins(numero_dossier, periode, matricule_salarie or "")
        if op == "header":
            return client.bulletin_entete(
                numero_dossier, _need(matricule_salarie, "matricule_salarie", op),
                periode)
        if op == "lines":
            return client.bulletin_lignes(
                numero_dossier, _need(matricule_salarie, "matricule_salarie", op),
                periode)
        if op == "totals":
            return client.bulletin_cumuls(
                numero_dossier, _need(matricule_salarie, "matricule_salarie", op),
                periode)
        raise _bad("op must be 'list', 'header', 'lines' or 'totals'")

    # --- Payroll variables (EVP) ---

    @mcp.tool()
    def silae_variables_to_enter(numero_dossier: str) -> object:
        """List the payroll variables (EVP) still awaiting entry for a dossier.

        Args:
            numero_dossier: Dossier (folder) number.
        """
        return _client().list_variables_a_saisir(numero_dossier)
