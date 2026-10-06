"""The tool catalogue with each tool's state — what `oto_list_my_tools` returns (oto#170).

The catalogue is the RAW registry of the `Provider` (the same one as `_resolve_tool` in
`meta.py`); the state of each tool for the person reading is DERIVED from the session's
hiding layers (`session_visibility.compute_hidden_layers`), never copied from a rule.
Three states, and a legend that says what to do for each.
"""
from __future__ import annotations

from typing import Optional

from fastmcp import Context

from .. import providers, session_visibility, tool_alias, tool_registry
from ..tool_visibility import namespace_of

# Budget for one catalogue line. ~725 entries rendered at once: every character
# is multiplied by the number of tools. 100 chars are enough to say what a tool does;
# the detail is in `oto_tool_schema`, which gets read BEFORE calling anyway.
CATALOG_BLURB = 100


def namespace_help(ns: str) -> str:
    """Catalogue line for a namespace's connector (curated, in French) — the bridge
    between a natural-language query and English docstrings. Fail-soft."""
    try:
        con = providers.connector_for_namespace(ns)
        return f"{con.label} {con.help}" if con else ""
    # noqa: SILENT — namespace help missing rather than wrong
    except Exception:
        return ""
# The three states of a catalogue tool, for the person reading it (oto#170).
# Derived from the LAYERS of `session_visibility` — never copied: `installed` = in
# no layer, `installable` = hidden by a DISPLAY layer only (the tool
# stays callable through `oto_call`), `not_exposed` = behind a call guard
# (activation, RBAC, beta, role floor) or turned off at startup.
ETATS = ("installed", "installable", "not_exposed")
LEGENDE = {
    "installed": "in your toolbox: call it directly.",
    # #1112: a tool's state says its VISIBILITY, never the connection — an agent
    # said "LinkedIn not connected" after reading a selection state.
    "credential": "on a group: a key or an account EXISTS for you (tier, kind), "
                  "whatever the state of the tools; never verified live here — its "
                  "`next_step` names the tool that verifies it. An `installable` state does "
                  "NOT mean not connected.",
    "installable": "callable right away with oto_call(name, arguments); to install it "
                   "durably: oto_connector(op='select', name=<connector>) — or "
                   "oto_enable_tool(name) if you were the one who hid it.",
    "not_exposed": "NOT callable: the connector is not open to your organization, or "
                   "reserved for other members, or the tool exceeds your role — an org "
                   "admin opens it (oto_connector_activation); it is not a missing "
                   "capability.",
}


async def catalogue_avec_etat(ctx: Context, sub: str, prefix: str,
                              *, org=session_visibility._DERIVE_ORG) -> list[dict]:
    """The ENTIRE catalogue — the raw `Provider` registry, the same as `_resolve_tool`
    ("including disabled ones") — each tool with its state for (sub, active org).

    `org`: derived from the default session (`_DERIVE_ORG`), but can be set
    explicitly — a caller checking a trigger of ANOTHER org than its
    own (`oto_trigger`, warnings for declared tools) has no active session
    in that org to derive it from: passing it here avoids exactly the trap
    that cost a silent run (webhook payload read against the delegate's org, not the
    work's org — 2026-09-16).

    ⚠️ Until 2026-09-12 the catalogue started from the list ALREADY FILTERED by the
    session (`list_tools(run_middleware=False)` after `apply_session_transforms`):
    a tool whose connector was not installed, not exposed or reserved did not
    appear at all, and the response carried `catalog_disabled_count: 0` by
    construction. Two agents concluded that no WhatsApp tool, then no Google
    Chat tool, existed (oto#170) — while the description promised "every tool".
    The state comes from the session's hiding layers, not from a rule copied here."""
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.server import _is_backend_tool
    from fastmcp.server.transforms.visibility import is_enabled
    # The raw registry, minus what no model will ever see: a component
    # turned off at startup, or a tool reserved for an interface (`meta.ui.visibility
    # = ["app"]`). This is the filter of `FastMCP.list_tools` WITHOUT its session
    # part (`apply_session_transforms`) — that part is exactly what the catalogue
    # must traverse instead of being subject to. `_is_backend_tool` is private in fastmcp:
    # the pin is exact, and a move breaks here at first import, not silently.
    bruts = [t for t in await Provider.list_tools(ctx.fastmcp)
             if is_enabled(t) and not _is_backend_tool(t)]
    # Layers are computed on THIS raw registry, not on the session's list:
    # mid-session, an already-hidden tool is no longer in it, falls in no
    # layer, and came out `installed` — all 818 tools were (oto-backend#1112).
    couches = await session_visibility.compute_hidden_layers(
        ctx, sub, org=org, noms={t.name for t in bruts})
    masques = set().union(*couches.values())
    non_appelables = set().union(*(noms for nom, noms in couches.items()
                                   if nom not in session_visibility.COUCHES_INSTALLABLES))
    # The catalogue announces names as the user SEES them (cf.
    # `tool_alias`); everything computed — namespace, state — starts again from the
    # canonical name. The round trip `canonical(public(x)) == x` is total, so no name
    # gets lost along the way.
    entries = []
    for t in bruts:
        if t.name in non_appelables:
            etat = "not_exposed"
        elif t.name in masques:
            etat = "installable"
        else:
            etat = "installed"
        entries.append({
            "name": tool_alias.public(t.name, prefix),
            "namespace": namespace_of(t.name),
            "state": etat,
            "description": tool_registry.blurb(t.description, CATALOG_BLURB),
            "description_full": " ".join((t.description or "").split()),
            # The connector's catalogue line: the only FRENCH text of the entry
            # (docstrings are in English). Serves search, not output.
            "namespace_help": namespace_help(namespace_of(t.name)),
        })
    return sorted(entries, key=lambda e: e["name"])


