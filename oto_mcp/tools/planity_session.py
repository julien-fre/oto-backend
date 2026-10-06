"""Planity — the per-credential SESSION, foundation of the tool modules.

Opening a Planity session is expensive — several network round trips before the
first useful read. Reopening it on every tool call would make the connector
unusable.

A live client is therefore kept **per credential**, key = SHA-256 fingerprint of
`email:password` — never the plain identifier, since this dictionary's key lives
in process memory and can be read in a dump. The client is closed after a period
of inactivity. Nothing is persisted: the reference credential is in the vault, the
session exists only in RAM and dies with the process.

⚠️ **The pool is bound to ITS event loop.** A `PlanityClient` holds WebSockets and
an async httpx client, which only make sense in the loop where they were opened.
The server is single-loop, so in production this case never occurs; an entry from
another loop is treated as absent and dropped — never handed back, because a
client from a dead loop would fail later, far from here, with a message that
wouldn't mention any of this.

⚠️ **No module-level lock.** An `asyncio.Lock` created at import binds to the first
loop that awaits it and rejects the following ones. Instead, the pool entry
carries the opening TASK: two concurrent calls for the same credential find the
same task and wait for the same client, which is exactly what a per-key lock
provided — without the global state that breaks.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..mcp_errors import McpError

if TYPE_CHECKING:  # the annotation only — never evaluated at runtime
    from oto.tools.planity import PlanityClient

log = logging.getLogger("oto_mcp.tools.planity")

#: The three coordinates of the Planity application, under the `planity` connector
#: in `connector_settings`, PLATFORM scope.
#:
#: ⚠️ **They are public by design, and they are not secrets**: any browser that
#: opens `pro.planity.com` receives them. They identify the Planity application
#: and authorize nothing on their own — what authorizes is the person's password,
#: which lives in the ENCRYPTED vault. They can therefore appear in an error
#: message or a debug log without it being a leak.
#:
#: That is also why they are NOT in the vault: storing three public identifiers,
#: identical for everyone, there would guarantee that the next reader treats them
#: as a secret — rotation, alerts, wasted time. The vault also rejects a platform
#: key for a connector without `platform` in its `auth_modes`, and adding it would
#: make the connector sheet announce that a platform key can serve the user, which
#: is false.
#:
#: If they are not hardcoded in oto-core, it is for GENERICITY: that repo is public
#: and open source, and a client published there describes a protocol — it doesn't
#: ship a third-party company's coordinates as if it were their official
#: integration. They belong to Planity; the instance operator is the one who sets
#: them and answers for what they call.
_REGLAGES = ("firebase_api_key", "firebase_app_id", "rest_api")

#: The command that sets them. It lives IN the refusal message: a diagnostic that
#: doesn't state the action sends people searching, and that is how a valid
#: configuration gets redone six times.
_COMMANDE = ('oto_admin_connector_setting(op="set", connector="planity", '
             'key="<key>", value="<value>")')


def _reglages() -> dict:
    """The coordinates set in the database, platform scope. `{}` if the database is silent.

    What the `connector_settings` rule protects: **no read of this table on the hot
    path of a tool call**. Its first reader, the cardinality check, is consulted up
    to four times per call, on a single-loop server, against a remote managed
    database — one read per consultation there would be the freeze that
    `docs/event-loop-perf.md` documents, hence the in-memory snapshot.

    This read complies, and it is measurable: it only happens at **construction of
    a Planity client** — at most once per credential and per pool TTL (30 min),
    never per call — and it runs in a worker thread (`asyncio.to_thread` in
    `_client`), so outside the loop. A cold path, off the loop: both halves of the
    property.

    What it buys: one write, and all deployed colors (blue, green, canary) see it
    at the next client — without a restart AND without a per-process `op=reload`,
    which an in-memory snapshot would have required. An instance's setting must
    not depend on who remembers to reload what.
    """
    from ..db import connector_settings as store

    return {r["key"]: (r["value"] or "").strip()
            for r in store.list_connector_settings()
            if r["scope_type"] == "platform" and r["connector"] == "planity"
            and r["key"] in _REGLAGES}


def coordonnees_manquantes() -> list[str]:
    """Those of the three keys that are missing from the database. Empty = all there."""
    poses = _reglages()
    return [nom for nom in _REGLAGES if not poses.get(nom)]


def _coeur():
    """The `oto.tools.planity` package of oto-core, imported AT CALL TIME.

    Imported here and not at module load, for a PRODUCT reason: the connector stays
    **registered** even when oto-core's `planity` extra is missing or the
    coordinates are not set. It is then visible, selectable, and every call refuses
    by NAMING what is missing — instead of vanishing from the catalog, which goes
    unnoticed and can't be explained."""
    # `importlib` and not `import oto.tools.planity as …`: `oto` is a namespace
    # package (PEP 420) shared between oto-core and oto-cli, and the
    # `import a.b.c as x` form resolves by attribute on the parent there — which
    # fails when the subpackage doesn't exist yet on that path. `import_module`
    # goes through the normal import mechanism and returns what is already loaded.
    import importlib

    try:
        coeur = importlib.import_module("oto.tools.planity")
    except ImportError as e:
        raise _bad(
            f"The `planity` connector is not installed on this instance: it "
            f"needs oto-core's `planity` extra (`oto-core[planity]`), which brings "
            f"`httpx` and `websockets`. This is an instance configuration, not "
            f"a problem with your account — notify the operator. Detail: {e}") from e
    return coeur


