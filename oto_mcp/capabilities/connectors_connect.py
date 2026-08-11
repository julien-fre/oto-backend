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

from pydantic import BaseModel, ConfigDict

from .. import connector_flow
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding
from .registry import CAPABILITIES


class ConnectorConnectInput(BaseModel):
    name: str                                   # le connecteur, depuis le chemin
    params: Optional[dict] = None               # les valeurs de `connect.params`


class ConnectorConnectStarted(BaseModel):
    """Le flux est AMORCÉ, rien n'est connecté. Une 200 ici ne dit qu'une chose :
    « voici l'URL de consentement à ouvrir ». Le credential n'existera qu'au retour
    du fournisseur sur le callback — un client qui traite cette réponse comme un
    succès de connexion affichera « connecté » à quelqu'un qui n'a encore rien
    autorisé. Pour l'état réel, sonder la carte du connecteur (`oto_instance
    op=verify`) après le retour.

    `auth_url` est à ouvrir dans un NAVIGATEUR : c'est une page de consentement
    humaine, pas un appel d'API. Elle est à usage unique et porte un `state` à durée
    de vie courte — la stocker pour plus tard donne un lien mort.

    ⚠️ **Le reste de la réponse dépend du connecteur.** Chaque flux est déclaré par
    son propre module et échoue à l'accord sur ce qu'il ÉCHOTE : Zoho renvoie
    `connector`, Salesforce renvoie `scope`. Seul `auth_url` est commun à tous —
    ne dépendre de rien d'autre, et lire les champs additionnels sans les exiger.
    (Le refus de connecteur, lui, n'arrive jamais ici : un connecteur sans flux
    déclaré répond 400 `no_connection_flow`, un connecteur réservé par l'org 403
    `connector_restricted`.)"""
    model_config = ConfigDict(extra="allow")

    auth_url: str
    # Zoho seulement : écho du connecteur demandé (`zoho`|`zohodesk`|`zohoanalytics`).
    connector: Optional[str] = None
    # Salesforce seulement : le palier pour lequel le consentement est demandé
    # (`member` par défaut, `org`, `group`) — il détermine où le refresh token
    # atterrira au retour, pas qui a le droit de cliquer.
    scope: Optional[str] = None


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
        Output=ConnectorConnectStarted,
        mcp=None,     # les faces MCP par connecteur existent déjà (oto_zoho_connect…)
        rest=RestBinding(verb="POST", path="/api/me/connectors/{name}/connect"),
        description=("Démarre le flux de connexion déclaré par ce connecteur et renvoie "
                     "l'URL de consentement à ouvrir. Les valeurs attendues sont "
                     "décrites par `connect.params` du catalogue."),
    ),
]
