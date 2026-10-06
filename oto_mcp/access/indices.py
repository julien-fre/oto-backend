"""The HINTS for "nothing resolves" refusals — what a credential refusal adds
so the agent knows what to do, rather than a bare "no key".

Extracted from `rbac` on 2026-09-24 (oto-backend#499), when the second project hint
pushed it past the package's size bound: `rbac` says who has the RIGHT and lists
what is within reach; this module turns that into TEXT for an already-raised refusal.

Two shared properties, which are the reason for keeping them together:

- **read-only, never a resolution**: we name a gesture (`group=`, `_project=`),
  the agent performs it — nothing silently switches a call onto someone else's bill;
- **fail-soft**: a hint is added to a refusal, a DB hiccup returns the refusal
  without it, never a 500 in its place.

Depends on `rbac` (instances within reach); `resolve` calls it at refusal time.
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import credentials_store, db, links, providers
from . import rbac

logger = logging.getLogger(__name__)


_REVOKED_REASON_LABELS = {
    db.REVOKED_CREDENTIAL_REMOVED: "key removed",
    db.REVOKED_RENAMED_ONTO_EXISTING: "account renamed onto another one already set",
    db.REVOKED_VAULT_ROW_MISSING: "vault row gone (maintenance)",
}


def _revoked_hint(sub: str, org: Optional[int], provider: str) -> str:
    """Actionable suffix for "no credential configured" errors: says whether THIS
    connector existed here and was then REMOVED, rather than letting people believe it
    was never set. Empty string if nothing ever existed, or without an org (no member
    scope to query).

    oto#42, entry 11 of batch 1 — four reports the same day (03/09) for this
    single cause: each ran its own investigation to find information already in the
    database. Read-only (`connector_instances.most_recent_revocation`), never a
    routing criterion — see its docstring.

    Fail-soft BY CONSTRUCTION (like `chain_shadow.observe`): it is a hint ON TOP
    of an already-raised refusal, never a path resolution depends on — a DB
    hiccup here must return the normal refusal (without the second hint), not replace
    the refusal with a 500."""
    if org is None:
        return ""
    try:
        rev = db.most_recent_revocation("member", credentials_store.member_id(org, sub), provider)
    # noqa: SILENT — best-effort hint: a DB hiccup leaves the refusal WITHOUT the second hint
    except Exception:
        logger.warning("revocation hint unavailable for %s (fail-soft)", provider,
                       exc_info=True)
        return ""
    if not rev:
        return ""
    motif = _REVOKED_REASON_LABELS.get(rev["revoked_reason"], rev["revoked_reason"])
    quand = str(rev["revoked_at"])[:10]
    return f"\n(a `{provider}` existed here and was removed on {quand} — {motif})"


def _projects_pinning(sub: str, org: Optional[int], provider: str) -> list[dict]:
    """Projects READABLE by `sub` in `org` that already pin an instance of
    `provider` (oto-backend#499). Readable = the set-based scoping of `op=list`
    (`ownership.accessible_project_ids`): the hint is a visibility object, it never
    names another entity's project. Read-only — we NAME the gesture
    (`_project=`), we resolve nothing. Fail-soft: hiccup ⇒ [] (hint without this line)."""
    from .. import ownership
    try:
        return db.projects_pinning_instance(
            ownership.accessible_project_ids(sub, org), provider)
    # noqa: SILENT — best-effort hint: a DB hiccup leaves the refusal with the generic hint
    except Exception:
        logger.warning("project-pinning hint for %s unavailable (fail-soft)", provider,
                       exc_info=True)
        return []


def _reachable_hint(sub: str, org: Optional[int], provider: str) -> str:
    """Actionable suffix for "nothing resolves" errors: surfaces the instances
    within reach with the pin GESTURE for each — call token first (`_group=`/
    `_org=`, per-call, stateless), `_instance=` for fine grain. Empty string if
    nothing is within reach.

    The durable advice depends on what EXISTS (#499): if a readable project already
    pins an instance of this provider, we name THAT project and the gesture that
    activates it (`_project=<id>` on the call) — "link the instance to your project"
    sent people to redo a link already set, and the agent fumbled. The link advice
    only shows up by default."""
    items = rbac.reachable_instances(sub, org, provider)
    # The project binding is read under the CARRIER, as resolution reads it
    # (`scope.project_pinned_instance(porteur)`); the text keeps the called name.
    pins = _projects_pinning(sub, org, providers.credential_provider(provider))
    if not items and not pins:
        return ""
    out = ""
    if items:
        lines = []
        for it in items[:4]:
            if it["kind"] == "group":
                lines.append(
                    f"· team « {it['name']} » → pass group={it['id']} on the call "
                    f"(or instance=group:{it['id']}:{provider})")
            else:
                lines.append(
                    f"· org « {it['name']} » → pass org={it['id']} on the call")
        more = len(items) - 4
        if more > 0:
            lines.append(f"· … +{more} (oto_instance op=list to see all)")
        # The hint names keys that belong to the cited entity, and a contractor
        # is a member of ITS clients' orgs: without this caveat, it reads as
        # self-service and the agent switches the call onto a client's credits for
        # work that is not theirs (reported 12/08 on in-house prospecting
        # redirected to a client org's key).
        out += (f"\nNB — `{provider}` keys exist within reach, at the cited entity's "
                f"expense — only switch a call to one if it is made for that entity:\n"
                + "\n".join(lines))
    if pins:
        # Same caveat: the pinned instance has a payer; we name the gesture, the agent
        # performs it if it works for this project — nothing resolves on its own.
        if not items:
            out += (f"\nNB — a project you can read pins a `{provider}` instance, "
                    f"at the expense of the entity that owns it — only pass it if the call "
                    f"is made for that project:")
        out += "\n" + "\n".join(
            f"· project #{p['id']} « {p['name']} » already pins a "
            f"`{provider}` instance → pass _project={p['id']} on the call"
            for p in pins[:4])
        if len(pins) > 4:
            out += f"\n· … +{len(pins) - 4} project(s) (oto_project op=list)"
    else:
        out += "\nDurable: link the instance to your project (oto_project op=link)."
    return out


def _poser_ou_accorder(sub: str, lien_org, porteur: str) -> str:
    """The gesture proposed when no `porteur` key resolves. Setting your own key, always;
    lending a PLATFORM key only if oto holds one for this connector.
    Without it, "ask an admin to grant you a platform key" pointed to an impossible
    gesture — reported by an org_admin who had done exactly that
    (#1156). Lending is up to oto's admins, not the org's: we say so.

    Fail-soft like the other refusal hints (`_revoked_hint`): a DB hiccup
    here returns the refusal without the second proposal, never a 500 in its place."""
    poser = f"Set your own key{links.ou_poser_la_cle(sub, org=lien_org, connecteur=porteur)}"
    try:
        pretable = bool(credentials_store.list_platform_instances(porteur))
    # noqa: SILENT — best-effort hint: a DB hiccup leaves the refusal without offering the loan
    except Exception:
        logger.warning("platform keys `%s` unreadable for the refusal (fail-soft)", porteur,
                       exc_info=True)
        return f"{poser}."
    if not pretable:
        return f"{poser} — oto does not provide a `{porteur}` platform key."
    return (f"{poser}, or ask oto's admins to lend your org the "
            f"`{porteur}` platform key.")
