"""Capacité « connexion Salesforce » — fabrique le lien de consentement.

ADR 0042 §Convergence des surfaces : un verbe de plateforme naît **capacité**, pas
route REST écrite à la main. Calqué sur `zoho_connect.py` (même forme, même raison) —
et comme lui, gagne une **face MCP** : l'agent peut fabriquer le lien et le tendre à
l'utilisateur, ce qui est le geste utile en conversation.

Ce qui RESTE en route écrite à la main (`api_routes_salesforce.py`) : le **callback**.
Salesforce y redirige le NAVIGATEUR — sans en-tête d'auth, avec une réponse 302 — ce
qu'un contrat de capacité (JSON + autz) ne peut pas exprimer. Déclaré comme tel dans
`test_rest_modules_are_capabilities.py`.

Particularité Salesforce (cf. `salesforce_oauth.py`) : le client OAuth est
**per-customer** (chaque org crée sa Connected App), donc `start` lit le triplet
client_id/client_secret/login_url DÉJÀ posé sur la carte du connecteur — c'est un
prérequis, pas une constante de plateforme.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .. import salesforce_oauth
from ..connectors import flow as connector_flow
from ._authz import ORG_MEMBER
from ._types import AuthzDenied, Capability, ResolvedCtx
from .registry import CAPABILITIES


class SalesforceConnectInput(BaseModel):
    op: Literal["start"] = "start"
    # Niveau où RANGER le credential : le membre (défaut), l'org entière, ou l'équipe
    # active. Le droit est vérifié à la construction du lien ET re-vérifié au retour
    # (le state vit 10 min — cf. le callback).
    scope: Optional[Literal["member", "org", "group"]] = "member"


def start_for(ctx: ResolvedCtx, scope: str,
              return_app: Optional[str] = None) -> connector_flow.FlowStart:
    """URL de consentement à ouvrir, pour le niveau demandé. Partagé avec le flux
    générique (`connector_flow`, déclaré dans tools/salesforce.py) : une seule façon
    de démarrer, deux surfaces.

    `return_app` : uniquement porté par le chemin REST générique (le navigateur
    d'un front sait qui il est) ; le chemin MCP ci-dessous (`_start`) ne le passe
    jamais — un agent Claude n'a pas de navigateur à rerediriger."""
    from mcp.shared.exceptions import McpError

    from .. import access
    try:
        access.require_connector_access("salesforce", ctx.sub)
    except McpError as e:
        raise AuthzDenied(403, "connector_restricted", e.error.message)
    try:
        auth_url = salesforce_oauth.build_auth_url(ctx.sub, scope or "member", return_app)
    except ValueError as e:
        raise AuthzDenied(400, "invalid_scope_param", str(e))
    except PermissionError as e:
        raise AuthzDenied(403, "org_admin_required", str(e))
    except LookupError as e:
        raise AuthzDenied(400, "missing_credentials", str(e))
    except RuntimeError as e:
        raise AuthzDenied(400, "oauth_misconfigured", str(e))
    # Le palier RETENU est spécifique à Salesforce (les autres flux n'en ont pas) : il
    # descend dans `details`. Il porte une information — c'est le scope effectif, défaut
    # résolu — mais aucun client générique n'a à le connaître pour ouvrir l'URL.
    return connector_flow.FlowStart(auth_url=auth_url, details={"scope": scope or "member"})


def _start(ctx: ResolvedCtx, inp: SalesforceConnectInput) -> dict:
    return start_for(ctx, inp.scope or "member").as_dict()


CAPABILITIES += [
    Capability(
        key="me.salesforce_connect",
        handler=_start,
        Input=SalesforceConnectInput,
        authz=ORG_MEMBER,
        mcp="oto_salesforce_connect",
        # Plus de face REST NOMMÉE : le chemin fixe `/api/me/connectors/{name}/connect`
        # (capacité `me.connector_connect`) la sert désormais, via le MÊME `start_for`.
        # La face MCP reste — un agent connaît le connecteur qu'il connecte.
        rest=None,
        description=(
            "Connect Salesforce. op='start' returns the consent URL to OPEN in a "
            "browser — on return, the refresh token is stored in the vault. "
            "Prerequisite: the Connected App's Consumer Key + Consumer Secret + Login "
            "URL must already be saved on the connector card (Salesforce's OAuth "
            "client is per-customer: each org creates its own Connected App). "
            "`scope`: member (default) | org | group — org/group require admin."),
    ),
]
