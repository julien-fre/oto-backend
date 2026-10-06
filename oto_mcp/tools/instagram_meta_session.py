"""Instagram (statistiques) — a call's token, and its survival.

The other half of the connector: `auth/instagram_meta.py` carries ACQUISITION (the
"Connect" click, the consent return, what the card displays); here we
serve — resolve a call's token, renew it in time, and refuse by naming
the cause. The line between the two is the trigger.

⚠️ **This token can only be renewed while it is alive.** Meta issues no
`refresh_token` on this product: an authorization left dormant for sixty days
is not degraded, it is LOST, and only the user can redo it. Hence
two renewal triggers rather than one:

- **on use**, very early (from 53 days remaining out of 60), so that even
  infrequent use is enough to keep the connection alive;
- **and a DAILY pass** (`renouveler_les_jetons`, maintenance job
  `instagram-tokens`), because lazy renewal dies of disuse:
  a person who does not check their statistics for two months would lose their
  connection *without having done anything*, and nothing would have warned them. This is not
  a failure mode we can ask the user to prevent.

Three refusals, three different actions, which is why they do not look alike:
no connected account (authorize), expired authorization (re-authorize — we state the
DATE), call outage (retry). Meta returns all three as a 400.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import credentials_store
from ..auth import instagram_meta as ig_auth
from ..auth.instagram_meta import CONNECTOR, _coeur, _ctx_org, _row, _scope
from ..connectors import health as connector_health
from ..mcp_errors import McpError

if TYPE_CHECKING:  # the annotation only — never evaluated at runtime
    from oto.tools.instagram_meta import InstagramClient

logger = logging.getLogger("oto_mcp.tools.instagram_meta")

_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre")


class InstagramReauthRequired(RuntimeError):
    """The authorization is dead: a NEW consent is needed, not a retry.

    The message carried by this exception is the one shown to the user —
    it names the date and the action. Distinct type because callers use it
    to mark the vault row (`connector_health`) without confusing it
    with a transient outage, which must not mark anything at all."""


def _date_fr(horodatage: Optional[str]) -> str:
    """"8 novembre 2026" — a date you read, not an ISO 8601 you decipher.

    An expiry message without a date reads as an outage; with the date, it
    reads as what it is. Unreadable timestamp ⇒ empty string, and the caller says
    the sentence without the date rather than displaying `None`."""
    dt = _coeur().parse_ts(horodatage)
    return f"{dt.day} {_MOIS[dt.month - 1]} {dt.year}" if dt else ""


def _message_expire(expires_at: Optional[str]) -> str:
    """⚠️ **No address, and that is deliberate.** This message used to hard-code our dashboard;
    served to a partner's agent, it sent them to us for an action they
    must perform on their own side. The right link depends on the ACCOUNT (`config.dashboard_url_for`) —
    yet neither this function nor the two other sites in this module hold the `sub`:
    they are called from the health marking and from the refusal translator, which
    only carry an entity. Propagating the account up to here would be a refactor
    unrelated to the defect; naming the page without addressing it says the same thing to an agent
    and sends no one to the wrong place. Do not "put the link back" without the `sub`.
    """
    quand = _date_fr(expires_at)
    return (
        f"Your Instagram authorization expired{f' on {quand}' if quand else ''} — "
        "it is valid for 60 days and cannot be renewed once past. "
        "Reconnect your account from your connectors page, connector \"Instagram "
        "(statistiques)\".")


def resolve_token(sub: str) -> tuple[str, str]:
    """`(token, user_id)` of the connected account — renewed FIRST if needed.

    Three refusals, and each names its cause because they call for three different
    actions: no connected account (authorize), expired authorization
    (re-authorize, and we state the date), inconsistent vault (the
    `user_id` is missing, reconnecting sets it again).

    Preventive renewal is performed HERE, before returning the token, and its
    failure is never swallowed: serving a token we know is about to die
    would move the failure one call later, where nothing would say why."""
    org_id = _ctx_org(sub)
    entity_type, entity_id = _scope(org_id, sub)
    row = _row(org_id, sub)
    if not row or not row.get("secret"):
        from .. import config
        raise RuntimeError(
            "No Instagram account connected. Authorize oto from your connectors "
            f"page ({config.dashboard_url_for(sub)}/, connector \"Instagram "
            "(statistiques)\") — the connection is made with your Instagram account, "
            "without Facebook.")
    meta = row.get("meta") or {}
    user_id = str(meta.get("user_id") or "")
    if not user_id:
        raise RuntimeError(
            "The connected Instagram account has no professional account "
            "identifier on record: reconnect it from your connectors page.")
    coeur = _coeur()
    expires_at, connected_at = meta.get("expires_at"), meta.get("connected_at")
    if coeur.is_expired(expires_at, connected_at):
        _marquer_mort(entity_type, entity_id, "", expires_at)
        raise InstagramReauthRequired(_message_expire(expires_at))
    jeton = row["secret"]
    if coeur.needs_refresh(expires_at, meta.get("refreshed_at") or connected_at):
        jeton = _renouveler(coeur, entity_type, entity_id, "", jeton, meta,
                            expires_at)
    return jeton, user_id


def _marquer_mort(entity_type: str, entity_id: str, account: str,
                  expires_at: Optional[str]) -> None:
    """Records the rejection on the row the call REALLY uses.

    This is what lets the card say "authorization expired, to
    reconnect" BEFORE anyone calls: without marking, the user only discovers
    the expiry by trying, and the card shows "connected" on an account that
    no longer is."""
    connector_health.mark_rejected(entity_type, entity_id, CONNECTOR, account,
                                   _message_expire(expires_at))


def _renouveler(coeur, entity_type: str, entity_id: str, account: str,
                jeton: str, meta: dict, expires_at: Optional[str]) -> str:
    """Exchanges the token for a new 60-day one and rewrites the row. Returns the new one.

    **A single renewal for both paths** — a user's call and
    the daily pass. They differ by what triggers them, not by what they
    write: separating them would have made two places to rewrite an expiry, hence
    one day two ways of writing it, one of them wrong.

    The secret is replaced WHOLE (for this connector, the blob IS the token) and the
    `meta` passed back COMPLETE: `set_credential` without `meta` overwrites it with `{}`, which
    would erase the account identifier and the expiry — that is, everything that
    makes it possible to serve, then to renew the next time.

    The health marking is cleared HERE: a successful renewal is the only
    proof that the row works again, and without this clearing the card would stay red
    on a healthy connection (same reason as the Google rotation).
    """
    try:
        frais = coeur.refresh_long_lived(jeton, expires_at=expires_at)
    except coeur.InstagramAuthExpired as e:
        _marquer_mort(entity_type, entity_id, account, expires_at)
        raise InstagramReauthRequired(_message_expire(expires_at)) from e
    except Exception as e:
        # Transient outage: we mark NOTHING — marking would make the card say
        # "authorization dead" about a living authorization, and would send
        # the user to redo a consent they do not need. We also do not
        # serve the token we just failed to extend: the outage would then surface
        # one call later, where nothing would explain it.
        raise RuntimeError(
            f"Renewing the Instagram authorization did not succeed: {e} "
            "Your authorization is not at fault — retry.") from e
    maintenant = coeur.utcnow()
    neuf = dict(meta)
    neuf["expires_at"] = coeur.iso(maintenant + timedelta(seconds=frais["expires_in"]))
    neuf["refreshed_at"] = coeur.iso(maintenant)
    neuf.pop("health_ko", None)
    neuf.pop("health_reason", None)
    # `set_by` = the owner of the consent, read from the row itself:
    # writing it on behalf of a system job would make "who set this key" lie.
    # ⚠️ The split is only valid at the member tier, where `entity_id` is
    # `{org}:{sub}`. At `user` scope, `entity_id` IS the sub — and a qualified sub
    # (tenant) looks deceptively like it: we would attribute the row to half of an
    # identifier. This connector only writes at member level; the guard is here
    # so that this stays true if another tier is added.
    sub = (str(entity_id).partition(":")[2]
           if entity_type == credentials_store.MEMBER else "")
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     frais["access_token"], set_by=sub or None,
                                     meta=neuf, account=account)
    logger.info("instagram_meta: authorization renewed (%s, until %s)",
                entity_id, neuf["expires_at"])
    return frais["access_token"]


# --- the daily pass ------------------------------------------------------------

def renouveler_les_jetons(*, dry_run: bool = False) -> dict:
    """Renews all authorizations nearing their term. Never raises.

    **Lazy renewal is not enough here, and this is not a convenience.**
    A Meta token can only be renewed while it is alive: a person who does not check
    their statistics for two months would lose their connection *without having done anything*,
    and nothing would have warned them. A daily pass is the only thing
    that makes the connection's survival independent of use.

    Fail-open per ROW: a dead authorization — the normal case after a
    revocation — must not prevent renewing the others. It is marked,
    counted, and the pass continues.
    """
    from ..db import _conn as db_conn

    sortie = {"examines": 0, "renouveles": 0, "expires": 0, "echecs": 0,
              "dry_run": dry_run}
    try:
        coeur = _coeur()
    except RuntimeError as e:
        # oto-core too old: the connector does not serve either, there is nothing
        # to renew. We SAY so rather than returning a reassuring zero.
        return {**sortie, "note": str(e)}
    with db_conn._connect() as conn:
        lignes = conn.execute(
            "SELECT entity_type, entity_id, account FROM connector_credentials "
            "WHERE connector = %s",
            (CONNECTOR,)).fetchall()
    for ligne in lignes:
        sortie["examines"] += 1
        entity_type, entity_id = ligne["entity_type"], ligne["entity_id"]
        account = ligne["account"] or ""
        try:
            row = credentials_store.get_credential_with_meta(
                entity_type, entity_id, CONNECTOR, account)
            if not row or not row.get("secret"):
                continue
            meta = dict(row.get("meta") or {})
            expires_at = meta.get("expires_at")
            emis = meta.get("refreshed_at") or meta.get("connected_at")
            if coeur.is_expired(expires_at, emis):
                # Nothing to renew: a consent is what is needed. We mark,
                # so the card says so without waiting for the next call — and
                # not in dry-run: "dry-run" means we write NOTHING, including
                # this marking, otherwise the command lies about what it does.
                if not dry_run:
                    _marquer_mort(entity_type, entity_id, account, expires_at)
                sortie["expires"] += 1
                continue
            if not coeur.needs_refresh(expires_at, emis):
                continue
            if not dry_run:
                _renouveler(coeur, entity_type, entity_id, account, row["secret"],
                            meta, expires_at)
            sortie["renouveles"] += 1
        except Exception:  # noqa: BLE001 — a dead row does not stop the pass
            sortie["echecs"] += 1
            logger.warning("instagram_meta: renewal failed for %s",
                           entity_id, exc_info=True)
    return sortie


# --- a call's client ------------------------------------------------------------

def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


async def _client() -> InstagramClient:
    """The Instagram client of THIS caller, token renewed if needed.

    The name and the UNQUOTED return annotation are a contract: the version-skew
    probe (`tests/test_tools_client_methods_exist.py`) recognizes factories
    named `_client` and reads the class they return to verify,
    at the pinned oto-core tag, that every method the tools call exists.
    Renaming this function or quoting its annotation takes the connector out of that
    coverage SILENTLY — and this is the connector that needs it most, its
    core living in the other repo.

    Everything goes to a THREAD: resolution touches the database, renewal
    talks to Meta, and the client itself is synchronous. This server is single-loop
    (`docs/event-loop-perf.md`) — any one of these three calls played in the loop
    freezes it until upstream answers.

    `renew` is the CATCH-UP, not the policy: it only fires if Meta rejects the
    token mid-call (revocation, rotation), once, and it rewrites the
    vault along the way. The normal renewal has already happened above.
    """
    from .. import access

    sub = access.current_user_sub_or_raise()
    try:
        jeton, user_id = await asyncio.to_thread(resolve_token, sub)
    except RuntimeError as e:
        # `InstagramReauthRequired` is one: both already carry the message
        # we show, one for expiry (with its date), the other for an
        # unconnected account or an inconsistent vault. Distinguishing them here
        # would change nothing about what we return — what really distinguishes them is
        # that one marks the vault row and the other does not, and that is done earlier.
        raise _bad(str(e)) from e

    def renouveler_a_chaud() -> str:
        org_id = _ctx_org(sub)
        entity_type, entity_id = _scope(org_id, sub)
        row = _row(org_id, sub) or {}
        return _renouveler(_coeur(), entity_type, entity_id, "",
                           row.get("secret") or jeton, dict(row.get("meta") or {}),
                           (row.get("meta") or {}).get("expires_at"))

    return _coeur().InstagramClient(jeton, user_id, renew=renouveler_a_chaud)


async def appeler(geste: str, fn, *args):
    """Plays a core call off the loop and translates its refusals.

    The point is not to catch: it is **not to confuse**. A dead token and
    an Instagram outage both surface as `RuntimeError` from the core, and
    presenting them alike costs in both directions — one makes the user retry endlessly a
    connection that needs redoing, the other makes them redo a consent nobody
    needed.

    ⚠️ The text of an upstream exception only crosses this boundary for the
    errors the core WRITES itself (`InstagramError`), and those carry
    neither URL nor raw body — a property held over there, and tested over there. For
    everything else we return the TYPE, never the message: on this API the token travels
    as a URL parameter, and an exception from an intermediate layer could carry it
    all the way into an agent transcript.
    """
    coeur = _coeur()
    try:
        return await asyncio.to_thread(fn, *args)
    except InstagramReauthRequired as e:
        # The catch-up tried and Meta refused: the message already carries the date.
        raise _bad(str(e)) from e
    except coeur.InstagramAuthExpired as e:
        # Meta rejects the token while the stored expiry said it was alive:
        # this is a REVOCATION, not an expiry — saying "expired on <date>"
        # would be false, and would send them looking for an expiry that is not at fault.
        raise _bad(
            "Instagram no longer recognizes this account's authorization: it was "
            "revoked, or the account changed. Reconnect it from your connectors "
            "page, connector \"Instagram (statistiques)\".") from e
    except (coeur.InstagramApiError, ValueError) as e:
        raise _bad(f"Instagram could not serve {geste}: {e}") from e
    except Exception as e:
        logger.warning("instagram_meta: %s failed — %s", geste, type(e).__name__)
        raise _bad(
            f"Instagram did not respond for {geste} ({type(e).__name__}). This is not "
            "an authorization refusal — retry, and if it persists, the problem is on "
            "Instagram's side.") from e


def avertir_au_demarrage() -> None:
    """What the operator needs to know AT BOOT, in one line. Never raises."""
    ig_auth.avertir_au_demarrage()
    try:
        _coeur()
    except RuntimeError as e:
        logger.warning(
            "instagram_meta: connector mounted but the core is not installed — "
            "the `instagram_meta_*` tools will refuse, saying so. Detail: %s", e)
