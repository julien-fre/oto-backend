"""Déclaration de registre du connecteur `payfit`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# payfit : paie et RH, en LECTURE (entreprise, annuaire, contrats, absences).
# keyed api_key (Bearer), BYO seulement : une clé API PayFit est créée par un
# admin de l'entreprise et n'ouvre que cette entreprise — une clé plateforme
# n'aurait aucun sens.
#
# ⚠️ Logiciel de PAIE : NIR, IBAN, rémunérations, motifs d'absence liés à la
# santé. Bulletins, compta, virements, mutuelle ne sont PAS servis, et tout ce
# qui sort passe par une liste blanche — cf. `tools/payfit.py`.
CONNECTOR = _c(
    "payfit", ["payfit"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="PayFit",
    help="paie et RH (lecture) : entreprise, annuaire, contrats, absences "
         "(sans paie ni données personnelles)",
    href="https://payfit.com",
)

CATEGORY = "RH"
PUBLISHER = "PayFit"
LOGO_DOMAIN = "payfit.com"

DESCRIPTION = (
    "L'entreprise, l'annuaire professionnel des collaborateurs, leurs contrats "
    "(poste, dates, nature) et leurs absences dans PayFit, en lecture. Ni bulletins "
    "ni éléments de paie ; NIR, coordonnées bancaires, adresse et date de naissance "
    "ne sont jamais servis, et le motif d'une absence n'apparaît que pour un congé "
    "ordinaire."
)
