"""Déclaration de registre du connecteur `browser`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# browser : connecteur GÉNÉRIQUE de lecture derrière login (oto-private#79). Les
# trois précédents sont écrits en dur pour UNE API privée qu'on exploite en
# profondeur ; celui-ci sert le besoin inverse — lire N sites (média payant,
# intranet, back-office sans API) sans un cycle de dev par site. **Multi-compte** :
# un compte du coffre = un site (host), donc un Context Browserbase par site
# (sessions isolées, cf. MULTI_ACCOUNT_PROVIDERS). byo_user : une session loguée
# est physiologiquement personnelle. Hors socle, installable depuis la library ;
# `browser_eval` (JS arbitraire) reste masqué par défaut (DEFAULT_HIDDEN_TOOLS).
CONNECTOR = _c(
    "browser", ["browser"], auth_modes={"byo_user"}, personal_session=True,
    secret_kind="cookie", label="Navigateur connecté", account_noun="site",
    help="lire un site derrière login — un login par site, session Browserbase",
)

SANS_LOGO_DE_MARQUE = True
