"""Déclaration de registre du connecteur `salesforce`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# salesforce : OAuth2 Connected App → credential multi-champs (ADR 0011, comme
# zoho), résolu via resolve_credential_fields. byo_user OU byo_org (équipe sales
# partage une Connected App). Pas de table de région fixe : le refresh Salesforce
# renvoie l'`instance_url`, `login_url` ne fait que sélectionner prod vs sandbox
# (ou un My Domain).
# salesforce : plus de `refresh_token` posé à la main — le flow OAuth live
# (salesforce_oauth.py) est désormais le SEUL chemin pour l'obtenir. Le
# formulaire ne collecte que le triplet client_id/client_secret/login_url ;
# « il reste le consentement » se dit par `status_hints` (register_state +
# pending_action, déclarés dans tools/salesforce.py) — PAS par une méthode d'auth
# à part : le jeu de `auth_method` est fermé et lu par un switch du dashboard. Le
# `client_id`/`client_secret` restent PER-CUSTOMER (chaque org crée sa
# propre Connected App) — pas de client Otomata partagé possible ici,
# contrairement à google/atlassian/folkmcp (cf. salesforce_oauth.py).
CONNECTOR = _c(
    "salesforce", ["salesforce"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", label="Salesforce",
    help="CRM Salesforce (Contacts, Accounts/companies, Leads, Opportunities, notes)",
    href="https://login.salesforce.com", credential_fields=(
        CredentialField("client_id", "Clé du consommateur", secret=True,
                        help="Consumer Key de la Connected App"),
        CredentialField("client_secret", "Secret du consommateur", secret=True,
                        help="révélé par « Détails du consommateur » sur ton "
                             "application Salesforce, après vérification par email"),
        # ⚠️ Le libellé disait « login.salesforce.com (prod) ou test.salesforce.com
        # (sandbox) ». C'est daté : My Domain est obligatoire depuis, et une org qui
        # bloque l'authentification via login.salesforce.com — de plus en plus le
        # défaut — fait échouer le consentement. Vécu le 31/07.
        CredentialField("login_url", "Login URL (ton My Domain)",
                        secret=False,
                        help="https://<ton-domaine>.my.salesforce.com — SANS le "
                             "« -setup » du domaine de la console. Sandbox : "
                             "https://<domaine>.sandbox.my.salesforce.com"),
    ),
)

CATEGORY = "Prospection"
PUBLISHER = "Salesforce"
LOGO_DOMAIN = "salesforce.com"