def endpoints():
    """The Planity application's coordinates, or a refusal that NAMES what is missing.

    The refusal is the point: an unconfigured connector must say which side is at
    fault. Without it, the call fails further on with a Firebase 400, which the
    error chain translates — correctly, but wrongly here — into "email or password
    refused". The user would then re-enter a perfectly good credential, in a loop.
    And it names the ACTION, not just what's missing."""
    poses = _reglages()
    manquantes = [nom for nom in _REGLAGES if not poses.get(nom)]
    if manquantes:
        raise _bad(
            f"The `planity` connector is not configured on this instance: "
            f"{', '.join(manquantes)} missing. This is not your credential — "
            f"there is nothing to re-enter on your side: notify the instance "
            f"operator, who sets them with {_COMMANDE}.")
    return _coeur().PlanityEndpoints(
        firebase_api_key=poses["firebase_api_key"],
        firebase_app_id=poses["firebase_app_id"],
        rest_api=poses["rest_api"],
    )


def fenetre(date_from, date_to, preset):
    """`(gte_ms, lte_ms)` — the date window, resolved by the core."""
    return _coeur().resolve_range(date_from, date_to, preset)


def iso(ms):
    """A Planity timestamp (milliseconds) as ISO Europe/Paris, or `None`."""
    return _coeur().ms_to_iso(ms)


def periode(gte_ms, lte_ms) -> dict:
    """The covered window, STATED — bounds, number of days, timezone, and whether it
    ends today.

    A revenue figure without its window invites projecting from it, and that is
    where it breaks: most presets end at NOW, not at the end of the day. The last
    day is therefore partial, a rate computed on it is too low, and "at the current
    rate there are N days left" comes out wrong with nothing signaling it.
    `ends_today` and `complete` are there so the agent knows instead of assuming,
    and `days` so it doesn't recount a duration it is given."""
    import time

    debut, fin = iso(gte_ms), iso(lte_ms)
    aujourdhui = iso(int(time.time() * 1000))[:10]
    jours = None
    if debut and fin:
        from datetime import date

        d, f = date.fromisoformat(debut[:10]), date.fromisoformat(fin[:10])
        jours = (f - d).days + 1
    finit_aujourdhui = bool(fin) and fin[:10] == aujourdhui
    return {
        "from": debut, "to": fin,
        "from_date": debut[:10] if debut else None,
        "to_date": fin[:10] if fin else None,
        "days": jours,
        "timezone": "Europe/Paris",
        "ends_today": finit_aujourdhui,
        # The last day isn't over: any daily rate computed on this window
        # underestimates it, and so does a projection built on it.
        "complete": not finit_aujourdhui,
    }


