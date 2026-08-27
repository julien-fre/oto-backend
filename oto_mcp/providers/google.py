"""Déclaration de registre du connecteur `google`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# namespaces = préfixes RÉELS des tools (namespace_of = 1er token avant `_`) :
# gmail_* / tasks_*. PAS "data" : datastore est un SPINE plateforme (ADR 0016),
# pas un connecteur Google — chargé explicitement dans register_all, non gaté
# par l'activation (cf. middleware.py « tools plateforme … data … jamais gatés »).
CONNECTOR = _c(
    "google", ["gmail", "tasks", "calendar", "sheets", "drive", "chat"],
    auth_modes={"byo_user"},
    personal_session=True, secret_kind="oauth",
    label="Google", help="Gmail + Tasks + Calendar + Sheets + Drive + Chat (OAuth)",
    modules=("gmail", "tasks", "calendar", "sheets", "drive", "chat"),
)

CATEGORY = "Comms"
PUBLISHER = "Google"
LOGO_DOMAIN = "google.com"
