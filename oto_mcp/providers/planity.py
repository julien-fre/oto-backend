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
#
# ⚠️ L'ÉDITEUR, C'EST NOUS (corrigé le 2026-09-02). La fiche a annoncé jusque-là
# « Planity » comme éditeur, avec le logo planity.com : ça se lisait comme une
# intégration officielle, alors que le serveur monté est le NÔTRE et qu'il
# rejoue la chaîne d'auth de Planity avec l'email et le mot de passe de
# l'utilisateur — ce que la fiche engage de plus lourd, et qui n'y figurait pas.
# Le NOM du connecteur et de ses tools ne bouge pas : ce qui change est ce que
# la fiche DIT.
CONNECTOR = _c(
    "planity", ["planity"], kind="mount",
    mount_url="https://planity-mcp.oto.zone/mcp",
    auth_modes={"byo_user"}, secret_kind="basic_auth",
    label="Planity",
    help="agenda + caisse Planity (RDV, clients, CA, stats) — passerelle "
         "opérée par Otomata, avec ton email et ton mot de passe Planity",
    href="https://planity-mcp.oto.zone",
)

CATEGORY = "Métier"
# Déclaré, pas laissé au défaut : « Otomata » est aussi ce que rend l'ABSENCE de
# constante, et un oubli ne doit pas se confondre avec ce choix-ci.
PUBLISHER = "Otomata"
# Pas le logo de Planity : le service monté est le nôtre, pas une intégration
# officielle de Planity. Monogramme côté UI.
SANS_LOGO_DE_MARQUE = True
