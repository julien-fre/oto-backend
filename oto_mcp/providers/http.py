"""Déclaration de registre du connecteur `http`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# Client HTTP multi-auth : contrairement au bridge, oto DÉTIENT le secret de
# l'API cible (coffre AES, byo_org) et tape l'API directement (pas de service
# distant). `auth_mode` discrimine le mode (bearer/header/query/basic/oauth2/
# none) ; les champs secrets requis dépendent du mode (validés au call-time par
# oto_http.build_auth). Lecture seule (GET), garde-fou anti-SSRF sur l'hôte.
# À DISTINGUER du bridge (credential hors plateforme) : ici la clé est confiée
# à oto — pas de custody côté client.
CONNECTOR = _c(
    "http", ["http"], auth_modes={"byo_org"}, secret_kind="fields",
    label="HTTP",
    help="connecte n'importe quelle API HTTP à oto : renseigne l'URL de base, "
         "le mode d'auth (bearer / clé en header ou query / basic / oauth2) et "
         "le secret correspondant. oto stocke le secret (coffre chiffré) et tape "
         "l'API directement, en lecture seule (GET).",
    credential_fields=(
        CredentialField("base_url", "URL de base", secret=False, reveal=True,
                        help="racine HTTPS de l'API (ex. https://api.acme.com)"),
        CredentialField("auth_mode", "Mode d'auth", secret=False, reveal=True,
                        help="bearer | header | query | basic | oauth2 | none"),
        CredentialField("label", "Nom affiché", secret=False, reveal=True,
                        required=False, help="ex. « API Acme » — visible de ta seule org"),
        CredentialField("token", "Token / clé API", secret=True, required=False,
                        help="valeur du bearer, ou de la clé (modes header/query)"),
        CredentialField("header_name", "Nom du header", secret=False, reveal=True,
                        required=False, help="mode header (ex. x-api-key)"),
        CredentialField("query_param", "Nom du param", secret=False, reveal=True,
                        required=False, help="mode query (ex. api_key)"),
        CredentialField("username", "Utilisateur", secret=False, reveal=True,
                        required=False, help="mode basic"),
        CredentialField("password", "Mot de passe", secret=True, required=False,
                        whitespace_significant=True, help="mode basic"),
        CredentialField("token_url", "URL du token", secret=False, reveal=True,
                        required=False, help="mode oauth2 (endpoint client-credentials)"),
        CredentialField("client_id", "Client ID", secret=False, reveal=True,
                        required=False, help="mode oauth2"),
        CredentialField("client_secret", "Client secret", secret=True,
                        required=False, help="mode oauth2"),
        CredentialField("scope", "Scope", secret=False, reveal=True,
                        required=False, help="mode oauth2 (optionnel)"),
    ),
)

SANS_LOGO_DE_MARQUE = True
