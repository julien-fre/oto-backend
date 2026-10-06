"""EFFECTIVE readiness of a connector: "does it WORK?", layer by layer.

SINGLE diagnostic seam, born from signal **#476** (org 196, 2026-08-16). The connector
card rendered `state:"active"` + `recommended:true`, the `oto_instance(op="verify")`
probe answered `ok:true` — and nothing could be sent: no hosted channel was linked,
`free_tier.daily_quota` was 0. **Three green readings, capability missing.** The
operator read "active" as "connected", which is the natural reading, and searched for
five days in the wrong place.

The cause is not a wrong computation: each surface published ONE layer while letting
people believe it answered for all three. `state` only says "the member installed it
in their toolbox"; `verify` only tests the resolved key; `identities` only counts the
linked accounts. `docs/connector-model.md` has named the three layers for a long
time — what was missing was the place that reads them TOGETHER, and says so.

Two surfaces consume it, which is why it lives here rather than in either of them:
the connector card (`capabilities/connectors/selection`, verdict `ready`) and the
identity list (`capabilities/connectors/identities`, the WHY of an empty list —
signal **#504**). A second wording of the same verdict would reopen exactly the
divergence being repaired — it already happened between `option_ok` and
`status_for.subscribed` (fixed on 2026-07-07, see `access.option_open`).

⚠️ **The diagnosis does NOT include the selection state** (`not_selected` / `paused`),
and that is deliberate: a non-selected connector is still callable through `oto_call`
(universal dispatch, ADR 0036). Signal **#577** proved it in prod — the seven
"invisible" tools all answered on the first try against the org credential.
Selection governs tool VISIBILITY, not readiness. Mixing the two would recreate the
#476 confusion under another name.

⚠️ **The cost is real, and it dictates usage.** Measured in prod on 2026-08-28 (real
account, catalog of 90 connectors, org 196): `credential_mode_for` costs **1,993 ms
for all 90** — one cascade walk per connector, ≈22 ms each — and the server is
SINGLE-LOOP. A single connector: **~244 ms**. Hence the rule for callers: diagnose
on DEMAND (targeted read), and SAY that the rest was not computed rather than let an
absence pass for a clean bill of health.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import credentials_store, links, status_hints

# Stable machine tokens (the order is the evaluation order, see `diagnose`).
PAID_OPTION_OFF = "paid_option_off"      # layer 3 — the option is not unlocked
NO_CREDENTIAL = "no_credential"          # layer 2 — no key resolves
OVER_QUOTA = "over_quota"                # layer 2 — the key resolves, the day's quota is spent
CREDENTIAL_REJECTED = "credential_rejected"  # layer 2 — the key resolves, UPSTREAM refuses it
PENDING_STEP = "pending_step"            # layers OK, one action left (link a channel…)


@dataclass(frozen=True)
class Diagnosis:
    """The FIRST missing layer, and the action that unlocks it.

    `reason` is a stable machine token; `next_step` is the sentence that ALL surfaces
    render as-is (agent, front, error message) — never reworded downstream, otherwise
    two surfaces tell two versions of the same fact."""
    reason: str
    next_step: str


def _connections_url(sub: Optional[str]) -> str:
    """Where a hosted account is CONNECTED (hosted-auth) — same tenant rule."""
    from .. import config
    return f"{config.dashboard_url_for(sub)}/console/connections"


def diagnose(sub: str, connector: str, *, org, group) -> Optional[Diagnosis]:
    """`None` = the three layers are unlocked AND nothing is pending: a call would go
    through. Otherwise the FIRST missing layer, in the order of
    `docs/connector-model.md` — this order decides what the refusal NAMES when several
    are missing at once, and it goes from the most encompassing to the finest.

    ⚠️ `org` / `group` are REQUIRED explicitly (no default): this diagnosis is also
    computed FOR A THIRD PARTY, and `current_org` is scoped to the current ACTOR.
    Letting it drift would read the requester's org in someone else's state — the
    2026-06-24 bug (`has_option(target)` reading the requester's org), and the reason
    `access._UNSET` exists."""
    from .. import access

    # Layer 3 — the paid option. First because it is encompassing: option closed ⟹
    # neither a usable platform key nor a possible connection. `paid_option_for`
    # first, so that we only talk about options for connectors that have one (most
    # only have layers 1+2, and `option_open` answers `True` for them by construction).
    opt = access.paid_option_for(connector)
    if opt is not None and not access.option_open(sub, connector, org=org):
        return Diagnosis(PAID_OPTION_OFF, (
            # WHICH admin (oto#108): "granted by an admin" made the org administrator
            # think they were the target and look for an action they don't have.
            f"The `{opt}` option is not open for you here: an org administrator "
            f"must subscribe to the plan that includes it (billing page), the "
            f"platform team must grant it to the org, or you can use your OWN "
            f"`{connector}` key — a key of your own unlocks the option by construction "
            f"(there is no platform seat left to protect). Set it"
            f"{links.ou_poser_la_cle(sub, org=org)}."))

    # Layer 2 — the key. `credential_mode_for` is the MIRROR of the real cascade
    # (`resolve_credential`): the card's verdict therefore cannot diverge from what
    # the call would do. `forbidden` = nothing resolves; this is NOT an RBAC refusal.
    # ⚠️ The key may belong to ANOTHER connector (`Connector.credential_of` — hosted
    # messaging channels borrow the provider account's key). The message must then
    # name the card where it is SET, not the one being diagnosed: sending someone to
    # the "Whatsapp section" of their account page is a dead end, there is no field.
    # Layer 1 (above) stays on the REQUESTED connector — its activation and its ACL
    # are what govern it.
    from .. import providers
    porteur = providers.credential_provider(connector)
    # ⚠️ A carrier that HOLDS no credential (`secret_kind="none"`: law, web, culture,
    # land registry, osm… — outside `CREDENTIAL_PROVIDERS`) has NO layer 2. Walking
    # the cascade anyway returned `forbidden` by construction (nothing to resolve),
    # read as `no_credential`: the card sent people to set a key that doesn't exist,
    # on a connector whose own sheet says `auth: none` (oto#173).
    # `mode = None` = "no key involved" — no quota or rejection to read either.
    if porteur not in providers.CREDENTIAL_PROVIDERS:
        step = status_hints.pending_action(connector, sub, org, group, {"mode": None})
        return Diagnosis(PENDING_STEP, step) if step else None
    mode = access.credential_mode_for(sub, connector, org=org, group=group)
    if mode == "forbidden":
        return Diagnosis(NO_CREDENTIAL, (
            f"No `{porteur}` key resolves for you in this org: set "
            f"your own{links.ou_poser_la_cle(sub, org=org, connecteur=porteur)}, or "
            f"ask an admin to lend you the platform key."))
    if mode == OVER_QUOTA:
        # Distinct from `no_credential` ON PURPOSE: the key is fine, it is the day
        # that is over. Confusing them sends people to reconfigure a healthy credential.
        return Diagnosis(OVER_QUOTA, (
            f"The platform `{porteur}` key's quota is exhausted for today — the "
            f"key resolves, it is just out of steam. Set your own "
            f"key{links.ou_poser_la_cle(sub, org=org)} to continue without limit, "
            f"or try again tomorrow."))

    # Layer 2 (continued) — the key resolves, but the PROVIDER refused it. The verdict
    # already existed in the database (`meta.health_ko`) and had no reader here: a
    # revoked key rendered `ready: true`, and its holder only learned about the refusal
    # on the first call, as the raw upstream message (#541, org on `linear`:
    # "AUTHENTICATION_ERROR: Authentication required, not authenticated"). AFTER
    # `no_credential` on purpose — saying "rejected" about a key that doesn't exist
    # would send people to re-set what they never set.
    #
    # ⚠️ `meta.health_ko` has FOUR writers since the removal of MCP federation
    # (2026-09-09, ADR 0069 — there were six): the `oto_instance op=verify` probe
    # (broad coverage, but nothing to read until someone replays it);
    # `credentials_for` in `auth/google.py`, which marks its OWN row on a failed
    # refresh without going through `verify`, at MEMBER scope; and
    # `tools/salesforce.py` / `tools/zoho.py`, which mark on refresh refusal
    # (`SalesforceAuthError`/`ZohoAuthError`) in the tool itself, not only in
    # their `verify` probe. All four go through the SAME shared helper and the
    # SAME scope guard (`connectors/health.py`). This diagnosis reads the
    # result, whoever wrote it: it never had to change for that, and that is the point.
    rejet = access.credential_rejection_for(sub, connector, org=org, group=group)
    if rejet and rejet.startswith(credentials_store.NO_QUOTA_REASON_PREFIX):
        # Credits exhausted: the key is GOOD, it is the account that is dry. Saying
        # "set it again" would send people to re-set the same key, which would fail
        # the same way.
        qui = ("" if mode == "user" else
               f" This is a `{mode}`-tier key: its owner is the one who tops it up.")
        return Diagnosis(CREDENTIAL_REJECTED, (
            f"The `{porteur}` key that resolves for you here is OUT OF CREDITS at the provider: "
            f"{rejet}. Top up the account's credits at `{porteur}`, or set another "
            f"key.{qui} The finding clears itself on the first successful call, or "
            f"by replaying `oto_instance op=verify`."))
    if rejet:
        ou = (f"Set it again{links.ou_poser_la_cle(sub, org=org, connecteur=porteur)}."
              if mode == "user" else
              f"This is a `{mode}`-tier key: ask an admin to set it again.")
        return Diagnosis(CREDENTIAL_REJECTED, (
            f"The `{porteur}` key that resolves for you here was REJECTED by the service "
            f"on the last test: {rejet}. {ou} The finding clears by replaying "
            f"`oto_instance op=verify` (a success erases it), or by setting the key again."))

    # The action that remains. Generic `status_hints` seam: the specifics (unipile =
    # "link a channel", zoho/salesforce = "authorize oto") live in the connector's
    # module, never here. We RELAY its wording, we don't reword it.
    step = status_hints.pending_action(connector, sub, org, group, {"mode": mode})
    if step:
        return Diagnosis(PENDING_STEP, step)
    return None


def no_identity_step(sub: Optional[str], connector: str, noun: str = "account") -> str:
    """The default action when the identity list is empty without any layer
    missing (#504): the connector declares no `status_hints`, but silence is still
    the default to repair — we name the state rather than returning a bare `[]`.
    `noun` = the provider's WORD (`access.account_noun`): saying "account" for a Slack
    workspace forces the reader to translate."""
    return (f"No {noun} is linked to `{connector}` yet: the key resolves, one still "
            f"has to be connected from {_connections_url(sub)}.")
