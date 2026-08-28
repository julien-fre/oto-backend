"""Déclaration de registre du connecteur `droit`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# droit : jurisprudence (juris_*) + codes consolidés (loi_*) + conventions
# collectives (ccn_*), servis par le service FOD (fod/juris, fod/loi, fod/ccn). Extrait
# de `sirene`/`fr` (n'était pas de l'INSEE : DILA/Justice/Légifrance). Open
# data, sans clé. 3 namespaces → 1 carte « Info légale FR ».
CONNECTOR = _c(
    "droit", ["juris", "loi", "ccn"], secret_kind="none",
    label="Info légale FR",
    help="jurisprudence, codes consolidés, conventions collectives (open data DILA/Légifrance)",
    href="https://www.legifrance.gouv.fr", modules=("droit",),
)

CATEGORY = "Data FR"
PUBLISHER = "Légifrance / DILA"
DESCRIPTION = (
    "L'information légale française : jurisprudence (Cour de "
    "cassation, Conseil d'État, Conseil constitutionnel, CEDH/CJUE), "
    "codes consolidés versionnés (texte en vigueur à une date) et "
    "conventions collectives de branche (KALI). Sources "
    "DILA/Légifrance."
)
LOGO_DOMAIN = "legifrance.gouv.fr"
