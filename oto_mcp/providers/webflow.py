"""Déclaration de registre du connecteur `webflow`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# webflow : CMS (collections + items), API v2 (developers.webflow.com/data).
# keyed=True, UN seul champ (token) : un Site API token Webflow est bound à
# UN site (vérifié contre reference/authentication/site-token — « Site
# tokens are created per site »), donc pas de site_id à saisir — le client
# (oto-core) le résout lui-même via GET /sites (scope sites:read) au
# premier appel, mis en cache. Paste-the-token, comme folk/cognism — pas de
# second champ à aller chercher dans les settings. byo-only (pas de clé
# plateforme, pas d'accord commercial Otomata↔Webflow). Scope v1 = lecture/
# écriture des collections/items STAGED (draft) + publish explicite — pas de
# pages/assets/forms/ecommerce ici.
CONNECTOR = _c(
    "webflow", ["webflow"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    label="Webflow",
    help="CMS — collections & items (site API token)",
    publisher="Webflow", href="https://webflow.com",
    credential_fields=(
        CredentialField("token", "Site API token", secret=True,
                        help="Site Settings → Apps & Integrations → API access — "
                             "génère un token avec les scopes cms:read, "
                             "cms:write et sites:read (ce dernier permet à oto "
                             "de retrouver le site sans que tu aies à copier "
                             "son ID)"),
    ),
)

CATEGORY = "CMS"
LOGO_DOMAIN = "webflow.com"
