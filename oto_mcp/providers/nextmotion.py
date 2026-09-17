"""Déclaration de registre du connecteur `nextmotion`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# nextmotion : gestion de cliniques de médecine esthétique, côté ADMINISTRATIF
# (cliniques, praticiens, agenda, catalogue, devis, factures). keyed api_key
# (Bearer), BYO seulement : une clé agit au nom de l'utilisateur de l'application
# qui l'a générée, sur les cliniques dont il est employé — une clé plateforme
# n'aurait aucun sens.
#
# ⚠️ Éditeur d'un logiciel qui HÉBERGE DES DONNÉES DE SANTÉ. Le contenu médical
# (dossier, antécédents, photos, ordonnances, consentements, soins, consultations)
# n'est PAS servi, et le patient embarqué dans les rendez-vous, devis et factures
# n'est servi que par son id (liste blanche) — cf. `tools/nextmotion.py`.
CONNECTOR = _c(
    "nextmotion", ["nextmotion"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Nextmotion",
    help="clinique esthétique : agenda, praticiens, prestations, devis, factures "
         "(sans le dossier médical)",
    href="https://www.nextmotion.net",
)

CATEGORY = "Métier"
PUBLISHER = "Nextmotion"
LOGO_DOMAIN = "nextmotion.net"

DESCRIPTION = (
    "Le côté administratif d'une clinique de médecine esthétique gérée avec "
    "Nextmotion : cliniques, praticiens, agenda et créneaux libres, catalogue de "
    "prestations et tarifs, devis et factures. Le dossier médical n'est pas servi, "
    "et un patient n'y apparaît que par un identifiant, sans nom ni coordonnées."
)
