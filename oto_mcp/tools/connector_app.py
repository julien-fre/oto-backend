"""`oto_connector_app` — a card in the conversation: the connector's name, what the
person will do, and a Connect button that opens its page in their product.

The visual face of `oto_connector(op="setup_link")` (same handler, same refusals): the
agent that needs a key shows the card instead of asking for the key. The button only
OPENS a link (`OpenLink`, through the host) — it calls no tool, writes nothing, and the
key is typed on the page, never in the card nor in the chat.

Same conventions as the other Prefab apps (`docs/mcp-apps.md`): guarded `prefab_ui`
import, no `-> Card` return annotation, two channels (the card to the host, the same
facts as text to the model), every colour left to the host. Spine, outside the
activation gate.
"""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from .. import access
from ..capabilities._types import AuthzDenied, ResolvedCtx

_BOUTON = {"oauth": "Connect", "hosted": "Log in", "none": "Add to my tools"}

# What a REFUSAL card says, by code — fixed words only. The refusal's own message
# repeats the `name` the model passed (and a Custom API link carrying it): shown in a
# card the host paints as ours, a prompt-injected agent could put "your session
# expired, re-enter your key at <elsewhere>" under our heading. The message goes to
# the MODEL (text channel); the person reads only this.
_REFUS = {
    "unknown_connector": "No connector with that name is available to your workspace.",
    "ambiguous_connector": "That name matches several connectors.",
    "use_http_setup_link": "This tool connects through the Custom API form instead.",
}
_REFUS_DEFAUT = "This connector can't be shown here."


def _alias(sub: str, texte: str) -> str:
    """Tool names as THIS account's product spells them (white-label prefix), like
    `oto_procedure_app` — never our names to a partner's end user."""
    from .. import tool_alias
    prefix = tool_alias.prefix_for(sub)
    return tool_alias.rewrite_prose(texte, prefix) if prefix else texte


def _texte(out: dict) -> str:
    """What the model reads: the same facts as the card, and the link."""
    lignes = [f"{out['label']} (`{out['connector']}`) — connection card shown to the user."]
    if out.get("url"):
        lignes.append(f"Link: {out['url']}")
    else:
        lignes.append(f"No link: {out.get('url_missing') or 'no connectors page'}.")
    if out.get("fields"):
        lignes.append("The form asks for: " + ", ".join(f["label"] for f in out["fields"]))
    lignes.append(out["instructions"])
    return "\n".join(lignes)


def register(mcp: FastMCP) -> None:
    try:
        from prefab_ui.actions import OpenLink  # type: ignore
        from prefab_ui.components import (  # type: ignore
            Button, Card, Column, Heading, Muted, Text,
        )
    # noqa: SILENT — extra `apps` absent ⇒ no app, `oto_connector op=setup_link` is enough
    except Exception:  # pragma: no cover - extra `apps` absent
        return

    from ..capabilities.connectors import setup_link as _setup

    def _carte_refus(sub: str, e: AuthzDenied) -> ToolResult:
        with Card() as card:
            with Column(gap=3):
                Heading("Cannot show this connector")
                Text(_REFUS.get(e.code, _REFUS_DEFAUT))
        return ToolResult(content=[TextContent(
            type="text", text=_alias(sub, f"Refused ({e.code}): {e.message}"))],
            structured_content=card)

    @mcp.tool(app=True)
    def oto_connector_app(name: str):  # no `-> Card`: see docs/mcp-apps.md (gotcha #69)
        """Show a CONNECT card for one connector: its name, what the person will do
        (paste a key, approve access, log in) and a Connect button that opens the
        connector's page in their product — READ ONLY, takes no secret.

        Use it instead of asking for an API key, password or token in the chat: the
        person clicks, connects on the page, and tells you when it is done. Then check
        with `oto_connector(op="list", name=…)` (`credential`). The text result gives
        YOU the link and the fields. Without a rendering client, use
        `oto_connector(op="setup_link", name=…)` and send the `url`.

        Args:
            name: the connector — exact name, label or namespace (e.g. "hunter",
                "HubSpot"). The Custom API has its own link: `http(op="setup_link")`.
        """
        sub = access.current_user_sub_or_raise()
        ctx = ResolvedCtx(sub=sub, org_id=access.current_org(sub))
        try:
            out = _setup.setup_link(ctx, name)
        except AuthzDenied as e:
            return _carte_refus(sub, e)

        with Card() as card:
            with Column(gap=3):
                Heading(f"Connect {out['label']}")
                Text(out["action"])
                if out.get("fields"):
                    Muted("You will be asked for: "
                          + ", ".join(f["label"] for f in out["fields"]))
                if out.get("url"):
                    Button(_BOUTON.get(out["method"], "Connect"),
                           on_click=OpenLink(out["url"]))
                else:
                    Muted("No connection page is available for your account: "
                          + (out.get("url_missing") or "ask your admin."))
                Muted("Never paste a key in the chat.")
        return ToolResult(content=[TextContent(type="text", text=_alias(sub, _texte(out)))],
                          structured_content=card)
