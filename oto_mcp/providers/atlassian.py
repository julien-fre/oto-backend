"""Déclaration de registre du connecteur `atlassian`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# atlassian : MCP fédéré (kind=mount, #40). Le Rovo Remote MCP d'Atlassian
# (mcp.atlassian.com/v1/mcp, Jira+Confluence) a son propre AS OAuth 2.1 + DCR +
# PKCE ; client PUBLIC (token_endpoint_auth_method=none, pas de secret), flow web
# per-user dans atlassian_oauth.py. Le cloudid/site est résolu par l'AS Atlassian.
# Inerte tant que `atlassian` n'est pas dans OTO_MCP_MOUNTS_ENABLED (défaut =
# aucun mount) ET que ATLASSIAN_OAUTH_CLIENT_ID n'est pas posé.
CONNECTOR = _c(
    "atlassian", ["atlassian"], kind="mount",
    mount_url="https://mcp.atlassian.com/v1/mcp",
    auth_modes={"byo_user"}, secret_kind="oauth",
    label="Atlassian",
    help="Jira / Confluence (MCP fédéré)", href="https://atlassian.com",
)

CATEGORY = "Métier"
PUBLISHER = "Atlassian"
LOGO_DOMAIN = "atlassian.com"
