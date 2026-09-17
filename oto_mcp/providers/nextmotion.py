"""Déclaration de registre du connecteur `nextmotion`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# nextmotion : gestion de cliniques de médecine esthétique, côté ADMINISTRATIF
# (cliniques, praticiens, agenda, catalogue, ventes, leads, statistiques, stock,
# réglages). keyed api_key (Bearer), BYO seulement : une clé agit au nom de
# l'utilisateur de l'application qui l'a générée, sur les cliniques dont il est
# employé — une clé plateforme n'aurait aucun sens.
#
# ⚠️ Éditeur d'un logiciel qui HÉBERGE DES DONNÉES DE SANTÉ. Le contenu médical
# (dossier, antécédents, photos, ordonnances, consentements, soins, consultations,
# visites, questionnaires) n'est PAS servi ; tout ce qui sort passe par une liste
# blanche, et le patient n'y est servi que par son id — cf. `tools/nextmotion.py`.
#
# Cinq modules, une seule clé : les outils de `nextmotion.py` et ses frères, montés
# ensemble par `modules`.
CONNECTOR = _c(
    "nextmotion", ["nextmotion"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Nextmotion",
    help="clinique esthétique : agenda, catalogue, devis, factures, paiements, leads, "
         "statistiques, stock (sans le dossier médical)",
    href="https://www.nextmotion.net",
    modules=("nextmotion", "nextmotion_catalogue", "nextmotion_agenda",
             "nextmotion_ventes", "nextmotion_crm"),
)

CATEGORY = "Métier"
PUBLISHER = "Nextmotion"
LOGO_DOMAIN = "nextmotion.net"

DESCRIPTION = (
    "Le côté administratif d'une clinique de médecine esthétique gérée avec "
    "Nextmotion : cliniques, praticiens, agenda (salles, appareils, plages, "
    "absences, demandes en ligne, créneaux libres), catalogue et forfaits, devis, "
    "factures et paiements, leads, statistiques de chiffre d'affaires, stock et "
    "réglages. Le dossier médical n'est pas servi, et un patient n'y apparaît que "
    "par un identifiant, sans nom ni coordonnées."
)
