"""Déclaration de registre du connecteur `planity`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# planity : MCP fédéré (kind=mount). Serveur autonome stateless distant
# (planity-mcp.oto.zone) monté via proxy FastMCP ; credential per-user =
# base64("email:password") du compte Planity de l'user, injecté par requête
# dans le bearer (planity-mcp le décode et rejoue la chaîne d'auth Planity).
CONNECTOR = _c(
    "planity", ["planity"], kind="mount",
    mount_url="https://planity-mcp.oto.zone/mcp",
    auth_modes={"byo_user"}, secret_kind="basic_auth",
    label="Planity",
    help="agenda + caisse Planity (RDV, clients, CA, stats) — MCP fédéré",
    href="https://planity-mcp.oto.zone",
)

CATEGORY = "Métier"
PUBLISHER = "Planity"
LOGO_DOMAIN = "planity.com"
