"""Déclaration de registre du connecteur `linear`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# linear : issues, projets, cycles (sprints), équipes, labels, commentaires,
# webhooks. keyed api_key (header `Authorization` SANS préfixe `Bearer` —
# spécificité Linear), **byo_org only** (pas de byo_user, pas de clé
# plateforme) : une clé API Linear est scopée au workspace par nature, et
# contrairement à un pool de crédits vendeur mutualisable (AI Ark, cf. le
# connecteur `linkedin` déposé, #279), il n'y a pas de raison de pool
# partagé ici — chaque org qui veut Linear pose sa propre clé workspace.
CONNECTOR = _c(
    "linear", ["linear"], auth_modes={"byo_org"}, keyed=True,
    secret_kind="api_key", label="Linear",
    help="issues, projets, cycles, équipes, labels, commentaires, webhooks",
    href="https://linear.app",
)

CATEGORY = "Métier"
PUBLISHER = "Linear"
LOGO_DOMAIN = "linear.app"

DESCRIPTION = (
    "Le suivi de projet Linear : issues, projets, cycles (sprints), équipes, "
    "labels, commentaires et webhooks. Une clé par workspace, posée par l'org — "
    "pas de clé personnelle ni de pool partagé, une clé API Linear est scopée à "
    "un espace de travail."
)
