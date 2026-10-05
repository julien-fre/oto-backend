"""Déclaration de registre du connecteur `sharepoint`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# sharepoint : fichiers Microsoft 365 (sites SharePoint, bibliothèques de
# documents, OneDrive) via Microsoft Graph, AU NOM DE LA PERSONNE : chacune se
# connecte avec son compte Microsoft 365 (OAuth, permissions déléguées) et l'agent
# voit exactement ce qu'elle voit. L'application est celle d'oto, multilocataire,
# dont les coordonnées sont posées au palier plateforme (`auth/microsoft.py`) ; le
# client n'enregistre aucune application. L'hôte Graph est fixe : pas de garde
# d'egress à poser.
CONNECTOR = _c(
    "sharepoint", ["sharepoint"],
    auth_modes={"byo_user"},
    # Le consentement naît du compte Microsoft de la personne, pas de son org.
    personal_session=True, secret_kind="oauth",
    label="SharePoint & OneDrive",
    help="tes fichiers Microsoft 365 : sites, bibliothèques, OneDrive — chercher, lire, "
         "déposer, avec tes droits",
    href="https://learn.microsoft.com/graph/api/resources/sharepoint",
)

CATEGORY = "Knowledge"
PUBLISHER = "Microsoft"
LOGO_DOMAIN = "microsoft.com"

DESCRIPTION = (
    "Tes fichiers Microsoft 365 : ton OneDrive, les sites SharePoint et les "
    "bibliothèques de documents auxquels tu as accès. Chercher, lire un document "
    "(Word, PDF, Excel…) et en déposer un. Tu te connectes avec ton compte "
    "Microsoft : l'agent voit ce que tu vois, ni plus ni moins."
)
