"""Déclaration de registre du connecteur `wordpress`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import CredentialField, _c

# wordpress : API REST cœur (`wp/v2`), auth = mot de passe d'application
# (WordPress ≥ 5.6, HTTP Basic sur HTTPS) — aucune extension à installer.
# Trois champs, comme n8n : le site s'auto-héberge, il n'y a pas d'hôte unique
# (`site_url` passe par `egress.check_url`). Deux façons de poser le même
# credential : le formulaire (coller le mot de passe) ou le flux « Connecter »
# (`tools/wordpress.py`, écran d'autorisation natif de WordPress,
# `wp-admin/authorize-application.php`) qui le remplit sans copier-coller.
# Multi-compte dérivé (`fields`) : un compte = un site — une agence en gère dix.
CONNECTOR = _c(
    "wordpress", ["wordpress"], auth_modes={"byo_user", "byo_org"},
    secret_kind="fields", account_noun="site",
    label="WordPress",
    help="articles, pages, médias et taxonomies (mot de passe d'application)",
    href="https://wordpress.org",
    credential_fields=(
        CredentialField("site_url", "URL du site", secret=False,
                        help="ex. https://blog.example.com"),
        CredentialField("username", "Identifiant WordPress", secret=False,
                        help="ton identifiant de connexion à wp-admin"),
        CredentialField("application_password", "Mot de passe d'application",
                        secret=True,
                        help="Utilisateurs → Profil → Mots de passe d'application "
                             "(pas ton mot de passe de connexion)"),
    ),
)

CATEGORY = "CMS"
PUBLISHER = "WordPress"
LOGO_DOMAIN = "wordpress.org"

DESCRIPTION = (
    "Ton site WordPress : rédiger et programmer des articles, gérer pages, "
    "contenus personnalisés, médias, catégories et étiquettes. Tout part en "
    "brouillon, la publication est un geste à part."
)
