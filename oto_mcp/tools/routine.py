"""Provider `routine` — credential-only (routine Claude Code), aucun tool propre.

Le credential (`routine_id` + jeton de déclenchement) est résolu par la capacité
`me.automation.fire` (`capabilities/automation.py`) via
`access.resolve_credential_fields("routine")`. Le verbe vit en CAPACITÉ et non ici,
parce que le dashboard voudra un bouton « déclencher » : un `@mcp.tool()` aurait
forcé une seconde implémentation REST (ADR 0042 §Convergence des surfaces).

Ce module existe uniquement pour satisfaire l'invariant « un fichier tools/ par
provider kind=tools » (test_capabilities_drift) ; `register_all` l'importe et appelle
`register()` qui n'enregistre rien.
"""
from __future__ import annotations

from fastmcp import FastMCP


def register(mcp: FastMCP) -> None:  # noqa: ARG001 — credential consommé par la capacité
    return
