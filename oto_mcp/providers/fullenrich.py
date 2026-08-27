"""Déclaration de registre du connecteur `fullenrich`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# ⚠️ **Sans `platform_key_open`, et c'est le lot L5** (blueprint ADR 0053) : le
# premier connecteur dont l'accès à la clé plateforme passe par une ARÊTE de
# `grants` et non plus par un flag + une allowlist dans la ligne du coffre
# (`grants_chain.CHAIN_CONNECTORS`). Le flag n'avait qu'une fonction — empêcher
# qu'un grant individuel FERME la clé partagée pour tous (le pansement de
# l'incident du 31/07, oto-backend#245) — et cette fonction n'a plus d'objet :
# `credentials_store.platform_grant` ne touche plus la ligne du coffre pour ce
# connecteur, il pose une arête. Conséquence assumée et VRAIE : le catalogue
# cesse d'annoncer un free-tier fullenrich (`public_catalog` le dérive de ce
# flag) — sous le modèle de chaîne, la clé plateforme s'accorde, elle n'est pas
# ouverte. Les neuf autres connecteurs à `platform_key_open` ne bougent pas
# (tripwire `tests/test_grants_l5_platform_chain.py`).
CONNECTOR = _c(
    "fullenrich", ["fullenrich"], auth_modes={"byo_user", "byo_org", "platform"}, keyed=True,
    secret_kind="api_key", default_quota=5,
    label="FullEnrich", help="enrichissement waterfall", href="https://app.fullenrich.com",
)

CATEGORY = "Prospection"
PUBLISHER = "FullEnrich"
LOGO_DOMAIN = "fullenrich.com"
