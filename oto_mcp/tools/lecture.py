"""A tool that only READS at its provider DECLARES it, on its decorator:
`@mcp.tool(annotations=LECTURE)`.

The declaration is the protocol's standard annotation (`readOnlyHint`): the MCP client sees it
too. It says "this call changes nothing at the third party" — not "it is free"
(an Apollo or AI Ark search bills its credits), nor "it does not run a
model": a model connector (jev, lighton) reads, but a recipe does not call it
(`recipes/contrat.NAMESPACES_A_MODELE`).

**What reads it**: `oto_recipe`. A "pull" recipe calls ONLY tools declared
here — an undeclared tool is refused, never presumed a reader: the default is refusal.
A tool multiplexed by `op` is only declared if ALL its ops read.
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

LECTURE = ToolAnnotations(readOnlyHint=True)


def en_lecture(tool) -> bool:
    """Has the tool (FastMCP object) declared itself read-only?"""
    annotations = getattr(tool, "annotations", None)
    return getattr(annotations, "readOnlyHint", None) is True
