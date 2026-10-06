"""The organization kit — ONE application function (ADR 0050 §E8, oto#166).

The kit (`orgs.default_connectors`) is the list of connectors an org installs
in its members' toolbox — those who arrive (at seeding,
`session_visibility`) AND those who are already there (here, at the admin's action).
Every org action on those toolboxes goes through `appliquer`:

| action (capability)                                | call                               |
|----------------------------------------------------|------------------------------------|
| set the whole kit (`connectors.recommend`)         | `appliquer(org, kit=[…])`          |
| add to the kit (`connectors.bulk_select`)          | `appliquer(org, ajouter=[name])`   |
| remove from the kit (`connectors.unset_default`)   | `appliquer(org, retirer=[name])`   |
| push to ONE member (`connectors.force.member`)     | `appliquer(org, ajouter=[name],`   |
|                                                    | `          pousser_a=sub)`         |

**Write guard (§E2)**: an ADD to the kit names a connector known to the registry and
exposed for the org, otherwise the whole action is refused, reason named, nothing is written
(`AjoutRefuse`). A connector cut AFTER being put in the kit stays there: installed, hidden
for everyone, it comes back on its own on reopening — the response lists it (`cut`).

Only the DIFFERENCE between the old and the new kit applies to members
(decision Q4 of 11/09: "a future change applies to all members,
former ones included; what was set before is not replayed"). A connector named
by the action but already in the kit is replayed for nobody, and the response says so
(`unchanged`).

An add installs for each member of the org, provenance `kit` (§E4), through
`selection.install_for_member` — never over the member: an existing row
(active or paused) stays, a member's removal is not undone. The kit and the
toolboxes are written in ONE transaction, the org row locked (`FOR
UPDATE`): two admins changing the kit at the same time don't compute their
difference on the same "before".

A REMOVAL from the kit (decision Q1 of 11/09) uninstalls the connector for each member
whose row carries the provenance `kit`, active or paused — and nowhere else:
installed or taken back by the member (`membre`), pushed by an admin (`admin`), coming from
the base (`socle`) or predating the trace (`inconnue`), it stays.

The PUSH to a member (decision Q2) installs for them alone, provenance `admin`, with
the same exceptions — it doesn't touch the kit, and never undoes their removal.

The response is counted per connector: installed for N, already active for M, left
for P who have it paused, left for R who removed it themselves. Visible on screen right away; for a
member's agent, at their NEXT conversation — the tool registry is frozen at opening.
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import selection as sel

ADDED = "added"
REMOVED = "removed"

AGENT_NOTE = ("Effect visible on screen right away; for a member's agent, at their "
              "NEXT conversation — the tool registry of an open conversation "
              "is frozen and no write changes anything there.")
# Served to the ADMIN when their action names a connector already in the kit (reading of
# Q4 retained on 11/09/2026, ADR 0050 §E): their click was received, there was nothing
# to change. The text says WHY and HOW to proceed if they really want it for the
# current members — an admin decision, not a platform catch-up. It
# is true before as after the application of Q1: a removal from the kit
# never uninstalls anything but what the kit itself set.
UNCHANGED_NOTE = (
    "Already in the kit: your action was received, but it doesn't change the kit, so "
    "it installs nothing for the current members. The kit only applies to members who "
    "already joined its CHANGES made since 11/09/2026; what it contained before "
    "is not replayed. To install it for the current members anyway: remove it "
    "from the kit, then add it back. That removal uninstalls nothing the kit didn't set "
    "itself; the re-add installs it for each member who doesn't have it — except for whoever "
    "removed it themselves since 11/09/2026, who keeps it removed.")


CUT_NOTE = ("Cut for your organization: it stays in the kit and installed for your "
            "members, but hidden for everyone while it is cut; it comes back on its own when you "
            "make it available again.")

# Reason for an add refusal, as served (E2: "the refusal says why").
RAISONS = {
    "unknown": "is unknown to the connector registry",
    "platform_disabled": "is cut by the platform: your organization can't install it",
    "org_disabled": ("is not available to your members (your organization cut it): "
                     "make it available first"),
}


class OrgInconnue(LookupError):
    """The targeted org doesn't exist."""


class AjoutRefuse(ValueError):
    """An add names a connector the org can't install (ADR 0050 §E2).
    `refus` = `[{"connector", "reason"}]`, reason ∈ `RAISONS`. Raised BEFORE any
    write, within the transaction: nothing is written, neither to the kit nor for the members."""

    def __init__(self, refus: list[dict]):
        self.refus = refus
        super().__init__("; ".join(f"{r['connector']}: {r['reason']}" for r in refus))


def refus_d_ajout(org_id: int, noms: Iterable[str]) -> list[dict]:
    """E2 — only a connector known to the registry and exposed for the org may be ADDED
    to the kit. The guard applies to the action that adds; a connector cut AFTER being put
    in the kit stays there (see `coupes`). Returns the refusals, reason named; empty = all pass.
    Cut by the org (the master exposes it, the org override removes it) is distinguished from
    cut by the platform: those are not the same actions to reopen it."""
    from .. import providers
    from . import activation
    noms = list(noms)
    if not noms:
        return []
    expo = activation.exposed_connectors(org_id)
    refus = []
    for n in noms:
        if n not in providers.REGISTRY:
            refus.append({"connector": n, "reason": "unknown"})
        elif n not in expo:
            refus.append({"connector": n, "reason": "org_disabled"
                          if activation.is_exposed(n, None) else "platform_disabled"})
    return refus


