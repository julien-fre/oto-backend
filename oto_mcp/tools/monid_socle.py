"""Foundation of the `monid` connector: what its four tools share.

The bounds and time budget of the launch, the translation of a Monid refusal into an
instruction (chosen on the CODE, the unknown outcome apart), a run's envelope and its
next step, the two guards of the platform key (list of runs, balance), the
projection of a page. The WHY of these choices lives in the docstring of `tools/monid.py`,
which keeps the probe, `register()` and the tools.

Split out of the tools module to stay under the 500-line-per-file cap
(`docs/conventions.md`). It has no `register()`: it is not a connector, it is
a helper. No client call lives here — they stay written in plain sight in the tools,
where the version-skew probe reads them.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, ErrorData
from oto.tools.monid.client import (MonidHTTPError, MonidProtocolError, is_terminal,
                                    run_cost_usd)

from .. import output_projection
from ..mcp_errors import McpError

_WAIT_MAX_S = 40      # bound of `wait_seconds` (launch and re-read)
_RUN_READ_S = 35      # read of POST /v1/run, at most
_RUN_READ_MIN_S = 10  # below this, a synchronous run would almost surely end in an unknown outcome
_CONNECT_S = 10       # connect timeout the client hard-codes on `run()`
_RUN_BUDGET_S = 45    # launch + wait deadline, from the tool's entry
_DISCOVER_LIMIT_MAX = 40
_RUNS_LIMIT_MAX = 100

_DROP_ENDPOINT = ("providerDisplayDescription", "supportedX402Networks")
_DROP_RUN = ("caller",)
_HINT_FULL = "full=True returns the whole records"


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _hors_op(op: str, **donnes: Any) -> None:
    """Refuses an argument that does not apply to the chosen `op`, rather than ignoring it:
    a filter passed to `op="get"` would suggest it filtered. Compared by IDENTITY:
    `0 == False`, and a `min_score=0` passed to inspect would otherwise be swallowed silently."""
    en_trop = sorted(k for k, v in donnes.items() if v is not None and v is not False)
    if en_trop:
        raise _bad(f"op='{op}' does not take {en_trop}.")


def _borne(nom: str, valeur: Any, bas: int, haut: int) -> None:
    if isinstance(valeur, bool) or not isinstance(valeur, int) or not bas <= valeur <= haut:
        raise _bad(f"`{nom}` must be an integer from {bas} to {haut} (got {valeur!r}).")


def _extrait(valeur: Any, n: int = 200) -> str:
    texte = valeur if isinstance(valeur, str) else json.dumps(valeur, ensure_ascii=False,
                                                              default=str)
    texte = texte.strip()
    return texte if len(texte) <= n else texte[:n] + "…"


def _rid(e: Any) -> str:
    rid = getattr(e, "request_id", None)
    return f" [request_id {rid}]" if rid else ""


def _cause(e: BaseException) -> str:
    """What went wrong, in two words: the HTTP code, or the original transport incident."""
    if isinstance(e, MonidHTTPError):
        return f"HTTP {e.status_code}"
    if isinstance(e, MonidProtocolError):
        return type(e.__cause__).__name__ if e.__cause__ else "response without a readable run"
    return type(e).__name__


# --- translation of refusals --------------------------------------------------

def _issue_inconnue(e: Any, *, is_platform: bool = False) -> McpError:
    """`INTERNAL_ERROR` and not `INVALID_PARAMS` (deliberate departure from `_bad`): "invalid
    argument" pushes the agent to fix then call again — the double payment. The prohibition
    comes first. Under the platform key, the list of runs is closed: the refusal
    does not point to it, it refers to an administrator. Goes up to Sentry (not "expected")."""
    ou_chercher = ("It went through the platform key: ask an administrator "
                   "to check the platform's Monid workspace." if is_platform else
                   "Look for it first in monid_runs(op=\"list\").")
    return McpError(ErrorData(code=INTERNAL_ERROR, message=(
        "DO NOT RETRY monid_run, neither identically nor modified: UNKNOWN outcome of the "
        f"launch ({_cause(e)}), the run may exist and be billed. "
        f"{ou_chercher}{_rid(e)}")))


def _traduire(e: Exception, *, is_platform: bool = False) -> Exception:
    """The exception to raise for a Monid refusal — chosen on the CODE, never on the text.

    4xx → named refusal (the call or the key must change). 429 and 5xx stay what they
    are: the error taxonomy classes them as retryable — except a launch whose outcome
    is unknown, which is never let through as retryable nor as an argument error
    (`_issue_inconnue`: `INTERNAL_ERROR`, not retryable)."""
    if getattr(e, "may_have_run", False):
        return _issue_inconnue(e, is_platform=is_platform)
    if isinstance(e, MonidProtocolError):
        return _bad(f"Unusable response from Monid: {e}{_rid(e)}")
    status, rid = e.status_code, _rid(e)
    corps = e.body if isinstance(e.body, dict) else {}
    if status == 503 and "walletStatus" in corps and e.retry_after is None:
        etat = corps.get("walletStatus") or "not yet created"
        return _bad(f"Monid wallet unavailable (503, status {etat}): Monid "
                    f"announces no recovery delay — see its dashboard.{rid}")
    if status == 429 or status >= 500:
        return e
    detail = e.upstream_message or _extrait(e.body)
    if status == 400:
        msg = (f"Monid refused the input (400): {detail}. Check it against the schema "
               "returned by monid_endpoint(op=\"inspect\").")
    elif status == 401:
        msg = ("Monid rejects the key (401): missing, malformed or revoked. "
               + ("This is the platform key: notify an administrator."
                  if is_platform else
                  "Create a new key in the Monid dashboard (API keys) and "
                  "replace it on the connector card."))
    elif status == 402:
        msg = ("Monid wallet balance insufficient (402): "
               + ("it is served by the platform key, an administrator has to "
                  "top it up." if is_platform else "top it up on monid.ai."))
    elif status == 403:
        msg = ("Monid denies access (403): the key is not tied to any workspace, "
               "or this run belongs to another workspace.")
    elif status == 404:
        msg = ("Unknown to Monid (404): endpoint or run not found. Run "
               "monid_endpoint(q=…) again and pass provider + endpoint EXACTLY as returned"
               + ("." if is_platform else
                  "; a run can be found in monid_runs(op=\"list\")."))
    elif status == 409:
        msg = ("Run already finished or not stoppable (409): nothing to stop — re-read it with "
               "monid_runs(op=\"get\").")
    else:
        msg = f"Monid refused the request (HTTP {status}): {detail}."
    return _bad(msg + rid)


def _appel(fn: Callable[[], Any], *, is_platform: bool = False) -> Any:
    try:
        return fn()
    except ValueError as e:
        raise _bad(str(e)) from None
    except (MonidHTTPError, MonidProtocolError) as e:
        raise _traduire(e, is_platform=is_platform) from None


# --- a run's envelope ---------------------------------------------------------

def _http_fournisseur(run: dict) -> Optional[int]:
    reponse = run.get("providerResponse")
    code = reponse.get("httpStatus") if isinstance(reponse, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _est_un_run(corps: Any, run_id: Optional[str] = None) -> bool:
    """The client's test (`run()`): a dict carrying `runId` and `status`, non-empty
    strings — and, when `run_id` is given, THAT run."""
    return (isinstance(corps, dict)
            and all(isinstance(corps.get(k), str) and corps[k] for k in ("runId", "status"))
            and (run_id is None or corps["runId"] == run_id))


def _relecture_ratee(run: dict, cause: str) -> str:
    rid = run.get("runId")
    return (f"Run accepted ({run.get('status')}), but its re-read failed ({cause}): it "
            f"may still be running. Re-read it with monid_runs(op=\"get\", run_id=\"{rid}\") "
            "— do not relaunch it.")


def _suite(run: dict, done: bool, run_id: Optional[str] = None) -> Optional[str]:
    """The next step, by status; `None` when the result is ready to read.
    `run_id` = the identifier known to the caller, if the body carries none."""
    rid, status = run.get("runId") or run_id, run.get("status")
    if not done:
        return (f"Run {status}: not finished yet. Re-read it with monid_runs(op=\"get\", "
                f"run_id=\"{rid}\", wait_seconds=30), or stop it (and its spending) with "
                f"monid_runs(op=\"stop\", run_id=\"{rid}\").")
    if status == "BLOCKED":
        motif = _extrait(run.get("reason") or "no reason given")
        return ("Blocked before execution by a Monid workspace cap (budget or number "
                f"of runs): \"{motif}\". Nothing is billed; relaunching will block again "
                "until that cap is changed at Monid.")
    if status == "FAILED":
        return "Failure on Monid's side, not the provider's: not billed."
    if status == "TIMED_OUT":
        return ("Run timed out: not billed. Retry with a smaller volume, "
                "or later.")
    if status == "STOPPED":
        return "Run stopped."
    http = _http_fournisseur(run)
    if http is None:
        return ("The provider returned no HTTP status: read `run.output` and "
                "`run.providerResponse` before relying on the result.")
    if 200 <= http < 300:
        return None
    erreur = (run.get("providerResponse") or {}).get("error")
    return (f"The provider answered HTTP {http}"
            + (f": {_extrait(erreur)}" if erreur else "")
            + " — Monid does not bill a non-2xx response (a 404 often means "
              "\"nothing found\").")


def _provider_ok(run: dict) -> Optional[bool]:
    """COMPLETED with a 2xx answer from the provider; `None` if that status is missing."""
    if run.get("status") != "COMPLETED":
        return False
    http = _http_fournisseur(run)
    return None if http is None else 200 <= http < 300


def _enveloppe(run: Any, *, relecture: Optional[str] = None,
               run_id: Optional[str] = None) -> dict:
    """`run_id` = the identifier the caller knows (accepted, or requested): the next
    step never says "run None", and a body that is not a run says so."""
    r = run if isinstance(run, dict) else {}
    lisible = _est_un_run(run)
    done = lisible and is_terminal(r)
    if relecture is None and not lisible:
        relecture = (f"Unreadable response from Monid for run {run_id} (the body is not "
                     f"a run): re-read it with monid_runs(op=\"get\", run_id=\"{run_id}\") "
                     "— do not relaunch it.")
    return {"run": run, "done": done, "provider_ok": _provider_ok(r) if done else None,
            "cost_usd": run_cost_usd(r), "next_step": relecture or _suite(r, done, run_id)}


# --- the platform key's guards ------------------------------------------------

def _garde_liste(is_platform: bool) -> None:
    """The list of runs is that of the key's WORKSPACE. Under the platform key,
    that workspace is shared by all the orgs that have a grant: listing it would expose
    their runs (and identifiers that open `get` and `stop`). Refused, and named."""
    if is_platform:
        raise _bad("The list of runs is not served under the platform key: its "
                   "Monid workspace is shared, its history holds other organizations' runs. "
                   "Re-read a run by its identifier (monid_runs(op=\"get\", "
                   "run_id=…)); for the history, set your own Monid key.")


def _garde_solde(is_platform: bool) -> None:
    """The balance is that of the key's wallet. Under the platform key, it is
    the platform's SHARED wallet: its balance is not that of the org that has a
    grant, and is not served to it. A launch short of funds says so through its 402.
    Refused before anything is sent, and named."""
    if is_platform:
        raise _bad("The balance is not served under the platform key: its Monid wallet "
                   "is shared among the organizations that have access to it, and its balance "
                   "is not disclosed to them. A launch short of funds says so "
                   "(402); to see a balance, set your own Monid key.")


def _projeter(page: Any, drop: tuple, full: bool) -> Any:
    """Removes whole COLUMNS from the items, and says so in `projection`."""
    if full or not isinstance(page, dict):
        return page
    out = output_projection.project(page, items_path="items", item_drop=drop)
    out["projection"] = {"omitted": list(drop), "hint": _HINT_FULL}
    return out
