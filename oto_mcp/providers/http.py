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
# oto_http.build_auth).
# À DISTINGUER du bridge (credential hors plateforme) : ici la clé est confiée
# à oto — pas de custody côté client.
#
# ⚠️ Ce commentaire a annoncé « lecture seule (GET), garde-fou anti-SSRF sur
# l'hôte » jusqu'au 2026-08-27 — DEUX affirmations fausses (oto-backend#449) : le
# connecteur porte aussi `http_post`, et aucune garde SSRF applicative n'existe sur
# ce chemin. C'est VOULU : un `http` d'org vise légitimement un pont sur VPC privé
# ou un service en loopback ; le filtrage sortant est un contrôle d'egress de
# plateforme, pas du code par-connecteur.
# Jeu FERMÉ des modes d'auth. RECOPIÉ de `oto.tools.http.AUTH_MODES` — pas importé :
# le registre reste pur (aucune dépendance runtime au niveau module, sinon une dép
# absente retirerait le connecteur du catalogue au lieu de le dégrader). La copie est
# tenue par le tripwire `test_http_auth_modes.py`, qui la compare à oto-core ET vérifie
# que les champs déclarés requis par mode sont EXACTEMENT ceux que `build_auth` exige.
AUTH_MODES = ("bearer", "header", "query", "basic", "oauth2", "none")

CONNECTOR = _c(
    "http", ["http"], auth_modes={"byo_org"}, secret_kind="fields",
    label="HTTP",
    # `auth_mode` SÉLECTIONNE les autres champs (oto-backend#449) : un formulaire
    # `bearer` n'a pas à montrer les six champs d'oauth2 et de basic, et l'écriture
    # refuse un mode incohérent au lieu de l'accepter puis d'échouer au 1er appel.
    field_discriminator="auth_mode",
    help="connecte n'importe quelle API HTTP à oto : renseigne l'URL de base, "
         "le mode d'auth (bearer / clé en header ou query / basic / oauth2) et "
         "le secret correspondant. oto stocke le secret (coffre chiffré) et tape "
         "l'API directement, en lecture (GET) comme en écriture (POST).",
    # `when=` = les modes qui rendent le champ PERTINENT ; `required` s'applique alors
    # DANS ces modes-là. Les trois champs sans `when` valent quel que soit le mode.
    credential_fields=(
        CredentialField("base_url", "URL de base", secret=False,
                        help="racine de l'API (ex. https://api.acme.com). `http://` "
                             "est accepté et légitime : pont sur réseau privé, "
                             "service en loopback"),
        CredentialField("auth_mode", "Mode d'auth", secret=False,
                        choices=AUTH_MODES,
                        help="ce que l'API attend pour t'authentifier — il décide des "
                             "champs à remplir ensuite"),
        CredentialField("label", "Nom affiché", secret=False,
                        required=False, help="ex. « API Acme » — visible de ta seule org"),
        CredentialField("token", "Token / clé API", secret=True,
                        when=("bearer", "header", "query"),
                        help="valeur du bearer, ou de la clé (modes header/query)"),
        CredentialField("header_name", "Nom du header", secret=False,
                        when=("header",), help="ex. x-api-key"),
        CredentialField("query_param", "Nom du param", secret=False,
                        when=("query",), help="ex. api_key"),
        CredentialField("username", "Utilisateur", secret=False,
                        when=("basic",), help="identifiant du couple basic"),
        CredentialField("password", "Mot de passe", secret=True, when=("basic",),
                        whitespace_significant=True, help="mot de passe du couple basic"),
        CredentialField("token_url", "URL du token", secret=False,
                        when=("oauth2",), help="endpoint client-credentials"),
        CredentialField("client_id", "Client ID", secret=False,
                        when=("oauth2",), help="identifiant de l'application cliente"),
        CredentialField("client_secret", "Client secret", secret=True,
                        when=("oauth2",), help="secret de l'application cliente"),
        CredentialField("scope", "Scope", secret=False,
                        when=("oauth2",), required=False,
                        help="scopes demandés au serveur de token (optionnel)"),
    ),
)

SANS_LOGO_DE_MARQUE = True