def coupes(org_id: int, kit: Iterable[str]) -> list[str]:
    """The kit connectors the org no longer exposes: they stay there (the admin's
    intent), installed and hidden for everyone, and come back on their own on reopening."""
    from .. import providers
    from . import activation
    kit = list(kit)
    if not kit:
        return []
    expo = activation.exposed_connectors(org_id)
    return [n for n in kit if n in providers.REGISTRY and n not in expo]


def _dedupe(noms: Iterable[str]) -> list[str]:
    vus: set[str] = set()
    out: list[str] = []
    for n in noms:
        if n not in vus:
            vus.add(n)
            out.append(n)
    return out


def appliquer(org_id: int, *, kit: Optional[Iterable[str]] = None,
              ajouter: Iterable[str] = (), retirer: Iterable[str] = (),
              pousser_a: Optional[str] = None) -> dict:
    """Applies an org action to the kit and its members' toolboxes. See the module.
    `pousser_a=sub` = the named push: ONE connector (`ajouter`), ONE member,
    provenance `admin`, kit untouched."""
    from .. import db

    ajouter, retirer = _dedupe(ajouter), _dedupe(retirer)
    if kit is not None and (ajouter or retirer):
        raise ValueError("appliquer: `kit` (the whole kit) OR `ajouter`/`retirer`, not both")
    if pousser_a is not None and (kit is not None or retirer or len(ajouter) != 1):
        raise ValueError("appliquer: a push adds ONE connector to ONE member, without touching the kit")
    with db._connect() as conn:
        row = conn.execute("SELECT default_connectors FROM orgs WHERE id = %s FOR UPDATE",
                           (org_id,)).fetchone()
        if row is None:
            raise OrgInconnue(org_id)
        avant = list(row["default_connectors"] or [])
        if pousser_a is not None:
            nommes, apres = [], avant
        elif kit is not None:
            nommes = _dedupe(kit)
            apres = nommes
        else:
            nommes = ajouter + retirer
            apres = [n for n in avant if n not in set(retirer)] + [
                n for n in ajouter if n not in avant]
        ajouts = list(ajouter) if pousser_a is not None else [n for n in apres if n not in avant]
        retraits = [n for n in avant if n not in apres]
        refus = refus_d_ajout(org_id, ajouts)
        if refus:
            raise AjoutRefuse(refus)       # before any write: the transaction is cancelled
        if pousser_a is None and (ajouts or retraits
                                  or (kit is not None and row["default_connectors"] is None)):
            conn.execute("UPDATE orgs SET default_connectors = %s WHERE id = %s",
                         (apres, org_id))
        membres = [pousser_a] if pousser_a is not None else [r["sub"] for r in conn.execute(
            "SELECT sub FROM org_members WHERE org_id = %s ORDER BY joined_at, sub",
            (org_id,)).fetchall()]
        origine = sel.ADMIN if pousser_a is not None else sel.KIT
        effets: list[dict] = []
        for c in ajouts:
            comptes = {"installed": 0, "already_active": 0, "paused": 0,
                       "removed_by_member": 0}
            retire_le = None
            for m in membres:
                issue = sel.install_for_member(conn, m, c, org_id, origine)
                comptes[issue] += 1
                if issue == "removed_by_member" and pousser_a is not None:
                    retire_le = conn.execute(
                        "SELECT removed_at FROM connector_selection_removed "
                        "WHERE sub = %s AND org_id = %s AND connector = %s",
                        (m, org_id, c)).fetchone()["removed_at"]
            effet = {"connector": c, "change": ADDED, **comptes}
            if retire_le is not None:
                effet["removed_at"] = str(retire_le)
            effets.append(effet)
        for c in retraits:
            # E5, decision Q1: uninstalled where the KIT set it (active or paused),
            # and nowhere else. We count what remains, by provenance.
            cur = conn.execute(
                "DELETE FROM user_selected_connectors WHERE org_id = %s AND connector = %s "
                "AND origin = %s AND sub = ANY(%s)", (org_id, c, sel.KIT, membres))
            reste = {r["origin"]: int(r["n"]) for r in conn.execute(
                "SELECT origin, count(*) AS n FROM user_selected_connectors "
                "WHERE org_id = %s AND connector = %s AND sub = ANY(%s) GROUP BY origin",
                (org_id, c, membres)).fetchall()}
            effets.append({"connector": c, "change": REMOVED,
                           "uninstalled": cur.rowcount or 0, "kept": reste})
    unchanged = [n for n in nommes if n not in ajouts and n not in retraits]
    cut = coupes(org_id, apres)
    out = {"org_id": org_id, "kit": apres, "members": len(membres),
           "changes": effets, "unchanged": unchanged, "cut": cut, "note": AGENT_NOTE}
    if unchanged:
        out["unchanged_note"] = UNCHANGED_NOTE
    if cut:
        out["cut_note"] = CUT_NOTE
    return out
