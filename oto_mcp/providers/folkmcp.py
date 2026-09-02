"""Déclaration de registre du connecteur `folkmcp`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# folkmcp : MCP OFFICIEL de Folk (kind=mount, #85), COEXISTANT avec le
# connecteur natif `folk` (clé API REST). Namespace distinct `folkmcp` ; le MCP
# distant préfixe déjà ses tools `folk_*` → `mount_strip_prefix="folk_"` évite
# le double `folkmcp_folk_*` : les tools montés sont `folkmcp_*` (le forward
# garde le nom d'origine). Pas de collision avec le natif `folk_*`.
# AS = Stytch (app.folk.app/oauth/authorize + api.stytch.folk.app), client
# PUBLIC + DCR + PKCE, flow web per-user dans folk_oauth.py. Le MCP Folk s'auth
# UNIQUEMENT par OAuth (pas de clé). Inerte tant que `folkmcp` n'est pas dans
# OTO_MCP_MOUNTS_ENABLED (défaut = aucun mount). Coexistence gérée par la
# visibilité per-user (ADR 0011/0031) : un user voit soit `folk`, soit `folkmcp`.
CONNECTOR = _c(
    "folkmcp", ["folkmcp"], kind="mount",
    mount_url="https://mcp.folk.app/mcp", mount_strip_prefix="folk_",
    auth_modes={"byo_user"}, secret_kind="oauth",
    label="Folk (MCP)",
    help="CRM Folk via son MCP officiel (fédéré, OAuth per-user)",
    href="https://folk.app",
)

# ⚠️ L'ÉDITEUR, C'EST FOLK — et le DÉFAUT disait « Otomata » (corrigé le
# 2026-09-02). Ce module ne déclarait ni `PUBLISHER` ni `LOGO_DOMAIN` : faute de
# constante, `publisher_name` retombe sur « Otomata », donc la fiche servait le
# MCP *officiel* de Folk sous NOTRE nom — l'appropriation du produit d'un tiers,
# alors que l'appel part chez `mcp.folk.app` (cf. `mount_url` ci-dessus). Un
# défaut qui PARLE à la place d'une déclaration absente rend l'omission
# indiscernable du choix ; ici il a menti. Mêmes constantes que le connecteur
# natif `folk` : même éditeur, même marque.
PUBLISHER = "Folk"
LOGO_DOMAIN = "folk.app"