def grouper_par_connecteur(entries: list[dict],
                           credentials: Optional[dict[str, dict]] = None) -> list[dict]:
    """The default projection of `op=list`: one group per namespace, the group's state
    and its tools by name — the exceptions (a tool in a different state than its
    connector: hidden by the person, hidden by default, out of reach) named
    separately under `states`. Measured on 2026-09-12 on 724 tools: 25k characters,
    against 53k for one line per tool without description and 115k with.

    `credentials` = `connectors.credential_presence.par_connecteur` — the SAME function
    as the `oto_connector` line (#1112): the group of a connector with an existing key or
    account carries `credential`, otherwise nothing."""
    groupes: dict[str, list[dict]] = {}
    for e in entries:
        groupes.setdefault(e["namespace"], []).append(e)
    out = []
    for ns, outils in sorted(groupes.items()):
        con = providers.connector_for_namespace(ns)
        compte: dict[str, int] = {}
        for e in outils:
            compte[e["state"]] = compte.get(e["state"], 0) + 1
        etat = max(compte, key=lambda k: (compte[k], k))
        groupe = {"namespace": ns,
                  "connector": con.name if con else None,
                  "label": con.label if con else "platform",
                  "state": etat,
                  "tools": [e["name"] for e in outils]}
        ecarts = {e["name"]: e["state"] for e in outils if e["state"] != etat}
        if ecarts:
            groupe["states"] = ecarts
        if con is not None and con.name in (credentials or {}):
            groupe["credential"] = credentials[con.name]
        out.append(groupe)
    return out


def hint_zero_resultat(tb: Optional[dict]) -> str:
    """The hint for a tool search that finds nothing.

    Two possible causes, and **the wrong answer costs a false report**. Without a
    toolbox gap, zero does mean "rephrase" — the search is lexical over
    English docstrings. With a gap (#577), zero says NOTHING about whether the
    tool exists: the session was built for the home org at handshake, the tools of
    the pinned org's connectors are not listed in it, and they remain callable.

    Serving the first text in the second case is what produced the
    "source unreachable" report of signal #616, on an active and reachable connector."""
    if tb:
        return ("Zero results HERE does NOT mean the tool doesn't exist: this session's "
                "toolbox is built for another org (see `toolbox_scope`), "
                "so the tools of the pinned org's connectors are not listed in it. "
                "Call it with `oto_call(name=..., arguments={...})` before concluding "
                "that a source is unreachable.")
    return ("No tool carries these words. The search is LEXICAL and the docstrings "
            "are in ENGLISH: retry the same intent in English before any other "
            "conclusion — measured on 2026-09-08, « transférer propriétaire équipe "
            "ressource » returns 0 tools and « transfer ownership resource team » returns "
            "`oto_resource` first. Otherwise, find the domain in `namespaces`, or "
            "retry without `query` for the full catalogue.")
