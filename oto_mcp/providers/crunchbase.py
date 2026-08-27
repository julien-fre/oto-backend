"""Déclaration de registre du connecteur `crunchbase`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# LinkedIn n'est plus un connecteur browser ici : remplacé par le connecteur
# `unipile` (LinkedIn hébergé). Le browser LinkedIn local reste dans oto-cli.
# crunchbase : fiches société/personne via l'API PRIVÉE du frontend
# (`www.crunchbase.com/v4/data`, schéma v4 sans user_key). Exécution =
# **Browserbase** (Chrome distant hébergé, ADR 0026) : l'user se logue 1× via
# Live View (`crunchbase_connect_start`), sa session persiste dans un Context =
# le credential per-user (coffre `crunchbase`). Plus de scraping DOM in-process.
CONNECTOR = _c(
    "crunchbase", ["crunchbase"], auth_modes={"byo_user"}, personal_session=True,
    secret_kind="cookie", label="Crunchbase",
    help="fiches société/personne (session Browserbase)", publisher="Crunchbase",
    href="https://www.crunchbase.com/",
)

CATEGORY = "Prospection"
PUBLISHER = "Crunchbase"
LOGO_DOMAIN = "crunchbase.com"
