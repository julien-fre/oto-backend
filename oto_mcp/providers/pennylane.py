"""Déclaration de registre du connecteur `pennylane`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

CONNECTOR = _c(
    "pennylane", ["pennylane"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key",
    # Le grand livre est un domaine à lui : son propre module, même clé et même
    # namespace (`pennylane_*`, le gate d'activation lit le 1er token).
    modules=("pennylane", "pennylane_ledger"),
    label="Pennylane", help="compta", href="https://app.pennylane.com",
)

CATEGORY = "Finance"
PUBLISHER = "Pennylane"
LOGO_DOMAIN = "pennylane.com"

DESCRIPTION = (
    "La comptabilité de l'entreprise dans Pennylane : factures, clients, "
    "fournisseurs, transactions bancaires, balance comptable, et le grand livre "
    "— lire les écritures, en poser une, lettrer des lignes entre elles. À "
    "distinguer de `pennylaneged`, qui donne accès au bac documentaire (GED) via "
    "une session navigateur plutôt qu'une clé API. Les droits dépendent de la clé "
    "posée, pas du connecteur : chaque geste demande son propre scope."
)
