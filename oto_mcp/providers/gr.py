"""Déclaration de registre du connecteur `gr`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# Grèce : lookup entité via registre GEMI (autocomplete) + VIES. Open data,
# sans clé. Inerte tant que non activé en DB (deny-by-default), comme foncier/sante.
CONNECTOR = _c(
    "gr", ["gr"], secret_kind="none",
    label="Entreprises Grèce",
    help="chercher une entreprise grecque au registre GEMI, vérifier un numéro "
         "de TVA européen (VIES) — open data",
)

# « Data GR » était à la fois le libellé et une CATÉGORIE à un seul membre : ni
# « Grèce » ni « entreprises » n'apparaissaient, et le filtre par type portait une
# ligne pour lui seul. Rangé (2026-09-02) là où vivent déjà les registres
# d'entreprises européens — hithorizons, topograph.
CATEGORY = "Prospection"
PUBLISHER = "GEMI / VIES"
SANS_LOGO_DE_MARQUE = True