def avertir_au_demarrage() -> None:
    """States AT BOOT what will prevent the connector from serving. Never raises.

    The connector stays registered: without this line, an operator who forgot a
    variable would only learn about it at a user's first call."""
    try:
        manquantes = coordonnees_manquantes()
    except Exception as e:  # noqa: SILENT — at boot the database may not be ready; the call itself will say everything
        log.info("planity: configuration not verifiable at startup (%s) — the "
                 "first call will decide.", type(e).__name__)
        manquantes = []
    if manquantes:
        log.warning(
            "planity: connector mounted but NOT configured — %s missing. The "
            "`planity_*` tools will refuse and say so. Set with: %s",
            ", ".join(manquantes), _COMMANDE)
    try:
        _coeur()
    except McpError as e:
        log.warning(
            "planity: connector mounted but the core is not installable — "
            "install the `oto-core[planity]` extra (httpx + websockets). The "
            "`planity_*` tools will refuse and say so. Detail: %s", e)

#: A client unused for this long is closed. Long enough for an entire
#: conversation to reuse the same session, short enough not to hold a WebSocket
#: open at Planity all day after a single call.
_TTL_INACTIF = 30 * 60


@dataclass
class _Entree:
    boucle: asyncio.AbstractEventLoop
    ouverture: "asyncio.Future[PlanityClient]"
    dernier_usage: float


_entrees: dict[str, _Entree] = {}


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _eur(cents: int | float | None) -> float:
    """Planity counts in CENTS; we return euros at the tool boundary.

    Lives here because the four tool modules need it: duplicated, the conversion
    would eventually diverge by a factor of 100 in just one of them, which reads
    as a revenue figure and not as a bug."""
    return round(float(cents or 0) / 100, 2)


def _eur_ou_rien(cents: int | float | None) -> float | None:
    """Like `_eur`, but an ABSENT amount stays absent instead of being zero.

    `_eur(None)` returns `0.0`, which is right when zero is a real zero and wrong
    everywhere else: a service with no price becomes free, a batch with no purchase
    price becomes costless, and a margin gets computed on it. A `None` that crosses
    over says "we don't know"; a `0.0` pretends to know, and never raises."""
    if cents is None:
        return None
    return round(float(cents) / 100, 2)


def _refus_planity(e: BaseException, geste: str) -> McpError:
    """Translates a Planity refusal into an ACTIONABLE error, or relays it as is.

    The case that matters is Firebase's 400 at step 1 of the auth chain: it means
    "this email or password is wrong", and without this reading it surfaces as a
    raw `HTTPStatusError` that the agent interprets as a service outage — hence
    "retry", when retrying cannot succeed.

    ⚠️ **The text of an upstream exception NEVER crosses this boundary.** This
    connector is the only one whose credential is a PASSWORD, and it passes it as
    an argument to three functions: any exception built with those arguments — a
    `RuntimeError(f"... {email} / {password}")` from a later core version, from an
    intermediate lib, from an error case we haven't written — would end up word for
    word in the response returned to the agent, hence in a transcript and in the
    call log. No real case does this today (httpx puts the URL in its messages,
    never the request body; measured): that is precisely why the door had to be
    closed BEFORE having an incident to tell. We therefore return the TYPE and, if
    it exists, the HTTP status — never the message. What we lose in diagnosis, the
    server log has."""
    if isinstance(e, McpError):
        return e            # already actionable — retranslating it would impoverish it
    statut = getattr(getattr(e, "response", None), "status_code", None)
    if statut in (400, 401, 403):
        return _bad(
            f"Planity refused {geste} ({statut}): it is the Planity account's email "
            "or password that is wrong, not an argument of the call — "
            "replaying it identically will fail the same way. Re-enter the `planity` "
            "credential on your connectors page, with the `pro.planity.com` login.")
    porte = f" (HTTP {statut})" if statut else ""
    log.warning("planity: %s failed — %s%s", geste, type(e).__name__, porte)
    return _bad(
        f"Planity did not respond to {geste}: {type(e).__name__}{porte}. This is "
        "not a credential refusal — retry, and if it persists, the problem is on "
        "Planity's side.")


