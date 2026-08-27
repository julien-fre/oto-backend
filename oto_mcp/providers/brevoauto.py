"""Déclaration de registre du connecteur `brevoauto`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# brevoauto : automations (workflows marketing) via l'API PRIVÉE de l'éditeur
# (`workflow-apis.brevo.com/v1`). Connecteur SÉPARÉ du `brevo` keyé (API publique
# v3, plus bas) car le credential diffère — session navigateur ici, clé API là ;
# même éditeur, deux surfaces disjointes (la clé v3 n'ouvre pas l'authoring
# d'automations). Même partition que pennylane / pennylaneged.
# Exécution = **Browserbase** (Chrome distant hébergé) : l'user se logue 1× via
# Live View (`brevoauto_connect_start`), sa session persiste dans un Context = le
# credential per-user (coffre). Pas de browser sur la box, pas d'export de cookie.
# personal_session (session physiologiquement per-user). Expérimental (API non
# documentée) : hors socle, installable depuis la library.
CONNECTOR = _c(
    "brevoauto", ["brevoauto"], auth_modes={"byo_user"}, personal_session=True,
    secret_kind="cookie",
    label="Brevo (automation)", help="automations marketing (session Browserbase)",
    publisher="Brevo", href="https://app.brevo.com/automation/automations",
)

CATEGORY = "Automatisation"
LOGO_DOMAIN = "brevo.com"
