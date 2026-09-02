"""Déclaration de registre du connecteur `sante`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "sante", ["sante"], secret_kind="none",
    # « Santé » promettait un domaine ; c'est un annuaire d'établissements (2026-09-02).
    label="Établissements de santé",
    help="annuaire FINESS des établissements de santé et médico-sociaux + "
         "évaluations ESSMS de la HAS (open data)",
)

CATEGORY = "Data FR"
PUBLISHER = "HAS / FINESS"
DESCRIPTION = (
    "Les établissements de santé et médico-sociaux français : "
    "répertoire FINESS complet et évaluations ESSMS de la HAS, avec "
    "recherche multicritère."
)
LOGO_DOMAIN = "has-sante.fr"