async def _ouvrir(email: str, password: str, coordonnees) -> "PlanityClient":
    """Builds a client and VALIDATES the credential right away.

    Authentication is played here, not at the first business call: a wrong
    credential must fail on "connection refused", not later on "this salon is not
    accessible", which reads like a permissions problem at Planity."""
    client = _coeur().PlanityClient(email, password, coordonnees)
    try:
        await client.auth.get_tokens()
    except BaseException:
        await client.close()
        raise
    log.info("planity: session opened (pool = %d)", len(_entrees) + 1)
    return client


async def _evincer_les_inactifs(boucle: asyncio.AbstractEventLoop) -> None:
    maintenant = time.monotonic()
    for cle, entree in list(_entrees.items()):
        if entree.boucle is not boucle:
            # Dead loop: we can neither close nor reuse it. We SAY so.
            log.warning("planity: session dropped (event loop changed)")
            _entrees.pop(cle, None)
            continue
        if maintenant - entree.dernier_usage <= _TTL_INACTIF:
            continue
        _entrees.pop(cle, None)
        if not entree.ouverture.done():
            # Opening never finished yet inactive: letting it run with nobody
            # waiting for it would hold a client outside the pool, invisible and
            # never closed.
            entree.ouverture.cancel()
        elif not entree.ouverture.cancelled() and entree.ouverture.exception() is None:
            try:
                await entree.ouverture.result().close()
            # noqa: SILENT — best-effort close of a client already removed from the pool
            # Same rule as above: the TYPE, never the text — this
            # exception is born on a client built with the password.
            except Exception as e:
                log.info("planity: closing an inactive session failed (%s)",
                         type(e).__name__)
        log.info("planity: inactive session released (pool = %d)", len(_entrees))


async def _client(account: Optional[str] = None) -> PlanityClient:
    """THIS caller's Planity client — from the pool, or opened on demand.

    The name and the UNquoted return annotation are a contract: the version-skew
    probe (`tests/test_tools_client_methods_exist.py`) recognizes factories named
    `_client` and reads the class they return to verify, at the pinned oto-core
    tag, that every method called by the tools exists. Renaming this function or
    quoting its annotation takes the connector out of that coverage SILENTLY — and
    this connector is the one that needs it most, its core living in the other
    repo.

    The credential (email + password) is resolved from the vault by the normal
    cascade (`byo_user`, member tier): resolution hits the database, so it runs in
    a worker thread, never in the loop."""
    # The instance's coordinates BEFORE the credential: an unconfigured instance
    # must say so, not blame the user's password.
    # Both reads run in a WORKER THREAD: they hit the database, and this server is
    # single-loop (`docs/event-loop-perf.md`).
    coordonnees = await asyncio.to_thread(endpoints)
    fields = await asyncio.to_thread(
        access.resolve_credential_fields, "planity", account)
    email, password = fields.get("email"), fields.get("password")
    if not email or not password:
        raise _bad(
            "The `planity` credential on file is incomplete: it needs both the email "
            "AND the password of the `pro.planity.com` account. Re-enter it on your connectors page.")

    boucle = asyncio.get_running_loop()
    await _evincer_les_inactifs(boucle)

    cle = hashlib.sha256(f"{email}:{password}".encode()).hexdigest()
    entree = _entrees.get(cle)
    if entree is None or entree.boucle is not boucle:
        entree = _Entree(
            boucle, asyncio.ensure_future(_ouvrir(email, password, coordonnees)),
            time.monotonic())
        _entrees[cle] = entree
    try:
        client = await entree.ouverture
    except BaseException as e:
        # A failure is NEVER cached: otherwise a corrected password would
        # keep failing until the TTL expires.
        _entrees.pop(cle, None)
        raise _refus_planity(e, "the connection to your account") from e
    entree.dernier_usage = time.monotonic()
    return client
