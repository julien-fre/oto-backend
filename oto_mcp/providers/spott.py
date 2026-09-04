"""Déclaration de registre du connecteur `spott`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# spott : ATS **et** CRM des cabinets de recrutement (agences/staffing) — le
# candidat ET l'entreprise cliente dans le même produit, d'où un périmètre plus
# large que les autres ATS (clients, contacts clients, placements/honoraires).
# keyed api_key (header x-api-key), byo-only : chaque cabinet pose SA clé.
CONNECTOR = _c(
    "spott", ["spott"], auth_modes={"byo_user", "byo_org"}, keyed=True,
    secret_kind="api_key", label="Spott",
    help="ATS et CRM des cabinets de recrutement — candidats, offres et "
         "candidatures, mais aussi clients et placements",
    href="https://spott.io",
)

CATEGORY = "Recrutement"
PUBLISHER = "Spott"
LOGO_DOMAIN = "spott.io"

DESCRIPTION = (
    "L'ATS ET le CRM d'un cabinet de recrutement : candidats, offres et "
    "candidatures, mais aussi les entreprises clientes et les placements "
    "facturés — le candidat et le client dans le même produit."
)
