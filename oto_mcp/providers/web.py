"""Déclaration de registre du connecteur `web`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# Le lecteur qui ESCALADE (#348) : fetch nu → scraper serper → navigateur
# jetable opt-in. Capacité NUE (ADR 0010 : les fournisseurs des crans ne
# sont pas substituables par l'appelant, c'est une cascade) ; pas de
# credential propre — chaque cran résout le sien (serper par la cascade,
# Browserbase par la config plateforme).
CONNECTOR = _c(
    "web", ["web"], secret_kind="none",
    label="Lecteur de page web",
    help="lire une page web publique, même quand elle résiste — fetch, puis "
         "scraper, puis navigateur jetable (payant, sur demande)",
)

CATEGORY = "Web"
# Éditeur : capacité NUE et maison — c'est notre cascade qui rend le service, et aucun
# de ses crans n'est un service que l'appelant choisit. DÉCLARÉ, et pas dérivé d'un
# défaut : depuis le 2026-09-02 il n'y en a plus (`Connector.publisher_name`).
PUBLISHER = "Otomata"
SANS_LOGO_DE_MARQUE = True

DESCRIPTION = (
    "Lire une page web publique, même quand elle résiste à un simple fetch : le "
    "lecteur escalade de lui-même — fetch nu, puis scraper, puis navigateur "
    "jetable en dernier recours (payant, sur demande explicite). Pas un moteur "
    "de recherche : donne une URL, pas une requête."
)
