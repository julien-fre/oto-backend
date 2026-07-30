"""Capacité « démarrer le flux de connexion d'un connecteur » — UN chemin, tous les flux.

ADR 0042 §Convergence des surfaces. Avant : chaque connecteur à flux exposait son propre
chemin (`/api/zoho/oauth/start`, `/api/salesforce/oauth/start`), le front avait donc une
fonction cliente par connecteur et une liste de noms en dur pour décider laquelle appeler.
Le nom du connecteur voyage désormais en **paramètre de chemin** (précédent :
`/api/me/connectors/{name}/session/start`), et ce qu'il faut fournir est décrit par le
catalogue (`connect.params`) — le dashboard rend un formulaire générique.

Le geste lui-même reste chez le connecteur (`connector_flow.declare`, appelé depuis son
module) : cette capacité ne fait que router, garder, et traduire un refus.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import connector_flow
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class ConnectorConnectInput(BaseModel):
    name: str                                   # le connecteur, depuis le chemin
    params: Optional[dict] = None               # les valeurs de `connect.params`


def _connect(ctx: ResolvedCtx, inp: ConnectorConnectInput) -> dict:
    from mcp.shared.exceptions import McpError

    from .. import access
    if not connector_flow.supports(inp.name):
        raise AuthzDenied(
            400, "no_connection_flow",
            f"« {inp.name} » n'a pas de flux de connexion : son credential se pose "
            "au formulaire de la fiche.")
    # MÊME gate que l'usage (ADR 0025) : on n'ouvre pas un consentement pour un
    # connecteur que l'org a réservé. Aligné sur `api_key_save`, qui gate déjà la POSE
    # — jusqu'ici les deux capacités de démarrage divergeaient là-dessus (salesforce
    # gardait, zoho non).
    try:
        access.require_connector_access(inp.name, ctx.sub)
    except McpError as e:
        raise AuthzDenied(403, "connector_restricted", e.error.message)
    return connector_flow.start(inp.name, ctx, inp.params or {})


CAPABILITIES += [
    Capability(
        key="me.connector_connect",
        handler=_connect,
        Input=ConnectorConnectInput,
        authz=ORG_MEMBER,
        mcp=None,     # les faces MCP par connecteur existent déjà (oto_zoho_connect…)
        rest=RestBinding(verb="POST", path="/api/me/connectors/{name}/connect"),
        description=("Démarre le flux de connexion déclaré par ce connecteur et renvoie "
                     "l'URL de consentement à ouvrir. Les valeurs attendues sont "
                     "décrites par `connect.params` du catalogue."),
    ),
]
